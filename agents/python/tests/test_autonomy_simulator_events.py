"""#299 -- the simulator and the admin list for the price-drop and back-in-stock events, through the worker."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import asyncpg
import pytest

from shared.autonomy import engine
from tests import autonomy_support as aus
from tests.test_proposal_execute import _as, _call
from worker.main import HANDLERS
from worker.runner import run_once

SIMULATE = "/api/admin/events/simulate"
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
    monkeypatch.setattr("shared.config.settings.AUTONOMY_ENABLED", True)
    monkeypatch.setattr("shared.config.settings.AUTONOMY_SIMULATOR_ENABLED", True)
    return await aus.world(clean_db)


def _admin():
    return _as(aus.ADMIN, "admin")


async def _tick(w: aus.World):
    return await run_once(w.pool, HANDLERS, "w1", **TICK)


async def _bought(w: aus.World, price: str = "100.00") -> uuid.UUID:
    product = await aus.product(w, "Widget", price=price)
    await aus.line(w, await aus.order(w, aus.CUSTOMER, status="delivered", age_days=2), product, price=price)
    return product


async def test_the_price_drop_scenario_picks_a_recent_line_drops_it_a_tenth_and_pays_alone(w: aus.World) -> None:
    product = await _bought(w)

    sent = (await _call(_admin(), "POST", SIMULATE, {"scenario": "price-drop"})).json()
    await _tick(w)

    assert sent["enqueued"] is True and sent["event_type"] == engine.TOPIC_PRICE_DROPPED
    assert (sent["product_id"], sent["new_price"], sent["order_id"]) == (str(product), "90.00", None)
    (row,) = await aus.decisions(w.pool)
    assert (row["tier"], row["amount"]) == ("auto", Decimal("10.00"))
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == Decimal("10.00")


async def test_the_price_drop_scenario_does_not_repeat_a_line_it_already_used(w: aus.World) -> None:
    await _bought(w)
    await _call(_admin(), "POST", SIMULATE, {"scenario": "price-drop"})
    await _tick(w)

    again = await _call(_admin(), "POST", SIMULATE, {"scenario": "price-drop"})

    assert again.status_code == 409


async def test_an_explicit_price_drop_replays_to_nothing(w: aus.World) -> None:
    product = await _bought(w)
    body = {"event_type": "product.price_dropped", "product_id": str(product), "new_price": 95, "event_id": "fixed"}

    first = await _call(_admin(), "POST", SIMULATE, body)
    second = await _call(_admin(), "POST", SIMULATE, body)
    await _tick(w)

    assert [first.json()["enqueued"], second.json()["enqueued"]] == [True, False]
    assert len(await aus.decisions(w.pool)) == 1


async def test_the_back_in_stock_scenario_notifies_the_cart_holder_through_the_worker(w: aus.World) -> None:
    product = await aus.product(w, "Lamp", stock=4)
    await aus.cart(w, aus.CUSTOMER, product)

    sent = (await _call(_admin(), "POST", SIMULATE, {"scenario": "back-in-stock"})).json()
    await _tick(w)

    assert sent["event_type"] == engine.TOPIC_BACK_IN_STOCK and sent["product_id"] == str(product)
    note = await w.pool.fetchrow("SELECT * FROM notifications WHERE kind = 'autonomy.back_in_stock'")
    assert note["user_email"] == aus.CUSTOMER
    # and the same product is not announced again by the scenario
    assert (await _call(_admin(), "POST", SIMULATE, {"scenario": "back-in-stock"})).status_code == 409


async def test_the_back_in_stock_scenario_ignores_a_product_that_is_out_of_stock(w: aus.World) -> None:
    await aus.cart(w, aus.CUSTOMER, await aus.product(w, "Lamp", stock=0))

    assert (await _call(_admin(), "POST", SIMULATE, {"scenario": "back-in-stock"})).status_code == 409


@pytest.mark.parametrize(
    "body",
    [
        {"event_type": "product.price_dropped"},  # no product
        {"event_type": "product.price_dropped", "product_id": str(uuid.uuid4())},  # no price
        {"event_type": "product.price_dropped", "product_id": "nope", "new_price": 5},
        {"event_type": "product.price_dropped", "product_id": str(uuid.uuid4()), "new_price": 1.005},
        {"event_type": "inventory.back_in_stock"},
        {"event_type": "order.shipment_dropped"},
        {"scenario": "price-drop", "product_id": str(uuid.uuid4())},
        {"scenario": "price-drop", "event_type": "inventory.back_in_stock"},
        {"scenario": "price-drop", "delay_days": 3},
        {"scenario": "small", "product_id": str(uuid.uuid4())},
        {"product_id": str(uuid.uuid4()), "new_price": 5},
    ],
)
async def test_a_malformed_product_event_request_is_a_422(w: aus.World, body: dict[str, Any]) -> None:
    resp = await _call(_admin(), "POST", SIMULATE, body)

    assert resp.status_code == 422, resp.text
    assert await w.pool.fetchval("SELECT count(*) FROM outbox") == 0


async def test_an_explicit_event_for_a_missing_product_is_a_404(w: aus.World) -> None:
    resp = await _call(
        _admin(),
        "POST",
        SIMULATE,
        {"event_type": "product.price_dropped", "product_id": str(uuid.uuid4()), "new_price": 5},
    )

    assert resp.status_code == 404


async def test_the_delay_scenarios_still_work_and_say_which_event_they_sent(w: aus.World) -> None:
    await aus.order(w, aus.CUSTOMER)

    sent = (await _call(_admin(), "POST", SIMULATE, {"scenario": "small"})).json()

    assert sent["event_type"] == engine.TOPIC_SHIPMENT_DELAYED and sent["delay_days"] == 3 and sent["order_id"]


@pytest.mark.parametrize("email", [aus.CUSTOMER, aus.SUPPORT])
async def test_the_new_events_are_admin_only(w: aus.World, email: str) -> None:
    role = "customer" if email == aus.CUSTOMER else "support"

    resp = await _call(_as(email, role), "POST", SIMULATE, {"scenario": "price-drop"})

    assert resp.status_code == 403


async def test_the_list_says_which_scenarios_and_whether_the_responder_is_on(
    w: aus.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (await _call(_admin(), "GET", "/api/admin/decisions")).json()

    assert body["scenarios"] == {"small": 3, "medium": 6, "large": 12}
    assert body["event_scenarios"] == {
        "price-drop": "product.price_dropped",
        "back-in-stock": "inventory.back_in_stock",
    }
    assert body["responder_enabled"] is False
    monkeypatch.setattr("shared.config.settings.AUTONOMY_RESPONDER_ENABLED", True)
    assert (await _call(_admin(), "GET", "/api/admin/decisions")).json()["responder_enabled"] is True
