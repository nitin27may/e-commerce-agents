"""The Alice post-pass makes her latest order delivered, draws no random numbers, and is idempotent."""

from __future__ import annotations

import random
import sys
from pathlib import Path

import asyncpg
import pytest

# scripts/ lives at the repo root, outside the agents/python project.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts import seed as seed_mod  # noqa: E402


@pytest.mark.asyncio
async def test_alice_latest_order_is_delivered(clean_db: asyncpg.Pool) -> None:
    async with clean_db.acquire() as conn:
        user_ids = await seed_mod.seed_users(conn)
        products = await seed_mod.seed_products(conn, user_ids)
        random.seed(42)
        await seed_mod.seed_orders(conn, user_ids, products)
        # Force the update path: the random seed may already have delivered her latest order.
        await conn.execute(
            """UPDATE orders SET status = 'processing' WHERE id = (SELECT o.id FROM orders o
               JOIN users u ON u.id = o.user_id WHERE u.email = $1 ORDER BY o.created_at DESC, o.id DESC LIMIT 1)""",
            seed_mod.ALICE_EMAIL,
        )
        random.seed(1234)
        before = random.getstate()
        await seed_mod.seed_alice_delivered_order(conn)
        assert random.getstate() == before
        await seed_mod.seed_alice_delivered_order(conn)
        row = await conn.fetchrow(
            """SELECT o.id, o.status, o.tracking_number FROM orders o JOIN users u ON u.id = o.user_id
               WHERE u.email = $1 ORDER BY o.created_at DESC, o.id DESC LIMIT 1""",
            seed_mod.ALICE_EMAIL,
        )
        history = [
            r["status"]
            for r in await conn.fetch(
                "SELECT status FROM order_status_history WHERE order_id = $1 ORDER BY timestamp", row["id"]
            )
        ]
    assert row["status"] == "delivered" and row["tracking_number"]
    assert history == ["placed", "confirmed", "shipped", "out_for_delivery", "delivered"]
