"""Admin side of event autonomy (#94): the event simulator, the decisions list and the reversal.

* ``simulate`` injects an ``order.shipment_delayed`` event as an outbox row, deterministically (a scenario picks the
  oldest eligible order of a customer with no autonomy activity today; an explicit order is used as given). It is
  off unless ``AUTONOMY_ENABLED`` *and* ``AUTONOMY_SIMULATOR_ENABLED`` are set, and answers 404 otherwise so a
  normal install exposes nothing. There is no production event producer; this is the only way an event reaches the
  engine, and the outbox worker does the rest.
* ``list_decisions`` / ``reverse`` are not gated by the flags: an admin must be able to see, and undo, what the
  system did even after the feature is switched off.

Identity comes from the verified token (the route passes the actor), never the body.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from orchestrator.approvals_service import Actor, ApprovalHTTPError
from shared.autonomy import engine
from shared.autonomy.policy import AutonomyPolicyError, get_policy
from shared.config import settings
from shared.db import get_pool
from shared.demo_tour import TOUR_EMAIL
from shared.log_safe import log_safe
from shared.outbox import enqueue

logger = logging.getLogger(__name__)

MAX_NOTE_LENGTH = 500

#: Scenario name -> delay in days. With the shipped policy they land in the three tiers: a $5 credit alone, a $15
#: credit alone with staff told, and a $40 credit that needs approval.
SCENARIOS: dict[str, int] = {"small": 3, "medium": 6, "large": 12}


def _require_enabled() -> None:
    if not (settings.AUTONOMY_ENABLED and settings.AUTONOMY_SIMULATOR_ENABLED):
        raise ApprovalHTTPError(404, "Not found")


def _refuse(exc: engine.AutonomyError) -> ApprovalHTTPError:
    return ApprovalHTTPError(exc.status_code, exc.detail)


async def _pick_order(scenario: str) -> uuid.UUID:
    """The oldest eligible order of a customer with no autonomy activity today and no event still queued."""
    policy = get_policy()
    rule = policy.event(engine.TOPIC_SHIPMENT_DELAYED)
    if rule is None:
        raise ApprovalHTTPError(409, "The autonomy policy has no shipment-delay rule")
    order_id = await get_pool().fetchval(
        """SELECT o.id FROM orders o JOIN users u ON u.id = o.user_id
            WHERE o.status = ANY($1::text[]) AND u.role = 'customer' AND u.is_active IS NOT FALSE
              AND lower(u.email) <> $4
              AND NOT EXISTS (SELECT 1 FROM agent_decisions d WHERE d.order_id = o.id)
              AND NOT EXISTS (SELECT 1 FROM agent_decisions d
                               WHERE d.user_id = o.user_id AND d.entry_type = 'decision' AND d.budget_day = $2)
              AND NOT EXISTS (SELECT 1 FROM outbox ob JOIN orders o2 ON o2.id = ob.aggregate_id
                               WHERE ob.topic = $3 AND ob.processed_at IS NULL AND o2.user_id = o.user_id)
            ORDER BY o.created_at, o.id LIMIT 1""",
        sorted(rule.eligible_order_statuses),
        engine.utc_day(),
        engine.TOPIC_SHIPMENT_DELAYED,
        TOUR_EMAIL,  # the demo reset deletes this user's orders; no decision row may ever reference them
    )
    if order_id is None:
        raise ApprovalHTTPError(409, f"No eligible order is left for the {scenario!r} scenario")
    return order_id


async def simulate(
    actor: Actor,
    *,
    scenario: str | None = None,
    order_id: str | None = None,
    delay_days: int | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    """Enqueue one ``order.shipment_delayed`` event. Replaying the same ``event_id`` enqueues nothing."""
    _require_enabled()
    if event_id is not None and not event_id.strip():
        raise ApprovalHTTPError(422, "event_id cannot be empty")
    if scenario is not None:
        if scenario not in SCENARIOS:
            raise ApprovalHTTPError(422, f"scenario must be one of {', '.join(SCENARIOS)}")
        if order_id is not None or delay_days is not None:
            raise ApprovalHTTPError(422, "Give a scenario or an order and a delay, not both")
        days = SCENARIOS[scenario]
        order = await _pick_order(scenario)
        event = event_id or f"sim-{scenario}-{order}"
    else:
        if order_id is None or delay_days is None:
            raise ApprovalHTTPError(422, "Give a scenario, or an order_id and delay_days")
        try:
            order = uuid.UUID(str(order_id))
        except ValueError:
            raise ApprovalHTTPError(422, "order_id must be an order id") from None
        if isinstance(delay_days, bool) or not 0 <= delay_days <= 365:
            raise ApprovalHTTPError(422, "delay_days must be between 0 and 365")
        days = delay_days
        owner = await get_pool().fetchrow(
            "SELECT u.email, u.role FROM orders o JOIN users u ON u.id = o.user_id WHERE o.id = $1", order
        )
        if owner is None:
            raise ApprovalHTTPError(404, "Order not found")
        # The explicit path used to skip the scenario picker's customers-only filter: an admin could simulate an
        # event on their own order and be the beneficiary of what staff then approve. Only a customer's order, and
        # never the acting admin's, is a valid subject (the engine also refuses to pay staff on its own).
        if (
            owner["role"] != "customer"
            or str(owner["email"]).lower() == actor.email.strip().lower()
            or str(owner["email"]).strip().lower() == TOUR_EMAIL
        ):
            raise ApprovalHTTPError(422, "Events can only be simulated on a customer's order")
        event = event_id or f"sim-{order}-{days}"
    event = event.strip()[:200]
    key = engine.event_key(engine.TOPIC_SHIPMENT_DELAYED, event)
    try:
        async with get_pool().acquire() as conn:
            outbox_id = await enqueue(
                conn,
                engine.TOPIC_SHIPMENT_DELAYED,
                {"event_id": event, "order_id": str(order), "delay_days": days, "simulated": True},
                dedupe_key=key,
                aggregate_id=str(order),
            )
    except AutonomyPolicyError:  # pragma: no cover - get_policy already succeeded above
        raise ApprovalHTTPError(500, "Autonomy policy unavailable") from None
    logger.info("autonomy event simulated outbox=%s enqueued=%s", log_safe(outbox_id), outbox_id is not None)
    return {
        "enqueued": outbox_id is not None,
        "outbox_id": outbox_id,
        "event_id": event,
        "order_id": str(order),
        "delay_days": days,
    }


async def list_decisions(*, state: str | None = None, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    pool = get_pool()
    decisions = await engine.list_decisions(pool, state=state, limit=limit, offset=offset)
    return {
        "decisions": decisions,
        "total": await engine.count_decisions(pool, state=state),  # every match, not just this page
        "limit": max(1, min(int(limit), 200)),
        "offset": max(0, int(offset)),
        "budgets": await engine.budget_overview(pool),
        "enabled": settings.AUTONOMY_ENABLED,
        "simulator_enabled": settings.AUTONOMY_ENABLED and settings.AUTONOMY_SIMULATOR_ENABLED,
        "scenarios": SCENARIOS if settings.AUTONOMY_ENABLED and settings.AUTONOMY_SIMULATOR_ENABLED else {},
    }


async def get_decision(decision_id: str) -> dict[str, Any]:
    found = await engine.get_decision(get_pool(), decision_id)
    if found is None:
        raise ApprovalHTTPError(404, "Decision not found")
    return found


async def reverse(decision_id: str, actor: Actor, *, note: str | None = None) -> dict[str, Any]:
    cleaned = (note or "").strip()[:MAX_NOTE_LENGTH] or None
    try:
        return await engine.reverse(get_pool(), decision_id, actor_email=actor.email, note=cleaned)
    except engine.AutonomyError as exc:
        raise _refuse(exc) from None
