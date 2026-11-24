"""#299 -- ``order.risk_flagged``: a deterministic risk score from facts in the database, against real Postgres.

Low risk is nothing, medium opens an approval, high holds the order (reversibly, on the record) AND opens an approval.
The engine never cancels, refunds or clears an order itself, never acts alone on a risk review, never holds a staff
member's order, and never touches the demo tour user.
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
TOPIC = engine.TOPIC_RISK_FLAGGED
STAFF = svc.Actor(aus.SUPPORT, "support")  # a support reviewer cannot decide a manager route (order totals above 100)
ADMIN = svc.Actor(aus.ADMIN, "admin")


@pytest.fixture
async def w(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> aus.World:
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    world = await aus.world(clean_db)
    await aus.age_account(world, aus.CUSTOMER, 100)  # an old account: not a signal unless a test wants it
    return world


async def _handle(w: aus.World, order: uuid.UUID | None, event_id: str = "risk-1", **extra: Any) -> dict[str, Any]:
    return await engine.handle_event(w.pool, TOPIC, aus.risk(order, event_id, **extra), outbox_id=1)


async def _rows(w: aus.World) -> list[asyncpg.Record]:
    return await aus.decisions(w.pool)


async def _held(w: aus.World, order: uuid.UUID) -> asyncpg.Record:
    return await w.pool.fetchrow("SELECT status, on_hold, held_at, hold_decision_id FROM orders WHERE id = $1", order)


async def _high(w: aus.World, email: str = aus.CUSTOMER) -> uuid.UUID:
    """600 (20) + a 1 day old account (25) + three orders inside a day (25) = 70, the lowest high score."""
    await aus.age_account(w, email, 1)
    await aus.risk_order(w, email, total="20.00", hours_ago=5)
    await aus.risk_order(w, email, total="20.00", hours_ago=2)
    return await aus.risk_order(w, email, total="600.00")


async def _medium(w: aus.World, email: str = aus.CUSTOMER) -> uuid.UUID:
    """600 (20) + a 1 day old account (25) = 45."""
    await aus.age_account(w, email, 1)
    return await aus.risk_order(w, email, total="600.00")


# ── the three tiers ──────────────────────────────────────────────────────────


async def test_a_low_risk_order_is_left_alone_and_logged(w: aus.World) -> None:
    order = await aus.risk_order(w, total="50.00")

    result = await _handle(w, order)

    assert result["tier"] == "no_action" and result["status"] == "no_action"
    (row,) = await _rows(w)
    assert (row["action"], row["tier"], row["outcome"], row["amount"]) == ("none", "no_action", "no_action", None)
    assert aus.loads(row["reasons"]) == ["risk_low"] and row["order_id"] == order
    assert aus.loads(row["input"])["event"]["risk"]["score"] == 0
    assert await w.pool.fetchval("SELECT count(*) FROM approvals") == 0
    assert (await _held(w, order))["on_hold"] is False


async def test_a_medium_risk_order_opens_an_approval_and_holds_nothing(w: aus.World) -> None:
    order = await _medium(w)

    result = await _handle(w, order)

    assert result["tier"] == "needs_approval" and result["status"] == "awaiting_approval"
    (row,) = await _rows(w)
    assert (row["action"], row["tier"], row["outcome"], row["amount"]) == (
        "order_risk_review",
        "needs_approval",
        "awaiting_approval",
        D("600.00"),
    )
    assert row["reversible"] is False and row["budget_key"] is None and row["budget_day"] is None
    risk = aus.loads(row["input"])["event"]["risk"]
    assert risk["score"] == 45 and [s["signal"] for s in risk["signals"]] == ["large-order", "new-account"]
    approval = await w.pool.fetchrow("SELECT * FROM approvals WHERE id = $1", row["approval_id"])
    assert (approval["kind"], approval["source"], approval["status"], approval["requester_email"]) == (
        "autonomous_action",
        "event",
        "pending_review",
        "autonomy@system",
    )
    assert approval["subject_email"] == aus.CUSTOMER
    held = await _held(w, order)
    assert held["on_hold"] is False and held["status"] == "placed"
    kinds = [n["kind"] for n in await w.pool.fetch("SELECT kind FROM notifications")]
    assert kinds == ["autonomy.approval_needed"]  # staff only: the customer is not told of a medium review
    assert await w.pool.fetchval("SELECT count(*) FROM store_credit_ledger") == 0


async def test_a_high_risk_order_is_held_reversibly_and_opens_an_approval(w: aus.World) -> None:
    order = await _high(w)

    result = await _handle(w, order)

    assert result["tier"] == "needs_approval" and result["held"] is True
    row = next(r for r in await _rows(w) if r["order_id"] == order)
    assert row["reversible"] is True and row["approval_id"] is not None
    assert aus.loads(row["input"])["hold"] is True and aus.loads(row["input"])["event"]["risk"]["score"] == 70
    held = await _held(w, order)
    assert held["on_hold"] is True and held["hold_decision_id"] == row["id"] and held["held_at"] is not None
    assert held["status"] == "placed"  # a flag: the order is not cancelled and its status is untouched
    note = await w.pool.fetchrow("SELECT * FROM notifications WHERE kind = 'autonomy.order_held'")
    assert note["user_email"] == aus.CUSTOMER and note["decision_id"] == row["id"]
    assert await w.pool.fetchval("SELECT count(*) FROM store_credit_ledger") == 0


async def test_the_score_is_read_from_the_database_not_from_the_event(w: aus.World) -> None:
    order = await aus.risk_order(w, total="50.00")

    await _handle(w, order, risk_score=100, order_total="99999.00", hold=True, signals=["everything"])

    (row,) = await _rows(w)
    assert row["tier"] == "no_action" and (await _held(w, order))["on_hold"] is False


async def test_the_engine_never_cancels_refunds_or_acts_alone_on_a_risk_review(w: aus.World) -> None:
    high, medium, low = await _high(w), await aus.risk_order(w, aus.OTHER, total="10.00"), None
    for i, order in enumerate((high, medium, low)):
        await _handle(w, order, f"e{i}")

    tiers = {r["tier"] for r in await _rows(w)}
    assert tiers <= {"needs_approval", "no_action"}
    assert await w.pool.fetchval("SELECT count(*) FROM orders WHERE status NOT IN ('placed')") == 0
    assert await w.pool.fetchval("SELECT count(*) FROM store_credit_ledger") == 0
    assert await w.pool.fetchval("SELECT count(*) FROM agent_decisions WHERE tier IN ('auto', 'auto_notify')") == 0


# ── signals, read from the order ─────────────────────────────────────────────


async def test_velocity_counts_this_customers_orders_in_the_24_hours_before_it(w: aus.World) -> None:
    await aus.risk_order(w, total="10.00", hours_ago=30)  # outside the window
    await aus.risk_order(w, total="10.00", hours_ago=24)  # exactly 24 hours earlier: outside (strictly newer counts)
    await aus.risk_order(w, total="10.00", hours_ago=23.9)
    await aus.risk_order(w, aus.OTHER, total="10.00", hours_ago=1)  # someone else's order never counts
    order = await aus.risk_order(w, total="10.00")

    await _handle(w, order)

    facts = aus.loads((await _rows(w))[0]["input"])["event"]["risk"]["facts"]
    assert facts["orders_in_window"] == "2" and aus.loads((await _rows(w))[0]["input"])["event"]["risk"]["score"] == 0


async def test_three_orders_in_a_day_is_a_signal_and_five_a_second(w: aus.World) -> None:
    for hours in (4, 3, 2, 1):
        await aus.risk_order(w, total="10.00", hours_ago=hours)
    order = await aus.risk_order(w, total="10.00")

    await _handle(w, order)

    risk = aus.loads((await _rows(w))[0]["input"])["event"]["risk"]
    assert risk["facts"]["orders_in_window"] == "5" and risk["score"] == 40
    assert [s["signal"] for s in risk["signals"]] == ["order-velocity", "high-order-velocity"]


async def test_items_in_the_order_are_summed_by_quantity(w: aus.World) -> None:
    order = await aus.risk_order(w, total="40.00", items=8)

    await _handle(w, order)

    risk = aus.loads((await _rows(w))[0]["input"])["event"]["risk"]
    assert risk["facts"]["items_count"] == "8" and risk["score"] == 15


@pytest.mark.parametrize(
    ("shipping", "billing", "points"),
    [
        (
            {"street": "1 Main St", "zip": "10001", "country": "US"},
            {"street": "9 Other Rd", "zip": "10001", "country": "US"},
            15,
        ),
        (
            {"street": "1 Main St", "zip": "10001", "country": "US"},
            {"street": "1 Main St", "zip": "20002", "country": "US"},
            15,
        ),
        (
            {"street": "1 Main St", "zip": "10001", "country": "US"},
            {"street": "1 Main St", "zip": "10001", "country": "CA"},
            15,
        ),
        (
            {"street": "1 Main St", "zip": "10001", "country": "US"},
            {"street": " 1  MAIN st ", "zip": "10001", "country": "us"},
            0,
        ),
        (
            {"street": "1 Main St", "zip": "10001", "country": "US"},
            None,
            0,
        ),  # no billing address: unknown, adds nothing
        ({}, {"street": "1 Main St"}, 0),
    ],
)
async def test_the_address_signal_compares_street_zip_and_country(
    w: aus.World, shipping: dict[str, Any], billing: dict[str, Any] | None, points: int
) -> None:
    order = await aus.risk_order(w, total="10.00", shipping=shipping, billing=billing)

    await _handle(w, order)

    assert aus.loads((await _rows(w))[0]["input"])["event"]["risk"]["score"] == points


def test_address_mismatch_is_unknown_when_either_side_is_missing() -> None:
    assert engine.address_mismatch(None, {"street": "a"}) is None
    assert engine.address_mismatch({"street": "a"}, "{}") is None
    assert engine.address_mismatch('{"street": "a"}', {"street": "A"}) == 0
    assert engine.address_mismatch({"zip": "1"}, {"zip": "2"}) == 1


async def test_a_new_account_is_a_signal_and_a_week_old_one_is_not(w: aus.World) -> None:
    await aus.age_account(w, aus.CUSTOMER, 6)
    young = await aus.risk_order(w, total="10.00")
    await aus.age_account(w, aus.OTHER, 7)
    old = await aus.risk_order(w, aus.OTHER, total="10.00")

    await _handle(w, young, "y")
    await _handle(w, old, "o")

    scores = {r["order_id"]: aus.loads(r["input"])["event"]["risk"]["score"] for r in await _rows(w)}
    assert scores == {young: 25, old: 0}


# ── no_action reasons ────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", ["shipped", "delivered", "cancelled", "returned"])
async def test_an_order_past_the_hold_stage_is_not_reviewed(w: aus.World, status: str) -> None:
    order = await aus.risk_order(w, status=status)

    await _handle(w, order)

    (row,) = await _rows(w)
    assert aus.loads(row["reasons"]) == ["order_not_eligible"] and row["tier"] == "no_action"


@pytest.mark.parametrize(
    ("payload_order", "reason"),
    [(None, "invalid_event_order"), ("not-a-uuid", "invalid_event_order"), (uuid.uuid4(), "order_not_found")],
)
async def test_an_event_naming_no_real_order_is_logged_with_no_ids(
    w: aus.World, payload_order: Any, reason: str
) -> None:
    await _handle(w, payload_order)

    (row,) = await _rows(w)
    assert aus.loads(row["reasons"]) == [reason] and row["user_id"] is None and row["order_id"] is None


async def test_an_order_already_on_hold_is_not_held_again(w: aus.World) -> None:
    order = await _high(w)
    await w.pool.execute("UPDATE orders SET on_hold = TRUE WHERE id = $1", order)

    await _handle(w, order)

    (row,) = await _rows(w)
    assert aus.loads(row["reasons"]) == ["order_already_held"]


# ── at most once ─────────────────────────────────────────────────────────────


async def test_a_replayed_event_acts_once(w: aus.World) -> None:
    order = await _high(w)

    first = await _handle(w, order)
    second = await _handle(w, order)

    assert second["decision_id"] == first["decision_id"]
    assert len([r for r in await _rows(w) if r["order_id"] == order]) == 1
    assert await w.pool.fetchval("SELECT count(*) FROM approvals") == 1


async def test_a_second_event_id_for_a_reviewed_order_opens_no_second_approval(w: aus.World) -> None:
    order = await _medium(w)
    await _handle(w, order, "risk-1")

    await _handle(w, order, "risk-2")

    rows = await _rows(w)
    assert [r["tier"] for r in rows] == ["needs_approval", "no_action"]
    assert aus.loads(rows[1]["reasons"]) == ["order_already_reviewed"]
    assert await w.pool.fetchval("SELECT count(*) FROM approvals") == 1


async def test_two_concurrent_events_for_one_high_risk_order_hold_it_and_ask_once(w: aus.World) -> None:
    order = await _high(w)

    results = await asyncio.gather(*(_handle(w, order, f"c{i}") for i in range(4)))

    assert sum(1 for r in results if r["tier"] == "needs_approval") == 1
    assert sum(1 for r in results if r["tier"] == "no_action") == 3
    assert await w.pool.fetchval("SELECT count(*) FROM approvals") == 1
    assert (await _held(w, order))["on_hold"] is True


# ── guards ───────────────────────────────────────────────────────────────────


async def test_a_staff_members_order_is_never_held_by_the_engine_and_they_cannot_decide_it(w: aus.World) -> None:
    order = await _high(w, aus.SUPPORT)

    await _handle(w, order)

    row = next(r for r in await _rows(w) if r["order_id"] == order)
    assert row["tier"] == "needs_approval" and "beneficiary_is_staff" in aus.loads(row["reasons"])
    assert row["reversible"] is False and (await _held(w, order))["on_hold"] is False
    with pytest.raises(svc.ApprovalHTTPError):  # the subject never decides their own review
        await svc.decide(str(row["approval_id"]), STAFF, decision="approve")
    out = await svc.decide(str(row["approval_id"]), svc.Actor(aus.ADMIN, "admin"), decision="approve")
    assert out["result"]["success"] is True


async def test_the_demo_tour_user_is_never_reviewed_and_no_row_references_it(w: aus.World) -> None:
    from shared.demo_tour import TOUR_EMAIL

    w.users[TOUR_EMAIL] = await aus.user(w.pool, TOUR_EMAIL)
    order = await aus.risk_order(w, TOUR_EMAIL, total="5000.00")

    await _handle(w, order)

    (row,) = await _rows(w)
    assert aus.loads(row["reasons"]) == ["demo_user_excluded"]
    assert row["user_id"] is None and row["order_id"] is None
    assert (await _held(w, order))["on_hold"] is False


# ── staff decide the review ──────────────────────────────────────────────────


async def test_approving_a_held_review_clears_it_and_releases_the_hold(w: aus.World) -> None:
    order = await _high(w)
    await _handle(w, order)
    decision = next(r for r in await _rows(w) if r["order_id"] == order)

    out = await svc.decide(str(decision["approval_id"]), ADMIN, decision="approve")

    assert out["result"]["success"] is True and out["result"]["hold_released"] is True
    assert out["approval"]["status"] == "executed"
    held = await _held(w, order)
    assert held["on_hold"] is False and held["hold_decision_id"] is None and held["held_at"] is None
    (execution,) = await aus.decisions(w.pool, "approved_execution")
    assert execution["action"] == "order_risk_review" and execution["actor_email"] == aus.ADMIN
    assert execution["reversible"] is False
    (reversal,) = await aus.decisions(w.pool, "reversal")
    assert reversal["reverses_id"] == decision["id"] and reversal["actor_email"] == aus.ADMIN
    assert await w.pool.fetchval("SELECT count(*) FROM notifications WHERE kind = 'autonomy.order_released'") == 1
    assert await w.pool.fetchval("SELECT count(*) FROM store_credit_ledger") == 0


async def test_approving_a_medium_review_has_no_hold_to_release(w: aus.World) -> None:
    order = await _medium(w)
    await _handle(w, order)
    (decision,) = await _rows(w)

    out = await svc.decide(str(decision["approval_id"]), ADMIN, decision="approve")

    assert out["result"]["success"] is True and out["result"]["hold_released"] is False
    assert await aus.decisions(w.pool, "reversal") == []


async def test_a_review_has_nothing_to_edit(w: aus.World) -> None:
    order = await _medium(w)
    await _handle(w, order)
    (decision,) = await _rows(w)

    with pytest.raises(svc.ApprovalHTTPError) as err:
        await svc.decide(str(decision["approval_id"]), ADMIN, decision="approve", edits={"amount": "1.00"})

    assert err.value.status_code == 422
    assert await aus.decisions(w.pool, "approved_execution") == []


async def test_rejecting_a_held_review_leaves_the_hold_in_place(w: aus.World) -> None:
    order = await _high(w)
    await _handle(w, order)
    decision = next(r for r in await _rows(w) if r["order_id"] == order)

    out = await svc.decide(str(decision["approval_id"]), ADMIN, decision="reject")

    assert out["approval"]["status"] == "rejected"
    assert (await _held(w, order))["on_hold"] is True  # upheld: only an admin's release lifts it
    assert await aus.decisions(w.pool, "approved_execution") == []


# ── reversal ─────────────────────────────────────────────────────────────────


async def test_an_admin_can_release_a_hold_with_a_recorded_reversal(w: aus.World) -> None:
    order = await _high(w)
    await _handle(w, order)
    decision = next(r for r in await _rows(w) if r["order_id"] == order)

    out = await engine.reverse(w.pool, str(decision["id"]), actor_email=aus.ADMIN, note="customer called in")

    assert out["status"] == "reversed"
    held = await _held(w, order)
    assert held["on_hold"] is False and held["hold_decision_id"] is None and held["status"] == "placed"
    (reversal,) = await aus.decisions(w.pool, "reversal")
    assert (reversal["reverses_id"], reversal["actor_email"], reversal["note"]) == (
        decision["id"],
        aus.ADMIN,
        "customer called in",
    )
    assert await w.pool.fetchval("SELECT count(*) FROM notifications WHERE kind = 'autonomy.order_released'") == 1
    with pytest.raises(engine.AutonomyError) as again:
        await engine.reverse(w.pool, str(decision["id"]), actor_email=aus.ADMIN)
    assert again.value.status_code == 409


async def test_a_released_hold_does_not_close_the_review_and_approving_it_later_adds_no_second_reversal(
    w: aus.World,
) -> None:
    order = await _high(w)
    await _handle(w, order)
    decision = next(r for r in await _rows(w) if r["order_id"] == order)
    await engine.reverse(w.pool, str(decision["id"]), actor_email=aus.ADMIN)

    listed = (await engine.list_decisions(w.pool))[0]
    assert listed["state"] == "awaiting_approval" and listed["hold_active"] is False and listed["can_reverse"] is False

    out = await svc.decide(str(decision["approval_id"]), ADMIN, decision="approve")

    assert out["result"]["success"] is True and out["result"]["hold_released"] is False
    assert len(await aus.decisions(w.pool, "reversal")) == 1


async def test_a_review_without_a_hold_cannot_be_reversed(w: aus.World) -> None:
    order = await _medium(w)
    await _handle(w, order)
    (decision,) = await _rows(w)

    with pytest.raises(engine.AutonomyError) as err:
        await engine.reverse(w.pool, str(decision["id"]), actor_email=aus.ADMIN)

    assert err.value.status_code == 409


async def test_a_reversal_only_ever_releases_its_own_hold(w: aus.World) -> None:
    order = await _high(w)
    await _handle(w, order)
    decision = next(r for r in await _rows(w) if r["order_id"] == order)
    # something else now holds the order (a later review): the older decision must not lift that hold
    await w.pool.execute("UPDATE orders SET hold_decision_id = $2 WHERE id = $1", order, uuid.uuid4())

    with pytest.raises(engine.AutonomyError) as err:
        await engine.reverse(w.pool, str(decision["id"]), actor_email=aus.ADMIN)

    assert err.value.status_code == 409
    assert (await _held(w, order))["on_hold"] is True


async def test_the_list_marks_a_held_order_and_the_sql_state_agrees(w: aus.World) -> None:
    order = await _high(w)
    await _handle(w, order)

    held = next(d for d in await engine.list_decisions(w.pool) if d["order_id"] == str(order))

    assert held["hold_active"] is True and held["can_reverse"] is True and held["state"] == "awaiting_approval"
    assert await engine.count_decisions(w.pool, state="awaiting_approval") == 1
    medium = await aus.risk_order(w, aus.OTHER, total="600.00")
    await aus.age_account(w, aus.OTHER, 1)
    await _handle(w, medium, "m")
    plain = next(d for d in await engine.list_decisions(w.pool) if d["order_id"] == str(medium))
    assert plain["hold_active"] is False and plain["can_reverse"] is False


async def test_the_decision_log_stays_append_only_for_holds(w: aus.World) -> None:
    order = await _high(w)
    await _handle(w, order)
    decision = next(r for r in await _rows(w) if r["order_id"] == order)

    with pytest.raises(asyncpg.PostgresError):
        await w.pool.execute("UPDATE agent_decisions SET reversible = FALSE WHERE id = $1", decision["id"])
    with pytest.raises(asyncpg.PostgresError):
        await w.pool.execute("DELETE FROM agent_decisions WHERE id = $1", decision["id"])
