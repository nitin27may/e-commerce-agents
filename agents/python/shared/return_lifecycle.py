"""The return lifecycle after approval (#95): the waits a return makes, as rows.

A paused return is a ``hitl_requests`` row (MAF's resume token and the checkpoint to resume from) plus, for a wait
that can run out, an outbox timer. This module opens those rows; ``worker/return_handlers.py`` answers them.

* **carrier scan** — ``kind = 'carrier_scan'``. Opened when an approved return has its label. Answered by an
  ``return.carrier_scanned`` outbox event, or expired by ``return.scan_timeout``.

A wait is found by the return it belongs to (``payload->>'return_id'``), never by scanning a user's rows.

Behind ``RETURN_LIFECYCLE_ENABLED``: with it off the workflow never pauses here, so nothing opens a wait.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from shared.config import settings
from shared.log_safe import log_safe
from shared.notifications import notify
from shared.outbox import enqueue

logger = logging.getLogger(__name__)

KIND_CARRIER_SCAN = "carrier_scan"
TOPIC_CARRIER_SCANNED = "return.carrier_scanned"
TOPIC_SCAN_TIMEOUT = "return.scan_timeout"

#: Where a customer follows a return (the /runs page shows the run and its timeline).
RUNS_LINK = "/runs"


async def open_scan_wait(
    pool: asyncpg.Pool,
    *,
    workflow_run_id: Any,
    user_email: str,
    wait: dict[str, Any],
    checkpoint_id: Any,
) -> str:
    """Record that a return is waiting for the carrier's first scan; return the ``hitl_requests`` id.

    One transaction: the wait row, its expiry timer and the customer's "label ready" notification commit together,
    so a wait never exists without a timer that will eventually close it. Idempotent per request id: opening the same
    wait twice returns the row that is already there.
    """
    return_id = str(wait["return_id"])
    timeout_days = int(wait.get("timeout_days") or settings.RETURN_SCAN_TIMEOUT_DAYS)
    payload = {
        "return_id": return_id,
        "order_id": wait.get("order_id"),
        "label_url": wait.get("label_url"),
        "timeout_days": timeout_days,
        "text": f"Waiting for the carrier to scan return {return_id}.",
    }
    async with pool.acquire() as conn, conn.transaction():
        existing = await conn.fetchval(
            "SELECT id FROM hitl_requests WHERE workflow_run_id = $1 AND request_id = $2",
            workflow_run_id,
            wait["request_id"],
        )
        if existing:
            return str(existing)
        hitl_id = await conn.fetchval(
            """INSERT INTO hitl_requests
                   (workflow_run_id, request_id, checkpoint_id, user_email, kind, payload, status)
               VALUES ($1, $2, $3, $4, $5, $6::jsonb, 'pending')
               RETURNING id""",
            workflow_run_id,
            wait["request_id"],
            checkpoint_id,
            user_email,
            KIND_CARRIER_SCAN,
            json.dumps(payload, default=str),
        )
        await enqueue(
            conn,
            TOPIC_SCAN_TIMEOUT,
            {"hitl_id": str(hitl_id), "return_id": return_id},
            available_at=datetime.now(UTC) + timedelta(days=timeout_days),
            dedupe_key=f"{TOPIC_SCAN_TIMEOUT}:{hitl_id}",
            aggregate_id=return_id,
        )
        await notify(
            conn,
            kind="return.label_ready",
            title="Your return label is ready",
            body=(
                f"Return {return_id} is approved. Send the package back; "
                "we update this return when the carrier scans it."
            ),
            link=RUNS_LINK,
            user_email=user_email,
        )
    logger.info("return.scan_wait_opened hitl=%s return=%s", log_safe(hitl_id), log_safe(return_id))
    return str(hitl_id)


async def open_scan_wait_row(pool: asyncpg.Pool, hitl_id: str) -> asyncpg.Record | None:
    """The still-pending carrier-scan wait with this id, or ``None``."""
    return await pool.fetchrow(
        "SELECT * FROM hitl_requests WHERE id = $1 AND kind = $2 AND status = 'pending'", hitl_id, KIND_CARRIER_SCAN
    )


async def pending_scan_wait_for_return(pool: asyncpg.Pool, return_id: str) -> asyncpg.Record | None:
    """The carrier-scan wait still open for a return."""
    return await pool.fetchrow(
        """SELECT * FROM hitl_requests
            WHERE kind = $1 AND status IN ('pending', 'processing') AND payload->>'return_id' = $2
            ORDER BY created_at DESC LIMIT 1""",
        KIND_CARRIER_SCAN,
        return_id,
    )


async def request_scan(pool: asyncpg.Pool, return_id: str, *, carrier: str = "SIM", tracking: str = "") -> int | None:
    """Announce that the carrier scanned a return's package (the producer; the worker resumes the workflow).

    Nothing outside the admin simulator calls this today: there is no carrier integration. Raises ``LookupError`` if
    the return is not waiting for a scan. Idempotent per return: a second scan is ignored (returns ``None``).
    """
    wait = await pending_scan_wait_for_return(pool, return_id)
    if wait is None:
        raise LookupError(f"return {return_id} is not waiting for a carrier scan")
    async with pool.acquire() as conn:
        return await enqueue(
            conn,
            TOPIC_CARRIER_SCANNED,
            {
                "hitl_id": str(wait["id"]),
                "return_id": return_id,
                "carrier": carrier,
                "tracking": tracking or f"TRK-{return_id[:8].upper()}",
                "scanned_at": datetime.now(UTC).isoformat(),
            },
            dedupe_key=f"{TOPIC_CARRIER_SCANNED}:{return_id}",
            aggregate_id=return_id,
        )
