"""One tick of the outbox worker: claim due rows, run each in its own transaction, retry or park failures.

Claiming is ``UPDATE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED)``: rows a concurrent worker is claiming
are skipped, not waited for, so any number of workers can run and each row goes to exactly one. A claim is a
*lease* (``locked_until`` + ``locked_by``): if the worker dies, the lease runs out and another worker takes the
row. Claiming also counts the attempt, so a row that crashes its worker every time still runs out of attempts
instead of looping forever.

Each claimed row is then handled in its own transaction on its own connection: the handler's writes and the
"processed" mark commit together or not at all. The transaction first re-checks that it still owns the lease (a
worker whose lease was taken over must not also finish the row) and holds the row lock until commit.

A handler that raises rolls its transaction back. The row then records a *fixed* error class (never exception
text or payload), is pushed out by capped exponential backoff and, after ``OUTBOX_MAX_ATTEMPTS`` tries, is
parked: ``failed_at`` is set and it is neither retried nor processed again until an admin deals with it.

Logs carry ids, topics and error classes only.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import asyncpg

from shared.log_safe import log_safe

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutboxRow:
    id: int
    topic: str
    aggregate_id: str | None
    payload: dict[str, Any]
    attempts: int


#: A topic handler. It runs inside the row's transaction: write through ``conn`` and it commits with the mark.
Handler = Callable[[asyncpg.Connection, OutboxRow], Awaitable[None]]


@dataclass(frozen=True)
class TickResult:
    claimed: int = 0
    done: int = 0
    failed: int = 0
    parked: int = 0
    skipped: int = 0


class UnknownTopicError(LookupError):
    """No handler is registered for the row's topic."""


def error_class(exc: BaseException) -> str:
    """A short fixed label for ``outbox.last_error``. Never derived from the message: it could hold payload text."""
    if isinstance(exc, UnknownTopicError):
        return "unknown_topic"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, asyncpg.PostgresError):
        return "database_error"
    return "handler_error"


def backoff_seconds(attempts: int, base: float, cap: float) -> float:
    """Delay before the next try after ``attempts`` tries: ``base * 2**(attempts - 1)``, never above ``cap``."""
    return min(cap, base * (2 ** max(attempts - 1, 0)))


async def claim(
    pool: asyncpg.Pool, worker_id: str, *, batch_size: int, lease_seconds: float, max_attempts: int
) -> list[OutboxRow]:
    """Lease up to ``batch_size`` due rows to ``worker_id``, counting one attempt on each.

    ``approval.timeout_apply`` for a paused return a .NET orchestrator opened (``approvals.recommendation->>'stack'
    = 'dotnet'``, workflow source) is never claimed here: only that stack can resume its own checkpoint, and its
    hosted service (``ApprovalTimeoutWorker``) drains those rows. Excluded in the claim, not in the handler, so the
    row stays unprocessed for its owner instead of being marked done.
    """
    rows = await pool.fetch(
        """UPDATE outbox SET locked_until = NOW() + make_interval(secs => $2), locked_by = $1,
                             attempts = attempts + 1
            WHERE id IN (SELECT id FROM outbox
                          WHERE processed_at IS NULL AND failed_at IS NULL
                            AND available_at <= NOW() AND attempts < $4
                            AND (locked_until IS NULL OR locked_until < NOW())
                            AND NOT (topic = 'approval.timeout_apply' AND EXISTS (
                                  SELECT 1 FROM approvals a
                                   WHERE a.id = outbox.aggregate_id AND a.source = 'workflow'
                                     AND a.recommendation->>'stack' = 'dotnet'))
                          ORDER BY available_at, id
                          LIMIT $3
                            FOR UPDATE SKIP LOCKED)
        RETURNING id, topic, aggregate_id, payload, attempts""",
        worker_id,
        float(lease_seconds),
        batch_size,
        max_attempts,
    )
    claimed = [
        OutboxRow(
            id=r["id"],
            topic=r["topic"],
            aggregate_id=str(r["aggregate_id"]) if r["aggregate_id"] else None,
            payload=json.loads(r["payload"]) if isinstance(r["payload"], str) else dict(r["payload"]),
            attempts=r["attempts"],
        )
        for r in rows
    ]
    return sorted(claimed, key=lambda r: r.id)


async def park_exhausted(pool: asyncpg.Pool, max_attempts: int) -> int:
    """Park rows that used every attempt and whose last lease ran out without an outcome (a crashed worker)."""
    result = await pool.execute(
        """UPDATE outbox SET failed_at = NOW(), last_error = 'lease_expired', locked_until = NULL, locked_by = NULL
            WHERE processed_at IS NULL AND failed_at IS NULL AND attempts >= $1
              AND locked_until IS NOT NULL AND locked_until < NOW()""",
        max_attempts,
    )
    return int(result.rsplit(" ", 1)[1])


async def release(pool: asyncpg.Pool, row: OutboxRow, worker_id: str) -> None:
    """Hand a claimed-but-unstarted row back (graceful shutdown), un-counting the attempt."""
    await pool.execute(
        """UPDATE outbox SET locked_until = NULL, locked_by = NULL, attempts = GREATEST(attempts - 1, 0)
            WHERE id = $1 AND locked_by = $2 AND processed_at IS NULL""",
        row.id,
        worker_id,
    )


async def _record_failure(
    pool: asyncpg.Pool,
    row: OutboxRow,
    worker_id: str,
    exc: BaseException,
    *,
    max_attempts: int,
    backoff_base: float,
    backoff_cap: float,
) -> bool:
    """Record the failure on the row; return True if it was parked."""
    parked = await pool.fetchval(
        """UPDATE outbox SET last_error = $3, locked_until = NULL, locked_by = NULL,
                             available_at = NOW() + make_interval(secs => $4),
                             failed_at = CASE WHEN attempts >= $5 THEN NOW() END
            WHERE id = $1 AND locked_by = $2 AND processed_at IS NULL
        RETURNING failed_at IS NOT NULL""",
        row.id,
        worker_id,
        error_class(exc),
        backoff_seconds(row.attempts, backoff_base, backoff_cap),
        max_attempts,
    )
    logger.warning(
        "outbox row failed id=%s topic=%s class=%s attempts=%s parked=%s",
        log_safe(row.id),
        log_safe(row.topic),
        error_class(exc),
        row.attempts,
        bool(parked),
    )
    return bool(parked)


async def process_row(
    pool: asyncpg.Pool,
    row: OutboxRow,
    handlers: Mapping[str, Handler],
    worker_id: str,
    *,
    lease_seconds: float,
    max_attempts: int,
    backoff_base: float,
    backoff_cap: float,
) -> str:
    """Handle one claimed row. Returns ``done``, ``failed``, ``parked`` or ``skipped`` (lease lost)."""
    try:
        handler = handlers.get(row.topic)
        if handler is None:
            raise UnknownTopicError(row.topic)
        async with pool.acquire() as conn, conn.transaction():
            owned = await conn.fetchval(
                """SELECT 1 FROM outbox WHERE id = $1 AND locked_by = $2
                      AND processed_at IS NULL AND failed_at IS NULL FOR UPDATE""",
                row.id,
                worker_id,
            )
            if not owned:
                logger.info("outbox row skipped, lease lost id=%s", log_safe(row.id))
                return "skipped"
            async with asyncio.timeout(lease_seconds):
                await handler(conn, row)
            await conn.execute(
                """UPDATE outbox SET processed_at = NOW(), locked_until = NULL, locked_by = NULL, last_error = NULL
                    WHERE id = $1""",
                row.id,
            )
        return "done"
    except Exception as exc:  # noqa: BLE001 - any handler failure is retried or parked, never propagated
        parked = await _record_failure(
            pool, row, worker_id, exc, max_attempts=max_attempts, backoff_base=backoff_base, backoff_cap=backoff_cap
        )
        return "parked" if parked else "failed"


async def run_once(
    pool: asyncpg.Pool,
    handlers: Mapping[str, Handler],
    worker_id: str,
    *,
    batch_size: int,
    lease_seconds: float,
    max_attempts: int,
    backoff_base: float,
    backoff_cap: float,
    stop: asyncio.Event | None = None,
) -> TickResult:
    """One tick: park crash-exhausted rows, claim a batch, handle each row. ``stop`` hands the rest back."""
    swept = await park_exhausted(pool, max_attempts)
    rows = await claim(pool, worker_id, batch_size=batch_size, lease_seconds=lease_seconds, max_attempts=max_attempts)
    counts = {"done": 0, "failed": 0, "parked": swept, "skipped": 0}
    for index, row in enumerate(rows):
        if stop is not None and stop.is_set():
            for rest in rows[index:]:
                await release(pool, rest, worker_id)
            break
        outcome = await process_row(
            pool,
            row,
            handlers,
            worker_id,
            lease_seconds=lease_seconds,
            max_attempts=max_attempts,
            backoff_base=backoff_base,
            backoff_cap=backoff_cap,
        )
        counts[outcome] += 1
    return TickResult(claimed=len(rows), **counts)
