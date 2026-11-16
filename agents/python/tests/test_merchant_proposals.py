"""M-3 (#93) -- deterministic proposal generation: the pure rules, then the stored batch against real Postgres."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

import asyncpg
import pytest

from shared.approvals import repo
from shared.merchant.guards import ReasonCode
from shared.merchant.limits import MerchantLimits, get_limits
from shared.merchant.proposals import (
    APPROVAL_KIND,
    DEFAULT_RULES,
    SYSTEM_REQUESTER,
    GenerationRules,
    ProductSignals,
    WarehouseStock,
    build_candidates,
    cover_days,
    generate_proposal,
    markdown_price,
)
from tests import merchant_support as ms

D = Decimal


def _sig(name: str = "P", *, price: str = "100.00", stock: int = 200, sold: int = 0, incoming: int = 0, wh=None):
    warehouses = wh if wh is not None else (WarehouseStock(uuid.uuid4(), "East", stock, 10),)
    return ProductSignals(uuid.uuid4(), name, D(price), D("50"), stock, sold, incoming, tuple(warehouses))


# ── the pure rules ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("price", ["0.99", "16.99", "37.49", "99.99", "1399.99", "7.77", "1000000.01"])
@pytest.mark.parametrize("pct", ["5", "10"])
def test_a_markdown_never_exceeds_the_percentage_it_was_sized_for(price: str, pct: str) -> None:
    new = markdown_price(D(price), D(pct))
    assert new < D(price)
    assert (D(price) - new) / D(price) * 100 <= D(pct)
    assert new == new.quantize(D("0.01"))


def test_cover_days_is_none_without_sales() -> None:
    assert cover_days(100, 0, 90) is None
    assert cover_days(100, 10, 90) == D("900.0")


def test_an_overstocked_product_gets_a_markdown_and_deeper_when_nothing_sells() -> None:
    slow = _sig("Slow", stock=100, sold=30)  # 300 days of cover: the lighter step
    dead = _sig("Dead", stock=100, sold=0)  # no sales at all: the deeper step
    brisk = _sig("Brisk", stock=100, sold=90)  # 100 days of cover: leave alone
    candidates = {c.product_name: c for c in build_candidates([slow, dead, brisk])}

    assert set(candidates) == {"Slow", "Dead"}
    assert candidates["Slow"].proposed_value == D("95.00")
    assert candidates["Dead"].proposed_value == D("90.00")
    assert candidates["Dead"].evidence["cover_days"] is None
    assert all(c.field == "price" for c in candidates.values())


def test_a_product_with_little_stock_is_not_marked_down() -> None:
    assert build_candidates([_sig(stock=DEFAULT_RULES.markdown_min_stock - 1, sold=0)]) == []


def test_a_short_warehouse_gets_a_restock_that_refills_it_to_the_target() -> None:
    short = WarehouseStock(uuid.uuid4(), "East", 4, 10)
    fine = WarehouseStock(uuid.uuid4(), "West", 80, 10)
    (candidate,) = build_candidates([_sig(stock=84, sold=50, wh=(short, fine))])

    assert candidate.field == "restock_quantity"
    assert candidate.proposed_value == D(10 * DEFAULT_RULES.restock_target_multiple - 4)
    assert candidate.evidence["short_warehouses"] == [
        {"warehouse": "East", "quantity": 4, "threshold": 10, "order": 26}
    ]


def test_a_restock_already_on_its_way_suppresses_both_a_restock_and_a_markdown() -> None:
    short = WarehouseStock(uuid.uuid4(), "East", 0, 10)
    assert build_candidates([_sig(stock=0, wh=(short,), incoming=50)]) == []


def test_a_product_is_never_both_restocked_and_marked_down() -> None:
    short = WarehouseStock(uuid.uuid4(), "East", 1, 10)
    rich = WarehouseStock(uuid.uuid4(), "West", 500, 10)
    fields = [c.field for c in build_candidates([_sig(stock=501, sold=0, wh=(short, rich))])]
    assert fields == ["restock_quantity"]


def test_candidates_come_most_urgent_first_and_the_order_is_stable() -> None:
    a = _sig("A", stock=100, sold=30)
    b = _sig("B", stock=400, sold=30)
    short = WarehouseStock(uuid.uuid4(), "East", 0, 10)
    r = _sig("R", stock=0, wh=(short,))
    first = [(c.product_name, c.field) for c in build_candidates([a, b, r])]
    assert first == [("R", "restock_quantity"), ("B", "price"), ("A", "price")]
    assert first == [(c.product_name, c.field) for c in build_candidates([r, b, a])]


# ── generation against a real database ───────────────────────────────────────


@pytest.fixture
async def w(clean_db: asyncpg.Pool) -> ms.World:
    return await ms.world(clean_db)


async def _lines(pool: asyncpg.Pool, proposal_id: str) -> dict[str, asyncpg.Record]:
    rows = await pool.fetch(
        """SELECT l.*, p.name FROM proposal_lines l JOIN products p ON p.id = l.product_id WHERE l.proposal_id = $1""",
        uuid.UUID(proposal_id),
    )
    return {r["name"]: r for r in rows}


async def test_generation_stores_the_batch_its_lines_and_one_approval_in_the_inbox(w: ms.World) -> None:
    await ms.product(w, "Overstock", price="100.00", stock=(300, 300), sold=20)
    await ms.product(w, "Low", price="40.00", stock=(2, 100), threshold=10, sold=40)

    result = await generate_proposal(w.pool, w.seller_id, trigger="manual")

    assert result.proposal_id and result.approval_id
    assert result.kind == "mixed" and result.lines == 2 and result.blocked == 0
    lines = await _lines(w.pool, result.proposal_id)
    assert lines["Overstock"]["field"] == "price" and lines["Overstock"]["proposed_value"] == D("90.00")
    assert lines["Overstock"]["current_value"] == D("100.00") and lines["Overstock"]["status"] == "proposed"
    assert json.loads(lines["Overstock"]["guard_result"])["ok"] is True
    assert lines["Low"]["field"] == "restock_quantity" and lines["Low"]["proposed_value"] == D("28")

    approval = await repo.get(w.pool, result.approval_id)
    assert approval["source"] == "proposal" and approval["kind"] == APPROVAL_KIND
    assert str(approval["source_ref"]) == result.proposal_id
    assert approval["status"] == "pending_review" and approval["due_at"] is not None
    assert approval["requester_email"] == SYSTEM_REQUESTER and approval["subject_email"] == ms.SELLER
    assert approval["policy_version"] == get_limits().policy_version
    timers = await w.pool.fetch("SELECT topic FROM outbox WHERE aggregate_id = $1 ORDER BY id", approval["id"])
    assert [t["topic"] for t in timers] == ["approval.reminder_due", "approval.sla_breached"]
    note = await w.pool.fetchrow("SELECT * FROM notifications WHERE approval_id = $1", approval["id"])
    assert note["user_email"] == ms.SELLER and note["link"] == "/seller/proposals"


async def test_a_line_the_guards_refuse_is_stored_as_blocked_with_the_reason(w: ms.World) -> None:
    ok = await ms.product(w, "Fine", price="100.00", cost="50.00", stock=(300, 300), sold=20)
    thin = await ms.product(w, "Thin margin", price="100.00", cost="88.00", stock=(300, 300), sold=20)
    promo = await ms.product(w, "On promotion", price="100.00", stock=(300, 300), sold=20)
    await ms.active_promotion(w.pool, promo)
    await w.pool.execute("UPDATE products SET unit_cost = NULL WHERE name = 'On promotion'")
    assert ok and thin

    result = await generate_proposal(w.pool, w.seller_id)

    lines = await _lines(w.pool, result.proposal_id)
    assert lines["Fine"]["status"] == "proposed"
    assert lines["Thin margin"]["status"] == "blocked"
    assert json.loads(lines["Thin margin"]["guard_result"])["reasons"] == [ReasonCode.MARGIN_BELOW_FLOOR.value]
    assert lines["On promotion"]["status"] == "blocked"
    reasons = json.loads(lines["On promotion"]["guard_result"])["reasons"]
    assert ReasonCode.ACTIVE_PROMOTION.value in reasons and ReasonCode.MISSING_COST.value in reasons
    assert result.blocked == 2


async def test_running_twice_creates_one_batch_not_two(w: ms.World) -> None:
    await ms.product(w, "Overstock", stock=(300, 300), sold=20)

    first = await generate_proposal(w.pool, w.seller_id)
    second = await generate_proposal(w.pool, w.seller_id)

    assert first.proposal_id and second.proposal_id is None
    assert second.skipped_existing == 1
    assert await w.pool.fetchval("SELECT count(*) FROM proposals") == 1
    assert await w.pool.fetchval("SELECT count(*) FROM approvals") == 1


async def test_once_a_batch_is_decided_the_same_product_can_be_proposed_again(w: ms.World) -> None:
    await ms.product(w, "Overstock", stock=(300, 300), sold=20)
    first = await generate_proposal(w.pool, w.seller_id)
    await w.pool.execute("UPDATE proposals SET status = 'decided'")
    await w.pool.execute("UPDATE proposal_lines SET status = 'rejected'")

    again = await generate_proposal(w.pool, w.seller_id)

    assert again.proposal_id and again.proposal_id != first.proposal_id


async def test_a_batch_with_nothing_the_seller_could_accept_is_not_stored(w: ms.World) -> None:
    await ms.product(w, "Thin margin", price="100.00", cost="90.00", stock=(300, 300), sold=20)

    result = await generate_proposal(w.pool, w.seller_id)

    assert result.proposal_id is None
    assert await w.pool.fetchval("SELECT count(*) FROM proposals") == 0


async def test_only_the_sellers_own_active_products_are_considered(w: ms.World) -> None:
    await ms.product(w, "Theirs", seller=w.other_seller_id, stock=(300, 300), sold=20)
    await ms.product(w, "Retired", stock=(300, 300), sold=20, active=False)

    assert (await generate_proposal(w.pool, w.seller_id)).proposal_id is None
    other = await generate_proposal(w.pool, w.other_seller_id)
    assert other.proposal_id and set(await _lines(w.pool, other.proposal_id)) == {"Theirs"}


async def test_the_batch_is_trimmed_to_the_lines_per_batch_limit_keeping_the_most_urgent(w: ms.World) -> None:
    for i in range(6):
        await ms.product(w, f"Item {i}", stock=(300 + i * 50, 300), sold=20)
    limits = MerchantLimits(
        policy_version="t.1",
        margin_floor_pct=D("15"),
        max_change_pct_per_day=D("10"),
        block_during_active_promotion=True,
        max_lines_per_batch=4,
    )

    result = await generate_proposal(w.pool, w.seller_id, limits=limits)

    assert result.lines == 4 and result.skipped_over_batch == 2
    assert set(await _lines(w.pool, result.proposal_id)) == {
        "Item 5",
        "Item 4",
        "Item 3",
        "Item 2",
    }  # deepest cover first


async def test_the_markdown_lines_are_capped_so_the_page_stays_readable(w: ms.World) -> None:
    for i in range(12):
        await ms.product(w, f"Item {i:02d}", stock=(300, 300), sold=20)

    result = await generate_proposal(w.pool, w.seller_id)

    assert result.lines == GenerationRules().max_markdown_lines


async def test_the_seller_is_never_the_requester_so_deciding_is_never_self_approval(w: ms.World) -> None:
    await ms.product(w, "Overstock", stock=(300, 300), sold=20)
    result = await generate_proposal(w.pool, w.seller_id)

    # The database CHECK and the repository still forbid the requester deciding: the system actor can never decide...
    with pytest.raises(repo.SelfApprovalError):
        await repo.transition(
            w.pool, result.approval_id, [repo.ApprovalStatus.PENDING_REVIEW], "approved", decided_by=SYSTEM_REQUESTER
        )
    # ...and the seller is a different person, so they may.
    decided = await repo.transition(
        w.pool, result.approval_id, [repo.ApprovalStatus.PENDING_REVIEW], "approved", decided_by=ms.SELLER
    )
    assert decided["decided_by"] == ms.SELLER


async def test_a_rolled_back_generation_leaves_no_batch_no_approval_and_no_timer(w: ms.World) -> None:
    await ms.product(w, "Overstock", stock=(300, 300), sold=20)
    async with w.pool.acquire() as conn:
        with pytest.raises(RuntimeError):
            async with conn.transaction():
                await generate_proposal(conn, w.seller_id)
                raise RuntimeError("the caller failed afterwards")

    for table in ("proposals", "proposal_lines", "approvals", "outbox", "notifications"):
        assert await w.pool.fetchval(f"SELECT count(*) FROM {table}") == 0  # noqa: S608
