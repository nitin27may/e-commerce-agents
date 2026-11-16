"""Event autonomy in the outbox worker (#94): the event handler, and what an SLA breach does to an event approval.

``order.shipment_delayed`` rows are handed to :func:`shared.autonomy.engine.handle_event`, which decides and acts at
most once per event. Behind ``AUTONOMY_ENABLED`` (default **false**): with it off a row is consumed and does
nothing. Nothing outside the simulator produces the event today, and the simulator has its own switch.

The decision runs on the pool, not on the row's connection: ``run_once`` reserves the event on its own connection
and marks it complete when the decision has committed, so the decision must commit on its own too, or a later
failure in this row's transaction would roll the work back while the reservation said it was done.

:func:`on_event_breach` is called by the SLA handler (``worker/approval_handlers.py``) for an approval of source
``event`` whose review ran out of time. It never expires work in flight:

* a staff decision that holds the approval's claim is *executing*: the timer waits and looks again;
* an approved action that already ran but whose decision was never recorded is recorded, not expired;
* otherwise nobody decided it and nothing was done: it is expired (the decision log shows ``expired``), and staff
  are told. The customer is not: they never knew a credit was being considered.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from shared.approvals import repo
from shared.approvals.repo import ApprovalStatus as S
from shared.autonomy import engine
from shared.config import settings
from shared.db import get_pool
from shared.log_safe import log_safe
from shared.notifications import notify
from shared.outbox import enqueue
from worker.runner import Handler, OutboxRow

logger = logging.getLogger(__name__)

_DEFER_SECONDS = 30
#: A live claim is bounded by its TTL, so this is only a ceiling that stops the timer re-booking itself for ever.
_MAX_WAITS_SPARE = 12


def _max_waits() -> int:
    return int(settings.HITL_CLAIM_TTL_SECONDS // _DEFER_SECONDS) + _MAX_WAITS_SPARE


async def on_shipment_delayed(conn: asyncpg.Connection, row: OutboxRow) -> None:
    if not settings.AUTONOMY_ENABLED:
        logger.info("autonomy event ignored, engine disabled id=%s", log_safe(row.id))
        return
    result = await engine.handle_event(get_pool(), row.topic, row.payload, outbox_id=row.id)
    logger.info("autonomy event handled id=%s status=%s", log_safe(row.id), result.get("status"))


async def on_event_breach(conn: asyncpg.Connection, row: OutboxRow, approval: dict[str, Any]) -> None:
    """The review deadline of an ``event`` approval passed with no decision. ``conn`` holds the approval row lock."""
    approval_id = str(approval["id"])
    claim_live = await conn.fetchval(
        "SELECT claimed_at IS NOT NULL AND claimed_at >= NOW() - make_interval(secs => $2) "
        "FROM approvals WHERE id = $1",
        approval["id"],
        float(settings.HITL_CLAIM_TTL_SECONDS),
    )
    waits = int(row.payload.get("waits", 0))
    if claim_live and waits < _max_waits():
        await enqueue(
            conn,
            repo.TOPIC_SLA_BREACHED,
            {**row.payload, "waits": waits + 1},
            available_at=datetime.now(UTC) + timedelta(seconds=_DEFER_SECONDS),
            dedupe_key=f"{repo.TOPIC_SLA_BREACHED}:{approval_id}:{approval['escalation_level']}:defer:{waits + 1}",
            aggregate_id=approval_id,
        )
        logger.info("event approval expiry deferred, a decide is in flight id=%s", log_safe(approval_id))
        return
    ran = await engine.executed_for_approval(conn, approval_id)
    if ran is not None:
        # The credit was granted and the decide died before recording it: finish recording, never expire it.
        approved = await repo.transition(
            conn,
            approval_id,
            [S.PENDING_REVIEW],
            S.APPROVED,
            actor_email=ran["actor_email"],
            action="decided",
            payload={"decision": "approve", "recovered": True},
            decided_by=ran["actor_email"],
            decision={"decision": "approve", "recovered": True},
        )
        await repo.transition(
            conn,
            approval_id,
            [S(approved["status"])],
            S.EXECUTED,
            action="executed",
            payload={"success": True, "recovered": True},
        )
        return
    await repo.transition(
        conn,
        approval_id,
        [S.PENDING_REVIEW, S.NEEDS_INFO],
        S.EXPIRED,
        action="sla_timeout",
        payload={"reason": "sla_breached", "on_timeout": "expired"},
        decision={"decision": "expired", "reason": "sla_timeout"},
    )
    await notify(
        conn,
        kind="autonomy.proposal_expired",
        title="An automatic action expired without a decision",
        body="A goodwill credit the system asked about was not decided in time. Nothing was done.",
        link=engine.DECISIONS_LINK,
        approval_id=approval_id,
        role="admin",
    )


#: topic -> handler. Add a line per event type the engine learns.
HANDLERS: dict[str, Handler] = {engine.TOPIC_SHIPMENT_DELAYED: on_shipment_delayed}
