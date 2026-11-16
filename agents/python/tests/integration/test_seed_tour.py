"""C-2 (#86): deterministic seeding and a tour user whose data can be put back.

Real Postgres (``clean_db``), the real seeder, no LLM. What has to hold:

* order ids are explicit uuid5s -- the same on every seed -- and drawing them
  consumed no ``random`` values, so everything else seeded is unchanged;
* ``SEED_CLOCK_OFFSET_DAYS`` moves timestamps and nothing else;
* the tour user looks like the plan says (most recent order delivered, over $500,
  5 days old, plus a shipped order) and ``seed_tour_user`` never touches the
  global ``random`` stream;
* ``reset_tour_user`` restores return eligibility even after a return was made
  through the real tool (which caches its result under an idempotency key, keyed
  on an order id that is now stable -- a stale key would replay instead of act).
"""

from __future__ import annotations

import random
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import shared.db as shared_db
from shared.context import current_user_email, current_user_role
from shared.demo_tour import (
    DELIVERED_ORDER_KEY,
    SHIPPED_ORDER_KEY,
    TOUR_EMAIL,
    reset_tour_user,
    tour_order_id,
)

# scripts/ lives at the repo root, outside the agents/python project.
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from scripts import seed as seed_mod  # noqa: E402

pytestmark = pytest.mark.asyncio


async def _run_seed(monkeypatch: pytest.MonkeyPatch, database_url: str, *, offset: str = "0", **kwargs: Any) -> None:
    monkeypatch.setattr(seed_mod, "DATABASE_URL", database_url)
    monkeypatch.setattr(seed_mod, "hash_pw", lambda _pw: "test-hash")  # bcrypt x21 is most of a seed's wall time
    monkeypatch.setenv("SEED_CLOCK_OFFSET_DAYS", offset)
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    random.seed(42)  # what a fresh `python -m scripts.seed` process starts from
    await seed_mod.main(**kwargs)


async def _snapshot(pool: Any) -> dict[str, list[tuple[Any, ...]]]:
    """Everything the seeder wrote that is not a timestamp, a hash or the tour user."""
    queries = {
        "orders": """SELECT o.id, u.email, o.status, o.total, o.shipping_carrier, o.tracking_number
                     FROM orders o JOIN users u ON u.id = o.user_id
                     WHERE u.email <> $1 ORDER BY o.id""",
        "items": """SELECT oi.order_id, oi.product_id, oi.quantity, oi.subtotal
                    FROM order_items oi JOIN orders o ON o.id = oi.order_id JOIN users u ON u.id = o.user_id
                    WHERE u.email <> $1 ORDER BY oi.order_id, oi.product_id""",
        "reviews": """SELECT r.product_id, r.rating, r.title, r.verified_purchase
                      FROM reviews r ORDER BY r.product_id, r.title, r.rating, r.body""",
        "inventory": "SELECT product_id, quantity FROM warehouse_inventory ORDER BY product_id, quantity",
        "carts": "SELECT product_id, quantity FROM cart_items ORDER BY product_id, quantity",
    }
    out: dict[str, list[tuple[Any, ...]]] = {}
    async with pool.acquire() as conn:
        for name, sql in queries.items():
            args = (TOUR_EMAIL,) if "$1" in sql else ()
            out[name] = [tuple(r) for r in await conn.fetch(sql, *args)]
    return out


async def _oldest_and_newest(pool: Any) -> tuple[datetime, datetime]:
    row = await pool.fetchrow(
        """SELECT MIN(o.created_at) AS lo, MAX(o.created_at) AS hi
           FROM orders o JOIN users u ON u.id = o.user_id WHERE u.email <> $1""",
        TOUR_EMAIL,
    )
    return row["lo"], row["hi"]


async def test_order_ids_are_stable_and_the_clock_offset_moves_only_timestamps(
    clean_db: Any, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _run_seed(monkeypatch, database_url)
    first = await _snapshot(clean_db)
    first_lo, first_hi = await _oldest_and_newest(clean_db)
    assert len(first["orders"]) == 200
    assert {r[0] for r in first["orders"]} == {seed_mod.order_id_for(i) for i in range(200)}

    await _run_seed(monkeypatch, database_url)
    again = await _snapshot(clean_db)
    assert again == first, "seeding twice must give identical order ids and identical data"

    await _run_seed(monkeypatch, database_url, offset="13")
    shifted = await _snapshot(clean_db)
    lo, hi = await _oldest_and_newest(clean_db)
    assert shifted == first, "SEED_CLOCK_OFFSET_DAYS must not change any value that is not a timestamp"
    # Seeded 13 days "ago": every order timestamp sits ~13 days earlier (seconds of wall-clock slack).
    assert timedelta(days=12, hours=23) < first_hi - hi < timedelta(days=13, hours=1)
    assert timedelta(days=12, hours=23) < first_lo - lo < timedelta(days=13, hours=1)


async def test_tour_user_shape(clean_db: Any, database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    await _run_seed(monkeypatch, database_url)

    orders = await clean_db.fetch(
        """SELECT o.id, o.status, o.total, o.created_at FROM orders o JOIN users u ON u.id = o.user_id
           WHERE u.email = $1 ORDER BY o.created_at DESC""",
        TOUR_EMAIL,
    )
    assert [o["id"] for o in orders] == [tour_order_id(DELIVERED_ORDER_KEY), tour_order_id(SHIPPED_ORDER_KEY)]
    delivered, shipped = orders
    assert delivered["status"] == "delivered" and delivered["total"] > 500
    assert shipped["status"] == "shipped"
    age = datetime.now(UTC) - delivered["created_at"]
    assert timedelta(days=4, hours=23) < age < timedelta(days=5, hours=1)

    # Most recent order of the whole tour user is the delivered one, and it is
    # eligible for return through the real tool.
    from shared.tools.return_tools import check_return_eligibility

    monkeypatch.setattr(shared_db, "_pool", clean_db)
    current_user_email.set(TOUR_EMAIL)
    verdict = await check_return_eligibility(order_id=str(delivered["id"]))
    assert verdict["eligible"] is True

    spend = await clean_db.fetchval("SELECT total_spend FROM users WHERE email = $1", TOUR_EMAIL)
    assert spend == delivered["total"] + shipped["total"]
    items = await clean_db.fetchval(
        "SELECT COUNT(*) FROM order_items WHERE order_id = ANY($1::uuid[])", [delivered["id"], shipped["id"]]
    )
    assert items == 2


async def test_seed_tour_user_leaves_the_global_random_stream_alone(
    clean_db: Any, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _run_seed(monkeypatch, database_url)
    random.seed(99)
    state = random.getstate()
    async with clean_db.acquire() as conn:
        await seed_mod.seed_tour_user(conn)
    assert random.getstate() == state


async def test_reset_tour_user_only_leaves_every_other_row_untouched(
    clean_db: Any, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _run_seed(monkeypatch, database_url)
    before = await _snapshot(clean_db)

    await _run_seed(monkeypatch, database_url, reset_tour_only=True)

    assert await _snapshot(clean_db) == before


async def test_reset_restores_return_eligibility(
    clean_db: Any, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from shared.tools.return_tools import check_return_eligibility, initiate_return

    await _run_seed(monkeypatch, database_url)
    monkeypatch.setattr(shared_db, "_pool", clean_db)
    current_user_email.set(TOUR_EMAIL)
    current_user_role.set("customer")
    order_id = str(tour_order_id(DELIVERED_ORDER_KEY))

    # The tour: ask, return, and leave a trail (conversation, memory, cart, checkout spend).
    assert (await check_return_eligibility(order_id=order_id))["eligible"] is True
    first = await initiate_return(order_id=order_id, reason="changed my mind")
    assert "return_id" in first
    assert (await check_return_eligibility(order_id=order_id))["eligible"] is False
    user_id = await clean_db.fetchval("SELECT id FROM users WHERE email = $1", TOUR_EMAIL)
    await clean_db.execute("INSERT INTO conversations (user_id, title) VALUES ($1, 'tour chat')", user_id)
    await clean_db.execute(
        "INSERT INTO agent_memories (user_id, category, content) VALUES ($1, 'preference', 'likes blue')", user_id
    )
    await clean_db.execute("INSERT INTO carts (user_id) VALUES ($1)", user_id)
    await clean_db.execute("UPDATE users SET total_spend = 99999 WHERE id = $1", user_id)
    await clean_db.execute(
        "INSERT INTO orders (user_id, status, total, shipping_address) VALUES ($1, 'placed', 10, '{}'::jsonb)",
        user_id,
    )

    async with clean_db.acquire() as conn:
        assert await reset_tour_user(conn) is True

    # Back to the seeded state...
    assert (await check_return_eligibility(order_id=order_id))["eligible"] is True
    assert await clean_db.fetchval("SELECT COUNT(*) FROM returns WHERE user_id = $1", user_id) == 0
    assert await clean_db.fetchval("SELECT COUNT(*) FROM conversations WHERE user_id = $1", user_id) == 0
    assert await clean_db.fetchval("SELECT COUNT(*) FROM agent_memories WHERE user_id = $1", user_id) == 0
    assert await clean_db.fetchval("SELECT COUNT(*) FROM carts WHERE user_id = $1", user_id) == 0
    assert await clean_db.fetchval("SELECT COUNT(*) FROM orders WHERE user_id = $1", user_id) == 2
    assert await clean_db.fetchval("SELECT total_spend FROM users WHERE id = $1", user_id) < 99999

    # ...and the same order id can really be returned again. A stale idempotency
    # key would replay `first` and write no new row; this must act.
    second = await initiate_return(order_id=order_id, reason="changed my mind")
    assert second["return_id"] != first["return_id"]
    assert await clean_db.fetchval("SELECT COUNT(*) FROM returns WHERE user_id = $1", user_id) == 1


async def test_reset_is_a_no_op_when_there_is_no_tour_user(clean_db: Any) -> None:
    async with clean_db.acquire() as conn:
        assert await reset_tour_user(conn) is False
    assert uuid.UUID(str(tour_order_id(DELIVERED_ORDER_KEY))) != tour_order_id(SHIPPED_ORDER_KEY)
