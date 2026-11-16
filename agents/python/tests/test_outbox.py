"""D-3 -- the outbox: joins the caller's transaction, dedupes on key, supports future-dated timers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from shared import outbox

pytestmark = pytest.mark.integration


async def test_enqueue_returns_an_id_and_defaults(clean_db: asyncpg.Pool) -> None:
    async with clean_db.acquire() as conn:
        agg = str(uuid.uuid4())
        oid = await outbox.enqueue(conn, "approval.created", {"n": 1}, aggregate_id=agg)

    row = await clean_db.fetchrow("SELECT * FROM outbox WHERE id = $1", oid)
    assert row["topic"] == "approval.created" and str(row["aggregate_id"]) == agg
    assert row["attempts"] == 0 and row["processed_at"] is None and row["locked_until"] is None
    assert row["available_at"] <= datetime.now(UTC) + timedelta(seconds=5)
    assert row["dedupe_key"] is None


async def test_enqueue_rolls_back_with_the_callers_transaction(clean_db: asyncpg.Pool) -> None:
    async with clean_db.acquire() as conn:
        with pytest.raises(RuntimeError):
            async with conn.transaction():
                await outbox.enqueue(conn, "approval.created", {})
                raise RuntimeError("business write failed")
    assert await clean_db.fetchval("SELECT count(*) FROM outbox") == 0


async def test_enqueue_commits_with_the_callers_transaction(clean_db: asyncpg.Pool) -> None:
    async with clean_db.acquire() as conn:
        async with conn.transaction():
            await outbox.enqueue(conn, "a.b", {})
            # Not visible to another connection until the caller commits.
            assert await clean_db.fetchval("SELECT count(*) FROM outbox") == 0
    assert await clean_db.fetchval("SELECT count(*) FROM outbox") == 1


async def test_dedupe_key_makes_enqueue_idempotent(clean_db: asyncpg.Pool) -> None:
    async with clean_db.acquire() as conn:
        first = await outbox.enqueue(conn, "approval.reminder_due", {"v": 1}, dedupe_key="reminder:abc")
        second = await outbox.enqueue(conn, "approval.reminder_due", {"v": 2}, dedupe_key="reminder:abc")
        other = await outbox.enqueue(conn, "approval.reminder_due", {"v": 3}, dedupe_key="reminder:def")

    assert first is not None and second is None and other is not None
    assert await clean_db.fetchval("SELECT count(*) FROM outbox") == 2
    assert await clean_db.fetchval("SELECT payload->>'v' FROM outbox WHERE dedupe_key = 'reminder:abc'") == "1"


async def test_rows_without_a_key_are_never_deduplicated(clean_db: asyncpg.Pool) -> None:
    async with clean_db.acquire() as conn:
        ids = [await outbox.enqueue(conn, "x.y", {}) for _ in range(3)]
    assert len(set(ids)) == 3


async def test_a_future_available_at_is_a_timer(clean_db: asyncpg.Pool) -> None:
    due = datetime.now(UTC) + timedelta(hours=4)
    async with clean_db.acquire() as conn:
        oid = await outbox.enqueue(conn, "approval.sla_breached", {}, available_at=due)

    stored = await clean_db.fetchval("SELECT available_at FROM outbox WHERE id = $1", oid)
    assert abs((stored - due).total_seconds()) < 1
    ready = await clean_db.fetchval("SELECT count(*) FROM outbox WHERE processed_at IS NULL AND available_at <= NOW()")
    assert ready == 0


async def test_empty_topic_is_refused(clean_db: asyncpg.Pool) -> None:
    async with clean_db.acquire() as conn:
        with pytest.raises(ValueError):
            await outbox.enqueue(conn, "")
