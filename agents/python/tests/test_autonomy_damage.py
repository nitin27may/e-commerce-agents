"""#299 -- ``order.item_damaged``: a credit or a replacement offer per damaged order line, on real Postgres.

Same budgets, locks and guards as the delay credit and the price adjustment: sized from the order line (never the
event), tiered by rule, drawn inside a budget under one lock, ONE remedy per line ever; a staff beneficiary and the
demo tour user are never acted on alone.
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
TOPIC = engine.TOPIC_ITEM_DAMAGED
ADMIN = svc.Actor(aus.ADMIN, "admin")


@pytest.fixture
async def w(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> aus.World:
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    return await aus.world(clean_db)


async def _handle(w: aus.World, item: Any, event_id: str = "dmg-1", **extra: Any) -> dict[str, Any]:
    return await engine.handle_event(w.pool, TOPIC, aus.damaged(item, event_id, **extra), outbox_id=1)


async def _rows(w: aus.World) -> list[asyncpg.Record]:
    return await aus.decisions(w.pool)


async def _bought(
    w: aus.World,
    email: str = aus.CUSTOMER,
    *,
    price: str = "30.00",
    qty: int = 1,
    age_days: int = 3,
    stock: int = 0,
    status: str = "delivered",
    order: uuid.UUID | None = None,
    name: str = "Kettle",
) -> tuple[uuid.UUID, uuid.UUID]:
    """(order, line) for a delivered order of one product, with ``stock`` units on the shelf."""
    product = await aus.product(w, name, price=price, stock=stock)
    order_id = order or await aus.order(w, email, status=status, age_days=age_days)
    return order_id, await aus.line(w, order_id, product, price=price, qty=qty)


def _event(row: asyncpg.Record) -> dict[str, Any]:
    return aus.loads(row["input"])["event"]


# ── the remedy, its tier and what the customer is told ───────────────────────


async def test_a_small_credit_is_granted_alone_and_the_customer_is_told(w: aus.World) -> None:
    order, line = await _bought(w, price="30.00")  # no stock: a credit of 20 percent

    result = await _handle(w, line)

    assert result["tier"] == "auto" and result["amount"] == "6.00"
    (row,) = await _rows(w)
    assert (row["action"], row["tier"], row["outcome"], row["amount"], row["budget_key"]) == (
        "damage_goodwill",
        "auto",
        "executed",
        D("6.00"),
        "damage_goodwill",
    )
    assert row["order_item_id"] == line and row["order_id"] == order and row["reversible"] is True
    assert _event(row)["resolution"] == "credit" and row["event_key"] == f"{TOPIC}:dmg-1"
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("6.00")
    note = await w.pool.fetchrow("SELECT * FROM notifications WHERE user_email = $1", aus.CUSTOMER)
    assert note["kind"] == "autonomy.damage_credit" and note["decision_id"] == row["id"]
    assert "$6.00" in note["body"] and "Kettle" in note["body"] and "damaged" in note["body"]


async def test_a_mid_size_credit_acts_alone_and_staff_are_told(w: aus.World) -> None:
    _, line = await _bought(w, price="60.00")  # 12.00

    result = await _handle(w, line)

    assert result["tier"] == "auto_notify" and result["amount"] == "12.00"
    kinds = sorted((n["kind"], n["role"]) for n in await w.pool.fetch("SELECT kind, role FROM notifications"))
    assert kinds == [("autonomy.action_taken", "admin"), ("autonomy.damage_credit", None)]


async def test_the_credit_is_capped_at_the_budgets_per_action_maximum(w: aus.World) -> None:
    _, line = await _bought(w, price="200.00")  # 20 percent is 40.00, the event's max_credit is 15.00

    result = await _handle(w, line)

    assert result["tier"] == "auto_notify" and result["amount"] == "15.00"
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("15.00")


async def test_a_cheap_item_in_stock_is_offered_a_free_replacement_that_moves_no_credit(w: aus.World) -> None:
    _, line = await _bought(w, price="12.00", stock=5)

    result = await _handle(w, line)

    assert result["tier"] == "auto_notify" and result["amount"] == "12.00"
    (row,) = await _rows(w)
    assert _event(row)["resolution"] == "replacement_offer"
    assert aus.loads(row["budget"])["drew"] == {
        "count": 1,
        "amount": "12.00",
    }  # costed at its value, drawn on the budget
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == 0
    assert await w.pool.fetchval("SELECT count(*) FROM store_credit_ledger") == 0
    notes = {n["kind"]: n for n in await w.pool.fetch("SELECT * FROM notifications")}
    assert "replacement" in notes["autonomy.damage_replacement"]["body"]
    assert notes["autonomy.action_taken"]["role"] == "admin" and "send it" in notes["autonomy.action_taken"]["body"]


async def test_a_replacement_over_the_per_action_cap_asks_and_nothing_is_offered(w: aus.World) -> None:
    _, line = await _bought(w, price="25.00", stock=5)  # replacement worth 25.00 is over the 15.00 cap

    result = await _handle(w, line)

    assert result["tier"] == "needs_approval"
    (row,) = await _rows(w)
    assert _event(row)["resolution"] == "replacement_offer" and aus.loads(row["reasons"]) == [
        "Outside what the system may do alone"
    ]
    assert await w.pool.fetchval("SELECT count(*) FROM notifications WHERE user_email IS NOT NULL") == 0


async def test_only_the_damaged_quantity_is_valued(w: aus.World) -> None:
    _, line = await _bought(w, price="20.00", qty=3)

    await _handle(w, line, quantity=2)  # 40.00 damaged, out of stock: 20 percent

    (row,) = await _rows(w)
    assert row["amount"] == D("8.00") and _event(row)["line"]["damaged_quantity"] == 2


async def test_the_event_cannot_set_the_amount(w: aus.World) -> None:
    _, line = await _bought(w, price="30.00")

    await _handle(w, line, amount="999.00", credit="999.00", resolution="replacement_offer")

    (row,) = await _rows(w)
    assert row["amount"] == D("6.00") and _event(row)["resolution"] == "credit"


async def test_an_old_report_asks_even_for_a_small_credit(w: aus.World) -> None:
    _, line = await _bought(w, price="30.00", age_days=45)

    result = await _handle(w, line)

    assert result["tier"] == "needs_approval"
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == 0


# ── no_action reasons ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"status": "shipped"}, "order_not_eligible"),
        ({"status": "cancelled"}, "order_not_eligible"),
        ({"age_days": 61}, "damage_report_too_old"),
    ],
)
async def test_an_ineligible_line_is_logged_as_no_action(w: aus.World, kwargs: dict[str, Any], reason: str) -> None:
    _, line = await _bought(w, **kwargs)

    await _handle(w, line)

    (row,) = await _rows(w)
    assert row["tier"] == "no_action" and aus.loads(row["reasons"]) == [reason] and row["action"] == "none"
    assert await w.pool.fetchval("SELECT count(*) FROM store_credit_ledger") == 0


@pytest.mark.parametrize("quantity", [0, -1, True, "2", 1.5, 10_001])
async def test_a_bad_quantity_is_an_invalid_event(w: aus.World, quantity: Any) -> None:
    _, line = await _bought(w)

    await _handle(w, line, quantity=quantity)

    (row,) = await _rows(w)
    assert aus.loads(row["reasons"]) == ["invalid_event_quantity"]


async def test_more_units_than_the_line_has_is_refused(w: aus.World) -> None:
    _, line = await _bought(w, qty=2)

    await _handle(w, line, quantity=3)

    (row,) = await _rows(w)
    assert aus.loads(row["reasons"]) == ["quantity_exceeds_line"]


@pytest.mark.parametrize(("item", "reason"), [(None, "invalid_event_item"), (uuid.uuid4(), "line_not_found")])
async def test_an_event_naming_no_real_line_carries_no_ids(w: aus.World, item: Any, reason: str) -> None:
    await _handle(w, item)

    (row,) = await _rows(w)
    assert aus.loads(row["reasons"]) == [reason] and row["user_id"] is None and row["order_item_id"] is None


async def test_a_remedy_that_rounds_to_nothing_is_no_action(w: aus.World) -> None:
    _, line = await _bought(w, price="0.02")

    await _handle(w, line)

    (row,) = await _rows(w)
    assert aus.loads(row["reasons"]) == ["remedy_below_minimum"]


# ── one remedy per order line, ever ──────────────────────────────────────────


async def test_a_replayed_event_acts_once(w: aus.World) -> None:
    _, line = await _bought(w)

    first = await _handle(w, line)
    second = await _handle(w, line)

    assert second["decision_id"] == first["decision_id"]
    assert await w.pool.fetchval("SELECT count(*) FROM agent_decisions") == 1
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("6.00")


async def test_a_second_report_for_a_remedied_line_is_no_action(w: aus.World) -> None:
    _, line = await _bought(w)
    await _handle(w, line, "dmg-1")

    await _handle(w, line, "dmg-2")

    rows = await _rows(w)
    assert [r["tier"] for r in rows] == ["auto", "no_action"]
    assert aus.loads(rows[1]["reasons"]) == ["line_already_remedied"]
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("6.00")


async def test_a_reversal_does_not_free_the_line(w: aus.World) -> None:
    _, line = await _bought(w)
    await _handle(w, line, "dmg-1")
    (row,) = await _rows(w)

    out = await engine.reverse(w.pool, str(row["id"]), actor_email=aus.ADMIN)
    await _handle(w, line, "dmg-2")

    assert out["status"] == "reversed" and await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == 0
    assert [r["tier"] for r in await _rows(w)] == ["auto", "no_action"]
    note = await w.pool.fetchrow("SELECT * FROM notifications WHERE kind = 'autonomy.damage_reversed'")
    assert "$6.00" in note["body"]


async def test_reversing_a_replacement_offer_withdraws_it_without_touching_the_ledger(w: aus.World) -> None:
    _, line = await _bought(w, price="12.00", stock=5)
    await _handle(w, line)
    (row,) = await _rows(w)

    out = await engine.reverse(w.pool, str(row["id"]), actor_email=aus.ADMIN)

    assert out["status"] == "reversed" and await w.pool.fetchval("SELECT count(*) FROM store_credit_ledger") == 0
    note = await w.pool.fetchrow("SELECT * FROM notifications WHERE kind = 'autonomy.damage_reversed'")
    assert "replacement" in note["body"]
    with pytest.raises(engine.AutonomyError) as again:
        await engine.reverse(w.pool, str(row["id"]), actor_email=aus.ADMIN)
    assert again.value.status_code == 409


async def test_the_database_itself_refuses_a_second_remedy_on_a_line(w: aus.World) -> None:
    _, line = await _bought(w)
    await _handle(w, line)
    (row,) = await _rows(w)

    async with w.pool.acquire() as conn:
        with pytest.raises(asyncpg.UniqueViolationError):
            await engine._insert_row(  # noqa: SLF001 - the backstop is the point
                conn,
                id=uuid.uuid4(),
                entry_type="approved_execution",
                action="damage_goodwill",
                user_id=row["user_id"],
                order_id=row["order_id"],
                order_item_id=line,
                amount=D("1.00"),
                outcome="executed",
            )


async def test_two_events_racing_for_one_line_remedy_it_once(w: aus.World) -> None:
    _, line = await _bought(w)

    results = await asyncio.gather(_handle(w, line, "a"), _handle(w, line, "b"))

    assert sorted(r["tier"] for r in results) == ["auto", "no_action"]
    assert await w.pool.fetchval("SELECT count(*) FROM store_credit_ledger") == 1


async def test_an_open_approval_on_the_line_blocks_a_second_one(w: aus.World) -> None:
    _, line = await _bought(w, age_days=45)
    await _handle(w, line, "dmg-1")  # old: asks

    await _handle(w, line, "dmg-2")

    rows = await _rows(w)
    assert [r["tier"] for r in rows] == ["needs_approval", "no_action"]
    assert aus.loads(rows[1]["reasons"]) == ["approval_already_open"]
    assert await w.pool.fetchval("SELECT count(*) FROM approvals") == 1


async def test_the_orders_remedy_cap_is_a_no_action_not_an_approval(w: aus.World) -> None:
    order, first = await _bought(w, name="A")
    _, second = await _bought(w, name="B", order=order)
    _, third = await _bought(w, name="C", order=order)
    await _handle(w, first, "e1")
    result = await _handle(w, second, "e2")  # the customer's one remedy a day is used: asks
    assert result["tier"] == "needs_approval"
    approval = (await _rows(w))[1]["approval_id"]
    await svc.decide(str(approval), ADMIN, decision="approve")  # now two remedies on the order (the per-order cap)

    await _handle(w, third, "e3")

    last = (await _rows(w))[-1]
    assert last["tier"] == "no_action" and aus.loads(last["reasons"]) == ["order_remedy_limit"]
    assert await w.pool.fetchval("SELECT count(*) FROM approvals") == 1


# ── staff approval ───────────────────────────────────────────────────────────


async def test_approving_a_credit_pays_once_and_may_only_lower_it(w: aus.World) -> None:
    _, line = await _bought(w, age_days=45)  # 6.00, but old: asks
    await _handle(w, line)
    (row,) = await _rows(w)

    with pytest.raises(svc.ApprovalHTTPError):
        await svc.decide(str(row["approval_id"]), ADMIN, decision="approve", edits={"amount": "6.01"})
    out = await svc.decide(str(row["approval_id"]), ADMIN, decision="approve", edits={"amount": "4.00"})

    assert out["result"]["success"] is True and out["result"]["amount"] == "4.00"
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("4.00")
    (execution,) = await aus.decisions(w.pool, "approved_execution")
    assert execution["order_item_id"] == line and execution["action"] == "damage_goodwill"
    assert aus.loads(execution["input"])["resolution"] == "credit"
    assert (
        execution["actor_email"] == aus.ADMIN and execution["budget_day"] is None
    )  # a person's approval draws nothing
    note = await w.pool.fetchrow("SELECT * FROM notifications WHERE decision_id = $1", execution["id"])
    assert note["kind"] == "autonomy.damage_credit" and "$4.00" in note["body"]


async def test_approving_a_replacement_offers_it_without_a_credit_and_tells_staff_to_ship(w: aus.World) -> None:
    _, line = await _bought(w, price="25.00", stock=5)
    await _handle(w, line)
    (row,) = await _rows(w)

    out = await svc.decide(str(row["approval_id"]), ADMIN, decision="approve")

    assert out["result"]["success"] is True
    assert await w.pool.fetchval("SELECT count(*) FROM store_credit_ledger") == 0
    (execution,) = await aus.decisions(w.pool, "approved_execution")
    assert aus.loads(execution["input"])["resolution"] == "replacement_offer"
    kinds = sorted(n["kind"] for n in await w.pool.fetch("SELECT kind FROM notifications"))
    assert "autonomy.damage_replacement" in kinds and kinds.count("autonomy.action_taken") == 1
    reversed_ = await engine.reverse(
        w.pool, str(row["id"]), actor_email=aus.ADMIN
    )  # the approved offer can be withdrawn
    assert reversed_["status"] == "reversed" and await w.pool.fetchval("SELECT count(*) FROM store_credit_ledger") == 0


# ── guards ───────────────────────────────────────────────────────────────────


async def test_a_staff_buyer_is_never_remedied_alone_and_cannot_decide_it(w: aus.World) -> None:
    _, line = await _bought(w, aus.SUPPORT)

    await _handle(w, line)

    (row,) = await _rows(w)
    assert row["tier"] == "needs_approval" and "beneficiary_is_staff" in aus.loads(row["reasons"])
    assert await aus.ledger_balance(w.pool, w.users[aus.SUPPORT]) == 0
    with pytest.raises(svc.ApprovalHTTPError):
        await svc.decide(str(row["approval_id"]), svc.Actor(aus.SUPPORT, "support"), decision="approve")


async def test_the_demo_tour_user_is_never_remedied_and_no_row_references_it(w: aus.World) -> None:
    from shared.demo_tour import TOUR_EMAIL

    w.users[TOUR_EMAIL] = await aus.user(w.pool, TOUR_EMAIL)
    _, line = await _bought(w, TOUR_EMAIL)

    await _handle(w, line)

    (row,) = await _rows(w)
    assert aus.loads(row["reasons"]) == ["demo_user_excluded"]
    assert row["user_id"] is None and row["order_id"] is None and row["order_item_id"] is None
    assert await aus.ledger_balance(w.pool, w.users[TOUR_EMAIL]) == 0


# ── budgets ──────────────────────────────────────────────────────────────────


async def test_a_customers_second_remedy_of_the_day_is_over_the_cap_and_asks(w: aus.World) -> None:
    order, first = await _bought(w, name="A")
    _, second = await _bought(w, name="B", order=order)

    await _handle(w, first, "e1")
    await _handle(w, second, "e2")

    rows = await _rows(w)
    assert [r["tier"] for r in rows] == ["auto", "needs_approval"]
    assert aus.loads(rows[1]["budget"])["tripped"] == ["customer_daily_count"]


async def test_the_global_daily_count_is_shared_by_customers(
    w: aus.World, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "shared.config.settings.AUTONOMY_POLICY_PATH",
        aus.month_policy_file(
            tmp_path,
            "damage_goodwill",
            global_per_day={"max_count": 1},
            global_per_month={"max_count": 1},
        ),
    )
    _, one = await _bought(w, aus.CUSTOMER, name="A")
    _, two = await _bought(w, aus.OTHER, name="B")

    await _handle(w, one, "e1")
    await _handle(w, two, "e2")

    rows = await _rows(w)
    assert [r["tier"] for r in rows] == ["auto", "needs_approval"]
    assert aus.loads(rows[1]["budget"])["tripped"] == ["global_daily_count", "global_monthly_count"]


# ── the ops-responder never sees a damage decision ───────────────────────────


async def test_a_damage_decision_does_not_enqueue_a_model_draft(w: aus.World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shared.config.settings.AUTONOMY_RESPONDER_ENABLED", True)
    _, line = await _bought(w)
    await _handle(w, line)
    (row,) = await _rows(w)

    assert await w.pool.fetchval("SELECT count(*) FROM outbox WHERE topic = $1", engine.TOPIC_DRAFT_REQUESTED) == 0
    from shared.autonomy import responder

    assert (await responder.respond_to_decision(w.pool, str(row["id"])))["status"] == "skipped"
