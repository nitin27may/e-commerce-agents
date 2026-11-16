"""The autonomy engine (#94): turn an event into a logged decision, acting alone only inside a budget.

``handle_event`` is the whole flow for one event, run **at most once per event** (``run_once`` keyed by the
event, and a unique index on ``agent_decisions.event_key`` behind it):

1. A deterministic rule sizes the action from the event (``order.shipment_delayed`` -> a goodwill credit).
2. The tier rules (``autonomy.yaml``) pick ``auto`` / ``auto_notify`` / ``needs_approval`` from the facts. A fact
   that could not be determined never satisfies a condition, so missing evidence can only ask.
3. An autonomous tier must also fit every budget cap. **The check and the draw are one atomic step**: the whole
   decision runs in one transaction that first takes a Postgres advisory lock on ``(budget, UTC day)``. Two
   events racing for the last of a budget serialise on that lock; the second counts the first's committed draw
   and is turned into an approval. (The lock is per budget and day, so unrelated budgets never wait.) The decision
   then takes a second lock on the *order* (see :func:`lock_order`: the per-order cap is not a daily cap, and a
   day-keyed lock cannot serialise two events for one order that straddle midnight). Lock order: budget, order, rows.
4. Autonomous: the credit, the decision row, the customer's notification (and a staff heads-up for
   ``auto_notify``) commit together. Needs approval: the approval (kind ``autonomous_action``, source ``event``)
   and the decision row commit together; nothing is done until staff decide it
   (``orchestrator/approvals_service.py``).

``agent_decisions`` and ``store_credit_ledger`` are append-only (migration 0012). What happens to a decision later
is a new row: ``approved_execution`` when staff approve one, ``reversal`` when an admin undoes one. A reversal is
a negating ledger entry, never a delete, and does not give the day's budget back (the system did act).

No model is called anywhere here, and customer-facing text is a template.

Logs carry ids and fixed labels only, never an email or event text.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import asyncpg

from shared.approvals import repo
from shared.approvals.intake import ASSIGNED_ROLE, ROLE_RANK
from shared.autonomy.policy import (
    AUTONOMOUS_TIERS,
    AutonomyPolicy,
    AutonomyPolicyError,
    Budget,
    Usage,
    check_budget,
    evaluate_tier,
    get_policy,
)
from shared.demo_tour import TOUR_EMAIL
from shared.idempotency import run_once
from shared.log_safe import log_safe
from shared.notifications import notify
from shared.policy import PolicyError
from shared.policy import evaluate as evaluate_approval_route

logger = logging.getLogger(__name__)

SYSTEM_REQUESTER = "autonomy@system"
SOURCE = "event"
APPROVAL_KIND = "autonomous_action"
AGENT_NAME = "autonomy-engine"
TOPIC_SHIPMENT_DELAYED = "order.shipment_delayed"
ACTION_GOODWILL_CREDIT = "goodwill_credit"

REVIEW_LINK = "/admin/approvals"
DECISIONS_LINK = "/admin/decisions"
ORDERS_LINK = "/orders"

SCOPE_EVENT = "autonomy_event"
SCOPE_APPROVAL = "autonomy_approval"

#: Roles that review approvals. A staff member is never a beneficiary the engine may pay on its own: the credit would
#: land on someone who also holds the power to decide it. Taken from the approvals ranking so the two cannot drift.
STAFF_ROLES = frozenset(ROLE_RANK)

#: An approval in one of these is still waiting for a person.
_WAITING = ["pending_review", "needs_info"]
#: A delay beyond this is a data error, not a delay.
_MAX_DELAY_DAYS = 365


class AutonomyError(Exception):
    """A refusal that maps onto an HTTP status; the service layer turns it into ``ApprovalHTTPError``."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def event_key(topic: str, event_id: str) -> str:
    return f"{topic}:{event_id}"


def utc_day(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(UTC).date()


def money(value: Decimal) -> str:
    return f"{value:.2f}"


def _json(value: Any) -> str:
    return json.dumps(value, default=str)


def _loads(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


# ── parsing the event ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ShipmentDelayed:
    """``order.shipment_delayed`` as the engine reads it. ``order_id`` / ``delay_days`` are None when invalid."""

    event_id: str
    order_id: uuid.UUID | None
    delay_days: int | None


def parse_shipment_delayed(payload: dict[str, Any], outbox_id: int | None) -> ShipmentDelayed:
    """Read the payload strictly; a field that is absent or the wrong shape is None, never guessed."""
    raw_id = payload.get("event_id")
    event_id = str(raw_id) if isinstance(raw_id, (str, int)) and not isinstance(raw_id, bool) and str(raw_id) else None
    if event_id is None:
        event_id = f"outbox-{outbox_id}"  # no producer id: the row itself is the identity
    order_id: uuid.UUID | None
    try:
        order_id = uuid.UUID(str(payload["order_id"]))
    except (KeyError, ValueError, TypeError):
        order_id = None
    days = payload.get("delay_days")
    delay_days = days if isinstance(days, int) and not isinstance(days, bool) and 0 <= days <= _MAX_DELAY_DAYS else None
    return ShipmentDelayed(event_id[:200], order_id, delay_days)


# ── the result of one decision ───────────────────────────────────────────────


@dataclass
class Verdict:
    """What the engine decided, before it is written."""

    tier: str  # auto | auto_notify | needs_approval | no_action
    outcome: str  # executed | awaiting_approval | no_action
    reasons: list[str] = field(default_factory=list)
    rule_id: str | None = None
    amount: Decimal | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    tripped: list[str] = field(default_factory=list)


def _no_action(reason: str, **evidence: Any) -> Verdict:
    return Verdict("no_action", "no_action", [reason], evidence=evidence)


# ── budget usage (call only while holding the budget lock) ───────────────────


async def lock_budget(conn: asyncpg.Connection, budget_key: str, day: date) -> None:
    """Serialise every draw on ``budget_key`` for ``day`` until the transaction ends.

    ``pg_advisory_xact_lock`` blocks until the holder commits or rolls back, and the next statement in READ
    COMMITTED then sees the holder's rows: so "read the usage, then draw" cannot interleave across events.
    """
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"autonomy_budget:{budget_key}:{day}")


async def lock_order(conn: asyncpg.Connection, order_id: uuid.UUID) -> None:
    """Serialise every credit decision for ``order_id`` until the transaction ends.

    The budget lock is per UTC *day*, so it cannot protect the per-order cap: two events for one order that start on
    either side of midnight hold different budget locks, and two approvals for one order never hold a budget lock at
    all. Both would read "this order was not paid" and both pay. This lock is per order, whatever the day.

    **Lock order, everywhere: budget (:func:`lock_budget`), then order (this), then row locks** (the approval row,
    then the decision row). :func:`_execute_approved_in_tx` takes no budget lock (a person's approval draws nothing)
    but follows the same order from the order lock on, so the two paths cannot deadlock.
    """
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"autonomy_order:{order_id}")


async def credits_on_order(conn: asyncpg.Connection, order_id: uuid.UUID) -> int:
    """Credits actually granted on this order, by the engine alone or after a person approved them.

    Reversed ones still count: a second credit for the same delay is not something to hand out because the first was
    undone. Call under :func:`lock_order`.
    """
    return int(
        await conn.fetchval(
            """SELECT count(*) FROM agent_decisions
                WHERE order_id = $1 AND action = $2
                  AND ((entry_type = 'decision' AND outcome = 'executed') OR entry_type = 'approved_execution')""",
            order_id,
            ACTION_GOODWILL_CREDIT,
        )
    )


async def read_usage(
    conn: asyncpg.Connection, budget_key: str, day: date, *, user_id: uuid.UUID, order_id: uuid.UUID
) -> Usage:
    """What the budget has drawn today (all customers, then this customer) and what this order has been paid."""
    totals = await conn.fetchrow(
        """SELECT count(*) AS n, COALESCE(sum(amount), 0) AS total,
                  count(*) FILTER (WHERE user_id = $3) AS cust_n,
                  COALESCE(sum(amount) FILTER (WHERE user_id = $3), 0) AS cust_total
             FROM agent_decisions
            WHERE entry_type = 'decision' AND outcome = 'executed' AND tier = ANY($4::text[])
              AND budget_key = $1 AND budget_day = $2""",
        budget_key,
        day,
        user_id,
        sorted(AUTONOMOUS_TIERS),
    )
    on_order = await credits_on_order(conn, order_id)
    return Usage(
        global_count=totals["n"],
        global_amount=totals["total"],
        customer_count=totals["cust_n"],
        customer_amount=totals["cust_total"],
        order_count=on_order,
    )


def budget_snapshot(budget: Budget, day: date, usage: Usage | None, drew: Decimal | None) -> dict[str, Any]:
    """What the decision row records about the budget: caps, usage before, and what this row drew."""
    return {
        "budget_key": budget.key,
        "day": day.isoformat(),
        "caps": {
            "per_action_max_amount": money(budget.per_action_max_amount),
            "per_customer_per_day": {
                "max_count": budget.per_customer_per_day.max_count,
                "max_amount": money(budget.per_customer_per_day.max_amount),
            },
            "per_order_max_count": budget.per_order_max_count,
            "global_per_day": {
                "max_count": budget.global_per_day.max_count,
                "max_amount": money(budget.global_per_day.max_amount),
            },
        },
        "usage_before": None if usage is None else usage.to_dict(),
        "drew": None if drew is None else {"count": 1, "amount": money(drew)},
    }


# ── deciding ─────────────────────────────────────────────────────────────────


async def _approval_open_on_order(conn: asyncpg.Connection, order_id: uuid.UUID) -> bool:
    """Whether a credit for this order is already waiting on (or went through) an approval."""
    return bool(
        await conn.fetchval(
            """SELECT 1 FROM agent_decisions d JOIN approvals a ON a.id = d.approval_id
                WHERE d.entry_type = 'decision' AND d.order_id = $1 AND d.action = $2
                  AND d.outcome = 'awaiting_approval' AND a.status = ANY($3::text[])
                LIMIT 1""",
            order_id,
            ACTION_GOODWILL_CREDIT,
            _WAITING,
        )
    )


async def decide_shipment_delayed(
    conn: asyncpg.Connection,
    event: ShipmentDelayed,
    *,
    policy: AutonomyPolicy,
    day: date,
) -> tuple[Verdict, dict[str, Any]]:
    """The verdict for one ``order.shipment_delayed`` event, plus the context the row records. Read-only.

    Must run under :func:`lock_budget` so the usage it reads cannot change before the caller draws.
    """
    rule = policy.event(TOPIC_SHIPMENT_DELAYED)
    assert rule is not None  # the caller checked
    action = policy.action(rule.action)
    budget = policy.budget(action.budget)
    context: dict[str, Any] = {"order_id": None, "user_id": None, "customer_email": None, "budget": budget}
    if event.order_id is None:
        return _no_action("invalid_event_order"), context
    if event.delay_days is None:
        return _no_action("invalid_event_delay"), context
    order = await conn.fetchrow(
        """SELECT o.id, o.status, o.total, o.user_id, u.email, u.role
             FROM orders o JOIN users u ON u.id = o.user_id WHERE o.id = $1""",
        event.order_id,
    )
    if order is None:
        return _no_action("order_not_found"), context
    if str(order["email"]).strip().lower() == TOUR_EMAIL:
        # The demo tour's reset deletes this user's orders, and agent_decisions holds foreign keys to orders and users
        # that the append-only trigger would never let be nulled or cascaded. So no row of ours may ever reference the
        # tour user, not even a no_action one: the context keeps no ids.
        return _no_action("demo_user_excluded"), context
    context.update(order_id=order["id"], user_id=order["user_id"], customer_email=order["email"])
    if order["status"] not in rule.eligible_order_statuses:
        return _no_action("order_not_eligible", order_status=order["status"]), context
    amount = rule.credit_for(event.delay_days)
    if amount is None:
        return _no_action("delay_below_minimum", delay_days=event.delay_days), context
    # The order's ceiling (``per_order_max_count``) holds for both paths: at it, a further credit is not something the
    # system may do alone and not something the inbox should be asked either (approving it would be refused by the
    # same ceiling). It is logged as ``no_action``, not opened as an approval nobody could approve.
    credited = await credits_on_order(conn, order["id"])
    if credited >= budget.per_order_max_count:
        return _no_action("order_already_credited", amount=money(amount), credits_on_order=credited), context
    if await _approval_open_on_order(conn, order["id"]):
        return _no_action("approval_already_open", amount=money(amount)), context

    facts = {"amount": amount, "delay_days": event.delay_days, "order_total": order["total"]}
    verdict_tier = evaluate_tier(action, facts)
    verdict = Verdict(
        tier=verdict_tier.tier,
        outcome="executed" if verdict_tier.tier in AUTONOMOUS_TIERS else "awaiting_approval",
        reasons=[verdict_tier.reason],
        rule_id=verdict_tier.rule_id,
        amount=amount,
        evidence=verdict_tier.evidence,
    )
    context["usage"] = None
    if (order["role"] or "").lower() in STAFF_ROLES:
        # Whoever produced the event (a simulator, a future carrier feed): money for a staff member is always a
        # person's decision, and never that same person's (the decider check refuses the subject).
        verdict.tier = "needs_approval"
        verdict.outcome = "awaiting_approval"
        verdict.reasons = [*verdict.reasons, "beneficiary_is_staff"]
    if verdict.tier in AUTONOMOUS_TIERS:
        usage = await read_usage(conn, budget.key, day, user_id=order["user_id"], order_id=order["id"])
        context["usage"] = usage
        checked = check_budget(budget, amount, usage)
        if not checked.ok:
            # Over a cap: not dropped and not done -- a person decides. The cap that tripped is the reason.
            verdict.tier = "needs_approval"
            verdict.outcome = "awaiting_approval"
            verdict.tripped = list(checked.tripped)
            verdict.reasons = [*verdict.reasons, *(f"budget:{code}" for code in checked.tripped)]
    return verdict, context


async def _insert_row(conn: asyncpg.Connection, **c: Any) -> None:
    await conn.execute(
        """INSERT INTO agent_decisions (id, entry_type, event_key, event_type, outbox_id, agent_name, action,
                                        budget_key,
                                        user_id, order_id, amount, tier, outcome, reasons, input, budget,
                                        policy_version, approval_id, reversible, reverses_id, actor_email, note,
                                        budget_day)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14::jsonb, $15::jsonb, $16::jsonb,
                   $17, $18, $19, $20, $21, $22, $23)""",
        c["id"],
        c["entry_type"],
        c.get("event_key"),
        c.get("event_type"),
        c.get("outbox_id"),
        AGENT_NAME,
        c["action"],
        c.get("budget_key"),
        c.get("user_id"),
        c.get("order_id"),
        c.get("amount"),
        c.get("tier"),
        c["outcome"],
        _json(c.get("reasons", [])),
        _json(c.get("input", {})),
        _json(c.get("budget", {})),
        c.get("policy_version"),
        c.get("approval_id"),
        c.get("reversible", False),
        c.get("reverses_id"),
        c.get("actor_email"),
        c.get("note"),
        c.get("budget_day"),
    )


async def _grant_credit(
    conn: asyncpg.Connection, *, decision_id: uuid.UUID, user_id: uuid.UUID, amount: Decimal, reason: str
) -> None:
    """The one place store credit is added: a ledger row tied to the decision row that caused it."""
    await conn.execute(
        "INSERT INTO store_credit_ledger (user_id, amount, reason, decision_id) VALUES ($1, $2, $3, $4)",
        user_id,
        amount,
        reason,
        decision_id,
    )


def _credit_text(amount: Decimal, order_id: uuid.UUID, delay_days: int | None) -> tuple[str, str]:
    late = f"is running about {delay_days} day(s) late" if delay_days else "has been delayed"
    return (
        "We added a store credit to your account",
        f"Your order {str(order_id)[:8]} {late}, and we are sorry. We added ${money(amount)} of store credit to your "
        "account as an apology.",
    )


async def _route_for(amount: Decimal) -> tuple[str, str, str, list[str], dict[str, Any]]:
    """The approval route for an over-budget action: (route, assigned_role, version, reasons, evidence).

    Fails closed to a manager if the approval policy cannot decide.
    """
    try:
        decision = evaluate_approval_route(APPROVAL_KIND, {"amount": amount})
        return (
            decision.route,
            ASSIGNED_ROLE.get(decision.route, "admin"),
            decision.version,
            list(decision.reasons),
            decision.evidence,
        )
    except PolicyError:
        logger.warning("approval policy could not route an autonomous action; routed to a manager")
        return "manager", "admin", "", ["Policy engine unavailable: routed to a manager review"], {}


async def _decide_in_tx(
    pool: asyncpg.Pool, topic: str, payload: dict[str, Any], outbox_id: int | None, policy: AutonomyPolicy
) -> dict[str, Any]:
    event = parse_shipment_delayed(payload, outbox_id)
    key = event_key(topic, event.event_id)
    rule = policy.event(topic)
    assert rule is not None
    action = policy.action(rule.action)
    budget = policy.budget(action.budget)
    day = utc_day()
    async with pool.acquire() as conn, conn.transaction():
        await lock_budget(conn, budget.key, day)
        if event.order_id is not None:  # budget first, then the order: see lock_order
            await lock_order(conn, event.order_id)
        existing = await conn.fetchrow(
            "SELECT id, tier, outcome, amount, approval_id FROM agent_decisions "
            "WHERE entry_type = 'decision' AND event_key = $1",
            key,
        )
        if existing is not None:  # an event replayed (a second outbox row, a restarted worker): it acts once
            logger.info("autonomy event replayed, no second action decision=%s", log_safe(existing["id"]))
            return {
                "status": existing["outcome"],
                "tier": existing["tier"],
                "decision_id": str(existing["id"]),
                "amount": None if existing["amount"] is None else money(existing["amount"]),
                "approval_id": str(existing["approval_id"]) if existing["approval_id"] else None,
                "replayed": True,
            }
        verdict, ctx = await decide_shipment_delayed(conn, event, policy=policy, day=day)
        decision_id = uuid.uuid4()
        user_id, order_id = ctx["user_id"], ctx["order_id"]
        usage: Usage | None = ctx.get("usage")
        drew = verdict.amount if verdict.tier in AUTONOMOUS_TIERS else None
        row: dict[str, Any] = {
            "id": decision_id,
            "entry_type": "decision",
            "event_key": key,
            "event_type": topic,
            "outbox_id": outbox_id,
            "action": action.name if verdict.tier != "no_action" else "none",
            "budget_key": budget.key if verdict.tier != "no_action" else None,
            "user_id": user_id,
            "order_id": order_id,
            "amount": verdict.amount,
            "tier": verdict.tier,
            "outcome": verdict.outcome,
            "reasons": verdict.reasons,
            "input": {
                "event": {
                    "event_id": event.event_id,
                    "order_id": str(event.order_id) if event.order_id else None,
                    "delay_days": event.delay_days,
                },
                "rule": verdict.rule_id,
                "evidence": verdict.evidence,
            },
            "budget": budget_snapshot(budget, day, usage, drew) | {"tripped": verdict.tripped},
            "policy_version": policy.version,
            "reversible": action.reversible and verdict.tier in AUTONOMOUS_TIERS,
            "budget_day": day if drew is not None else None,
        }
        result: dict[str, Any] = {
            "status": verdict.outcome,
            "tier": verdict.tier,
            "decision_id": str(decision_id),
            "amount": None if verdict.amount is None else money(verdict.amount),
            "reasons": verdict.reasons,
        }
        if verdict.tier == "no_action":
            await _insert_row(conn, **row)
        elif verdict.tier in AUTONOMOUS_TIERS:
            assert verdict.amount is not None and user_id is not None and order_id is not None
            await _insert_row(conn, **row)
            await _grant_credit(
                conn,
                decision_id=decision_id,
                user_id=user_id,
                amount=verdict.amount,
                reason=f"goodwill: shipment delayed {event.delay_days} day(s)",
            )
            title, body = _credit_text(verdict.amount, order_id, event.delay_days)
            await notify(
                conn,
                kind="autonomy.goodwill_credit",
                title=title,
                body=body,
                link=ORDERS_LINK,
                user_email=ctx["customer_email"],
            )
            if verdict.tier == "auto_notify":
                await notify(
                    conn,
                    kind="autonomy.action_taken",
                    title="The system issued a goodwill credit on its own",
                    body=f"${money(verdict.amount)} for a late shipment. It can be reversed from the decisions page.",
                    link=DECISIONS_LINK,
                    role="admin",
                )
        else:  # needs_approval
            assert verdict.amount is not None and user_id is not None and order_id is not None
            route, role, version, route_reasons, route_evidence = await _route_for(verdict.amount)
            approval = await repo.create(
                conn,
                source=SOURCE,
                source_ref=str(decision_id),
                kind=APPROVAL_KIND,
                requester_email=SYSTEM_REQUESTER,
                subject_email=ctx["customer_email"],
                amount=verdict.amount,
                policy_version=version or None,
                route=route,
                assigned_role=role,
                status=repo.ApprovalStatus.PENDING_REVIEW,
                recommendation={
                    "action": action.name,
                    "reasons": verdict.reasons,
                    "route_reasons": route_reasons,
                    "evidence": verdict.evidence,
                    "route_evidence": route_evidence,
                    "autonomy_policy_version": policy.version,
                    "decision_id": str(decision_id),
                },
                actor_email=SYSTEM_REQUESTER,
                actor_role="system",
            )
            await _insert_row(conn, **row, approval_id=approval["id"])
            await notify(
                conn,
                kind="autonomy.approval_needed",
                title="An automatic action needs your approval",
                body=f"A ${money(verdict.amount)} goodwill credit for a late shipment is outside what the system "
                "may do alone.",
                link=REVIEW_LINK,
                approval_id=str(approval["id"]),
                role=role,
            )
            result["approval_id"] = str(approval["id"])
    logger.info(
        "autonomy decision id=%s event=%s tier=%s outcome=%s",
        log_safe(result["decision_id"]),
        log_safe(topic),
        result["tier"],
        result["status"],
    )
    return result


async def handle_event(
    pool: asyncpg.Pool, topic: str, payload: dict[str, Any], *, outbox_id: int | None = None
) -> dict[str, Any]:
    """Decide one event, at most once. Returns a JSON-safe summary (also what a replay returns).

    ``run_once`` keeps its reservation on its own pool connection, so the decision commits (inside
    :func:`_decide_in_tx`) before the reservation is marked complete; a crash in between is healed by the unique
    ``event_key`` index, which makes the retry find the first decision instead of writing a second.
    """
    policy = get_policy()
    if policy.event(topic) is None:
        return {"status": "ignored", "reason": "unknown_event"}
    event = parse_shipment_delayed(payload, outbox_id)
    return await run_once(
        SCOPE_EVENT, event_key(topic, event.event_id), lambda: _decide_in_tx(pool, topic, payload, outbox_id, policy)
    )


# ── a staff-approved action ──────────────────────────────────────────────────


async def decision_for_approval(db: asyncpg.Pool | asyncpg.Connection, approval_id: str) -> asyncpg.Record | None:
    return await db.fetchrow(
        "SELECT * FROM agent_decisions WHERE entry_type = 'decision' AND approval_id = $1", uuid.UUID(str(approval_id))
    )


async def _execute_approved_in_tx(
    pool: asyncpg.Pool, approval_id: str, approver: str, amount: Decimal, claim_token: str
) -> dict[str, Any]:
    async with pool.acquire() as conn, conn.transaction():
        # Lock order: (no budget lock: a person's approval draws nothing) order, then the approval row, then the
        # decision row.
        proposed = await conn.fetchrow(
            "SELECT order_id FROM agent_decisions WHERE entry_type = 'decision' AND approval_id = $1",
            uuid.UUID(approval_id),
        )
        if proposed is None:
            return {"success": False, "status": "failed", "reason": "decision_not_found"}
        if proposed["order_id"] is not None:
            await lock_order(conn, proposed["order_id"])
        # The approval row lock is the one the SLA handler and every state change take (``FOR UPDATE``), so
        # "is this approval still waiting, and is the claim still ours?" cannot change until we commit.
        approval = await conn.fetchrow(
            "SELECT status, claimed_by FROM approvals WHERE id = $1 FOR UPDATE", uuid.UUID(approval_id)
        )
        if approval is None:
            return {"success": False, "status": "failed", "reason": "approval_not_found"}
        decision = await conn.fetchrow(
            "SELECT * FROM agent_decisions WHERE entry_type = 'decision' AND approval_id = $1 FOR UPDATE",
            uuid.UUID(approval_id),
        )
        if decision is None:
            return {"success": False, "status": "failed", "reason": "decision_not_found"}
        done = await conn.fetchrow(
            "SELECT id, amount FROM agent_decisions WHERE entry_type = 'approved_execution' AND approval_id = $1",
            decision["approval_id"],
        )
        if done is not None:
            return {
                "success": True,
                "status": "executed",
                "decision_id": str(done["id"]),
                "amount": money(done["amount"]),
                "replayed": True,
            }
        # Pay only for a decide that still owns the approval. A decide whose claim went stale can have been overtaken
        # by the SLA handler (it expired the approval and told staff nothing was done) or by another reviewer: paying
        # now would make that statement false. Raising (not returning) releases the run_once reservation, so a
        # legitimate retry is not blocked by a cached refusal.
        if approval["status"] != "pending_review":
            raise AutonomyError(409, f"Request is already {approval['status']}")
        if approval["claimed_by"] != claim_token:
            raise AutonomyError(409, "This approval was taken over by another reviewer")
        if not Decimal(0) < amount <= decision["amount"]:
            return {"success": False, "status": "failed", "reason": "amount_out_of_range"}
        order = await conn.fetchrow(
            "SELECT o.id, u.email FROM orders o JOIN users u ON u.id = o.user_id WHERE o.id = $1 AND o.user_id = $2",
            decision["order_id"],
            decision["user_id"],
        )
        if order is None:
            return {"success": False, "status": "failed", "reason": "order_not_found"}
        policy = get_policy()
        try:
            ceiling = policy.budget(decision["budget_key"]).per_order_max_count
        except AutonomyPolicyError:  # the budget is gone from the policy: the strictest reading
            ceiling = 1
        try:  # the action's own ``reversible`` flag, as for an autonomous draw; an action the policy lost is not
            reversible = policy.action(decision["action"]).reversible
        except AutonomyPolicyError:
            reversible = False
        if await credits_on_order(conn, decision["order_id"]) >= ceiling:
            # Credited some other way since this was proposed. The engine no longer opens such an approval, so this
            # is a backstop for one opened before the order was credited (or by an older build): do not pay twice.
            return {"success": False, "status": "failed", "reason": "order_already_credited"}
        execution_id = uuid.uuid4()
        await _insert_row(
            conn,
            id=execution_id,
            entry_type="approved_execution",
            event_type=decision["event_type"],
            action=decision["action"],
            budget_key=decision["budget_key"],
            user_id=decision["user_id"],
            order_id=decision["order_id"],
            amount=amount,
            tier="needs_approval",
            outcome="executed",
            reasons=["approved by staff"],
            input={"decision_id": str(decision["id"]), "proposed_amount": money(decision["amount"])},
            budget={"budget_key": decision["budget_key"], "drew": None, "note": "a person authorised this: no draw"},
            policy_version=decision["policy_version"],
            approval_id=decision["approval_id"],
            reversible=reversible,
            actor_email=approver,
        )
        await _grant_credit(
            conn,
            decision_id=execution_id,
            user_id=decision["user_id"],
            amount=amount,
            reason="goodwill: approved by staff",
        )
        delay = _loads(decision["input"]).get("event", {}).get("delay_days")
        title, body = _credit_text(amount, decision["order_id"], delay)
        await notify(
            conn, kind="autonomy.goodwill_credit", title=title, body=body, link=ORDERS_LINK, user_email=order["email"]
        )
    logger.info("autonomy approved action executed approval=%s", log_safe(approval_id))
    return {"success": True, "status": "executed", "decision_id": str(execution_id), "amount": money(amount)}


async def execute_approved(
    pool: asyncpg.Pool, approval_id: str, *, approver_email: str, amount: Decimal, claim_token: str
) -> dict[str, Any]:
    """Run the staff-approved credit at most once for this approval (a retry, even edited, replays the first).

    ``claim_token`` is the deciding claim (``repo.claim``) the caller holds. The credit is written only while the
    approval is still ``pending_review`` *and* that token still holds the claim, checked under the approval's row
    lock inside the same transaction as the credit; otherwise it raises ``AutonomyError(409)`` and pays nothing.
    """
    return await run_once(
        SCOPE_APPROVAL,
        f"approval:{approval_id}",
        lambda: _execute_approved_in_tx(pool, approval_id, approver_email, amount, claim_token),
    )


async def executed_for_approval(db: asyncpg.Pool | asyncpg.Connection, approval_id: str) -> asyncpg.Record | None:
    """The ``approved_execution`` row for an approval whose action already ran, else None."""
    return await db.fetchrow(
        "SELECT id, actor_email, amount FROM agent_decisions "
        "WHERE entry_type = 'approved_execution' AND approval_id = $1",
        uuid.UUID(str(approval_id)),
    )


# ── reversal ─────────────────────────────────────────────────────────────────


async def reverse(pool: asyncpg.Pool, decision_id: str, *, actor_email: str, note: str | None = None) -> dict[str, Any]:
    """Undo an executed action with a negating entry. ``decision_id`` is the decision row an admin sees.

    Appends a ``reversal`` row and a negative ledger entry in one transaction; nothing is deleted or edited. Refused
    (409) if the action is not reversible, did not execute, was already reversed, or the credit has been used.
    """
    try:
        target_id = uuid.UUID(str(decision_id))
    except ValueError:
        raise AutonomyError(404, "Decision not found") from None
    async with pool.acquire() as conn, conn.transaction():
        decision = await conn.fetchrow(
            "SELECT * FROM agent_decisions WHERE id = $1 AND entry_type = 'decision' FOR UPDATE", target_id
        )
        if decision is None:
            raise AutonomyError(404, "Decision not found")
        if decision["tier"] == "needs_approval":
            executed = await conn.fetchrow(
                "SELECT * FROM agent_decisions WHERE entry_type = 'approved_execution' AND approval_id = $1 FOR UPDATE",
                decision["approval_id"],
            )
            if executed is None:
                raise AutonomyError(409, "Nothing was executed, so there is nothing to reverse")
        elif decision["outcome"] == "executed":
            executed = decision
        else:
            raise AutonomyError(409, "Nothing was executed, so there is nothing to reverse")
        if not executed["reversible"]:
            raise AutonomyError(409, "This action cannot be reversed")
        if await conn.fetchval(
            "SELECT 1 FROM agent_decisions WHERE entry_type = 'reversal' AND reverses_id = $1", executed["id"]
        ):
            raise AutonomyError(409, "Already reversed")
        # Lock the customer's ledger rows' sum by serialising on the customer: nothing else writes a negative entry,
        # but a future spend path must be able to take the same lock.
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"store_credit:{executed['user_id']}"
        )
        balance = await conn.fetchval(
            "SELECT COALESCE(sum(amount), 0) FROM store_credit_ledger WHERE user_id = $1", executed["user_id"]
        )
        if balance < executed["amount"]:
            raise AutonomyError(409, "The credit has already been used, so it cannot be reversed")
        customer = await conn.fetchval("SELECT email FROM users WHERE id = $1", executed["user_id"])
        reversal_id = uuid.uuid4()
        await _insert_row(
            conn,
            id=reversal_id,
            entry_type="reversal",
            event_type=executed["event_type"],
            action=executed["action"],
            budget_key=executed["budget_key"],
            user_id=executed["user_id"],
            order_id=executed["order_id"],
            amount=executed["amount"],
            outcome="reversed",
            reasons=["reversed by an admin"],
            input={"reverses": str(executed["id"]), "decision": str(decision["id"])},
            policy_version=executed["policy_version"],
            reverses_id=executed["id"],
            actor_email=actor_email,
            note=(note or None),
        )
        await _grant_credit(
            conn,
            decision_id=reversal_id,
            user_id=executed["user_id"],
            amount=-executed["amount"],
            reason="goodwill credit reversed",
        )
        await notify(
            conn,
            kind="autonomy.goodwill_reversed",
            title="A store credit was withdrawn",
            body=f"The ${money(executed['amount'])} goodwill credit added to your account was withdrawn.",
            link=ORDERS_LINK,
            user_email=customer,
        )
    logger.info("autonomy action reversed decision=%s", log_safe(decision_id))
    return {"status": "reversed", "reversal_id": str(reversal_id), "reverses_id": str(executed["id"])}


# ── reading ──────────────────────────────────────────────────────────────────

#: How many of the newest decisions a state filter looks through.
_WINDOW = 500

_STATE_SQL = """
SELECT d.id, d.event_key, d.event_type, d.action, d.budget_key, d.order_id, d.amount, d.tier, d.outcome,
       d.reasons, d.input, d.budget, d.policy_version, d.approval_id, d.reversible, d.created_at,
       u.email AS customer_email,
       a.status AS approval_status, a.assigned_role AS approval_role,
       x.id AS execution_id, x.reversible AS execution_reversible, x.amount AS applied_amount, x.actor_email AS approved_by,
       r.id AS reversal_id, r.created_at AS reversed_at, r.actor_email AS reversed_by, r.note AS reversal_note
  FROM agent_decisions d
  LEFT JOIN users u ON u.id = d.user_id
  LEFT JOIN approvals a ON a.id = d.approval_id
  LEFT JOIN agent_decisions x ON x.entry_type = 'approved_execution' AND x.approval_id = d.approval_id
  LEFT JOIN agent_decisions r ON r.entry_type = 'reversal' AND r.reverses_id = COALESCE(x.id, d.id)
 WHERE d.entry_type = 'decision'
"""


def state_of(row: asyncpg.Record | dict[str, Any]) -> str:
    """One word for where a decision stands, derived from the append-only rows around it."""
    if row["reversal_id"]:
        return "reversed"
    if row["tier"] == "no_action":
        return "no_action"
    if row["tier"] in AUTONOMOUS_TIERS:
        return "executed"
    status = row["approval_status"]
    if row["execution_id"]:
        return "executed_after_approval"
    return {
        "pending_review": "awaiting_approval",
        "needs_info": "awaiting_approval",
        "approved": "approving",
        "approved_with_edits": "approving",
        "rejected": "rejected",
        "expired": "expired",
        "failed": "failed",
    }.get(status or "", "awaiting_approval")


def _present(row: asyncpg.Record) -> dict[str, Any]:
    state = state_of(row)
    return {
        "id": str(row["id"]),
        "created_at": row["created_at"].isoformat(),
        "event_type": row["event_type"],
        "action": row["action"],
        "budget_key": row["budget_key"],
        "customer_email": row["customer_email"],
        "order_id": str(row["order_id"]) if row["order_id"] else None,
        "amount": None if row["amount"] is None else money(row["amount"]),
        "applied_amount": None if row["applied_amount"] is None else money(row["applied_amount"]),
        "tier": row["tier"],
        "state": state,
        "reasons": _loads(row["reasons"]),
        "budget": _loads(row["budget"]),
        "input": _loads(row["input"]),
        "policy_version": row["policy_version"],
        "approval_id": str(row["approval_id"]) if row["approval_id"] else None,
        "approval_status": row["approval_status"],
        "approved_by": row["approved_by"],
        "reversed_at": row["reversed_at"].isoformat() if row["reversed_at"] else None,
        "reversed_by": row["reversed_by"],
        "can_reverse": bool(
            state in ("executed", "executed_after_approval")
            and (row["execution_reversible"] if row["execution_id"] else row["reversible"])
        ),
    }


async def list_decisions(
    db: asyncpg.Pool | asyncpg.Connection, *, state: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    """Recent decisions, newest first, each with the state derived from its follow-up rows.

    ``state`` filters after the state is derived, over the newest ``_WINDOW`` rows; ``limit`` then caps the page.
    """
    rows = await db.fetch(f"{_STATE_SQL} ORDER BY d.created_at DESC, d.id LIMIT $1", _WINDOW)  # noqa: S608
    items = [_present(r) for r in rows]
    return [i for i in items if state is None or i["state"] == state][: max(1, min(int(limit), 200))]


async def get_decision(db: asyncpg.Pool | asyncpg.Connection, decision_id: str) -> dict[str, Any] | None:
    try:
        key = uuid.UUID(str(decision_id))
    except ValueError:
        return None
    row = await db.fetchrow(f"{_STATE_SQL} AND d.id = $1", key)  # noqa: S608 - the fragment is a module constant
    return _present(row) if row else None


async def budget_overview(
    db: asyncpg.Pool | asyncpg.Connection, *, policy: AutonomyPolicy | None = None
) -> list[dict[str, Any]]:
    """Each budget's caps and what it has drawn so far today (UTC): the numbers behind "budget use"."""
    active = policy or get_policy()
    day = utc_day()
    out: list[dict[str, Any]] = []
    for budget in active.budgets.values():
        row = await db.fetchrow(
            """SELECT count(*) AS n, COALESCE(sum(amount), 0) AS total FROM agent_decisions
                WHERE entry_type = 'decision' AND outcome = 'executed' AND tier = ANY($3::text[])
                  AND budget_key = $1 AND budget_day = $2""",
            budget.key,
            day,
            sorted(AUTONOMOUS_TIERS),
        )
        out.append(
            {
                "budget_key": budget.key,
                "day": day.isoformat(),
                "used_count": row["n"],
                "used_amount": money(row["total"]),
                "max_count": budget.global_per_day.max_count,
                "max_amount": money(budget.global_per_day.max_amount),
                "per_action_max_amount": money(budget.per_action_max_amount),
                "per_customer_max_count": budget.per_customer_per_day.max_count,
                "per_customer_max_amount": money(budget.per_customer_per_day.max_amount),
                "per_order_max_count": budget.per_order_max_count,
            }
        )
    return out
