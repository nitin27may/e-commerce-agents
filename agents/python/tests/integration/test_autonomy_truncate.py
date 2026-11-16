"""V-1 (#94) review fix: the append-only autonomy tables also refuse TRUNCATE, and clearing them is one named path.

``agent_decisions`` and ``store_credit_ledger`` refuse UPDATE and DELETE by row trigger, but TRUNCATE is not a row
operation: the seeder's ``TRUNCATE ... users CASCADE`` used to empty both without a word. A statement trigger now
refuses a TRUNCATE that would destroy rows; ``reset_autonomy_log()`` is the one deliberate way (the seeder and the test
fixtures use it).
"""

from __future__ import annotations

import random
import sys
import uuid
from pathlib import Path
from typing import Any

import asyncpg
import pytest

# scripts/ lives at the repo root, outside the agents/python project.
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from scripts import seed as seed_mod  # noqa: E402

pytestmark = pytest.mark.asyncio


async def _one_decision_and_credit(pool: Any) -> uuid.UUID:
    user_id = uuid.uuid4()
    await pool.execute(
        "INSERT INTO users (id, email, password_hash, name, role) "
        "VALUES ($1, 'truncate@example.com', 'x', 'T', 'customer')",
        user_id,
    )
    decision_id = uuid.uuid4()
    await pool.execute(
        """INSERT INTO agent_decisions (id, entry_type, event_key, action, user_id, amount, tier, outcome)
           VALUES ($1, 'decision', 'order.shipment_delayed:e1', 'goodwill_credit', $2, 5, 'auto', 'executed')""",
        decision_id,
        user_id,
    )
    await pool.execute(
        "INSERT INTO store_credit_ledger (user_id, amount, reason, decision_id) VALUES ($1, 5, 'goodwill', $2)",
        user_id,
        decision_id,
    )
    return user_id


@pytest.mark.parametrize(
    "statement",
    [
        "TRUNCATE agent_decisions CASCADE",
        "TRUNCATE store_credit_ledger",
        "TRUNCATE users CASCADE",  # the seeder's shape: the log is hit through the foreign key
    ],
)
async def test_a_stray_truncate_that_would_destroy_rows_is_refused(clean_db: asyncpg.Pool, statement: str) -> None:
    await _one_decision_and_credit(clean_db)

    with pytest.raises(asyncpg.RestrictViolationError, match="append-only"):
        await clean_db.execute(statement)

    assert await clean_db.fetchval("SELECT count(*) FROM agent_decisions") == 1
    assert await clean_db.fetchval("SELECT count(*) FROM store_credit_ledger") == 1


async def test_truncating_empty_tables_is_allowed_so_blanket_fixtures_keep_working(clean_db: asyncpg.Pool) -> None:
    await clean_db.execute("TRUNCATE users CASCADE")  # nothing in the logs: nothing lost


async def test_the_named_reset_path_clears_both_logs_and_rearms_the_guard(clean_db: asyncpg.Pool) -> None:
    await _one_decision_and_credit(clean_db)

    async with clean_db.acquire() as conn, conn.transaction():
        await conn.execute("SELECT reset_autonomy_log()")
        assert await conn.fetchval("SELECT count(*) FROM agent_decisions") == 0
        assert await conn.fetchval("SELECT count(*) FROM store_credit_ledger") == 0
        # the guard is closed again for the rest of the transaction: a log row written now is protected
        user_id = await conn.fetchval("SELECT id FROM users LIMIT 1")
        await conn.execute(
            "INSERT INTO store_credit_ledger (user_id, amount, reason) VALUES ($1, 1, 'after reset')", user_id
        )
        with pytest.raises(asyncpg.RestrictViolationError):
            async with conn.transaction():  # a savepoint, so the refusal does not abort the outer transaction
                await conn.execute("TRUNCATE store_credit_ledger")


async def test_a_full_reseed_still_works_and_clears_the_autonomy_log(
    clean_db: asyncpg.Pool, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(seed_mod, "DATABASE_URL", database_url)
    monkeypatch.setattr(seed_mod, "hash_pw", lambda _pw: "test-hash")
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    random.seed(42)
    await seed_mod.main()
    order = await clean_db.fetchrow("SELECT id, user_id FROM orders LIMIT 1")
    decision_id = uuid.uuid4()
    await clean_db.execute(
        """INSERT INTO agent_decisions (id, entry_type, event_key, action, user_id, order_id, amount, tier, outcome)
           VALUES ($1, 'decision', 'order.shipment_delayed:e1', 'goodwill_credit', $2, $3, 5, 'auto', 'executed')""",
        decision_id,
        order["user_id"],
        order["id"],
    )
    await clean_db.execute(
        "INSERT INTO store_credit_ledger (user_id, amount, reason, decision_id) VALUES ($1, 5, 'g', $2)",
        order["user_id"],
        decision_id,
    )

    random.seed(42)
    await seed_mod.main()  # the reseed path: "already has N users - clearing and re-seeding"

    assert await clean_db.fetchval("SELECT count(*) FROM users") > 0
    assert await clean_db.fetchval("SELECT count(*) FROM agent_decisions") == 0
    assert await clean_db.fetchval("SELECT count(*) FROM store_credit_ledger") == 0
