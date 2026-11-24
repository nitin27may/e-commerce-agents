"""#299 -- the simulator scenarios ``order-risk`` and ``item-damaged``, through the worker, and the admin read model."""

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
DECISIONS = "/api/admin/decisions"
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
    world = await aus.world(clean_db)
    await aus.age_account(world, aus.CUSTOMER, 100)
    return world


def _admin():
    return _as(aus.ADMIN, "admin")


async def _tick(w: aus.World):
    return await run_once(w.pool, HANDLERS, "w1", **TICK)


async def _delivered_line(w: aus.World, price: str = "30.00", email: str = aus.CUSTOMER) -> uuid.UUID:
    order = await aus.order(w, email, status="delivered", age_days=2)
    return await aus.line(w, order, await aus.product(w, "Kettle", price=price), price=price)


def test_the_worker_serves_both_new_topics() -> None:
    assert engine.TOPIC_RISK_FLAGGED in HANDLERS and engine.TOPIC_ITEM_DAMAGED in HANDLERS


# ── order-risk ───────────────────────────────────────────────────────────────


async def test_the_order_risk_scenario_picks_the_largest_eligible_order_and_the_worker_holds_a_high_risk_one(
    w: aus.World,
) -> None:
    await aus.age_account(w, aus.CUSTOMER, 1)
    await aus.risk_order(w, total="20.00", hours_ago=5)
    await aus.risk_order(w, total="20.00", hours_ago=2)
    big = await aus.risk_order(w, total="600.00")  # 20 + 25 (new account) + 25 (three orders a day) = 70

    sent = (await _call(_admin(), "POST", SIMULATE, {"scenario": "order-risk"})).json()
    await _tick(w)

    assert sent["enqueued"] is True and sent["event_type"] == engine.TOPIC_RISK_FLAGGED
    assert (sent["order_id"], sent["order_item_id"], sent["quantity"]) == (str(big), None, None)
    row = next(r for r in await aus.decisions(w.pool) if r["order_id"] == big)
    assert (row["action"], row["tier"], row["reversible"]) == ("order_risk_review", "needs_approval", True)
    assert (await w.pool.fetchval("SELECT on_hold FROM orders WHERE id = $1", big)) is True


async def test_the_order_risk_scenario_does_not_repeat_an_order_it_already_reviewed(w: aus.World) -> None:
    await aus.risk_order(w, total="800.00")
    await _call(_admin(), "POST", SIMULATE, {"scenario": "order-risk"})
    await _tick(w)

    again = await _call(_admin(), "POST", SIMULATE, {"scenario": "order-risk"})

    assert again.status_code == 409


async def test_the_order_risk_scenario_skips_shipped_held_staff_and_tour_orders(w: aus.World) -> None:
    from shared.demo_tour import TOUR_EMAIL

    w.users[TOUR_EMAIL] = await aus.user(w.pool, TOUR_EMAIL)
    await aus.risk_order(w, status="shipped")
    await aus.risk_order(w, aus.SUPPORT)
    await aus.risk_order(w, TOUR_EMAIL)
    held = await aus.risk_order(w)
    await w.pool.execute("UPDATE orders SET on_hold = TRUE WHERE id = $1", held)

    assert (await _call(_admin(), "POST", SIMULATE, {"scenario": "order-risk"})).status_code == 409


async def test_an_explicit_risk_event_replays_to_nothing(w: aus.World) -> None:
    order = await aus.risk_order(w, total="10.00")
    body = {"event_type": "order.risk_flagged", "order_id": str(order), "event_id": "fixed"}

    first = await _call(_admin(), "POST", SIMULATE, body)
    second = await _call(_admin(), "POST", SIMULATE, body)
    await _tick(w)

    assert [first.json()["enqueued"], second.json()["enqueued"]] == [True, False]
    assert len(await aus.decisions(w.pool)) == 1


# ── item-damaged ─────────────────────────────────────────────────────────────


async def test_the_item_damaged_scenario_picks_a_delivered_line_and_the_worker_credits_it(w: aus.World) -> None:
    line = await _delivered_line(w, "30.00")

    sent = (await _call(_admin(), "POST", SIMULATE, {"scenario": "item-damaged"})).json()
    await _tick(w)

    assert sent["event_type"] == engine.TOPIC_ITEM_DAMAGED and (sent["order_item_id"], sent["quantity"]) == (
        str(line),
        1,
    )
    (row,) = await aus.decisions(w.pool)
    assert (row["action"], row["tier"], row["amount"], row["order_item_id"]) == (
        "damage_goodwill",
        "auto",
        Decimal("6.00"),
        line,
    )
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == Decimal("6.00")


async def test_the_item_damaged_scenario_does_not_repeat_a_line_it_already_used(w: aus.World) -> None:
    await _delivered_line(w)
    await _call(_admin(), "POST", SIMULATE, {"scenario": "item-damaged"})
    await _tick(w)

    assert (await _call(_admin(), "POST", SIMULATE, {"scenario": "item-damaged"})).status_code == 409


async def test_an_explicit_damage_report_names_a_line_and_a_quantity(w: aus.World) -> None:
    order = await aus.order(w, aus.CUSTOMER, status="delivered", age_days=1)
    line = await aus.line(w, order, await aus.product(w, "Kettle", price="20.00"), price="20.00", qty=3)

    sent = (
        await _call(
            _admin(),
            "POST",
            SIMULATE,
            {"event_type": "order.item_damaged", "order_item_id": str(line), "quantity": 2},
        )
    ).json()
    await _tick(w)

    assert sent["order_id"] == str(order) and sent["quantity"] == 2
    (row,) = await aus.decisions(w.pool)
    assert row["amount"] == Decimal("8.00")


@pytest.mark.parametrize("who", ["admin", "tour", "staff"])
async def test_an_explicit_event_is_only_for_a_customers_records_never_the_admins_own(w: aus.World, who: str) -> None:
    from shared.demo_tour import TOUR_EMAIL

    email = {"admin": aus.ADMIN, "tour": TOUR_EMAIL, "staff": aus.SUPPORT}[who]
    if email not in w.users:
        w.users[email] = await aus.user(w.pool, email)
    line = await _delivered_line(w, email=email)
    order = await w.pool.fetchval("SELECT order_id FROM order_items WHERE id = $1", line)

    damaged = await _call(_admin(), "POST", SIMULATE, {"event_type": "order.item_damaged", "order_item_id": str(line)})
    risk = await _call(_admin(), "POST", SIMULATE, {"event_type": "order.risk_flagged", "order_id": str(order)})

    assert damaged.status_code == 422 and risk.status_code == 422
    assert await w.pool.fetchval("SELECT count(*) FROM outbox") == 0


@pytest.mark.parametrize(
    "body",
    [
        {"event_type": "order.item_damaged"},  # no line
        {"event_type": "order.item_damaged", "order_item_id": "nope"},
        {"event_type": "order.item_damaged", "order_item_id": str(uuid.uuid4()), "order_id": str(uuid.uuid4())},
        {"event_type": "order.item_damaged", "order_item_id": str(uuid.uuid4()), "quantity": 0},
        {"event_type": "order.risk_flagged"},  # no order
        {"event_type": "order.risk_flagged", "order_id": "nope"},
        {"event_type": "order.risk_flagged", "order_id": str(uuid.uuid4()), "quantity": 2},
        {"event_type": "order.risk_flagged", "order_id": str(uuid.uuid4()), "order_item_id": str(uuid.uuid4())},
        {"scenario": "order-risk", "order_id": str(uuid.uuid4())},
        {"scenario": "item-damaged", "order_item_id": str(uuid.uuid4())},
        {"scenario": "order-risk", "event_type": "order.item_damaged"},
        {"scenario": "order-risk", "delay_days": 3},
        {"scenario": "item-damaged", "product_id": str(uuid.uuid4())},
        {"scenario": "small", "order_item_id": str(uuid.uuid4())},
        {"scenario": "price-drop", "quantity": 2},
        {"order_item_id": str(uuid.uuid4())},
    ],
)
async def test_a_malformed_order_event_request_is_a_422(w: aus.World, body: dict[str, Any]) -> None:
    resp = await _call(_admin(), "POST", SIMULATE, body)

    assert resp.status_code == 422, resp.text
    assert await w.pool.fetchval("SELECT count(*) FROM outbox") == 0


async def test_an_explicit_event_for_a_missing_record_is_a_404(w: aus.World) -> None:
    damaged = await _call(
        _admin(), "POST", SIMULATE, {"event_type": "order.item_damaged", "order_item_id": str(uuid.uuid4())}
    )
    risk = await _call(_admin(), "POST", SIMULATE, {"event_type": "order.risk_flagged", "order_id": str(uuid.uuid4())})

    assert damaged.status_code == 404 and risk.status_code == 404


@pytest.mark.parametrize("scenario", ["order-risk", "item-damaged"])
async def test_the_new_scenarios_are_404_when_the_simulator_is_off_and_admin_only(
    w: aus.World, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    assert (await _call(_as(aus.CUSTOMER, "customer"), "POST", SIMULATE, {"scenario": scenario})).status_code == 403
    assert (await _call(_as(aus.SUPPORT, "support"), "POST", SIMULATE, {"scenario": scenario})).status_code == 403
    monkeypatch.setattr("shared.config.settings.AUTONOMY_SIMULATOR_ENABLED", False)

    assert (await _call(_admin(), "POST", SIMULATE, {"scenario": scenario})).status_code == 404


async def test_with_the_engine_off_a_new_event_row_is_consumed_and_does_nothing(
    w: aus.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    await aus.risk_order(w, total="800.00")
    await _delivered_line(w, email=aus.OTHER)
    await _call(_admin(), "POST", SIMULATE, {"scenario": "order-risk"})
    await _call(_admin(), "POST", SIMULATE, {"scenario": "item-damaged"})
    monkeypatch.setattr("shared.config.settings.AUTONOMY_ENABLED", False)

    await _tick(w)

    assert await w.pool.fetchval("SELECT count(*) FROM agent_decisions") == 0
    assert await w.pool.fetchval("SELECT count(*) FROM outbox WHERE processed_at IS NULL") == 0
    assert await w.pool.fetchval("SELECT count(*) FROM store_credit_ledger") == 0


# ── the admin read model ─────────────────────────────────────────────────────


async def test_the_list_carries_the_new_scenarios_and_the_months_budget_use(w: aus.World) -> None:
    body = (await _call(_admin(), "GET", DECISIONS)).json()

    assert body["event_scenarios"]["order-risk"] == "order.risk_flagged"
    assert body["event_scenarios"]["item-damaged"] == "order.item_damaged"
    budgets = {b["budget_key"]: b for b in body["budgets"]}
    assert set(budgets) == {"goodwill_credit", "price_adjustment", "damage_goodwill"}
    assert budgets["damage_goodwill"]["month_max_count"] == 150 and budgets["damage_goodwill"]["month_used_count"] == 0


async def test_an_admin_releases_a_hold_through_the_reverse_route(w: aus.World) -> None:
    await aus.age_account(w, aus.CUSTOMER, 1)
    await aus.risk_order(w, total="20.00", hours_ago=5)
    await aus.risk_order(w, total="20.00", hours_ago=2)
    big = await aus.risk_order(w, total="600.00")
    await _call(_admin(), "POST", SIMULATE, {"scenario": "order-risk"})
    await _tick(w)
    listed = (await _call(_admin(), "GET", DECISIONS)).json()["decisions"]
    held = next(d for d in listed if d["order_id"] == str(big))
    assert held["hold_active"] is True and held["can_reverse"] is True

    resp = await _call(_admin(), "POST", f"{DECISIONS}/{held['id']}/reverse", {"note": "verified by phone"})

    assert resp.status_code == 200, resp.text
    assert (await w.pool.fetchval("SELECT on_hold FROM orders WHERE id = $1", big)) is False
    after = (await _call(_admin(), "GET", f"{DECISIONS}/{held['id']}")).json()
    assert after["hold_active"] is False and after["can_reverse"] is False and after["reversed_by"] == aus.ADMIN
