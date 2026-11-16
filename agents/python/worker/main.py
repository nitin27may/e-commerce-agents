"""The outbox worker process: ``uv run python -m worker.main``.

Polls the ``outbox`` table (``worker.runner.run_once``), dispatching each due row to its topic handler. Any
number of workers may run against one database; each row goes to exactly one. SIGTERM / SIGINT stop it
gracefully: the batch in flight finishes its current row, the rest are handed back, then the pool closes.

Every loop touches ``OUTBOX_WORKER_HEARTBEAT_FILE``; the container healthcheck reads its age, which is how
a hung worker shows up as unhealthy without the process serving any port.

With ``OUTBOX_WORKER_ENABLED=false`` the process stays up and idle (so the stack still reports healthy) and
drains nothing.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket
from pathlib import Path

import asyncpg

from shared.config import settings
from shared.db import use_pool
from worker.approval_handlers import HANDLERS as APPROVAL_HANDLERS
from worker.autonomy_handlers import HANDLERS as AUTONOMY_HANDLERS
from worker.merchant_handlers import HANDLERS as MERCHANT_HANDLERS
from worker.merchant_handlers import ensure_scheduled
from worker.runner import Handler, run_once

#: Every topic the worker serves. Plain dispatch: adding a topic is adding a line to a handler module's dict.
HANDLERS: dict[str, Handler] = {**APPROVAL_HANDLERS, **MERCHANT_HANDLERS, **AUTONOMY_HANDLERS}

logger = logging.getLogger("worker")


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _heartbeat() -> None:
    with contextlib.suppress(OSError):
        Path(settings.OUTBOX_WORKER_HEARTBEAT_FILE).touch()


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)


async def run(stop: asyncio.Event, handlers: dict[str, Handler] | None = None) -> None:
    """The poll loop. Returns when ``stop`` is set."""
    handlers = HANDLERS if handlers is None else handlers
    _heartbeat()
    if not settings.OUTBOX_WORKER_ENABLED:
        logger.info("outbox worker disabled (OUTBOX_WORKER_ENABLED=false); idling")
        while not stop.is_set():
            _heartbeat()
            await _sleep_or_stop(stop, 5.0)
        return

    worker_id = _worker_id()
    pool = await asyncpg.create_pool(settings.DATABASE_URL, min_size=1, max_size=max(2, settings.OUTBOX_BATCH_SIZE))
    use_pool(pool)  # handlers that act on a paused workflow go through the same code as a staff decision
    try:
        await ensure_scheduled(pool)  # the daily merchant run, only when MERCHANT_OPS_SCHEDULE_ENABLED
    except (asyncpg.PostgresError, OSError) as exc:  # the schema may not be migrated yet: the poll loop below copes too
        logger.warning("merchant schedule not booked class=%s", type(exc).__name__)
    logger.info("outbox worker started id=%s topics=%s", worker_id, sorted(handlers))
    try:
        while not stop.is_set():
            _heartbeat()
            try:
                result = await run_once(
                    pool,
                    handlers,
                    worker_id,
                    batch_size=settings.OUTBOX_BATCH_SIZE,
                    lease_seconds=settings.OUTBOX_LEASE_SECONDS,
                    max_attempts=settings.OUTBOX_MAX_ATTEMPTS,
                    backoff_base=settings.OUTBOX_BACKOFF_BASE_SECONDS,
                    backoff_cap=settings.OUTBOX_BACKOFF_MAX_SECONDS,
                    stop=stop,
                )
            except (asyncpg.PostgresError, OSError) as exc:
                # The schema may not be migrated yet, or the database is restarting: wait and look again.
                logger.warning("outbox poll failed class=%s; retrying", type(exc).__name__)
                await _sleep_or_stop(stop, settings.OUTBOX_POLL_INTERVAL_SECONDS)
                continue
            if result.claimed:
                logger.info(
                    "outbox tick claimed=%s done=%s failed=%s parked=%s",
                    result.claimed,
                    result.done,
                    result.failed,
                    result.parked,
                )
            if result.claimed == 0:
                await _sleep_or_stop(stop, settings.OUTBOX_POLL_INTERVAL_SECONDS)
    finally:
        use_pool(None)
        await pool.close()
        logger.info("outbox worker stopped id=%s", worker_id)


async def _main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await run(stop)


def main() -> None:
    logging.basicConfig(level=settings.LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(_main())


if __name__ == "__main__":
    main()
