"""Merchant proposals: list, generate and decide -- through the existing approvals machinery (#93, M-3).

Every proposal batch has exactly one row in ``approvals`` (kind ``merchant_proposal``, source ``proposal``,
see :mod:`shared.merchant.proposals`), and this module moves that row with the same repository calls the
tool approvals use: ``repo.claim`` (a fenced, TTL'd claim, so two concurrent decides cannot both run),
guarded ``repo.transition`` (status, decider, audit event, all fenced by the claim token) and the same
reviewer-is-never-the-requester rule. There is no second approval system and no second state machine.

Who may decide. Only the seller who owns the batch (``proposals.seller_id``), or an ``admin``. A proposal that
belongs to someone else is answered 404, exactly as one that does not exist, so a seller cannot probe for
other sellers' ids. ``support`` is not a seller and may not decide a merchant's catalogue changes. The
requester on the approval is the system actor (:data:`shared.merchant.proposals.SYSTEM_REQUESTER`), so the
owning seller deciding is a reviewer deciding someone else's request, and the database CHECK
(``decided_by <> requester_email``) holds for every decision.

What a decision is. A batch is decided in one call: for each line, ``accept``, ``edit`` (accept at a different
value) or ``reject``. A line left out is rejected. A line the guards blocked when it was proposed can only be
rejected. Accepted lines are executed through :func:`shared.merchant.execute.execute_line`, which re-checks
the guards against the live facts and runs each line at most once (``proposal_line:{id}``). The approval is
recorded only after execution, as with tool approvals, so a crash leaves it open for a safe retry.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import asyncpg

from orchestrator.approvals_service import (
    Actor,
    ApprovalHTTPError,
    _claim_and_reauthorize,  # noqa: PLC2701 - the same claim-then-reauthorize every staff decision takes
    is_seller_stage,
    jsonable,
)
from shared.approvals import repo
from shared.approvals.repo import ApprovalStatus as S
from shared.config import settings
from shared.db import get_pool
from shared.log_safe import log_safe
from shared.merchant import execute as merchant_execute
from shared.merchant import proposals as merchant_proposals
from shared.merchant.guards import check_edit_bounds
from shared.merchant.limits import INT32_MAX, NUMERIC_12_2_MAX, get_limits
from shared.notifications import notify
from shared.policy import evaluate, get_policy

logger = logging.getLogger(__name__)

MAX_NOTE_LENGTH = 2000
_MAX_EDIT_VALUE = NUMERIC_12_2_MAX
_MAX_QUANTITY = Decimal(INT32_MAX)
_DECISIONS = ("accept", "edit", "reject")
_OPEN_LINE = ("proposed", "accepted", "edited")
#: Restock lines that count toward a batch's PO value: still to be decided, held for a manager, or already ordered.
_PO_LINE = ("proposed", "accepted", "edited", "awaiting_manager", "executed")
_PO_LINK = "/admin/approvals"


@dataclass(frozen=True)
class LineDecision:
    line_id: str
    decision: str  # accept | edit | reject
    value: Decimal | None = None


# ── PO value (#298) ──────────────────────────────────────────────────────────


def po_threshold() -> float | None:
    """The PO value above which the policy sends a batch to a manager (None: the policy has no such rule)."""
    for rule in get_policy().kind(merchant_proposals.APPROVAL_KIND).rules:
        if rule.route != "manager":
            continue
        for cond in rule.when:
            if cond.fact == "amount" and cond.op in ("gt", "gte") and isinstance(cond.value, float):
                return cond.value
    return None


def line_po_value(quantity: Decimal | None, unit_cost: Decimal | None) -> Decimal | None:
    """What one restock line commits: quantity x unit cost. None when either is unknown (never a guess)."""
    if quantity is None or unit_cost is None:
        return None
    return Decimal(quantity) * Decimal(unit_cost)


def _batch_po_value(decisions: dict[str, LineDecision], line_rows: dict[str, asyncpg.Record]) -> Decimal:
    """The PO value of what the seller is accepting now, plus restock lines an earlier attempt already held.

    A line without a recorded unit cost adds nothing: it cannot be valued, and execution blocks it anyway
    (``check_restock_line``), so it can never spend.
    """
    total = Decimal(0)
    for line_id, row in line_rows.items():
        if row["field"] != "restock_quantity":
            continue
        decision = decisions.get(line_id)
        if decision is not None:
            if decision.decision == "reject":
                continue
            quantity = decision.value if decision.decision == "edit" else row["proposed_value"]
        elif row["status"] == "awaiting_manager":
            quantity = row["final_value"]  # held by an interrupted earlier attempt: still part of this batch
        else:
            continue
        total += line_po_value(quantity, row["unit_cost"]) or Decimal(0)
    return total


# ── identity and ownership ───────────────────────────────────────────────────


async def seller_id_for(email: str) -> uuid.UUID | None:
    return await get_pool().fetchval("SELECT id FROM users WHERE lower(email) = lower($1)", email)


def _uuid(value: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise ApprovalHTTPError(404, f"{what} not found") from None


async def _load_for(proposal_id: str, actor: Actor) -> asyncpg.Record:
    """The proposal, if ``actor`` may see it (its seller, or an admin); otherwise 404 -- never 403."""
    row = await get_pool().fetchrow(
        """SELECT pr.*, u.email AS seller_email, u.name AS seller_name
             FROM proposals pr JOIN users u ON u.id = pr.seller_id WHERE pr.id = $1""",
        _uuid(proposal_id, "Proposal"),
    )
    if row is None:
        raise ApprovalHTTPError(404, "Proposal not found")
    if actor.role != "admin" and (actor.role != "seller" or row["seller_email"].lower() != actor.email.lower()):
        raise ApprovalHTTPError(404, "Proposal not found")
    return row


# ── reads ────────────────────────────────────────────────────────────────────


def _line_json(row: asyncpg.Record) -> dict[str, Any]:
    guard = row["guard_result"]
    evidence = row["evidence"]
    restock = row["field"] == "restock_quantity"
    quantity = row["final_value"] if row["final_value"] is not None else row["proposed_value"]
    return jsonable(
        {
            "id": row["id"],
            "unit_cost": row["unit_cost"] if restock else None,
            "po_value": line_po_value(quantity, row["unit_cost"]) if restock else None,
            "product_id": row["product_id"],
            "product_name": row["product_name"],
            "field": row["field"],
            "current_value": row["current_value"],
            "proposed_value": row["proposed_value"],
            "final_value": row["final_value"],
            "rationale": row["rationale"],
            "evidence": json.loads(evidence) if isinstance(evidence, str) else (evidence or {}),
            "guard": json.loads(guard) if isinstance(guard, str) else guard,
            "status": row["status"],
            "executed_at": row["executed_at"],
        }
    )


async def _lines(pool: asyncpg.Pool, proposal_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[dict[str, Any]]]:
    rows = await pool.fetch(
        """SELECT l.*, p.name AS product_name, p.unit_cost
             FROM proposal_lines l JOIN products p ON p.id = l.product_id
            WHERE l.proposal_id = ANY($1::uuid[])
            ORDER BY l.created_at, p.name, l.field, l.id""",
        proposal_ids,
    )
    out: dict[uuid.UUID, list[dict[str, Any]]] = {pid: [] for pid in proposal_ids}
    for row in rows:
        out[row["proposal_id"]].append(_line_json(row))
    return out


async def _approvals_for(pool: asyncpg.Pool, proposal_ids: list[uuid.UUID]) -> dict[str, dict[str, Any]]:
    rows = await pool.fetch(
        "SELECT * FROM approvals WHERE source = $1 AND source_ref = ANY($2::uuid[])",
        merchant_proposals.SOURCE,
        proposal_ids,
    )
    return {str(r["source_ref"]): r for r in rows}


def _po_summary(lines: list[dict[str, Any]]) -> dict[str, Any]:
    """The batch's PO value, the threshold, and whether the policy would send it to a manager."""
    value = sum(
        (
            Decimal(str(line["po_value"]))
            for line in lines
            if line["po_value"] is not None and line["status"] in _PO_LINE
        ),
        Decimal(0),
    )
    verdict = evaluate(merchant_proposals.APPROVAL_KIND, {"amount": value})
    return {"value": value, "threshold": po_threshold(), "needs_manager": verdict.route == "manager"}


def _proposal_json(row: asyncpg.Record, lines: list[dict[str, Any]], approval: asyncpg.Record | None) -> dict[str, Any]:
    return jsonable(
        {
            "stage": "manager" if approval is not None and approval["route"] is not None else "seller",
            "po": _po_summary(lines),
            "id": row["id"],
            "seller_email": row["seller_email"],
            "kind": row["kind"],
            "trigger": row["trigger"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "approval_id": approval["id"] if approval else None,
            "approval_status": approval["status"] if approval else None,
            "due_at": approval["due_at"] if approval else None,
            "lines": lines,
        }
    )


async def list_proposals(actor: Actor, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """The caller's proposals, newest first (an admin sees every seller's)."""
    pool = get_pool()
    where, args = [], []
    if actor.role != "admin":
        args.append(actor.email)
        where.append(f"lower(u.email) = lower(${len(args)})")
    if status:
        args.append(status)
        where.append(f"pr.status = ${len(args)}")
    args.append(max(1, min(int(limit), 200)))
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = await pool.fetch(
        f"""SELECT pr.*, u.email AS seller_email, u.name AS seller_name
              FROM proposals pr JOIN users u ON u.id = pr.seller_id {clause}
             ORDER BY pr.created_at DESC, pr.id LIMIT ${len(args)}""",  # noqa: S608 - clause is built from fixed text
        *args,
    )
    ids = [r["id"] for r in rows]
    lines = await _lines(pool, ids)
    approvals = await _approvals_for(pool, ids)
    return [_proposal_json(r, lines[r["id"]], approvals.get(str(r["id"]))) for r in rows]


async def get_proposal(proposal_id: str, actor: Actor) -> dict[str, Any]:
    row = await _load_for(proposal_id, actor)
    pool = get_pool()
    lines = await _lines(pool, [row["id"]])
    approvals = await _approvals_for(pool, [row["id"]])
    return _proposal_json(row, lines[row["id"]], approvals.get(str(row["id"])))


# ── generate ─────────────────────────────────────────────────────────────────


async def run_generation(actor: Actor, *, seller_email: str | None = None) -> dict[str, Any]:
    """Generate a proposal for the caller (a seller) or, for an admin, for the named seller."""
    target_email = actor.email
    if actor.role == "admin":
        if not seller_email:
            raise ApprovalHTTPError(422, "Name the seller to generate proposals for")
        target_email = seller_email
    seller_id = await seller_id_for(target_email)
    role = await get_pool().fetchval("SELECT role FROM users WHERE id = $1", seller_id) if seller_id else None
    if seller_id is None or role != "seller":
        raise ApprovalHTTPError(404, "Seller not found")
    result = await merchant_proposals.generate_proposal(get_pool(), seller_id, trigger="manual")
    return result.to_dict()


# ── decide ───────────────────────────────────────────────────────────────────


def _parse_decisions(raw: list[dict[str, Any]], lines: dict[str, asyncpg.Record]) -> dict[str, LineDecision]:
    """Validate the per-line decisions against the proposal's own lines. Raises 422 on anything off."""
    decisions: dict[str, LineDecision] = {}
    for item in raw:
        line_id = str(item.get("line_id", ""))
        if line_id not in lines:
            raise ApprovalHTTPError(422, "A decision names a line that is not in this proposal")
        if line_id in decisions:
            raise ApprovalHTTPError(422, "A line is decided twice")
        decision = item.get("decision")
        if decision not in _DECISIONS:
            raise ApprovalHTTPError(422, "Each line is accept, edit or reject")
        line = lines[line_id]
        value: Decimal | None = None
        if decision == "edit":
            try:
                value = Decimal(str(item.get("value")))
            except (InvalidOperation, ValueError):
                raise ApprovalHTTPError(422, "An edit needs a numeric value") from None
            if not value.is_finite() or value <= 0:
                raise ApprovalHTTPError(422, "An edit needs a positive value")
            # Bound it before any arithmetic: a huge exponent makes quantize() raise, and a value past the column
            # (NUMERIC(12,2) for a price, INTEGER for a quantity) would be a database error rather than a refusal.
            if value > _MAX_EDIT_VALUE:
                raise ApprovalHTTPError(422, "An edit value is too large")
            if line["field"] == "restock_quantity":
                if value != value.to_integral_value():
                    raise ApprovalHTTPError(422, "A restock quantity is a whole number")
                if value > _MAX_QUANTITY:
                    raise ApprovalHTTPError(422, "An edit value is too large")
            value = value.quantize(Decimal("0.01"))
            if value <= 0:
                raise ApprovalHTTPError(422, "An edit needs a positive value")
            # An edit that leaves the price where it is changes nothing and would only write a price_history row.
            if line["field"] == "price" and line["current_value"] is not None and value == line["current_value"]:
                raise ApprovalHTTPError(422, "That is the current price: no change — reject the line instead")
            # A reviewer's edit can never raise a line (the repo-wide rule): the same check runs again at apply time.
            edit = check_edit_bounds(
                field=line["field"],
                current_value=line["current_value"],
                proposed_value=line["proposed_value"],
                final_value=value,
                limits=get_limits(),
            )
            if edit.blocked:
                raise ApprovalHTTPError(
                    422,
                    "An edit cannot raise a restock quantity above what was proposed"
                    if line["field"] == "restock_quantity"
                    else "An edit must stay between the proposed price and the current price",
                )
        elif item.get("value") is not None:
            raise ApprovalHTTPError(422, "Only an edit carries a value")
        if decision != "reject" and line["status"] == "blocked":
            raise ApprovalHTTPError(422, "A blocked line can only be rejected")
        if line["status"] not in (*_OPEN_LINE, "blocked"):
            continue  # settled by an earlier attempt that was interrupted before the approval was recorded
        decisions[line_id] = LineDecision(line_id, decision, value)
    return decisions


async def decide(
    proposal_id: str,
    actor: Actor,
    *,
    lines: list[dict[str, Any]],
    note: str | None = None,
) -> dict[str, Any]:
    """Apply a seller's decision to a whole batch; returns the refreshed proposal and the per-line outcomes."""
    if actor.role not in ("seller", "admin"):
        raise ApprovalHTTPError(403, "Seller access required")
    proposal = await _load_for(proposal_id, actor)
    pool = get_pool()
    approval = await pool.fetchrow(
        "SELECT * FROM approvals WHERE source = $1 AND source_ref = $2", merchant_proposals.SOURCE, proposal["id"]
    )
    if approval is None:
        raise ApprovalHTTPError(409, "This proposal has no approval to decide")
    if repo._same_person(actor.email, approval["requester_email"]):  # noqa: SLF001 - the one rule, same comparison
        raise ApprovalHTTPError(403, "You cannot approve your own request")
    status = S(approval["status"])
    if status is not S.PENDING_REVIEW:
        raise ApprovalHTTPError(409, f"Proposal is already {status.value}")
    if not is_seller_stage(approval):  # the seller has decided; the PO tier is staff's (#298)
        raise ApprovalHTTPError(409, "Proposal is waiting for a manager's approval")
    note = (note or "").strip()[:MAX_NOTE_LENGTH] or None

    line_rows = {
        str(r["id"]): r
        for r in await pool.fetch(
            """SELECT l.id, l.field, l.status, l.current_value, l.proposed_value, l.final_value, p.unit_cost
                 FROM proposal_lines l JOIN products p ON p.id = l.product_id WHERE l.proposal_id = $1""",
            proposal["id"],
        )
    }
    decisions = _parse_decisions(lines, line_rows)
    for line_id, row in line_rows.items():  # a line left out is rejected (a blocked one is already settled)
        if line_id not in decisions and row["status"] in _OPEN_LINE:
            decisions[line_id] = LineDecision(line_id, "reject")
    # The batch's PO value decides, through the approvals policy, whether the seller may order alone. Over the
    # threshold the seller's accepted restock lines are recorded but wait for a manager (#298).
    po_value = _batch_po_value(decisions, line_rows)
    verdict = evaluate(merchant_proposals.APPROVAL_KIND, {"amount": po_value})
    held = frozenset(
        line_id
        for line_id, d in decisions.items()
        if verdict.route == "manager" and d.decision != "reject" and line_rows[line_id]["field"] == "restock_quantity"
    )

    token = uuid.uuid4().hex
    claimed = await repo.claim(
        pool, str(approval["id"]), token, statuses=[S.PENDING_REVIEW], ttl_seconds=settings.HITL_CLAIM_TTL_SECONDS
    )
    if claimed is None:
        raise ApprovalHTTPError(409, "Proposal is being decided by someone else")
    try:
        if not is_seller_stage(claimed):  # handed to a manager between the read above and the claim
            raise ApprovalHTTPError(409, "Proposal is waiting for a manager's approval")
        outcomes = await _apply(pool, proposal, decisions, line_rows, held)
        if await pool.fetchval(
            "SELECT count(*) FROM proposal_lines WHERE proposal_id = $1 AND status = 'awaiting_manager'", proposal["id"]
        ):
            record = await _hand_to_manager(approval, proposal, actor, token, decisions, note, po_value, verdict)
        else:
            record = await _record(approval, proposal, actor, token, decisions, outcomes, note)
    except BaseException:
        # Hand the claim back: executing is idempotent per line, so a retry finishes instead of repeating.
        await asyncio.shield(repo.release_claim(pool, str(approval["id"]), token))
        raise
    logger.info("proposal decided id=%s approval_status=%s", log_safe(proposal["id"]), log_safe(record["status"]))
    return {
        "proposal": await get_proposal(proposal_id, actor),
        "approval": jsonable(record),
        "results": outcomes,
    }


async def _apply(
    pool: asyncpg.Pool,
    proposal: asyncpg.Record,
    decisions: dict[str, LineDecision],
    line_rows: dict[str, asyncpg.Record],
    held: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Persist each line's decision, then execute the accepted ones (each at most once); one outcome per line.

    A line in ``held`` is accepted by the seller but not executed: it is recorded as ``awaiting_manager`` with the
    value the seller chose, and a manager decides it (:func:`decide_po`).
    """
    outcomes: list[dict[str, Any]] = []
    for line_id, decision in decisions.items():
        row = line_rows[line_id]
        if decision.decision == "reject":
            await pool.execute(
                "UPDATE proposal_lines SET status = 'rejected', final_value = NULL "
                "WHERE id = $1 AND status = ANY($2::text[])",
                uuid.UUID(line_id),
                list(_OPEN_LINE) + ["blocked"],
            )
            outcomes.append({"line_id": line_id, "status": "rejected", "success": False})
            continue
        value = decision.value if decision.decision == "edit" else row["proposed_value"]
        if line_id in held:
            await pool.execute(
                "UPDATE proposal_lines SET status = 'awaiting_manager', final_value = $2 "
                "WHERE id = $1 AND status = ANY($3::text[])",
                uuid.UUID(line_id),
                value,
                list(_OPEN_LINE),
            )
            outcomes.append({"line_id": line_id, "status": "awaiting_manager", "success": False})
            continue
        await pool.execute(
            "UPDATE proposal_lines SET status = $2, final_value = $3 WHERE id = $1 AND status = ANY($4::text[])",
            uuid.UUID(line_id),
            "edited" if decision.decision == "edit" else "accepted",
            value,
            list(_OPEN_LINE),
        )
        result = await merchant_execute.execute_line(pool, line_id, value)
        if "error" in result and "status" not in result:  # the run-once guard saw the same line in flight elsewhere
            raise ApprovalHTTPError(409, "A line is being executed by someone else")
        outcomes.append(result)
    return outcomes


async def _record(
    approval: asyncpg.Record,
    proposal: asyncpg.Record,
    actor: Actor,
    token: str,
    decisions: dict[str, LineDecision],
    outcomes: list[dict[str, Any]],
    note: str | None,
    *,
    stage: str = "seller",
) -> dict[str, Any]:
    """Record the decision on the approval (fenced by the claim), then its execution outcome, then the batch."""
    pool = get_pool()
    approval_id = str(approval["id"])
    accepted = [d for d in decisions.values() if d.decision != "reject"]
    executed = await pool.fetchval(
        "SELECT count(*) FROM proposal_lines WHERE proposal_id = $1 AND status = 'executed'", proposal["id"]
    )
    edited = any(d.decision == "edit" for d in decisions.values())
    approved = bool(accepted) or executed > 0  # an interrupted earlier attempt may already have executed lines
    payload = {
        "stage": stage,
        "decision": "approve" if approved else "reject",
        "note": note,
        "lines": [
            {"line_id": d.line_id, "decision": d.decision, "value": str(d.value) if d.value else None}
            for d in decisions.values()
        ],
    }
    target = (S.APPROVED_WITH_EDITS if edited else S.APPROVED) if approved else S.REJECTED
    try:
        decided = await repo.transition(
            pool,
            approval_id,
            [S.PENDING_REVIEW],
            target,
            actor_email=actor.email,
            actor_role=actor.role,
            action="decided",
            payload=payload,
            claimed_by=token,
            decided_by=actor.email,
            decision=payload,
        )
    except repo.ClaimLostError:
        raise ApprovalHTTPError(409, "This proposal was taken over by another reviewer") from None
    except repo.InvalidTransitionError:
        raise ApprovalHTTPError(409, "Proposal is no longer pending review") from None
    except repo.SelfApprovalError:
        raise ApprovalHTTPError(403, "You cannot approve your own request") from None

    final = decided
    if approved:
        final = await repo.transition(
            pool,
            approval_id,
            [S(decided["status"])],
            S.EXECUTED if executed else S.FAILED,
            actor_email=actor.email,
            actor_role=actor.role,
            action="executed" if executed else "execution_failed",
            payload={"executed": executed, "blocked": sum(1 for o in outcomes if o.get("status") == "blocked")},
        )
    await pool.execute(
        "UPDATE proposals SET status = $2, updated_at = NOW() WHERE id = $1",
        proposal["id"],
        "executed" if executed else "decided",
    )
    return final


# ── the manager tier (#298) ──────────────────────────────────────────────────


async def _hand_to_manager(
    approval: asyncpg.Record,
    proposal: asyncpg.Record,
    actor: Actor,
    token: str,
    decisions: dict[str, LineDecision],
    note: str | None,
    po_value: Decimal,
    verdict: Any,
) -> dict[str, Any]:
    """The seller has decided and restock lines wait: route the same approvals row to a manager, with a fresh clock.

    One transaction, fenced by the seller's claim: the route, the manager role, the new escalation level (so the
    seller-stage timers recognise themselves as stale), the deadline and its outbox timers, the proposal's status
    and the manager's notification commit together or not at all. The seller-stage decision is kept on the row
    (``recommendation.seller_stage``) because the manager tier must be a different person.
    """
    pool = get_pool()
    approval_id = str(approval["id"])
    now = datetime.now(UTC)
    held_ids = [
        str(r["id"])
        for r in await pool.fetch(
            "SELECT id FROM proposal_lines WHERE proposal_id = $1 AND status = 'awaiting_manager' ORDER BY id",
            proposal["id"],
        )
    ]
    seller_stage = {
        "decided_by": actor.email,
        "decided_at": now.isoformat(),
        "note": note,
        "po_value": str(po_value),
        "rule": verdict.evidence.get("rule"),
        "policy_version": verdict.version,
        "held_lines": held_ids,
        "lines": [
            {"line_id": d.line_id, "decision": d.decision, "value": str(d.value) if d.value else None}
            for d in decisions.values()
        ],
    }
    sla_hours = get_policy().kind(merchant_proposals.APPROVAL_KIND).sla_hours
    async with pool.acquire() as conn, conn.transaction():
        current = await conn.fetchrow("SELECT * FROM approvals WHERE id = $1 FOR UPDATE", approval["id"])
        if current is None or current["status"] != S.PENDING_REVIEW.value or current["route"] is not None:
            raise ApprovalHTTPError(409, "Proposal is no longer waiting for the seller")
        if current["claimed_by"] != token:
            raise ApprovalHTTPError(409, "This proposal was taken over by another reviewer")
        updated = await conn.fetchrow(
            """UPDATE approvals
                  SET route = 'manager', assigned_role = 'admin', escalation_level = escalation_level + 1,
                      due_at = $2, amount = $3, policy_version = $4, claimed_at = NULL, claimed_by = NULL,
                      recommendation = COALESCE(recommendation, '{}'::jsonb) || $5::jsonb, updated_at = NOW()
                WHERE id = $1 RETURNING *""",
            approval["id"],
            now + timedelta(hours=sla_hours),
            po_value.quantize(Decimal("0.01")),
            verdict.version,
            json.dumps({"seller_stage": seller_stage}),
        )
        await repo.append_event(
            conn, approval_id, "handed_to_manager", actor_email=actor.email, actor_role=actor.role, payload=seller_stage
        )
        await repo.schedule_review_timers(conn, dict(updated), now=now)
        await conn.execute(
            "UPDATE proposals SET status = 'awaiting_manager', updated_at = NOW() WHERE id = $1", proposal["id"]
        )
        await notify(
            conn,
            kind="merchant.po_review",
            title="A restock order needs manager approval",
            body=f"A seller accepted restock lines worth {po_value:.2f}, over the PO threshold. Review and decide.",
            link=_PO_LINK,
            approval_id=approval_id,
            role="admin",
        )
    logger.info("proposal handed to manager id=%s lines=%s", log_safe(proposal["id"]), len(held_ids))
    return await repo.get(pool, approval_id) or {}


def _parse_po_edits(edits: dict[str, Any], held: dict[str, asyncpg.Record]) -> dict[str, Decimal]:
    """A manager's per-line edits, ``{"final_value": {line_id: quantity}}``: lowered only, whole units."""
    editable = set(get_policy().kind(merchant_proposals.APPROVAL_KIND).editable_fields)
    if set(edits) - editable:
        raise ApprovalHTTPError(422, "Only a line's final value can be edited")
    raw = edits.get("final_value")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ApprovalHTTPError(422, "final_value maps a line id to the quantity to order")
    values: dict[str, Decimal] = {}
    for line_id, item in raw.items():
        row = held.get(str(line_id))
        if row is None or row["status"] != "awaiting_manager":
            raise ApprovalHTTPError(422, "An edit names a line that is not waiting for approval")
        try:
            value = Decimal(str(item))
        except (InvalidOperation, ValueError):
            raise ApprovalHTTPError(422, "An edit needs a numeric value") from None
        if not value.is_finite() or value <= 0 or value > _MAX_QUANTITY or value != value.to_integral_value():
            raise ApprovalHTTPError(422, "A restock quantity is a positive whole number")
        ceiling = min(row["final_value"], row["proposed_value"])
        if value > ceiling:  # the repo-wide rule: a reviewer never raises a line
            raise ApprovalHTTPError(422, "An edit cannot raise a restock quantity above what the seller accepted")
        if value != ceiling:
            values[str(line_id)] = value.quantize(Decimal("0.01"))
    return values


async def decide_po(
    approval: dict[str, Any], actor: Actor, *, decision: str, edits: dict[str, Any], note: str | None
) -> dict[str, Any]:
    """A manager approves or rejects the restock lines a seller accepted over the PO threshold.

    Reached from ``approvals_service.decide`` (the staff inbox) once the row is routed to a manager; the caller has
    already refused the seller-stage row and run ``authorize_decider`` (staff, at the manager level, never the
    requester or the subject). Approving executes each held line through ``execute_line``, which re-checks the guards
    on live facts, at most once. The manager may lower a line, never raise it. The seller-stage decider may not also
    be the manager: one person cannot approve their own purchase order. The result has the same shape as any staff
    decision, ``{"approval": ..., "result": ...}``.
    """
    if decision not in ("approve", "reject", "request_info"):
        raise ApprovalHTTPError(422, "decision must be approve, reject or request_info")
    if decision == "request_info":
        # The requester is the system actor: nobody could answer, and the approval would sit in needs_info.
        raise ApprovalHTTPError(422, "A restock order cannot ask for more information; approve or reject it")
    if decision == "reject" and edits:
        raise ApprovalHTTPError(422, "A rejection takes no edits")
    note = (note or "").strip()[:MAX_NOTE_LENGTH] or None
    status = S(approval["status"])
    if status is not S.PENDING_REVIEW:
        raise ApprovalHTTPError(409, f"Request is already {status.value}")
    pool = get_pool()
    token, fresh = await _claim_and_reauthorize(approval, actor, S.PENDING_REVIEW)
    approval_id = str(fresh["id"])
    try:
        if is_seller_stage(fresh):
            raise ApprovalHTTPError(404, "Approval not found")
        seller_stage = (fresh.get("recommendation") or {}).get("seller_stage") or {}
        if repo._same_person(actor.email, seller_stage.get("decided_by")):  # noqa: SLF001 - the one comparison
            raise ApprovalHTTPError(403, "You decided the seller's side of this batch; another manager must approve it")
        proposal = await pool.fetchrow("SELECT * FROM proposals WHERE id = $1", fresh["source_ref"])
        if proposal is None:
            raise ApprovalHTTPError(404, "Approval not found")
        rows = {
            str(r["id"]): r
            for r in await pool.fetch(
                """SELECT id, field, status, proposed_value, final_value FROM proposal_lines
                    WHERE proposal_id = $1 AND status IN ('awaiting_manager', 'accepted', 'edited')""",
                proposal["id"],
            )
        }
        decisions: dict[str, LineDecision] = {}
        if decision == "reject":
            for line_id, row in rows.items():
                if row["status"] == "awaiting_manager":
                    decisions[line_id] = LineDecision(line_id, "reject")
        else:
            values = _parse_po_edits(edits, rows)
            for line_id, row in rows.items():
                value = values.get(line_id)
                if row["status"] == "edited":  # an interrupted manager attempt: finish what it chose
                    value = row["final_value"]
                decisions[line_id] = LineDecision(line_id, "edit" if value is not None else "accept", value)
        outcomes = await _apply_po(pool, decisions, rows)
        record = await _record(fresh, proposal, actor, token, decisions, outcomes, note, stage="manager")
    except BaseException:
        await asyncio.shield(repo.release_claim(pool, approval_id, token))
        raise
    if fresh.get("subject_email"):
        applied = sum(1 for o in outcomes if o.get("status") == "executed")
        try:
            await notify(
                pool,
                kind="merchant.po_decided",
                title="Your restock order was decided",
                body=(
                    f"A manager decided the restock lines over the PO threshold: {applied} order(s) placed."
                    if applied
                    else "A manager decided the restock lines over the PO threshold: nothing was ordered."
                ),
                link="/seller/proposals",
                approval_id=approval_id,
                user_email=fresh["subject_email"],
            )
        except Exception:  # the decision is recorded; a lost notification must not turn it into a 500
            logger.exception("po decision notification failed id=%s", log_safe(approval_id))
    logger.info("po tier decided id=%s status=%s", log_safe(approval_id), log_safe(record["status"]))
    return {"approval": jsonable(record), "result": {"status": record["status"], "lines": outcomes}}


async def _apply_po(
    pool: asyncpg.Pool, decisions: dict[str, LineDecision], rows: dict[str, asyncpg.Record]
) -> list[dict[str, Any]]:
    """Persist the manager's decision per held line and execute the approved ones, each at most once."""
    outcomes: list[dict[str, Any]] = []
    for line_id, d in decisions.items():
        if d.decision == "reject":
            await pool.execute(
                "UPDATE proposal_lines SET status = 'rejected', final_value = NULL "
                "WHERE id = $1 AND status = 'awaiting_manager'",
                uuid.UUID(line_id),
            )
            outcomes.append({"line_id": line_id, "status": "rejected", "success": False})
            continue
        row = rows[line_id]
        value = d.value if d.value is not None else row["final_value"]
        await pool.execute(
            "UPDATE proposal_lines SET status = $2, final_value = $3 WHERE id = $1 AND status = 'awaiting_manager'",
            uuid.UUID(line_id),
            "edited" if d.decision == "edit" else "accepted",
            value,
        )
        result = await merchant_execute.execute_line(pool, line_id, value)
        if "error" in result and "status" not in result:
            raise ApprovalHTTPError(409, "A line is being executed by someone else")
        outcomes.append(result)
    return outcomes


async def po_review_detail(pool: asyncpg.Pool, approval: dict[str, Any]) -> dict[str, Any] | None:
    """What a manager sees for a restock batch over the PO threshold: the lines, their PO value, the seller's stage."""
    ref = approval.get("source_ref")
    if not ref:
        return None
    key = uuid.UUID(str(ref))
    lines = (await _lines(pool, [key])).get(key, [])
    restock = [line for line in lines if line["field"] == "restock_quantity"]
    return jsonable(
        {
            "proposal_id": ref,
            "stage": "seller" if is_seller_stage(approval) else "manager",
            "po": _po_summary(lines),
            "seller_stage": (approval.get("recommendation") or {}).get("seller_stage"),
            "lines": [
                {
                    "line_id": line["id"],
                    "product": line["product_name"],
                    "status": line["status"],
                    "quantity_proposed": line["proposed_value"],
                    "quantity_accepted": line["final_value"],
                    "unit_cost": line["unit_cost"],
                    "po_value": line["po_value"],
                }
                for line in restock
            ],
        }
    )
