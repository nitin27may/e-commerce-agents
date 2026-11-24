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

Three event types share this flow (``autonomy.yaml`` ``events:``): ``order.shipment_delayed`` (a goodwill credit per
order), ``product.price_dropped`` (a price-adjustment credit per recent order *line*, one per line ever) and
``inventory.back_in_stock`` (a notification to customers whose cart holds the product: no money, no budget). The two
newer ones fan out: one event becomes one decision per target, each in its own transaction under its own ``event_key``
(``topic:event_id:target``), so a replayed event acts once per target.

No model is called anywhere here, and customer-facing text is a template. The optional ops-responder
(``shared/autonomy/responder.py``) only re-words that text *after* the decision committed, from an outbox row this
module enqueues in the same transaction; it never sees or changes a tier or an amount.

Logs carry ids and fixed labels only, never an email or event text.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import asyncpg

from shared.approvals import repo
from shared.approvals.intake import ASSIGNED_ROLE, ROLE_RANK
from shared.autonomy.policy import (
    AUTONOMOUS_TIERS,
    Action,
    AutonomyPolicy,
    AutonomyPolicyError,
    Budget,
    EventRule,
    Usage,
    check_budget,
    evaluate_tier,
    get_policy,
)
from shared.demo_tour import TOUR_EMAIL
from shared.idempotency import run_once
from shared.log_safe import log_safe
from shared.notifications import notify
from shared.outbox import enqueue
from shared.policy import PolicyError
from shared.policy import evaluate as evaluate_approval_route

logger = logging.getLogger(__name__)

SYSTEM_REQUESTER = "autonomy@system"
SOURCE = "event"
APPROVAL_KIND = "autonomous_action"
AGENT_NAME = "autonomy-engine"
TOPIC_SHIPMENT_DELAYED = "order.shipment_delayed"
TOPIC_PRICE_DROPPED = "product.price_dropped"
TOPIC_BACK_IN_STOCK = "inventory.back_in_stock"
TOPIC_DRAFT_REQUESTED = "autonomy.draft_requested"
ACTION_GOODWILL_CREDIT = "goodwill_credit"
ACTION_PRICE_ADJUSTMENT = "price_adjustment"
ACTION_BACK_IN_STOCK = "back_in_stock_notice"
#: Actions that move money (a store credit), as opposed to a notice.
MONEY_ACTIONS = (ACTION_GOODWILL_CREDIT, ACTION_PRICE_ADJUSTMENT)

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
_CENT = Decimal("0.01")
#: ``products.price`` is NUMERIC(10, 2).
_MAX_PRICE = Decimal("99999999.99")


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


def _event_id(payload: dict[str, Any], outbox_id: int | None) -> str:
    raw_id = payload.get("event_id")
    event_id = str(raw_id) if isinstance(raw_id, (str, int)) and not isinstance(raw_id, bool) and str(raw_id) else None
    if event_id is None:
        event_id = f"outbox-{outbox_id}"  # no producer id: the row itself is the identity
    return event_id[:200]


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


@dataclass(frozen=True)
class ShipmentDelayed:
    """``order.shipment_delayed`` as the engine reads it. ``order_id`` / ``delay_days`` are None when invalid."""

    event_id: str
    order_id: uuid.UUID | None
    delay_days: int | None


def parse_shipment_delayed(payload: dict[str, Any], outbox_id: int | None) -> ShipmentDelayed:
    """Read the payload strictly; a field that is absent or the wrong shape is None, never guessed."""
    order_id = _uuid_or_none(payload["order_id"]) if "order_id" in payload else None
    days = payload.get("delay_days")
    delay_days = days if isinstance(days, int) and not isinstance(days, bool) and 0 <= days <= _MAX_DELAY_DAYS else None
    return ShipmentDelayed(_event_id(payload, outbox_id), order_id, delay_days)


@dataclass(frozen=True)
class PriceDropped:
    """``product.price_dropped``: the product and the price it now sells for. None when invalid."""

    event_id: str
    product_id: uuid.UUID | None
    new_price: Decimal | None


def parse_price_dropped(payload: dict[str, Any], outbox_id: int | None) -> PriceDropped:
    raw = payload.get("new_price")
    price: Decimal | None = None
    if isinstance(raw, (int, float, str, Decimal)) and not isinstance(raw, bool):
        try:
            candidate: Decimal | None = Decimal(str(raw))
        except ArithmeticError:
            candidate = None
        if (
            candidate is not None
            and candidate.is_finite()
            and Decimal(0) < candidate <= _MAX_PRICE
            and candidate == candidate.quantize(_CENT)  # more than cents is a bad event, not a price to round
        ):
            price = candidate.quantize(_CENT)
    product_id = _uuid_or_none(payload["product_id"]) if "product_id" in payload else None
    return PriceDropped(_event_id(payload, outbox_id), product_id, price)


@dataclass(frozen=True)
class BackInStock:
    event_id: str
    product_id: uuid.UUID | None


def parse_back_in_stock(payload: dict[str, Any], outbox_id: int | None) -> BackInStock:
    product_id = _uuid_or_none(payload["product_id"]) if "product_id" in payload else None
    return BackInStock(_event_id(payload, outbox_id), product_id)


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
    but follows the same order from the order lock on, so the two paths cannot deadlock. A price adjustment is per
    order *line*, and the line's order is the lock it takes, so two adjustments on lines of one order serialise too.
    """
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"autonomy_order:{order_id}")


async def credits_on_order(conn: asyncpg.Connection, order_id: uuid.UUID, action: str = ACTION_GOODWILL_CREDIT) -> int:
    """Credits of ``action`` actually granted on this order, by the engine alone or after a person approved them.

    Reversed ones still count: a second credit for the same delay is not something to hand out because the first was
    undone. Call under :func:`lock_order`.
    """
    return int(
        await conn.fetchval(
            """SELECT count(*) FROM agent_decisions
                WHERE order_id = $1 AND action = $2
                  AND ((entry_type = 'decision' AND outcome = 'executed') OR entry_type = 'approved_execution')""",
            order_id,
            action,
        )
    )


async def adjustments_on_line(conn: asyncpg.Connection, order_item_id: uuid.UUID) -> int:
    """Price adjustments actually paid on this order line (reversed ones count: the line gets one, ever)."""
    return int(
        await conn.fetchval(
            """SELECT count(*) FROM agent_decisions
                WHERE order_item_id = $1 AND action = $2
                  AND ((entry_type = 'decision' AND outcome = 'executed') OR entry_type = 'approved_execution')""",
            order_item_id,
            ACTION_PRICE_ADJUSTMENT,
        )
    )


async def read_usage(
    conn: asyncpg.Connection,
    budget_key: str,
    day: date,
    *,
    user_id: uuid.UUID,
    order_id: uuid.UUID,
    action: str = ACTION_GOODWILL_CREDIT,
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
    on_order = await credits_on_order(conn, order_id, action)
    return Usage(
        global_count=totals["n"],
        global_amount=totals["total"],
        customer_count=totals["cust_n"],
        customer_amount=totals["cust_total"],
        order_count=on_order,
    )


def budget_snapshot(budget: Budget | None, day: date, usage: Usage | None, drew: Decimal | None) -> dict[str, Any]:
    """What the decision row records about the budget: caps, usage before, and what this row drew."""
    if budget is None:  # a notice has no budget
        return {"budget_key": None, "day": day.isoformat(), "caps": None, "usage_before": None, "drew": None}
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


async def _approval_open_on_order(
    conn: asyncpg.Connection, order_id: uuid.UUID, action: str = ACTION_GOODWILL_CREDIT
) -> bool:
    """Whether a credit for this order is already waiting on (or went through) an approval."""
    return bool(
        await conn.fetchval(
            """SELECT 1 FROM agent_decisions d JOIN approvals a ON a.id = d.approval_id
                WHERE d.entry_type = 'decision' AND d.order_id = $1 AND d.action = $2
                  AND d.outcome = 'awaiting_approval' AND a.status = ANY($3::text[])
                LIMIT 1""",
            order_id,
            action,
            _WAITING,
        )
    )


async def _approval_open_on_line(conn: asyncpg.Connection, order_item_id: uuid.UUID) -> bool:
    """Whether an adjustment for this order line is already waiting on an approval."""
    return bool(
        await conn.fetchval(
            """SELECT 1 FROM agent_decisions d JOIN approvals a ON a.id = d.approval_id
                WHERE d.entry_type = 'decision' AND d.order_item_id = $1 AND d.action = $2
                  AND d.outcome = 'awaiting_approval' AND a.status = ANY($3::text[])
                LIMIT 1""",
            order_item_id,
            ACTION_PRICE_ADJUSTMENT,
            _WAITING,
        )
    )


async def _tier_and_budget(
    conn: asyncpg.Connection,
    *,
    action: Action,
    budget: Budget,
    facts: dict[str, Any],
    amount: Decimal,
    beneficiary_role: str | None,
    user_id: uuid.UUID,
    order_id: uuid.UUID,
    day: date,
    context: dict[str, Any],
) -> Verdict:
    """Tier rules, the staff-beneficiary guard and the budget check, shared by every money action.

    Must run under :func:`lock_budget` (and :func:`lock_order`) so the usage it reads cannot change before the caller
    draws.
    """
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
    if (beneficiary_role or "").lower() in STAFF_ROLES:
        # Whoever produced the event (a simulator, a future feed): money for a staff member is always a person's
        # decision, and never that same person's (the decider check refuses the subject).
        verdict.tier = "needs_approval"
        verdict.outcome = "awaiting_approval"
        verdict.reasons = [*verdict.reasons, "beneficiary_is_staff"]
    if verdict.tier in AUTONOMOUS_TIERS:
        usage = await read_usage(conn, budget.key, day, user_id=user_id, order_id=order_id, action=action.name)
        context["usage"] = usage
        checked = check_budget(budget, amount, usage)
        if not checked.ok:
            # Over a cap: not dropped and not done -- a person decides. The cap that tripped is the reason.
            verdict.tier = "needs_approval"
            verdict.outcome = "awaiting_approval"
            verdict.tripped = list(checked.tripped)
            verdict.reasons = [*verdict.reasons, *(f"budget:{code}" for code in checked.tripped)]
    return verdict


def _budget_of(policy: AutonomyPolicy, action: Action) -> Budget:
    if action.budget is None:  # a money action always has one: the loader enforces it
        raise AutonomyPolicyError(f"action {action.name!r} has no budget")
    return policy.budget(action.budget)


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
    budget = _budget_of(policy, action)
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
    verdict = await _tier_and_budget(
        conn,
        action=action,
        budget=budget,
        facts=facts,
        amount=amount,
        beneficiary_role=order["role"],
        user_id=order["user_id"],
        order_id=order["id"],
        day=day,
        context=context,
    )
    return verdict, context


async def decide_price_dropped(
    conn: asyncpg.Connection,
    event: PriceDropped,
    order_item_id: uuid.UUID,
    *,
    policy: AutonomyPolicy,
    day: date,
) -> tuple[Verdict, dict[str, Any]]:
    """The verdict for one order line of a ``product.price_dropped`` event. Read-only.

    The adjustment is ``(price the line was bought at - new price) x quantity``, sized from the order line itself,
    never from the event. One per line, ever (reversed ones count), and the order's ``per_order_max_count`` bounds how
    many lines of one order may be adjusted. Run under :func:`lock_budget` then :func:`lock_order`.
    """
    rule = policy.event(TOPIC_PRICE_DROPPED)
    assert rule is not None and event.new_price is not None and event.product_id is not None
    action = policy.action(rule.action)
    budget = _budget_of(policy, action)
    context: dict[str, Any] = {
        "order_id": None,
        "order_item_id": None,
        "user_id": None,
        "customer_email": None,
        "budget": budget,
        "line": {},
    }
    line = await conn.fetchrow(
        """SELECT oi.id, oi.order_id, oi.product_id, oi.quantity, oi.unit_price,
                  o.status, o.total, o.user_id, o.created_at, u.email, u.role, p.name AS product_name
             FROM order_items oi
             JOIN orders o ON o.id = oi.order_id JOIN users u ON u.id = o.user_id
             JOIN products p ON p.id = oi.product_id
            WHERE oi.id = $1""",
        order_item_id,
    )
    if line is None:
        return _no_action("line_not_found"), context
    if line["product_id"] != event.product_id:
        return _no_action("line_not_for_product"), context
    if str(line["email"]).strip().lower() == TOUR_EMAIL:
        return _no_action("demo_user_excluded"), context  # no ids on the row: see decide_shipment_delayed
    context.update(
        order_id=line["order_id"], order_item_id=line["id"], user_id=line["user_id"], customer_email=line["email"]
    )
    if line["status"] not in rule.eligible_order_statuses:
        return _no_action("order_not_eligible", order_status=line["status"]), context
    age_days = max(0, (datetime.now(UTC) - line["created_at"]).days)
    paid = Decimal(line["unit_price"])
    context["line"] = {
        "order_item_id": str(line["id"]),
        "product_id": str(event.product_id),
        "product_name": line["product_name"],
        "quantity": line["quantity"],
        "paid_unit_price": money(paid),
        "new_price": money(event.new_price),
        "days_since_purchase": age_days,
    }
    if age_days > rule.lookback_days:
        return _no_action("purchase_too_old", days_since_purchase=age_days), context
    if event.new_price >= paid:
        return _no_action("price_not_lower_than_paid", paid=money(paid), new_price=money(event.new_price)), context
    amount = ((paid - event.new_price) * line["quantity"]).quantize(_CENT, rounding=ROUND_HALF_UP)
    if await adjustments_on_line(conn, line["id"]) >= 1:
        return _no_action("line_already_adjusted", amount=money(amount)), context
    if await _approval_open_on_line(conn, line["id"]):
        return _no_action("approval_already_open", amount=money(amount)), context
    adjusted = await credits_on_order(conn, line["order_id"], ACTION_PRICE_ADJUSTMENT)
    if adjusted >= budget.per_order_max_count:
        return _no_action("order_adjustment_limit", amount=money(amount), adjustments_on_order=adjusted), context
    drop_percent = ((paid - event.new_price) / paid * 100).quantize(_CENT, rounding=ROUND_HALF_UP)
    facts = {
        "amount": amount,
        "days_since_purchase": age_days,
        "drop_percent": drop_percent,
        "order_total": line["total"],
    }
    verdict = await _tier_and_budget(
        conn,
        action=action,
        budget=budget,
        facts=facts,
        amount=amount,
        beneficiary_role=line["role"],
        user_id=line["user_id"],
        order_id=line["order_id"],
        day=day,
        context=context,
    )
    return verdict, context


async def _insert_row(conn: asyncpg.Connection, **c: Any) -> None:
    await conn.execute(
        """INSERT INTO agent_decisions (id, entry_type, event_key, event_type, outbox_id, agent_name, action,
                                        budget_key,
                                        user_id, order_id, amount, tier, outcome, reasons, input, budget,
                                        policy_version, approval_id, reversible, reverses_id, actor_email, note,
                                        budget_day, order_item_id)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14::jsonb, $15::jsonb, $16::jsonb,
                   $17, $18, $19, $20, $21, $22, $23, $24)""",
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
        c.get("order_item_id"),
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


# ── what the customer is told (templates; the responder may re-word them, never change them) ──────────────────────


def _credit_text(amount: Decimal, order_id: uuid.UUID, delay_days: int | None) -> tuple[str, str]:
    late = f"is running about {delay_days} day(s) late" if delay_days else "has been delayed"
    return (
        "We added a store credit to your account",
        f"Your order {str(order_id)[:8]} {late}, and we are sorry. We added ${money(amount)} of store credit to your "
        "account as an apology.",
    )


def _adjustment_text(amount: Decimal, order_id: uuid.UUID, line: dict[str, Any]) -> tuple[str, str]:
    name = line.get("product_name") or "An item in your order"
    return (
        "We added a store credit to your account",
        f"{name} dropped to ${line.get('new_price')}, and you paid ${line.get('paid_unit_price')} for it in order "
        f"{str(order_id)[:8]}. We added ${money(amount)} of store credit to your account to cover the difference.",
    )


def credit_message(
    action_name: str, amount: Decimal, order_id: uuid.UUID, event_input: dict[str, Any]
) -> tuple[str, str, str]:
    """``(notification kind, title, body)`` for a granted credit. Pure: the same inputs give the same text."""
    if action_name == ACTION_PRICE_ADJUSTMENT:
        title, body = _adjustment_text(amount, order_id, event_input.get("line", {}))
        return "autonomy.price_adjustment", title, body
    title, body = _credit_text(amount, order_id, event_input.get("delay_days"))
    return "autonomy.goodwill_credit", title, body


def _staff_note(action_name: str, amount: Decimal) -> str:
    what = "a price adjustment for a price drop" if action_name == ACTION_PRICE_ADJUSTMENT else "a goodwill credit"
    return f"${money(amount)} as {what}. It can be reversed from the decisions page."


def _ledger_reason(action_name: str, event_input: dict[str, Any], *, approved: bool) -> str:
    if action_name == ACTION_PRICE_ADJUSTMENT:
        return "price adjustment: approved by staff" if approved else "price adjustment: price dropped"
    if approved:
        return "goodwill: approved by staff"
    return f"goodwill: shipment delayed {event_input.get('delay_days')} day(s)"


def _reversal_message(action_name: str, amount: Decimal) -> tuple[str, str, str]:
    if action_name == ACTION_PRICE_ADJUSTMENT:
        return (
            "autonomy.price_adjustment_reversed",
            "A store credit was withdrawn",
            f"The ${money(amount)} price adjustment added to your account was withdrawn.",
        )
    return (
        "autonomy.goodwill_reversed",
        "A store credit was withdrawn",
        f"The ${money(amount)} goodwill credit added to your account was withdrawn.",
    )


def _approval_ask(action_name: str, amount: Decimal) -> str:
    what = (
        "price adjustment for a price drop"
        if action_name == ACTION_PRICE_ADJUSTMENT
        else "goodwill credit for a late shipment"
    )
    return f"A ${money(amount)} {what} is outside what the system may do alone."


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


# ── writing a decision ───────────────────────────────────────────────────────


async def enqueue_draft(conn: asyncpg.Connection, row_id: uuid.UUID) -> None:
    """Ask the worker to word this decision's customer message, when the responder is on.

    In the decision's own transaction, so the request exists exactly when the decision does. The row is only a
    request: the responder reads the committed decision and can change nothing on it.
    """
    from shared.config import settings

    if not settings.AUTONOMY_RESPONDER_ENABLED:
        return
    await enqueue(
        conn,
        TOPIC_DRAFT_REQUESTED,
        {"decision_id": str(row_id)},
        dedupe_key=f"{TOPIC_DRAFT_REQUESTED}:{row_id}",
        aggregate_id=str(row_id),
    )


@dataclass
class Plan:
    """One decision, ready to write: who it concerns, what was decided, and what the customer is told."""

    key: str
    topic: str
    action: Action
    budget: Budget | None
    day: date
    verdict: Verdict
    event_input: dict[str, Any]
    user_id: uuid.UUID | None = None
    order_id: uuid.UUID | None = None
    order_item_id: uuid.UUID | None = None
    customer_email: str | None = None
    usage: Usage | None = None
    #: A notice: (notification kind, title, body, link) sent to the customer when it acts.
    notice: tuple[str, str, str, str] | None = None


def _plan_row(plan: Plan, decision_id: uuid.UUID, outbox_id: int | None, policy: AutonomyPolicy) -> dict[str, Any]:
    v = plan.verdict
    acts = v.tier in AUTONOMOUS_TIERS
    drew = v.amount if acts and plan.action.kind != "notice" else None
    return {
        "id": decision_id,
        "entry_type": "decision",
        "event_key": plan.key,
        "event_type": plan.topic,
        "outbox_id": outbox_id,
        "action": plan.action.name if v.tier != "no_action" else "none",
        "budget_key": plan.budget.key if (v.tier != "no_action" and plan.budget is not None) else None,
        "user_id": plan.user_id,
        "order_id": plan.order_id,
        "order_item_id": plan.order_item_id,
        "amount": v.amount,
        "tier": v.tier,
        "outcome": v.outcome,
        "reasons": v.reasons,
        "input": {"event": plan.event_input, "rule": v.rule_id, "evidence": v.evidence},
        "budget": budget_snapshot(plan.budget, plan.day, plan.usage, drew) | {"tripped": v.tripped},
        "policy_version": policy.version,
        "reversible": plan.action.reversible and acts,
        # A notice has no budget, but the day it counts against is how a customer's daily notice cap is read.
        "budget_day": plan.day if (drew is not None or (acts and plan.action.kind == "notice")) else None,
    }


async def _notify_customer(
    conn: asyncpg.Connection, decision_id: uuid.UUID, *, kind: str, title: str, body: str, link: str, email: str | None
) -> None:
    note_id = await notify(conn, kind=kind, title=title, body=body, link=link, user_email=email)
    # The link a drafted message uses to find the notification it replaces the template text of.
    await conn.execute("UPDATE notifications SET decision_id = $2 WHERE id = $1", uuid.UUID(note_id), decision_id)


async def _write_plan(
    conn: asyncpg.Connection, plan: Plan, decision_id: uuid.UUID, outbox_id: int | None, policy: AutonomyPolicy
) -> dict[str, Any]:
    """Insert the decision and everything that must commit with it. Runs inside the caller's transaction."""
    v = plan.verdict
    row = _plan_row(plan, decision_id, outbox_id, policy)
    result: dict[str, Any] = {
        "status": v.outcome,
        "tier": v.tier,
        "decision_id": str(decision_id),
        "amount": None if v.amount is None else money(v.amount),
        "reasons": v.reasons,
    }
    if v.tier == "no_action":
        await _insert_row(conn, **row)
    elif v.tier in AUTONOMOUS_TIERS and plan.action.kind == "notice":
        assert plan.user_id is not None and plan.notice is not None
        await _insert_row(conn, **row)
        kind, title, body, link = plan.notice
        await _notify_customer(
            conn, decision_id, kind=kind, title=title, body=body, link=link, email=plan.customer_email
        )
    elif v.tier in AUTONOMOUS_TIERS:
        assert v.amount is not None and plan.user_id is not None and plan.order_id is not None
        await _insert_row(conn, **row)
        await _grant_credit(
            conn,
            decision_id=decision_id,
            user_id=plan.user_id,
            amount=v.amount,
            reason=_ledger_reason(plan.action.name, plan.event_input, approved=False),
        )
        kind, title, body = credit_message(plan.action.name, v.amount, plan.order_id, plan.event_input)
        await _notify_customer(
            conn, decision_id, kind=kind, title=title, body=body, link=ORDERS_LINK, email=plan.customer_email
        )
        if v.tier == "auto_notify":
            await notify(
                conn,
                kind="autonomy.action_taken",
                title="The system issued a store credit on its own",
                body=_staff_note(plan.action.name, v.amount),
                link=DECISIONS_LINK,
                role="admin",
            )
        await enqueue_draft(conn, decision_id)
    else:  # needs_approval
        assert v.amount is not None and plan.user_id is not None and plan.order_id is not None
        route, role, version, route_reasons, route_evidence = await _route_for(v.amount)
        approval = await repo.create(
            conn,
            source=SOURCE,
            source_ref=str(decision_id),
            kind=APPROVAL_KIND,
            requester_email=SYSTEM_REQUESTER,
            subject_email=plan.customer_email,
            amount=v.amount,
            policy_version=version or None,
            route=route,
            assigned_role=role,
            status=repo.ApprovalStatus.PENDING_REVIEW,
            recommendation={
                "action": plan.action.name,
                "reasons": v.reasons,
                "route_reasons": route_reasons,
                "evidence": v.evidence,
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
            body=_approval_ask(plan.action.name, v.amount),
            link=REVIEW_LINK,
            approval_id=str(approval["id"]),
            role=role,
        )
        result["approval_id"] = str(approval["id"])
    return result


def _replayed(existing: asyncpg.Record) -> dict[str, Any]:
    return {
        "status": existing["outcome"],
        "tier": existing["tier"],
        "decision_id": str(existing["id"]),
        "amount": None if existing["amount"] is None else money(existing["amount"]),
        "approval_id": str(existing["approval_id"]) if existing["approval_id"] else None,
        "replayed": True,
    }


_EXISTING_SQL = (
    "SELECT id, tier, outcome, amount, approval_id FROM agent_decisions "
    "WHERE entry_type = 'decision' AND event_key = $1"
)


def _log_decision(result: dict[str, Any], topic: str) -> None:
    logger.info(
        "autonomy decision id=%s event=%s tier=%s outcome=%s",
        log_safe(result["decision_id"]),
        log_safe(topic),
        result["tier"],
        result["status"],
    )


async def _decide_in_tx(
    pool: asyncpg.Pool, topic: str, payload: dict[str, Any], outbox_id: int | None, policy: AutonomyPolicy
) -> dict[str, Any]:
    event = parse_shipment_delayed(payload, outbox_id)
    key = event_key(topic, event.event_id)
    rule = policy.event(topic)
    assert rule is not None
    action = policy.action(rule.action)
    budget = _budget_of(policy, action)
    day = utc_day()
    async with pool.acquire() as conn, conn.transaction():
        await lock_budget(conn, budget.key, day)
        if event.order_id is not None:  # budget first, then the order: see lock_order
            await lock_order(conn, event.order_id)
        existing = await conn.fetchrow(_EXISTING_SQL, key)
        if existing is not None:  # an event replayed (a second outbox row, a restarted worker): it acts once
            logger.info("autonomy event replayed, no second action decision=%s", log_safe(existing["id"]))
            return _replayed(existing)
        verdict, ctx = await decide_shipment_delayed(conn, event, policy=policy, day=day)
        plan = Plan(
            key=key,
            topic=topic,
            action=action,
            budget=budget,
            day=day,
            verdict=verdict,
            event_input={
                "event_id": event.event_id,
                "order_id": str(event.order_id) if event.order_id else None,
                "delay_days": event.delay_days,
            },
            user_id=ctx["user_id"],
            order_id=ctx["order_id"],
            customer_email=ctx["customer_email"],
            usage=ctx.get("usage"),
        )
        result = await _write_plan(conn, plan, uuid.uuid4(), outbox_id, policy)
    _log_decision(result, topic)
    return result


async def _record_event_no_action(
    pool: asyncpg.Pool,
    topic: str,
    event_id: str,
    policy: AutonomyPolicy,
    action: Action,
    reason: str,
    event_input: dict[str, Any],
    outbox_id: int | None,
    **evidence: Any,
) -> dict[str, Any]:
    """A ``no_action`` row for a whole event (it named no valid target, or reached too many people), no ids on it."""
    key = event_key(topic, event_id)
    async with pool.acquire() as conn, conn.transaction():
        existing = await conn.fetchrow(_EXISTING_SQL, key)
        if existing is not None:
            return _replayed(existing)
        plan = Plan(
            key=key,
            topic=topic,
            action=action,
            budget=None,
            day=utc_day(),
            verdict=_no_action(reason, **evidence),
            event_input=event_input,
        )
        result = await _write_plan(conn, plan, uuid.uuid4(), outbox_id, policy)
    _log_decision(result, topic)
    return result


# ── price drops: one decision per order line ─────────────────────────────────


async def _decide_line_in_tx(
    pool: asyncpg.Pool,
    topic: str,
    event: PriceDropped,
    order_item_id: uuid.UUID,
    outbox_id: int | None,
    policy: AutonomyPolicy,
) -> dict[str, Any]:
    rule = policy.event(topic)
    assert rule is not None
    action = policy.action(rule.action)
    budget = _budget_of(policy, action)
    key = f"{event_key(topic, event.event_id)}:{order_item_id}"
    day = utc_day()
    async with pool.acquire() as conn, conn.transaction():
        # Budget first, then the order (a line never moves between orders, so its order can be read before the locks).
        await lock_budget(conn, budget.key, day)
        order_id = await conn.fetchval("SELECT order_id FROM order_items WHERE id = $1", order_item_id)
        if order_id is not None:
            await lock_order(conn, order_id)
        existing = await conn.fetchrow(_EXISTING_SQL, key)
        if existing is not None:
            logger.info("autonomy event replayed, no second action decision=%s", log_safe(existing["id"]))
            return _replayed(existing)
        verdict, ctx = await decide_price_dropped(conn, event, order_item_id, policy=policy, day=day)
        assert event.product_id is not None and event.new_price is not None
        plan = Plan(
            key=key,
            topic=topic,
            action=action,
            budget=budget,
            day=day,
            verdict=verdict,
            event_input={
                "event_id": event.event_id,
                "product_id": str(event.product_id),
                "new_price": money(event.new_price),
                "order_item_id": str(order_item_id),
                "line": ctx.get("line", {}),
            },
            user_id=ctx["user_id"],
            order_id=ctx["order_id"],
            order_item_id=ctx["order_item_id"],
            customer_email=ctx["customer_email"],
            usage=ctx.get("usage"),
        )
        result = await _write_plan(conn, plan, uuid.uuid4(), outbox_id, policy)
    _log_decision(result, topic)
    return result


async def _once(key: str, fn: Callable[[], Awaitable[dict[str, Any]]]) -> tuple[dict[str, Any], bool]:
    """``run_once`` for one target, and whether *this call* made the decision (False for any kind of replay)."""
    ran = False

    async def inner() -> dict[str, Any]:
        nonlocal ran
        ran = True
        return await fn()

    result = await run_once(SCOPE_EVENT, key, inner)
    return result, ran and not result.get("replayed")


def _summarise(results: list[tuple[dict[str, Any], bool]], **extra: Any) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for r, _ in results:
        label = r.get("tier") or r.get("status") or "unknown"
        counts[label] = counts.get(label, 0) + 1
    return {
        "status": "processed",
        "targets": len(results),
        "new": sum(1 for _, fresh in results if fresh),
        "by_tier": counts,
        **extra,
    }


def _echo(payload: dict[str, Any], event_id: str) -> dict[str, Any]:
    """What a whole-event ``no_action`` row records of the payload: the id and a bounded product reference."""
    raw = payload.get("product_id")
    return {"event_id": event_id, "product_id": None if raw is None else str(raw)[:64]}


async def _handle_price_dropped(
    pool: asyncpg.Pool, topic: str, payload: dict[str, Any], outbox_id: int | None, policy: AutonomyPolicy
) -> dict[str, Any]:
    event = parse_price_dropped(payload, outbox_id)
    rule = policy.event(topic)
    assert rule is not None
    action = policy.action(rule.action)
    echo = _echo(payload, event.event_id)

    async def record(reason: str, **evidence: Any) -> dict[str, Any]:
        return await run_once(
            SCOPE_EVENT,
            event_key(topic, event.event_id),
            lambda: _record_event_no_action(
                pool, topic, event.event_id, policy, action, reason, echo, outbox_id, **evidence
            ),
        )

    if event.product_id is None:
        return await record("invalid_event_product")
    if event.new_price is None:
        return await record("invalid_event_price")
    if not await pool.fetchval("SELECT 1 FROM products WHERE id = $1", event.product_id):
        return await record("product_not_found")
    lines = await pool.fetch(
        """SELECT oi.id FROM order_items oi
             JOIN orders o ON o.id = oi.order_id JOIN users u ON u.id = o.user_id
            WHERE oi.product_id = $1 AND oi.unit_price > $2 AND o.status = ANY($3::text[])
              AND o.created_at >= NOW() - make_interval(days => $4) AND lower(u.email) <> $5
            ORDER BY o.created_at, oi.id LIMIT $6""",
        event.product_id,
        event.new_price,
        sorted(rule.eligible_order_statuses),
        rule.lookback_days,
        TOUR_EMAIL,  # the demo reset deletes this user's orders; no decision row may ever reference them
        rule.max_lines + 1,
    )
    truncated = len(lines) > rule.max_lines
    results: list[tuple[dict[str, Any], bool]] = []
    for found in lines[: rule.max_lines]:
        item_id = found["id"]
        results.append(
            await _once(
                f"{event_key(topic, event.event_id)}:{item_id}",
                lambda item_id=item_id: _decide_line_in_tx(pool, topic, event, item_id, outbox_id, policy),
            )
        )
    if not results:
        return {"status": "no_targets", "targets": 0, "new": 0, "by_tier": {}}
    return _summarise(results, truncated=truncated)


# ── back in stock: one notice per customer ───────────────────────────────────


def _notice_text(product_name: str, product_id: uuid.UUID) -> tuple[str, str, str, str]:
    return (
        "autonomy.back_in_stock",
        f"{product_name} is back in stock",
        f"{product_name} is available again, and it is still in your cart.",
        f"/products/{product_id}",
    )


async def _notice_in_tx(
    pool: asyncpg.Pool,
    topic: str,
    event: BackInStock,
    user_id: uuid.UUID,
    product: asyncpg.Record,
    outbox_id: int | None,
    policy: AutonomyPolicy,
) -> dict[str, Any]:
    rule = policy.event(topic)
    assert rule is not None and event.product_id is not None
    action = policy.action(rule.action)
    key = f"{event_key(topic, event.event_id)}:{user_id}"
    day = utc_day()
    async with pool.acquire() as conn, conn.transaction():
        # A notice has no budget, but its per-customer daily cap is read-then-act like one: the same advisory lock,
        # keyed by the action, serialises it. No order is involved, so there is no second lock.
        await lock_budget(conn, action.name, day)
        existing = await conn.fetchrow(_EXISTING_SQL, key)
        if existing is not None:
            return _replayed(existing)
        who = await conn.fetchrow("SELECT id, email, role FROM users WHERE id = $1", user_id)
        base = {"event_id": event.event_id, "product_id": str(event.product_id), "product_name": product["name"]}
        ids: tuple[uuid.UUID | None, str | None] = (None, None)
        if who is None:
            verdict = _no_action("customer_not_found")
        elif str(who["email"]).strip().lower() == TOUR_EMAIL:
            verdict = _no_action("demo_user_excluded")  # no ids on the row: see decide_shipment_delayed
        else:
            ids = (who["id"], who["email"])
            if (who["role"] or "").lower() in STAFF_ROLES:
                verdict = _no_action("recipient_is_staff")
            else:
                sent_today = await conn.fetchval(
                    """SELECT count(*) FROM agent_decisions
                        WHERE entry_type = 'decision' AND action = $1 AND outcome = 'executed'
                          AND user_id = $2 AND budget_day = $3""",
                    action.name,
                    user_id,
                    day,
                )
                if sent_today >= rule.per_customer_per_day:
                    verdict = _no_action("customer_daily_notice_cap", sent_today=int(sent_today))
                else:
                    tier = evaluate_tier(action, {"recipients": 1})  # the audience was bounded by the caller
                    if tier.tier in AUTONOMOUS_TIERS:
                        verdict = Verdict(
                            tier=tier.tier,
                            outcome="executed",
                            reasons=[tier.reason],
                            rule_id=tier.rule_id,
                            evidence=tier.evidence,
                        )
                    else:  # a policy that does not let even one notice go alone: nothing for the inbox to approve
                        verdict = _no_action("notice_needs_review", rule=tier.rule_id)
        plan = Plan(
            key=key,
            topic=topic,
            action=action,
            budget=None,
            day=day,
            verdict=verdict,
            event_input=base,
            user_id=ids[0],
            customer_email=ids[1],
            notice=_notice_text(product["name"], event.product_id),
        )
        result = await _write_plan(conn, plan, uuid.uuid4(), outbox_id, policy)
    _log_decision(result, topic)
    return result


async def _handle_back_in_stock(
    pool: asyncpg.Pool, topic: str, payload: dict[str, Any], outbox_id: int | None, policy: AutonomyPolicy
) -> dict[str, Any]:
    event = parse_back_in_stock(payload, outbox_id)
    rule = policy.event(topic)
    assert rule is not None
    action = policy.action(rule.action)
    echo = _echo(payload, event.event_id)

    async def record(reason: str, **evidence: Any) -> dict[str, Any]:
        return await run_once(
            SCOPE_EVENT,
            event_key(topic, event.event_id),
            lambda: _record_event_no_action(
                pool, topic, event.event_id, policy, action, reason, echo, outbox_id, **evidence
            ),
        )

    if event.product_id is None:
        return await record("invalid_event_product")
    product = await pool.fetchrow("SELECT id, name, is_active FROM products WHERE id = $1", event.product_id)
    if product is None:
        return await record("product_not_found")
    stock = await pool.fetchval(
        "SELECT COALESCE(sum(quantity), 0) FROM warehouse_inventory WHERE product_id = $1", event.product_id
    )
    if not product["is_active"] or stock <= 0:
        # Never announce stock that is not there, whatever the event says.
        return await record("product_not_in_stock", stock=int(stock))
    audience = await pool.fetch(
        """SELECT c.user_id FROM carts c JOIN users u ON u.id = c.user_id
            WHERE EXISTS (SELECT 1 FROM cart_items ci WHERE ci.cart_id = c.id AND ci.product_id = $1)
              AND c.updated_at >= NOW() - make_interval(days => $2)
              AND u.is_active IS NOT FALSE AND lower(u.email) <> $3
            ORDER BY c.user_id LIMIT $4""",
        event.product_id,
        rule.cart_max_age_days,
        TOUR_EMAIL,
        rule.max_recipients + 1,
    )
    if not audience:
        return {"status": "no_targets", "targets": 0, "new": 0, "by_tier": {}}
    tier = evaluate_tier(action, {"recipients": len(audience)})
    if tier.tier not in AUTONOMOUS_TIERS:
        # More people than the system may notify alone: nobody is told, and the log says why. (A notice has nothing
        # for the inbox to approve, so this is a no_action a person can pick up from the decisions page.)
        return await record("recipients_over_cap", recipients=len(audience), cap=rule.max_recipients)
    results = [
        await _once(
            f"{event_key(topic, event.event_id)}:{row['user_id']}",
            lambda uid=row["user_id"]: _notice_in_tx(pool, topic, event, uid, product, outbox_id, policy),
        )
        for row in audience
    ]
    sent = [r for r, fresh in results if fresh and r.get("status") == "executed"]
    if sent:  # one heads-up per event, not one per customer
        async with pool.acquire() as conn:
            await notify(
                conn,
                kind="autonomy.action_taken",
                title="Back-in-stock notices were sent",
                body=f"{len(sent)} customer(s) with {product['name']} in their cart were told it is back.",
                link=DECISIONS_LINK,
                role="admin",
            )
    return _summarise(results)


async def handle_event(
    pool: asyncpg.Pool, topic: str, payload: dict[str, Any], *, outbox_id: int | None = None
) -> dict[str, Any]:
    """Decide one event, at most once per target. Returns a JSON-safe summary (also what a replay returns).

    ``run_once`` keeps its reservation on its own pool connection, so the decision commits (inside
    :func:`_decide_in_tx`) before the reservation is marked complete; a crash in between is healed by the unique
    ``event_key`` index, which makes the retry find the first decision instead of writing a second.
    """
    policy = get_policy()
    rule: EventRule | None = policy.event(topic)
    if rule is None:
        return {"status": "ignored", "reason": "unknown_event"}
    kind = policy.action(rule.action).kind
    if kind == "price_adjustment":
        return await _handle_price_dropped(pool, topic, payload, outbox_id, policy)
    if kind == "notice":
        return await _handle_back_in_stock(pool, topic, payload, outbox_id, policy)
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
        if await credits_on_order(conn, decision["order_id"], decision["action"]) >= ceiling:
            # Credited some other way since this was proposed. The engine no longer opens such an approval, so this
            # is a backstop for one opened before the order was credited (or by an older build): do not pay twice.
            return {"success": False, "status": "failed", "reason": "order_already_credited"}
        if decision["order_item_id"] is not None and await adjustments_on_line(conn, decision["order_item_id"]) >= 1:
            # One adjustment per order line, ever: the same backstop, per line.
            return {"success": False, "status": "failed", "reason": "line_already_adjusted"}
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
            order_item_id=decision["order_item_id"],
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
            reason=_ledger_reason(decision["action"], {}, approved=True),
        )
        event_input = _loads(decision["input"]).get("event", {})
        kind, title, body = credit_message(decision["action"], amount, decision["order_id"], event_input)
        await _notify_customer(
            conn, execution_id, kind=kind, title=title, body=body, link=ORDERS_LINK, email=order["email"]
        )
        await enqueue_draft(conn, execution_id)
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
            order_item_id=executed["order_item_id"],
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
            reason=(
                "price adjustment reversed"
                if executed["action"] == ACTION_PRICE_ADJUSTMENT
                else "goodwill credit reversed"
            ),
        )
        kind, title, body = _reversal_message(executed["action"], executed["amount"])
        await notify(conn, kind=kind, title=title, body=body, link=ORDERS_LINK, user_email=customer)
    logger.info("autonomy action reversed decision=%s", log_safe(decision_id))
    return {"status": "reversed", "reversal_id": str(reversal_id), "reverses_id": str(executed["id"])}


# ── reading ──────────────────────────────────────────────────────────────────

#: ``state_of`` in SQL, so a state filter and its count run in the database over every row, not over a window.
#: ``test_the_sql_state_filter_agrees_with_the_derived_state_for_every_state`` pins the two together.
_STATE_CASE = f"""CASE
    WHEN r.id IS NOT NULL THEN 'reversed'
    WHEN d.tier = 'no_action' THEN 'no_action'
    WHEN d.tier IN ({", ".join(f"'{t}'" for t in sorted(AUTONOMOUS_TIERS))}) THEN 'executed'
    WHEN x.id IS NOT NULL THEN 'executed_after_approval'
    WHEN a.status IN ('approved', 'approved_with_edits') THEN 'approving'
    WHEN a.status IN ('rejected', 'expired', 'failed') THEN a.status
    ELSE 'awaiting_approval'
END"""

_STATE_SQL = f"""
SELECT d.id, d.event_key, d.event_type, d.action, d.budget_key, d.order_id, d.amount, d.tier, d.outcome,
       d.reasons, d.input, d.budget, d.policy_version, d.approval_id, d.reversible, d.created_at,
       u.email AS customer_email, {_STATE_CASE} AS sql_state,
       a.status AS approval_status, a.assigned_role AS approval_role,
       x.id AS execution_id, x.reversible AS execution_reversible, x.amount AS applied_amount,
       x.actor_email AS approved_by,
       r.id AS reversal_id, r.created_at AS reversed_at, r.actor_email AS reversed_by, r.note AS reversal_note,
       d.order_item_id, dd.body AS draft_body, dd.source AS draft_source, dd.model AS draft_model,
       dd.fallback_reason AS draft_fallback
  FROM agent_decisions d
  LEFT JOIN users u ON u.id = d.user_id
  LEFT JOIN approvals a ON a.id = d.approval_id
  LEFT JOIN agent_decisions x ON x.entry_type = 'approved_execution' AND x.approval_id = d.approval_id
  LEFT JOIN agent_decisions r ON r.entry_type = 'reversal' AND r.reverses_id = COALESCE(x.id, d.id)
  LEFT JOIN decision_drafts dd ON dd.decision_id = COALESCE(x.id, d.id)
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
        "order_item_id": str(row["order_item_id"]) if row["order_item_id"] else None,
        # The customer message the ops-responder drafted (or the template it fell back to); None when none was made.
        "draft": (
            None
            if row["draft_body"] is None
            else {
                "body": row["draft_body"],
                "source": row["draft_source"],
                "model": row["draft_model"],
                "fallback_reason": row["draft_fallback"],
            }
        ),
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


_MAX_PAGE = 200


def _page(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(int(limit), _MAX_PAGE)), max(0, int(offset))


async def list_decisions(
    db: asyncpg.Pool | asyncpg.Connection, *, state: str | None = None, limit: int = 100, offset: int = 0
) -> list[dict[str, Any]]:
    """Decisions, newest first, each with the state derived from its follow-up rows.

    ``state`` is filtered in SQL over **all** rows (an old ``awaiting_approval`` decision stays findable however many
    newer rows there are); ``limit`` (at most 200) and ``offset`` page the result.
    """
    size, skip = _page(limit, offset)
    rows = await db.fetch(
        f"SELECT * FROM ({_STATE_SQL}) s WHERE ($1::text IS NULL OR s.sql_state = $1) "  # noqa: S608
        "ORDER BY s.created_at DESC, s.id LIMIT $2 OFFSET $3",
        state,
        size,
        skip,
    )
    return [_present(r) for r in rows]


async def count_decisions(db: asyncpg.Pool | asyncpg.Connection, *, state: str | None = None) -> int:
    """How many decisions match ``state`` (all of them when None): the total behind a paged list."""
    return int(
        await db.fetchval(
            f"SELECT count(*) FROM ({_STATE_SQL}) s WHERE ($1::text IS NULL OR s.sql_state = $1)",  # noqa: S608
            state,
        )
    )


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
