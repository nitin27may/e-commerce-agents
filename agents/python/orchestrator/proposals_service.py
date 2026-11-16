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
from decimal import Decimal, InvalidOperation
from typing import Any

import asyncpg

from orchestrator.approvals_service import Actor, ApprovalHTTPError, jsonable
from shared.approvals import repo
from shared.approvals.repo import ApprovalStatus as S
from shared.config import settings
from shared.db import get_pool
from shared.log_safe import log_safe
from shared.merchant import execute as merchant_execute
from shared.merchant import proposals as merchant_proposals

logger = logging.getLogger(__name__)

MAX_NOTE_LENGTH = 2000
_DECISIONS = ("accept", "edit", "reject")
_OPEN_LINE = ("proposed", "accepted", "edited")


@dataclass(frozen=True)
class LineDecision:
    line_id: str
    decision: str  # accept | edit | reject
    value: Decimal | None = None


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
    return jsonable(
        {
            "id": row["id"],
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
        """SELECT l.*, p.name AS product_name
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


def _proposal_json(row: asyncpg.Record, lines: list[dict[str, Any]], approval: asyncpg.Record | None) -> dict[str, Any]:
    return jsonable(
        {
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
            if line["field"] == "restock_quantity" and value != value.to_integral_value():
                raise ApprovalHTTPError(422, "A restock quantity is a whole number")
            value = value.quantize(Decimal("0.01"))
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
    note = (note or "").strip()[:MAX_NOTE_LENGTH] or None

    line_rows = {
        str(r["id"]): r
        for r in await pool.fetch(
            "SELECT id, field, status, proposed_value FROM proposal_lines WHERE proposal_id = $1", proposal["id"]
        )
    }
    decisions = _parse_decisions(lines, line_rows)
    for line_id, row in line_rows.items():  # a line left out is rejected (a blocked one is already settled)
        if line_id not in decisions and row["status"] in _OPEN_LINE:
            decisions[line_id] = LineDecision(line_id, "reject")

    token = uuid.uuid4().hex
    claimed = await repo.claim(
        pool, str(approval["id"]), token, statuses=[S.PENDING_REVIEW], ttl_seconds=settings.HITL_CLAIM_TTL_SECONDS
    )
    if claimed is None:
        raise ApprovalHTTPError(409, "Proposal is being decided by someone else")
    try:
        outcomes = await _apply(pool, proposal, decisions, line_rows)
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
) -> list[dict[str, Any]]:
    """Persist each line's decision, then execute the accepted ones (each at most once); one outcome per line."""
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
