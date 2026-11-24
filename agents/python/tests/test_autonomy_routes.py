"""V-2 (#94) -- the event simulator, the worker handler and the admin decisions API, off by default.

The whole feature is off unless ``AUTONOMY_ENABLED`` is set, the simulator needs its own switch on top, and with
either off the worker consumes a row and does nothing. Events go in as outbox rows and the worker does the rest.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

import asyncpg
import pytest

from shared.autonomy import engine
from shared.config import Settings
from tests import autonomy_support as aus
from tests.test_proposal_execute import _as, _call
from worker.main import HANDLERS
from worker.runner import run_once

D = Decimal
TICK: dict[str, Any] = {
    "batch_size": 20,
    "lease_seconds": 60,
    "max_attempts": 3,
    "backoff_base": 0.0,
    "backoff_cap": 0.0,
}
SIMULATE = "/api/admin/events/simulate"


@pytest.fixture
async def w(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> aus.World:
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    return await aus.world(clean_db)


@pytest.fixture
def on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shared.config.settings.AUTONOMY_ENABLED", True)
    monkeypatch.setattr("shared.config.settings.AUTONOMY_SIMULATOR_ENABLED", True)


def _admin():
    return _as(aus.ADMIN, "admin")


async def _tick(w: aus.World):
    return await run_once(w.pool, HANDLERS, "w1", **TICK)


async def _outbox(w: aus.World) -> list[asyncpg.Record]:
    return await w.pool.fetch("SELECT * FROM outbox WHERE topic = $1 ORDER BY id", aus.TOPIC)


# ── off by default ───────────────────────────────────────────────────────────


def test_the_feature_is_off_by_default() -> None:
    fresh = Settings(_env_file=None)

    assert fresh.AUTONOMY_ENABLED is False and fresh.AUTONOMY_SIMULATOR_ENABLED is False
    assert fresh.AUTONOMY_POLICY_PATH == ""
    assert fresh.AUTONOMY_RESPONDER_ENABLED is False


async def test_with_the_feature_off_the_worker_consumes_an_event_and_does_nothing(w: aus.World) -> None:
    order = await aus.order(w)
    await w.pool.execute(
        "INSERT INTO outbox (topic, payload, available_at) VALUES ($1, $2::jsonb, NOW() - interval '1 second')",
        aus.TOPIC,
        json.dumps(aus.payload(order, 12, "evt-1")),
    )

    result = await _tick(w)

    assert (result.claimed, result.done) == (1, 1)
    assert await w.pool.fetchval("SELECT count(*) FROM agent_decisions") == 0
    assert await w.pool.fetchval("SELECT count(*) FROM store_credit_ledger") == 0
    assert await w.pool.fetchval("SELECT count(*) FROM approvals") == 0


@pytest.mark.parametrize("enabled", [(False, True), (True, False), (False, False)])
async def test_the_simulator_is_a_404_unless_both_switches_are_on(
    w: aus.World, monkeypatch: pytest.MonkeyPatch, enabled: tuple[bool, bool]
) -> None:
    monkeypatch.setattr("shared.config.settings.AUTONOMY_ENABLED", enabled[0])
    monkeypatch.setattr("shared.config.settings.AUTONOMY_SIMULATOR_ENABLED", enabled[1])
    await aus.order(w)

    resp = await _call(_admin(), "POST", SIMULATE, {"scenario": "small"})

    assert resp.status_code == 404
    assert await _outbox(w) == []


@pytest.mark.parametrize(
    ("email", "role"), [(aus.CUSTOMER, "customer"), (aus.SUPPORT, "support"), ("s@example.com", "seller")]
)
async def test_with_the_simulator_off_a_non_admin_gets_the_same_404_as_everyone(
    w: aus.World, monkeypatch: pytest.MonkeyPatch, email: str, role: str
) -> None:
    """Review fix: the switch is checked before the role, so a 403 cannot reveal that the route exists."""
    monkeypatch.setattr("shared.config.settings.AUTONOMY_ENABLED", True)
    monkeypatch.setattr("shared.config.settings.AUTONOMY_SIMULATOR_ENABLED", False)

    off = await _call(_as(email, role), "POST", SIMULATE, {"scenario": "small"})
    admin_off = await _call(_admin(), "POST", SIMULATE, {"scenario": "small"})
    monkeypatch.setattr("shared.config.settings.AUTONOMY_SIMULATOR_ENABLED", True)
    on = await _call(_as(email, role), "POST", SIMULATE, {"scenario": "small"})

    assert (off.status_code, admin_off.status_code, on.status_code) == (404, 404, 403)
    assert off.json() == admin_off.json()


# ── who may call ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("email", "role"), [(aus.CUSTOMER, "customer"), (aus.SUPPORT, "support"), ("s@example.com", "seller")]
)
async def test_only_an_admin_may_use_the_admin_routes(w: aus.World, on: None, email: str, role: str) -> None:
    order = await aus.order(w)
    result = await engine.handle_event(w.pool, aus.TOPIC, aus.payload(order, 3, "evt-1"), outbox_id=1)

    for method, url, body in (
        ("POST", SIMULATE, {"scenario": "small"}),
        ("GET", "/api/admin/decisions", None),
        ("GET", f"/api/admin/decisions/{result['decision_id']}", None),
        ("POST", f"/api/admin/decisions/{result['decision_id']}/reverse", {}),
    ):
        resp = await _call(_as(email, role), method, url, body)
        assert resp.status_code == 403, (url, resp.text)
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("5.00")  # nothing reversed


# ── the simulator ────────────────────────────────────────────────────────────


async def test_the_three_scenarios_land_in_three_tiers_end_to_end(w: aus.World, on: None) -> None:
    for email in (aus.CUSTOMER, aus.OTHER, aus.THIRD):
        await aus.order(w, email)

    sent = [
        (await _call(_admin(), "POST", SIMULATE, {"scenario": name})).json() for name in ("small", "medium", "large")
    ]
    await _tick(w)

    assert [s["enqueued"] for s in sent] == [True, True, True]
    assert len({s["order_id"] for s in sent}) == 3, "each scenario picked a different customer's order"
    rows = {r["tier"]: r for r in await aus.decisions(w.pool)}
    assert set(rows) == {"auto", "auto_notify", "needs_approval"}
    assert [rows[t]["amount"] for t in ("auto", "auto_notify", "needs_approval")] == [D("5.00"), D("15.00"), D("40.00")]
    assert await w.pool.fetchval("SELECT count(*) FROM store_credit_ledger") == 2
    assert await w.pool.fetchval("SELECT count(*) FROM approvals WHERE status = 'pending_review'") == 1


async def test_a_scenario_is_deterministic(w: aus.World, on: None) -> None:
    first = await aus.order(w, aus.CUSTOMER)
    await aus.order(w, aus.OTHER)

    a = await _call(_admin(), "POST", SIMULATE, {"scenario": "small"})
    b = await _call(
        _admin(), "POST", SIMULATE, {"scenario": "small"}
    )  # nothing has run yet: the same customer is skipped

    assert a.json()["order_id"] == str(first) and a.json()["event_id"] == f"sim-small-{first}"
    assert b.json()["order_id"] != a.json()["order_id"]


async def test_replaying_the_same_event_enqueues_nothing_and_acts_once(w: aus.World, on: None) -> None:
    order = await aus.order(w)
    body = {"order_id": str(order), "delay_days": 3, "event_id": "evt-fixed"}

    first = await _call(_admin(), "POST", SIMULATE, body)
    second = await _call(_admin(), "POST", SIMULATE, body)
    await _tick(w)
    third = await _call(_admin(), "POST", SIMULATE, body)  # even after it ran
    await _tick(w)

    assert [first.json()["enqueued"], second.json()["enqueued"], third.json()["enqueued"]] == [True, False, False]
    assert await w.pool.fetchval("SELECT count(*) FROM agent_decisions") == 1
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("5.00")


async def test_a_redelivered_outbox_row_for_the_same_event_acts_once(w: aus.World, on: None) -> None:
    """Different outbox rows (no shared dedupe key) carrying one event: the engine's own guard holds."""
    order = await aus.order(w)
    for _ in range(3):
        await w.pool.execute(
            "INSERT INTO outbox (topic, payload, available_at) VALUES ($1, $2::jsonb, NOW() - interval '1 second')",
            aus.TOPIC,
            json.dumps(aus.payload(order, 3, "evt-1")),
        )

    result = await _tick(w)

    assert (result.claimed, result.done, result.failed) == (3, 3, 0)
    assert await w.pool.fetchval("SELECT count(*) FROM agent_decisions") == 1
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("5.00")
    assert await w.pool.fetchval("SELECT count(*) FROM notifications WHERE user_email = $1", aus.CUSTOMER) == 1


async def test_the_decision_records_the_outbox_row_that_carried_the_event(w: aus.World, on: None) -> None:
    order = await aus.order(w)
    sent = (await _call(_admin(), "POST", SIMULATE, {"order_id": str(order), "delay_days": 3})).json()
    await _tick(w)

    (decision,) = await aus.decisions(w.pool)

    assert decision["outbox_id"] == sent["outbox_id"] and decision["event_key"].endswith(sent["event_id"])
    assert sent["event_id"] == f"sim-{order}-3"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"scenario": "huge"},
        {"scenario": "small", "delay_days": 3},
        {"order_id": str(uuid.uuid4())},
        {"delay_days": 3},
        {"order_id": "not-a-uuid", "delay_days": 3},
        {"order_id": str(uuid.uuid4()), "delay_days": 366},
        {"order_id": str(uuid.uuid4()), "delay_days": -1},
        {"order_id": str(uuid.uuid4()), "delay_days": 3, "event_id": "   "},
    ],
)
async def test_a_bad_simulate_request_is_a_422(w: aus.World, on: None, body: dict[str, Any]) -> None:
    resp = await _call(_admin(), "POST", SIMULATE, body)

    assert resp.status_code == 422, resp.text
    assert await _outbox(w) == []


async def test_an_unknown_order_is_a_404_and_a_spent_scenario_a_409(w: aus.World, on: None) -> None:
    missing = await _call(_admin(), "POST", SIMULATE, {"order_id": str(uuid.uuid4()), "delay_days": 3})
    empty = await _call(_admin(), "POST", SIMULATE, {"scenario": "small"})

    assert (missing.status_code, empty.status_code) == (404, 409)


async def test_a_scenario_skips_delivered_orders_and_staff_accounts(w: aus.World, on: None) -> None:
    await aus.order(w, aus.CUSTOMER, status="delivered")
    await aus.order(w, aus.ADMIN)  # an admin's own order is not a customer's
    wanted = await aus.order(w, aus.OTHER, status="out_for_delivery")

    resp = await _call(_admin(), "POST", SIMULATE, {"scenario": "medium"})

    assert resp.json()["order_id"] == str(wanted)


# ── the decisions API ────────────────────────────────────────────────────────


async def test_the_decisions_list_shows_state_budget_use_and_the_reverse_button_flag(w: aus.World, on: None) -> None:
    for email in (aus.CUSTOMER, aus.OTHER, aus.THIRD):
        await aus.order(w, email)
    for name in ("small", "medium", "large"):
        await _call(_admin(), "POST", SIMULATE, {"scenario": name})
    await _tick(w)

    body = (await _call(_admin(), "GET", "/api/admin/decisions")).json()

    assert body["total"] == 3 and body["enabled"] is True and body["simulator_enabled"] is True
    assert body["scenarios"] == {"small": 3, "medium": 6, "large": 12}
    by_tier = {d["tier"]: d for d in body["decisions"]}
    assert by_tier["auto"]["state"] == "executed" and by_tier["auto"]["can_reverse"] is True
    assert by_tier["auto_notify"]["can_reverse"] is True
    assert (
        by_tier["needs_approval"]["state"] == "awaiting_approval" and by_tier["needs_approval"]["can_reverse"] is False
    )
    assert by_tier["auto"]["budget"]["drew"] == {"count": 1, "amount": "5.00"}
    assert by_tier["needs_approval"]["reasons"] == ["Outside what the system may do alone"]
    overview = next(b for b in body["budgets"] if b["budget_key"] == "goodwill_credit")
    assert {b["budget_key"] for b in body["budgets"]} == {"goodwill_credit", "price_adjustment", "damage_goodwill"}
    assert (overview["used_count"], overview["used_amount"]) == (2, "20.00")
    only = (await _call(_admin(), "GET", "/api/admin/decisions?state=awaiting_approval")).json()
    assert [d["tier"] for d in only["decisions"]] == ["needs_approval"]


async def test_reversing_through_the_api_restores_the_state_and_works_even_with_the_feature_off(
    w: aus.World, on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    order = await aus.order(w)
    result = await engine.handle_event(w.pool, aus.TOPIC, aus.payload(order, 3, "evt-1"), outbox_id=1)
    monkeypatch.setattr("shared.config.settings.AUTONOMY_ENABLED", False)  # switched off since: still undoable

    resp = await _call(_admin(), "POST", f"/api/admin/decisions/{result['decision_id']}/reverse", {"note": "oops"})
    again = await _call(_admin(), "POST", f"/api/admin/decisions/{result['decision_id']}/reverse", {})
    shown = await _call(_admin(), "GET", f"/api/admin/decisions/{result['decision_id']}")

    assert resp.status_code == 200 and resp.json()["status"] == "reversed", resp.text
    assert again.status_code == 409
    assert shown.json()["state"] == "reversed" and shown.json()["reversed_by"] == aus.ADMIN
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == 0


async def test_an_unknown_decision_is_a_404(w: aus.World, on: None) -> None:
    assert (await _call(_admin(), "GET", f"/api/admin/decisions/{uuid.uuid4()}")).status_code == 404
    assert (await _call(_admin(), "GET", "/api/admin/decisions/nope")).status_code == 404
    assert (await _call(_admin(), "POST", f"/api/admin/decisions/{uuid.uuid4()}/reverse", {})).status_code == 404
