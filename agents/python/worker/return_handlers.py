"""The return lifecycle in the outbox worker (#95): a carrier scan resumes a waiting return, or its timer expires it.

``return.carrier_scanned`` (produced by the admin simulator; there is no carrier integration) resumes the paused
workflow with a :class:`~workflows.return_replace.ScanEvent`. ``return.scan_timeout`` is the timer opened with the
wait: if the wait is still open it resumes the workflow with ``received=False``, which ends the run with an error and
marks the return ``expired``.

Both go through ``orchestrator.approvals_service.resume_workflow`` with ``kind='carrier_scan'``, so a resume is claimed
atomically, fenced, bounded by a timeout and runs as the requester, exactly like a reviewer's decision. The two events
can race (a scan arriving as the timer fires): whichever claims the wait first wins and the other finds it answered,
which is a 409 here and is dropped, not retried. Behind ``RETURN_LIFECYCLE_ENABLED``: with it off a row is consumed
and does nothing.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import asyncpg

from orchestrator.approvals_service import ApprovalHTTPError, resume_workflow
from shared.config import settings
from shared.log_safe import log_safe
from shared.notifications import notify
from shared.return_lifecycle import (
    INSPECTIONS_LINK,
    KIND_CARRIER_SCAN,
    RUNS_LINK,
    TOPIC_CARRIER_SCANNED,
    TOPIC_INSPECTION_TIMEOUT,
    TOPIC_SCAN_TIMEOUT,
)
from worker.runner import Handler, OutboxRow
from workflows.return_replace import ScanEvent

logger = logging.getLogger(__name__)

#: An answered or missing wait is final. Only a resume that timed out (504) is worth another attempt.
_FINAL = frozenset({404, 409})


async def _resume(row: OutboxRow, event: ScanEvent, reviewer: str) -> bool:
    """Answer the wait named in the row; ``True`` if this call resumed the workflow, ``False`` if it was not ours."""
    try:
        await resume_workflow(str(row.payload["hitl_id"]), event, reviewer_email=reviewer, kind=KIND_CARRIER_SCAN)
    except ApprovalHTTPError as exc:
        if exc.status_code in _FINAL:
            logger.info(
                "return wait already answered id=%s topic=%s status=%s",
                log_safe(row.id),
                row.topic,
                exc.status_code,
            )
            return False
        raise
    return True


def _parse_time(value: str) -> datetime:
    """The scan's own timestamp, or now if it carried none or an unreadable one (asyncpg wants a datetime)."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(UTC)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def _customer_of(conn: asyncpg.Connection, return_id: str) -> str | None:
    return await conn.fetchval(
        "SELECT u.email FROM returns r JOIN users u ON u.id = r.user_id WHERE r.id = $1::uuid", return_id
    )


async def on_carrier_scanned(conn: asyncpg.Connection, row: OutboxRow) -> None:
    if not settings.RETURN_LIFECYCLE_ENABLED:
        logger.info("carrier scan ignored, lifecycle disabled id=%s", log_safe(row.id))
        return
    payload = row.payload
    return_id = str(payload["return_id"])
    event = ScanEvent(
        received=True,
        carrier=str(payload.get("carrier", "")),
        tracking=str(payload.get("tracking", "")),
        scanned_at=str(payload.get("scanned_at", "")),
    )
    if not await _resume(row, event, reviewer="carrier"):
        return
    # The return is on its way back. Idempotent: a retry after this point changes nothing.
    await conn.execute(
        """UPDATE returns SET scanned_at = COALESCE(scanned_at, $2), status = 'shipped_back'
            WHERE id = $1::uuid AND status IN ('requested', 'approved')""",
        return_id,
        _parse_time(event.scanned_at),
    )
    customer = await _customer_of(conn, return_id)
    if customer:
        await notify(
            conn,
            kind="return.carrier_scanned",
            title="The carrier has your return",
            body=f"Return {return_id} was scanned by {event.carrier or 'the carrier'}.",
            link=RUNS_LINK,
            user_email=customer,
        )
    logger.info("return.carrier_scanned id=%s return=%s", log_safe(row.id), log_safe(return_id))


async def on_scan_timeout(conn: asyncpg.Connection, row: OutboxRow) -> None:
    if not settings.RETURN_LIFECYCLE_ENABLED:
        logger.info("scan timeout ignored, lifecycle disabled id=%s", log_safe(row.id))
        return
    return_id = str(row.payload["return_id"])
    note = f"no carrier scan within {settings.RETURN_SCAN_TIMEOUT_DAYS} days"
    if not await _resume(row, ScanEvent(received=False, note=note), reviewer="system"):
        return  # the scan got there first
    await conn.execute(
        "UPDATE returns SET status = 'expired' WHERE id = $1::uuid AND status IN ('requested', 'approved')", return_id
    )
    customer = await _customer_of(conn, return_id)
    if customer:
        await notify(
            conn,
            kind="return.expired",
            title="Your return expired",
            body=f"Return {return_id} was not scanned by the carrier in time, so it was closed.",
            link=RUNS_LINK,
            user_email=customer,
        )
    logger.info("return.scan_timeout id=%s return=%s", log_safe(row.id), log_safe(return_id))


async def on_inspection_timeout(conn: asyncpg.Connection, row: OutboxRow) -> None:
    """Support has not inspected a return in time. Tell the admins; never decide for them.

    Unlike the scan wait this does not resume anything. A package the carrier delivered is not the customer's
    fault, so an overdue inspection must not deny it: it is escalated to ``admin`` and stays pending. One nudge per
    wait (the timer's dedupe key), so a slow queue does not become a stream of notifications.
    """
    if not settings.RETURN_LIFECYCLE_ENABLED:
        logger.info("inspection timeout ignored, lifecycle disabled id=%s", log_safe(row.id))
        return
    still_waiting = await conn.fetchval(
        "SELECT 1 FROM hitl_requests WHERE id = $1::uuid AND kind = 'return_inspection' AND status = 'pending'",
        str(row.payload["hitl_id"]),
    )
    if not still_waiting:
        return  # inspected in time
    return_id = str(row.payload["return_id"])
    await notify(
        conn,
        kind="return.inspection_overdue",
        title="A return has waited too long for inspection",
        body=f"Return {return_id} has been waiting {settings.RETURN_INSPECTION_TIMEOUT_DAYS} days for inspection.",
        link=INSPECTIONS_LINK,
        role="admin",
    )
    logger.info("return.inspection_overdue id=%s return=%s", log_safe(row.id), log_safe(return_id))


#: topic -> handler.
HANDLERS: dict[str, Handler] = {
    TOPIC_CARRIER_SCANNED: on_carrier_scanned,
    TOPIC_SCAN_TIMEOUT: on_scan_timeout,
    TOPIC_INSPECTION_TIMEOUT: on_inspection_timeout,
}
