"""D-2 -- seeding the support user moves no existing seeded row.

The seeder is deterministic (``random.seed(42)``) and the replay fixtures are keyed on the data it
produces, so the new user must be appended without touching the global random stream.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import asyncpg
import pytest

# scripts/ lives at the repo root, outside the agents/python project.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts import seed as seed_mod  # noqa: E402


async def _snapshot(pool: asyncpg.Pool) -> tuple[list[tuple], list[tuple]]:
    async with pool.acquire() as conn:
        users = await conn.fetch(
            "SELECT email, name, role, loyalty_tier, total_spend FROM users WHERE email <> $1 ORDER BY email",
            seed_mod.SUPPORT_EMAIL,
        )
        orders = await conn.fetch(
            """SELECT o.id, u.email, o.status, o.total, o.discount_amount
               FROM orders o JOIN users u ON u.id = o.user_id ORDER BY o.id"""
        )
    return [tuple(r) for r in users], [tuple(r) for r in orders]


async def _no_embeddings(conn: asyncpg.Connection) -> int:
    return 0


async def _no_support(conn: asyncpg.Connection) -> None:
    return None


_REAL_SEED_SUPPORT_USER = seed_mod.seed_support_user


async def _run_seed(database_url: str, monkeypatch: pytest.MonkeyPatch, *, support: bool) -> None:
    monkeypatch.setattr(seed_mod, "DATABASE_URL", database_url)
    monkeypatch.setattr(seed_mod, "seed_product_embeddings", _no_embeddings)
    monkeypatch.setattr(seed_mod, "seed_support_user", _REAL_SEED_SUPPORT_USER if support else _no_support)
    random.seed(42)
    await seed_mod.main()


def test_support_user_is_not_in_the_random_feeding_user_list() -> None:
    assert all(email != seed_mod.SUPPORT_EMAIL for email, *_ in seed_mod.USERS)


@pytest.mark.asyncio
async def test_seeding_support_user_changes_no_existing_row(
    clean_db: asyncpg.Pool, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _run_seed(database_url, monkeypatch, support=False)
    without = await _snapshot(clean_db)
    assert without[0] and without[1], "the baseline seed produced no users/orders"

    # main() clears and re-seeds an already-seeded database.
    await _run_seed(database_url, monkeypatch, support=True)
    with_support = await _snapshot(clean_db)

    assert with_support == without
    row = await clean_db.fetchrow("SELECT role FROM users WHERE email = $1", seed_mod.SUPPORT_EMAIL)
    assert row is not None and row["role"] == "support"


@pytest.mark.asyncio
async def test_support_seed_step_draws_no_random_numbers(clean_db: asyncpg.Pool) -> None:
    async with clean_db.acquire() as conn:
        user_ids = await seed_mod.seed_users(conn)
        await seed_mod.seed_agent_catalog(conn)
        assert "admin.demo@gmail.com" in user_ids
        random.seed(1234)
        before = random.getstate()
        await seed_mod.seed_support_user(conn)
    assert random.getstate() == before
