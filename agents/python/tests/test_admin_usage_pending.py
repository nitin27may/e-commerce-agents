"""`/api/admin/usage` reports how many access requests still wait for an admin (real Postgres, no model)."""

from __future__ import annotations

from typing import Any

import pytest

import shared.db as shared_db
from orchestrator.routes.legacy import get_usage_stats

pytestmark = pytest.mark.asyncio

ADMIN = {"sub": "admin@example.test", "role": "admin", "user_id": ""}


async def _seed_requests(pool: Any, statuses: list[str]) -> None:
    async with pool.acquire() as conn:
        user_id = await conn.fetchval(
            "INSERT INTO users (email, name, password_hash, role) "
            "VALUES ('u@example.test', 'U', 'x', 'customer') RETURNING id"
        )
        await conn.execute("INSERT INTO agent_catalog (name, display_name, description) VALUES ('a1', 'A1', 'd')")
        for status in statuses:
            await conn.execute(
                """INSERT INTO access_requests (user_id, agent_name, role_requested, use_case, status)
                   VALUES ($1, 'a1', 'power_user', 'testing', $2)""",
                user_id,
                status,
            )


async def test_pending_requests_counts_only_pending(monkeypatch: pytest.MonkeyPatch, clean_db: Any) -> None:
    monkeypatch.setattr(shared_db, "_pool", clean_db)
    await _seed_requests(clean_db, ["pending", "pending", "approved", "denied"])

    stats = await get_usage_stats(admin=ADMIN)

    assert stats["overall"]["pending_requests"] == 2


async def test_pending_requests_is_zero_with_no_requests(monkeypatch: pytest.MonkeyPatch, clean_db: Any) -> None:
    monkeypatch.setattr(shared_db, "_pool", clean_db)

    stats = await get_usage_stats(admin=ADMIN)

    assert stats["overall"]["pending_requests"] == 0
