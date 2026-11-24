"""#299 -- ``product.price_dropped``: a price-adjustment credit per recent order line, against real Postgres.

Same engine, same guards as the shipment-delay credit: sized from the order line (never the event), tiered by rule,
drawn inside a budget under one lock, one adjustment per line ever, a staff beneficiary and the demo tour user are
never acted on alone.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import Any

import asyncpg
import pytest

from orchestrator import approvals_service as svc
from shared.autonomy import engine
from tests import autonomy_support as aus

D = Decimal
TOPIC = engine.TOPIC_PRICE_DROPPED
STAFF = svc.Actor(aus.SUPPORT, "support")


@pytest.fixture
async def w(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> aus.World:
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    return await aus.world(clean_db)


async def _handle(w: aus.World, product: uuid.UUID, new_price: Any, event_id: str = "drop-1") -> dict[str, Any]:
    return await engine.handle_event(w.pool, TOPIC, aus.drop(product, new_price, event_id), outbox_id=1)


async def _bought(
    w: aus.World,
    email: str = aus.CUSTOMER,
    *,
    price: str = "100.00",
    qty: int = 1,
    age_days: int = 1,
    name: str = "Widget",
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """(product, order, line) for a delivered order of one product bought at ``price``."""
    product = await aus.product(w, name, price=price)
    order = await aus.order(w, email, status="delivered", age_days=age_days)
    return product, order, await aus.line(w, order, product, price=price, qty=qty)


async def _rows(w: aus.World) -> list[asyncpg.Record]:
    return await aus.decisions(w.pool)


# ── sizing, tiers, and what the customer is told ─────────────────────────────


async def test_a_small_drop_pays_the_difference_alone_and_tells_the_customer(w: aus.World) -> None:
    product, order, line = await _bought(w)

    result = await _handle(w, product, "95.00")

    assert result["status"] == "processed" and result["targets"] == 1 and result["by_tier"] == {"auto": 1}
    (row,) = await _rows(w)
    assert (row["action"], row["tier"], row["outcome"], row["amount"]) == (
        "price_adjustment",
        "auto",
        "executed",
        D("5.00"),
    )
    assert row["order_item_id"] == line and row["order_id"] == order and row["budget_key"] == "price_adjustment"
    assert row["reversible"] is True and row["event_type"] == TOPIC
    assert row["event_key"] == f"{TOPIC}:drop-1:{line}"
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("5.00")
    note = await w.pool.fetchrow("SELECT * FROM notifications WHERE user_email = $1", aus.CUSTOMER)
    assert note["kind"] == "autonomy.price_adjustment" and note["decision_id"] == row["id"]
    assert "$5.00" in note["body"] and "Widget" in note["body"] and "$95.00" in note["body"]
    assert aus.loads(row["input"])["event"]["line"]["paid_unit_price"] == "100.00"


async def test_the_credit_is_sized_from_the_line_not_from_the_event(w: aus.World) -> None:
    """Paid 40.00 x 2; new price 35.50 => 9.00. A 'credit' field in the payload is ignored."""
    product, _, _ = await _bought(w, price="40.00", qty=2)

    await engine.handle_event(w.pool, TOPIC, aus.drop(product, "35.50", "d1", amount="999.00", credit="999.00"))

    (row,) = await _rows(w)
    assert row["amount"] == D("9.00") and row["tier"] == "auto"


async def test_a_mid_size_adjustment_acts_alone_and_staff_are_told(w: aus.World) -> None:
    product, _, _ = await _bought(w, price="100.00", qty=2)

    result = await _handle(w, product, "92.50")  # 7.50 x 2 = 15.00

    assert result["by_tier"] == {"auto_notify": 1}
    kinds = sorted((n["kind"], n["role"]) for n in await w.pool.fetch("SELECT kind, role FROM notifications"))
    assert kinds == [("autonomy.action_taken", "admin"), ("autonomy.price_adjustment", None)]


async def test_a_large_adjustment_waits_for_approval_and_nothing_is_paid(w: aus.World) -> None:
    product, _, line = await _bought(w, price="100.00", qty=3)

    result = await _handle(w, product, "90.00")  # 10 x 3 = 30.00, over the 20.00 per-action cap

    assert result["by_tier"] == {"needs_approval": 1}
    (row,) = await _rows(w)
    assert (row["tier"], row["outcome"], row["amount"], row["order_item_id"]) == (
        "needs_approval",
        "awaiting_approval",
        D("30.00"),
        line,
    )
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == 0
    approval = await w.pool.fetchrow("SELECT * FROM approvals WHERE id = $1", row["approval_id"])
    assert (approval["kind"], approval["source"], approval["status"]) == (
        "autonomous_action",
        "event",
        "pending_review",
    )


async def test_approving_pays_once_to_the_line_and_the_line_cannot_be_adjusted_again(w: aus.World) -> None:
    product, _, line = await _bought(w, price="100.00", qty=3)
    result = (await _handle(w, product, "90.00")) and (await _rows(w))[0]

    out = await svc.decide(str(result["approval_id"]), STAFF, decision="approve")

    assert out["result"]["success"] is True and out["result"]["amount"] == "30.00"
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("30.00")
    (execution,) = await aus.decisions(w.pool, "approved_execution")
    assert execution["order_item_id"] == line and execution["action"] == "price_adjustment"
    note = await w.pool.fetchrow("SELECT * FROM notifications WHERE decision_id = $1", execution["id"])
    assert note["kind"] == "autonomy.price_adjustment" and "$30.00" in note["body"]
    # a second drop on the same line: no_action, not another approval
    again = await _handle(w, product, "80.00", "drop-2")
    assert again["by_tier"] == {"no_action": 1}
    last = (await _rows(w))[-1]
    assert aus.loads(last["reasons"]) == ["line_already_adjusted"]
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("30.00")


# ── one adjustment per order line, ever ──────────────────────────────────────


async def test_a_replayed_event_acts_once(w: aus.World) -> None:
    product, _, _ = await _bought(w)

    first = await _handle(w, product, "95.00")
    second = await _handle(w, product, "95.00")  # same event id

    assert first["new"] == 1 and second["new"] == 0
    assert await w.pool.fetchval("SELECT count(*) FROM agent_decisions") == 1
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("5.00")


async def test_a_second_event_for_a_paid_line_is_no_action_and_pays_nothing(w: aus.World) -> None:
    product, _, _ = await _bought(w)
    await _handle(w, product, "95.00", "drop-1")

    await _handle(w, product, "90.00", "drop-2")

    rows = await _rows(w)
    assert [r["tier"] for r in rows] == ["auto", "no_action"]
    assert aus.loads(rows[1]["reasons"]) == ["line_already_adjusted"]
    assert rows[1]["user_id"] == w.users[aus.CUSTOMER] and rows[1]["action"] == "none"
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("5.00")


async def test_a_reversal_does_not_free_the_line(w: aus.World) -> None:
    product, _, _ = await _bought(w)
    result = await _handle(w, product, "95.00")
    (row,) = await _rows(w)

    reversed_ = await engine.reverse(w.pool, str(row["id"]), actor_email=aus.ADMIN)
    await _handle(w, product, "90.00", "drop-2")

    assert reversed_["status"] == "reversed" and result["by_tier"] == {"auto": 1}
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == 0
    assert [r["tier"] for r in await _rows(w)] == ["auto", "no_action"]
    kinds = [n["kind"] for n in await w.pool.fetch("SELECT kind FROM notifications ORDER BY kind")]
    assert "autonomy.price_adjustment_reversed" in kinds
    (reversal,) = await aus.decisions(w.pool, "reversal")
    assert reversal["order_item_id"] == row["order_item_id"]


async def test_the_database_itself_refuses_a_second_paid_adjustment_on_a_line(w: aus.World) -> None:
    product, _, line = await _bought(w)
    await _handle(w, product, "95.00")
    (row,) = await _rows(w)

    async with w.pool.acquire() as conn:
        with pytest.raises(asyncpg.UniqueViolationError):
            await engine._insert_row(  # noqa: SLF001 - the backstop is the point
                conn,
                id=uuid.uuid4(),
                entry_type="approved_execution",
                action="price_adjustment",
                user_id=row["user_id"],
                order_id=row["order_id"],
                order_item_id=line,
                amount=D("1.00"),
                outcome="executed",
            )


async def test_two_events_racing_for_one_line_pay_it_once(w: aus.World) -> None:
    product, _, _ = await _bought(w)

    results = await asyncio.gather(_handle(w, product, "95.00", "a"), _handle(w, product, "90.00", "b"))

    assert sorted(r["by_tier"].popitem()[0] for r in results) == ["auto", "no_action"]
    assert await w.pool.fetchval("SELECT count(*) FROM store_credit_ledger") == 1


# ── who is a target ──────────────────────────────────────────────────────────


async def test_one_event_pays_every_customer_who_bought_it_recently_each_once(w: aus.World) -> None:
    product = await aus.product(w, price="100.00")
    for email in (aus.CUSTOMER, aus.OTHER, aus.THIRD):
        await aus.line(w, await aus.order(w, email, status="delivered", age_days=2), product)

    result = await _handle(w, product, "98.00")

    assert result["targets"] == 3 and result["by_tier"] == {"auto": 3}
    for email in (aus.CUSTOMER, aus.OTHER, aus.THIRD):
        assert await aus.ledger_balance(w.pool, w.users[email]) == D("2.00")


async def test_old_cancelled_and_not_lower_purchases_are_not_targets(w: aus.World) -> None:
    product = await aus.product(w, price="100.00")
    await aus.line(w, await aus.order(w, aus.CUSTOMER, status="delivered", age_days=31), product)  # past the lookback
    await aus.line(w, await aus.order(w, aus.OTHER, status="cancelled", age_days=1), product)  # not eligible
    await aus.line(
        w, await aus.order(w, aus.THIRD, status="delivered", age_days=1), product, price="90.00"
    )  # paid less

    result = await _handle(w, product, "95.00")

    assert result == {"status": "no_targets", "targets": 0, "new": 0, "by_tier": {}}
    assert await w.pool.fetchval("SELECT count(*) FROM agent_decisions") == 0


async def test_a_purchase_older_than_a_fortnight_asks_even_for_a_tiny_amount(w: aus.World) -> None:
    product, _, _ = await _bought(w, age_days=20)

    result = await _handle(w, product, "99.00")

    assert result["by_tier"] == {"needs_approval": 1}
    (row,) = await _rows(w)
    assert (
        aus.loads(row["input"])["rule"] == "default-approval"
        and await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == 0
    )


async def test_a_drop_of_more_than_half_asks(w: aus.World) -> None:
    product, _, _ = await _bought(w, price="20.00")

    result = await _handle(w, product, "9.00")  # 55 percent: likelier a pricing mistake than a sale

    assert result["by_tier"] == {"needs_approval": 1}


async def test_a_staff_buyer_is_never_paid_alone(w: aus.World) -> None:
    product, _, _ = await _bought(w, aus.SUPPORT)

    await _handle(w, product, "95.00")

    (row,) = await _rows(w)
    assert row["tier"] == "needs_approval" and "beneficiary_is_staff" in aus.loads(row["reasons"])
    assert await aus.ledger_balance(w.pool, w.users[aus.SUPPORT]) == 0
    with pytest.raises(svc.ApprovalHTTPError):  # and the buyer cannot decide it either
        await svc.decide(str(row["approval_id"]), svc.Actor(aus.SUPPORT, "support"), decision="approve")


async def test_the_demo_tour_user_is_never_a_target_and_no_row_references_it(w: aus.World) -> None:
    from shared.demo_tour import TOUR_EMAIL

    w.users[TOUR_EMAIL] = await aus.user(w.pool, TOUR_EMAIL)
    product, _, _ = await _bought(w, TOUR_EMAIL)

    result = await _handle(w, product, "95.00")

    assert result["status"] == "no_targets"
    assert await w.pool.fetchval("SELECT count(*) FROM agent_decisions") == 0
    assert await aus.ledger_balance(w.pool, w.users[TOUR_EMAIL]) == 0


# ── budgets and the order cap ────────────────────────────────────────────────


async def test_a_customers_fourth_adjustment_of_the_day_is_over_the_cap_and_asks(w: aus.World) -> None:
    order = await aus.order(w, aus.CUSTOMER, status="delivered", age_days=1)
    products = [await aus.product(w, f"P{i}", price="50.00") for i in range(4)]
    for p in products:
        await aus.line(w, order, p, price="50.00")

    for i, p in enumerate(products):
        await _handle(w, p, "49.00", f"d{i}")

    rows = await _rows(w)
    assert [r["tier"] for r in rows] == ["auto", "auto", "auto", "needs_approval"]
    assert "budget:customer_daily_count" in aus.loads(rows[3]["reasons"])
    assert aus.loads(rows[3]["budget"])["tripped"] == ["customer_daily_count"]
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("3.00")


async def test_the_orders_adjusted_line_cap_is_a_no_action_not_an_approval(
    w: aus.World, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yaml

    doc = yaml.safe_load(open(aus.POLICY_PATH, encoding="utf-8").read())
    doc["budgets"]["price_adjustment"]["per_order_max_count"] = 1
    path = tmp_path / "autonomy.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    monkeypatch.setattr("shared.config.settings.AUTONOMY_POLICY_PATH", str(path))
    order = await aus.order(w, aus.CUSTOMER, status="delivered", age_days=1)
    first, second = await aus.product(w, "A", price="50.00"), await aus.product(w, "B", price="50.00")
    await aus.line(w, order, first, price="50.00")
    await aus.line(w, order, second, price="50.00")

    await _handle(w, first, "49.00", "d1")
    await _handle(w, second, "49.00", "d2")

    rows = await _rows(w)
    assert [r["tier"] for r in rows] == ["auto", "no_action"]
    assert aus.loads(rows[1]["reasons"]) == ["order_adjustment_limit"]
    assert await w.pool.fetchval("SELECT count(*) FROM approvals") == 0


# ── a bad event ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({"new_price": "5.00"}, "invalid_event_product"),
        ({"product_id": "not-a-uuid", "new_price": "5.00"}, "invalid_event_product"),
        ({"product_id": str(uuid.uuid4()), "new_price": "5.00"}, "product_not_found"),
        ({"product_id": "P", "new_price": "-1"}, "invalid_event_price"),
        ({"product_id": "P", "new_price": "0"}, "invalid_event_price"),
        ({"product_id": "P", "new_price": "1.005"}, "invalid_event_price"),
        ({"product_id": "P", "new_price": "NaN"}, "invalid_event_price"),
        ({"product_id": "P", "new_price": True}, "invalid_event_price"),
        ({"product_id": "P"}, "invalid_event_price"),
    ],
)
async def test_a_bad_event_is_one_no_action_row_with_no_ids(w: aus.World, payload: dict[str, Any], reason: str) -> None:
    product, _, _ = await _bought(w)
    payload = {**payload, "event_id": "bad-1"}
    if payload.get("product_id") == "P":
        payload["product_id"] = str(product)

    result = await engine.handle_event(w.pool, TOPIC, payload, outbox_id=1)
    replay = await engine.handle_event(w.pool, TOPIC, payload, outbox_id=1)

    assert result["status"] == "no_action" and result["reasons"] == [reason]
    assert replay["decision_id"] == result["decision_id"]
    (row,) = await _rows(w)
    assert (
        row["user_id"] is None and row["order_id"] is None and row["order_item_id"] is None and row["action"] == "none"
    )
    assert await w.pool.fetchval("SELECT count(*) FROM store_credit_ledger") == 0


async def test_the_admin_list_shows_the_new_event_and_its_line(w: aus.World) -> None:
    product, _, line = await _bought(w)
    await _handle(w, product, "95.00")

    (shown,) = await engine.list_decisions(w.pool)

    assert shown["event_type"] == TOPIC and shown["order_item_id"] == str(line)
    assert shown["can_reverse"] is True and shown["draft"] is None
    assert shown["input"]["event"]["line"]["product_name"] == "Widget"
