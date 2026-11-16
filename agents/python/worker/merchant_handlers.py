"""The daily merchant run (#93, M-4): ``merchant.daily_run_due`` generates proposals for every seller, books the next.

Behind ``MERCHANT_OPS_SCHEDULE_ENABLED`` (default **false**). With it off a due row is consumed and does
nothing and does *not* reschedule, so switching the flag off ends the chain; switching it on again is
:func:`ensure_scheduled` at worker start, which books the next run.

The chain is self-rescheduling: each run enqueues the next one in the same transaction that consumed it, with
a dedupe key built from the run's date, so a redelivered row, a restarted worker or two workers booking at
once produce one row per day, never two.

Each seller is generated inside its own savepoint: one seller whose generation fails (logged by exception class
only) does not stop the others or roll back their proposals. Generation is idempotent per open line
(:func:`shared.merchant.proposals.generate_proposal`), so a second run on the same day adds nothing.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time, timedelta

import asyncpg

from shared.config import settings
from shared.log_safe import log_safe
from shared.merchant.proposals import generate_proposal
from shared.outbox import enqueue
from worker.runner import Handler, OutboxRow

logger = logging.getLogger(__name__)

TOPIC_DAILY_RUN = "merchant.daily_run_due"


def next_run_at(after: datetime, hour: int) -> datetime:
    """The first ``hour``:00 UTC strictly after ``after``."""
    moment = after.astimezone(UTC)
    candidate = moment.replace(hour=hour, minute=0, second=0, microsecond=0)
    return candidate if candidate > moment else candidate + timedelta(days=1)


async def schedule_next(conn: asyncpg.Connection, after: datetime) -> int | None:
    """Book the next daily run (one per calendar day); the outbox id, or None if that day is already booked."""
    at = next_run_at(after, settings.MERCHANT_OPS_RUN_HOUR_UTC)
    return await enqueue(
        conn,
        TOPIC_DAILY_RUN,
        {"run_date": at.date().isoformat()},
        available_at=at,
        dedupe_key=f"{TOPIC_DAILY_RUN}:{at.date().isoformat()}",
    )


async def ensure_scheduled(pool: asyncpg.Pool) -> None:
    """Called when the worker starts: with the schedule enabled, make sure a next run is booked."""
    if not settings.MERCHANT_OPS_SCHEDULE_ENABLED:
        return
    async with pool.acquire() as conn:
        booked = await schedule_next(conn, datetime.now(UTC))
    logger.info("merchant daily run %s", "booked" if booked else "already booked")


def _scheduled_for(row: OutboxRow) -> datetime:
    """The moment this row's run was booked for (its ``run_date`` at the run hour); the epoch if it carries none."""
    try:
        day = date.fromisoformat(str(row.payload["run_date"]))
    except (KeyError, ValueError):
        return datetime.min.replace(tzinfo=UTC)
    return datetime.combine(day, time(settings.MERCHANT_OPS_RUN_HOUR_UTC), tzinfo=UTC)


async def on_daily_run_due(conn: asyncpg.Connection, row: OutboxRow) -> None:
    if not settings.MERCHANT_OPS_SCHEDULE_ENABLED:
        logger.info("merchant daily run ignored, schedule disabled id=%s", row.id)
        return
    now = datetime.now(UTC)
    sellers = await conn.fetch(
        """SELECT u.id FROM users u
            WHERE u.role = 'seller' AND u.is_active IS NOT FALSE
              AND EXISTS (SELECT 1 FROM products p WHERE p.seller_id = u.id AND p.is_active)
            ORDER BY u.email, u.id"""
    )
    created = 0
    for seller in sellers:
        try:
            async with conn.transaction():  # a savepoint: a failing seller rolls back alone
                result = await generate_proposal(conn, seller["id"], trigger="scheduled", now=now)
            created += 1 if result.proposal_id else 0
        except Exception as exc:  # noqa: BLE001 - one seller must not stop the run; class only, never text
            logger.warning(
                "merchant daily run failed for a seller seller=%s class=%s", log_safe(seller["id"]), type(exc).__name__
            )
    # Book the day after the run this row stands for, even if the row was processed early or the clock is behind:
    # the next key must never equal this row's own.
    await schedule_next(conn, max(now, _scheduled_for(row)))
    logger.info("merchant daily run done sellers=%s proposals=%s", len(sellers), created)


HANDLERS: dict[str, Handler] = {TOPIC_DAILY_RUN: on_daily_run_due}
