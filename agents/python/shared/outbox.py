"""Transactional outbox: events and timers as rows in Postgres.

``enqueue()`` takes the *caller's* connection and does a single INSERT on it, so the row commits or rolls
back with the business write that caused it — an approval that is created is announced, one that is rolled
back never was. There is no second connection and no implicit transaction here; if the caller has none
open, the INSERT autocommits on its own.

A timer is simply a row whose ``available_at`` is in the future. ``dedupe_key`` makes an enqueue idempotent:
a second call with the same key inserts nothing and returns ``None``. Rows without a key are never
deduplicated.

The worker that drains the table (``SELECT ... FOR UPDATE SKIP LOCKED``) is a later step.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import asyncpg


async def enqueue(
    conn: asyncpg.Connection,
    topic: str,
    payload: dict[str, Any] | None = None,
    available_at: datetime | None = None,
    dedupe_key: str | None = None,
    *,
    aggregate_id: str | None = None,
) -> int | None:
    """Insert one outbox row on ``conn``; return its id, or ``None`` if ``dedupe_key`` already exists.

    ``available_at`` defaults to now (the database's clock); pass a future time for a timer.
    ``aggregate_id`` is the approval / return / order the event is about, kept as a column so the worker
    and the audit views can find a subject's events without parsing the payload.
    """
    if not topic:
        raise ValueError("an outbox row needs a topic")
    return await conn.fetchval(
        """INSERT INTO outbox (topic, aggregate_id, payload, dedupe_key, available_at)
           VALUES ($1, $2, $3::jsonb, $4, COALESCE($5, NOW()))
           ON CONFLICT (dedupe_key) DO NOTHING
           RETURNING id""",
        topic,
        aggregate_id,
        json.dumps(payload or {}, default=str),
        dedupe_key,
        available_at,
    )
