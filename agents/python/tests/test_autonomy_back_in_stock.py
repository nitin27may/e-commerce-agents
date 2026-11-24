"""#299 -- ``inventory.back_in_stock``: a notification to customers whose cart holds the product. No money moves.

The engine's guards still apply where they mean something: stock is checked (never announce what is not there), a
customer hears at most N times a day, the audience is bounded, an event acts once per customer, staff and the demo tour
user are never notified by the engine, and every decision is a row in the same append-only log.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import asyncpg
import pytest
import yaml

from shared.autonomy import engine
from tests import autonomy_support as aus
from worker.main import HANDLERS
from worker.runner import run_once

TOPIC = engine.TOPIC_BACK_IN_STOCK
TICK: dict[str, Any] = {
    "batch_size": 20,
    "lease_seconds": 60,
    "max_attempts": 3,
    "backoff_base": 0.0,
    "backoff_cap": 0.0,
}


@pytest.fixture
async def w(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> aus.World:
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    return await aus.world(clean_db)


async def _handle(w: aus.World, product: uuid.UUID | str | None, event_id: str = "stock-1") -> dict[str, Any]:
    return await engine.handle_event(w.pool, TOPIC, aus.restock(product, event_id), outbox_id=1)


async def _notes(w: aus.World, kind: str = "autonomy.back_in_stock") -> list[asyncpg.Record]:
    return await w.pool.fetch("SELECT * FROM notifications WHERE kind = $1 ORDER BY user_email", kind)


async def test_every_customer_with_the_product_in_a_cart_is_told_and_staff_once(w: aus.World) -> None:
    product = await aus.product(w, "Lamp", price="30.00", stock=5)
    for email in (aus.CUSTOMER, aus.OTHER, aus.THIRD):
        await aus.cart(w, email, product)

    result = await _handle(w, product)

    assert result["status"] == "processed" and result["targets"] == 3 and result["by_tier"] == {"auto_notify": 3}
    notes = await _notes(w)
    assert [n["user_email"] for n in notes] == sorted([aus.CUSTOMER, aus.OTHER, aus.THIRD])
    assert notes[0]["title"] == "Lamp is back in stock" and notes[0]["link"] == f"/products/{product}"
    staff = await _notes(w, "autonomy.action_taken")
    assert len(staff) == 1 and staff[0]["role"] == "admin" and "3 customer(s)" in staff[0]["body"]
    rows = await aus.decisions(w.pool)
    assert len(rows) == 3
    for row in rows:
        assert (row["action"], row["tier"], row["outcome"]) == ("back_in_stock_notice", "auto_notify", "executed")
        assert row["amount"] is None and row["budget_key"] is None and row["reversible"] is False
        assert row["order_id"] is None and row["event_type"] == TOPIC
        assert aus.loads(row["input"])["event"]["product_name"] == "Lamp"
    assert await w.pool.fetchval("SELECT count(*) FROM store_credit_ledger") == 0
    assert {n["decision_id"] for n in notes} == {r["id"] for r in rows}


async def test_a_notice_moves_no_money_and_touches_no_budget(w: aus.World) -> None:
    product = await aus.product(w, stock=5)
    await aus.cart(w, aus.CUSTOMER, product)

    await _handle(w, product)

    assert await w.pool.fetchval("SELECT count(*) FROM approvals") == 0
    overview = {b["budget_key"]: b for b in await engine.budget_overview(w.pool)}
    assert set(overview) == {"goodwill_credit", "price_adjustment"}
    assert all(b["used_count"] == 0 for b in overview.values())
    (shown,) = await engine.list_decisions(w.pool)
    assert shown["state"] == "executed" and shown["can_reverse"] is False and shown["amount"] is None


async def test_a_replayed_event_notifies_nobody_twice(w: aus.World) -> None:
    product = await aus.product(w, stock=5)
    await aus.cart(w, aus.CUSTOMER, product)

    first = await _handle(w, product)
    second = await _handle(w, product)

    assert (first["new"], second["new"]) == (1, 0)
    assert len(await _notes(w)) == 1 and len(await _notes(w, "autonomy.action_taken")) == 1


async def test_a_customer_hears_at_most_twice_a_day(w: aus.World) -> None:
    products = [await aus.product(w, f"P{i}", stock=5) for i in range(3)]
    for p in products:
        await aus.cart(w, aus.CUSTOMER, p)

    for i, p in enumerate(products):
        await _handle(w, p, f"e{i}")

    rows = await aus.decisions(w.pool)
    assert [r["tier"] for r in rows] == ["auto_notify", "auto_notify", "no_action"]
    assert aus.loads(rows[2]["reasons"]) == ["customer_daily_notice_cap"]
    assert len(await _notes(w)) == 2


async def test_two_events_for_one_customer_at_once_cannot_both_exceed_the_cap(w: aus.World) -> None:
    products = [await aus.product(w, f"P{i}", stock=5) for i in range(4)]
    for p in products:
        await aus.cart(w, aus.CUSTOMER, p)

    await asyncio.gather(*(_handle(w, p, f"e{i}") for i, p in enumerate(products)))

    assert len(await _notes(w)) == 2


# ── when nothing is announced ────────────────────────────────────────────────


@pytest.mark.parametrize(("stock", "active"), [(0, True), (5, False)])
async def test_stock_that_is_not_there_is_never_announced(w: aus.World, stock: int, active: bool) -> None:
    product = await aus.product(w, stock=stock, active=active)
    await aus.cart(w, aus.CUSTOMER, product)

    result = await _handle(w, product)

    assert result["status"] == "no_action" and result["reasons"] == ["product_not_in_stock"]
    assert await _notes(w) == []
    (row,) = await aus.decisions(w.pool)
    assert row["user_id"] is None and row["action"] == "none"


async def test_a_stale_cart_is_not_told(w: aus.World) -> None:
    product = await aus.product(w, stock=5)
    await aus.cart(w, aus.CUSTOMER, product, age_days=31)
    await aus.cart(w, aus.OTHER, product, age_days=29)

    result = await _handle(w, product)

    assert result["targets"] == 1
    assert [n["user_email"] for n in await _notes(w)] == [aus.OTHER]


async def test_nobody_with_the_product_in_a_cart_is_no_targets(w: aus.World) -> None:
    product = await aus.product(w, stock=5)

    result = await _handle(w, product)

    assert result == {"status": "no_targets", "targets": 0, "new": 0, "by_tier": {}}
    assert await aus.decisions(w.pool) == []


async def test_staff_are_not_notified_by_the_engine(w: aus.World) -> None:
    product = await aus.product(w, stock=5)
    await aus.cart(w, aus.SUPPORT, product)
    await aus.cart(w, aus.CUSTOMER, product)

    result = await _handle(w, product)

    assert result["by_tier"] == {"auto_notify": 1, "no_action": 1}
    assert [n["user_email"] for n in await _notes(w)] == [aus.CUSTOMER]
    reasons = {aus.loads(r["reasons"])[0] for r in await aus.decisions(w.pool) if r["tier"] == "no_action"}
    assert reasons == {"recipient_is_staff"}


async def test_the_demo_tour_user_is_never_a_target_and_no_row_references_it(w: aus.World) -> None:
    from shared.demo_tour import TOUR_EMAIL

    w.users[TOUR_EMAIL] = await aus.user(w.pool, TOUR_EMAIL)
    product = await aus.product(w, stock=5)
    await aus.cart(w, TOUR_EMAIL, product)

    result = await _handle(w, product)

    assert result["status"] == "no_targets"
    assert await w.pool.fetchval("SELECT count(*) FROM agent_decisions WHERE user_id = $1", w.users[TOUR_EMAIL]) == 0
    assert await _notes(w) == []


async def test_an_audience_over_the_cap_is_told_nothing_and_logged_once(
    w: aus.World, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = yaml.safe_load(open(aus.POLICY_PATH, encoding="utf-8").read())
    doc["actions"]["back_in_stock_notice"]["rules"][0]["when"][0]["value"] = 1
    doc["events"]["inventory.back_in_stock"]["max_recipients"] = 1
    path = tmp_path / "autonomy.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    monkeypatch.setattr("shared.config.settings.AUTONOMY_POLICY_PATH", str(path))
    product = await aus.product(w, stock=5)
    await aus.cart(w, aus.CUSTOMER, product)
    await aus.cart(w, aus.OTHER, product)

    result = await _handle(w, product)

    assert result["status"] == "no_action" and result["reasons"] == ["recipients_over_cap"]
    assert await _notes(w) == []
    (row,) = await aus.decisions(w.pool)
    assert row["tier"] == "no_action" and row["user_id"] is None


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (aus.restock(None, "bad"), "invalid_event_product"),
        (aus.restock("nope", "bad"), "invalid_event_product"),
        (aus.restock(uuid.uuid4(), "bad"), "product_not_found"),
    ],
)
async def test_a_bad_event_is_one_no_action_row(w: aus.World, payload: dict[str, Any], reason: str) -> None:
    result = await engine.handle_event(w.pool, TOPIC, payload, outbox_id=1)

    assert result["reasons"] == [reason]
    (row,) = await aus.decisions(w.pool)
    assert row["user_id"] is None and row["action"] == "none"


# ── through the worker ───────────────────────────────────────────────────────


async def test_the_worker_routes_the_topic_when_autonomy_is_on_and_ignores_it_when_off(
    w: aus.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    product = await aus.product(w, stock=5)
    await aus.cart(w, aus.CUSTOMER, product)
    row = (
        "INSERT INTO outbox (topic, payload, available_at) VALUES ($1, $2::jsonb, NOW() - interval '1 second')",
        TOPIC,
        json.dumps(aus.restock(product, "w-1")),
    )
    await w.pool.execute(*row)

    off = await run_once(w.pool, HANDLERS, "w1", **TICK)

    assert (off.claimed, off.done) == (1, 1) and await aus.decisions(w.pool) == []
    monkeypatch.setattr("shared.config.settings.AUTONOMY_ENABLED", True)
    await w.pool.execute(*row)
    on = await run_once(w.pool, HANDLERS, "w1", **TICK)
    assert (on.claimed, on.done) == (1, 1)
    assert len(await _notes(w)) == 1
