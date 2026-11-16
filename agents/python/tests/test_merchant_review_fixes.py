"""Review fixes for the merchant proposals flow (#93): edits never raise a line, restock limits, hostile values.

Real Postgres, never mocked; the API is driven the way the seller page drives it.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest

from shared.merchant import execute as merchant_execute
from shared.merchant.guards import ReasonCode, check_restock_line
from shared.merchant.limits import get_limits
from shared.merchant.proposals import generate_proposal
from tests import merchant_support as ms
from tests.test_proposal_execute import _as, _batch, _call, _price, _restocks, w  # noqa: F401  (w is a fixture)

D = Decimal
LIMITS = get_limits()


async def _decide(proposal_id: str, line: dict[str, Any]):
    return await _call(
        _as(ms.SELLER, "seller"), "POST", f"/api/seller/proposals/{proposal_id}/decide", {"lines": [line]}
    )


# ── C1: restock limits in the guard ──────────────────────────────────────────


def test_a_restock_over_the_quantity_limit_is_blocked_and_the_limit_itself_is_allowed() -> None:
    cost = D("0.01")
    assert check_restock_line(quantity=LIMITS.max_restock_quantity, unit_cost=cost, limits=LIMITS).ok
    over = check_restock_line(quantity=LIMITS.max_restock_quantity + 1, unit_cost=cost, limits=LIMITS)
    assert over.reasons == (ReasonCode.RESTOCK_QUANTITY_EXCEEDED,)


def test_a_restock_over_the_order_value_limit_is_blocked_and_the_limit_itself_is_allowed() -> None:
    quantity = 100
    at_limit = LIMITS.max_restock_order_value / quantity
    assert check_restock_line(quantity=quantity, unit_cost=at_limit, limits=LIMITS).ok
    over = check_restock_line(quantity=quantity, unit_cost=at_limit + D("0.01"), limits=LIMITS)
    assert over.reasons == (ReasonCode.RESTOCK_VALUE_EXCEEDED,)
    assert over.numbers["order_value"] is not None


def test_a_huge_quantity_is_blocked_not_an_overflow() -> None:
    result = check_restock_line(quantity=2_000_000_000, unit_cost=D("1.00"), limits=LIMITS)
    assert ReasonCode.RESTOCK_QUANTITY_EXCEEDED in result.reasons


# ── C1: an edit can never raise a line ───────────────────────────────────────


async def test_a_restock_edit_above_the_proposed_quantity_is_422_and_changes_nothing(w: ms.World) -> None:  # noqa: F811
    proposal_id, products, lines = await _batch(w)

    resp = await _decide(proposal_id, {"line_id": lines["Short"], "decision": "edit", "value": 2_000_000_000})

    assert resp.status_code == 422, resp.text
    assert await _restocks(w, products["Short"]) == []
    assert await w.pool.fetchval("SELECT status FROM approvals WHERE source = 'proposal'") == "pending_review"


async def test_a_restock_edit_one_above_the_proposal_is_422_and_one_below_runs(w: ms.World) -> None:  # noqa: F811
    proposal_id, products, lines = await _batch(w)
    proposed = int(
        await w.pool.fetchval("SELECT proposed_value FROM proposal_lines WHERE id = $1", uuid.UUID(lines["Short"]))
    )

    over = await _decide(proposal_id, {"line_id": lines["Short"], "decision": "edit", "value": proposed + 1})
    ok = await _decide(proposal_id, {"line_id": lines["Short"], "decision": "edit", "value": proposed - 1})

    assert over.status_code == 422
    assert ok.status_code == 200
    (row,) = await _restocks(w, products["Short"])
    assert row["expected_quantity"] == proposed - 1


@pytest.mark.parametrize(
    "value,status", [("89.99", 422), ("90.00", 200), ("95.00", 200), ("100.00", 200), ("100.01", 422)]
)
async def test_a_price_edit_stays_between_the_proposed_and_the_current_price(
    w: ms.World,  # noqa: F811
    value: str,
    status: int,
) -> None:
    proposal_id, products, lines = await _batch(w)

    resp = await _decide(proposal_id, {"line_id": lines["Overstock"], "decision": "edit", "value": value})

    assert resp.status_code == status, resp.text
    if status == 422:
        assert await _price(w, products["Overstock"]) == D("100.00")


async def test_apply_line_refuses_a_value_that_raises_the_line_even_when_the_api_was_bypassed(
    w: ms.World,  # noqa: F811
) -> None:
    _, products, lines = await _batch(w)

    deeper = await merchant_execute.apply_line(w.pool, lines["Overstock"], D("70.00"))
    assert deeper["status"] == "blocked" and deeper["reasons"] == ["edit_beyond_proposal"]
    assert await _price(w, products["Overstock"]) == D("100.00")

    bigger = await merchant_execute.apply_line(w.pool, lines["Short"], D("2000000000"))
    assert bigger["status"] == "blocked" and "edit_beyond_proposal" in bigger["reasons"]
    assert await _restocks(w, products["Short"]) == []


async def test_apply_line_blocks_a_restock_over_the_live_value_limit(w: ms.World) -> None:  # noqa: F811
    """The cost went up after the batch was proposed: the order is now worth more than the limit."""
    _, products, lines = await _batch(w)
    await w.pool.execute("UPDATE products SET unit_cost = $2 WHERE id = $1", products["Short"], D("99999.00"))

    done = await merchant_execute.apply_line(w.pool, lines["Short"], D("28"))

    assert done["status"] == "blocked" and "restock_value_exceeded" in done["reasons"]
    assert await _restocks(w, products["Short"]) == []


# ── W5: hostile values are a 422, never a 500 ────────────────────────────────


@pytest.mark.parametrize("value", ["1E+30", "1e400", "99999999999.00", "10000000000", "NaN", "Infinity", "0.001", "-0"])
@pytest.mark.parametrize("which", ["Overstock", "Short"])
async def test_out_of_range_edit_values_are_422(w: ms.World, value: str, which: str) -> None:  # noqa: F811
    proposal_id, products, lines = await _batch(w)

    resp = await _decide(proposal_id, {"line_id": lines[which], "decision": "edit", "value": value})

    assert resp.status_code == 422, resp.text
    assert await _price(w, products["Overstock"]) == D("100.00")
    assert await _restocks(w, products["Short"]) == []


@pytest.mark.parametrize("value", [2_147_483_648, 4_294_967_296, 10**40])
async def test_a_restock_edit_past_int32_is_422(w: ms.World, value: int) -> None:  # noqa: F811
    proposal_id, products, lines = await _batch(w)

    resp = await _decide(proposal_id, {"line_id": lines["Short"], "decision": "edit", "value": value})

    assert resp.status_code == 422, resp.text
    assert await _restocks(w, products["Short"]) == []


# ── S3: a product deleted after generation ends cleanly ──────────────────────


async def test_a_product_deleted_after_generation_is_a_clean_outcome_with_nothing_changed(
    w: ms.World,  # noqa: F811
) -> None:
    gone = await ms.product(w, "Gone", price="100.00", cost="50.00", stock=(300, 300), sold=0)
    kept = await ms.product(w, "Kept", price="40.00", cost="20.00", stock=(2, 100), threshold=10, sold=40)
    result = await generate_proposal(w.pool, w.seller_id)
    rows = await w.pool.fetch("SELECT l.id, p.name FROM proposal_lines l JOIN products p ON p.id = l.product_id")
    lines = {r["name"]: str(r["id"]) for r in rows}
    assert set(lines) == {"Gone", "Kept"}
    await w.pool.execute("DELETE FROM restock_schedule WHERE product_id = $1", gone)
    await w.pool.execute("DELETE FROM warehouse_inventory WHERE product_id = $1", gone)
    await w.pool.execute("DELETE FROM products WHERE id = $1", gone)

    page = await _call(_as(ms.SELLER, "seller"), "GET", f"/api/seller/proposals/{result.proposal_id}")
    stale = await _call(
        _as(ms.SELLER, "seller"),
        "POST",
        f"/api/seller/proposals/{result.proposal_id}/decide",
        {"lines": [{"line_id": lines["Gone"], "decision": "accept"}, {"line_id": lines["Kept"], "decision": "reject"}]},
    )
    done = await _call(
        _as(ms.SELLER, "seller"),
        "POST",
        f"/api/seller/proposals/{result.proposal_id}/decide",
        {"lines": [{"line_id": lines["Kept"], "decision": "accept"}]},
    )

    assert page.status_code == 200 and [x["product_name"] for x in page.json()["lines"]] == ["Kept"]
    assert stale.status_code == 422  # the page that sent it was stale: a clean refusal, nothing applied
    assert done.status_code == 200, done.text
    assert await w.pool.fetchval("SELECT count(*) FROM price_history WHERE product_id = $1", kept) == 0
    assert len(await _restocks(w, kept)) == 1


# ── W4: a stale line never overwrites a newer state ──────────────────────────


async def test_a_price_that_moved_since_the_line_was_proposed_is_not_overwritten(w: ms.World) -> None:  # noqa: F811
    _, products, lines = await _batch(w)
    await w.pool.execute("UPDATE products SET price = 95.00 WHERE id = $1", products["Overstock"])

    done = await merchant_execute.apply_line(w.pool, lines["Overstock"], D("90.00"))

    assert done["status"] == "blocked" and done["reasons"] == ["price_moved"]
    assert await _price(w, products["Overstock"]) == D("95.00")
    history = await w.pool.fetchval("SELECT count(*) FROM price_history WHERE product_id = $1", products["Overstock"])
    assert history == 0
    stored = await w.pool.fetchval(
        "SELECT guard_result FROM proposal_lines WHERE id = $1", uuid.UUID(lines["Overstock"])
    )
    assert "price_moved" in stored and "95.00" in stored


async def test_a_moved_price_blocks_that_line_through_the_api_and_the_rest_still_runs(w: ms.World) -> None:  # noqa: F811
    proposal_id, products, lines = await _batch(w)
    await w.pool.execute("UPDATE products SET price = 120.00 WHERE id = $1", products["Overstock"])
    body = {"lines": [{"line_id": line_id, "decision": "accept"} for line_id in lines.values()]}

    resp = await _call(_as(ms.SELLER, "seller"), "POST", f"/api/seller/proposals/{proposal_id}/decide", body)

    assert resp.status_code == 200, resp.text
    results = {r["line_id"]: r for r in resp.json()["results"]}
    assert results[lines["Overstock"]]["reasons"] == ["price_moved"]
    assert results[lines["Short"]]["status"] == "executed"
    assert await _price(w, products["Overstock"]) == D("120.00")


async def test_a_restock_is_blocked_when_stock_is_already_on_order(w: ms.World) -> None:  # noqa: F811
    _, products, lines = await _batch(w)
    await w.pool.execute(
        "INSERT INTO restock_schedule (product_id, warehouse_id, expected_quantity, expected_date) "
        "VALUES ($1, $2, 30, CURRENT_DATE + 3)",
        products["Short"],
        w.warehouses[0],
    )

    done = await merchant_execute.apply_line(w.pool, lines["Short"], D("28"))

    assert done["status"] == "blocked" and done["reasons"] == ["restock_already_scheduled"]
    assert len(await _restocks(w, products["Short"])) == 1  # only the one that was already there
