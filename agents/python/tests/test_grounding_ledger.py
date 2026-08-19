"""GroundingLedger fact-recording — pure logic, no DB, no LLM.

Exercises the duck-typing that recognizes product/order/promo-shaped tool
results, including the real key-name mismatch between the two (products key
their id as "id"; get_order_details keys it as "order_id").
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from shared.grounding.ledger import (
    GroundingLedger,
    GroundingLedgerMiddleware,
    current_grounding_ledger,
    reset_grounding_ledger,
)

_PRODUCT_ID = "0fd372fa-ecb2-4db0-bb71-8628a784ced9"


def _function_context(result) -> SimpleNamespace:
    return SimpleNamespace(function=SimpleNamespace(name="some_tool"), result=result)


async def _noop() -> None:
    return None


def test_reset_grounding_ledger_sets_a_fresh_ledger() -> None:
    fresh = reset_grounding_ledger()
    assert isinstance(fresh, GroundingLedger)
    assert current_grounding_ledger.get() is fresh


@pytest.mark.asyncio
async def test_middleware_is_noop_when_ledger_unset() -> None:
    current_grounding_ledger.set(None)
    ctx = _function_context({"id": _PRODUCT_ID, "name": "X", "price": 5.0})
    await GroundingLedgerMiddleware().process(ctx, _noop)
    # Nothing to assert on — must simply not raise.


@pytest.mark.asyncio
async def test_records_single_product_result() -> None:
    ledger = reset_grounding_ledger()
    ctx = _function_context({"id": _PRODUCT_ID, "name": "Widget", "price": 19.99, "image_url": "u"})
    await GroundingLedgerMiddleware().process(ctx, _noop)

    fact = ledger.products[_PRODUCT_ID]
    assert fact.name == "Widget"
    assert fact.price == 19.99
    assert fact.image_url == "u"


@pytest.mark.asyncio
async def test_records_list_of_products() -> None:
    ledger = reset_grounding_ledger()
    ctx = _function_context([
        {"id": _PRODUCT_ID, "name": "A", "price": 1.0},
        {"id": "22222222-2222-2222-2222-222222222222", "name": "B", "price": 2.0},
    ])
    await GroundingLedgerMiddleware().process(ctx, _noop)
    assert set(ledger.products) == {_PRODUCT_ID, "22222222-2222-2222-2222-222222222222"}


@pytest.mark.asyncio
async def test_records_order_keyed_by_order_id_not_id() -> None:
    # get_order_details returns order_id, not id — this is the real
    # tool-shape mismatch the ledger's duck-typing has to bridge.
    ledger = reset_grounding_ledger()
    order_id = "1a2b3c4d-5e6f-7890-abcd-ef0123456789"
    ctx = _function_context({
        "order_id": order_id,
        "status": "shipped",
        "total": 199.99,
        "tracking_number": "TRK1",
        "items": [{"item_id": "x", "product_name": "Widget"}],
    })
    await GroundingLedgerMiddleware().process(ctx, _noop)

    fact = ledger.orders[order_id]
    assert fact.status == "shipped"
    assert fact.total == 199.99
    assert fact.tracking == "TRK1"


@pytest.mark.asyncio
async def test_order_line_items_are_not_recorded_as_products() -> None:
    # Order line items lack a product_id field entirely (only item_id), so
    # they must never be mistaken for a verifiable ProductFact.
    ledger = reset_grounding_ledger()
    ctx = _function_context({
        "order_id": "1a2b3c4d-5e6f-7890-abcd-ef0123456789",
        "status": "shipped",
        "total": 50.0,
        "items": [{"item_id": "abc", "product_name": "Widget", "unit_price": 25.0}],
    })
    await GroundingLedgerMiddleware().process(ctx, _noop)
    assert ledger.products == {}


@pytest.mark.asyncio
async def test_records_valid_coupon_as_promo() -> None:
    ledger = reset_grounding_ledger()
    ctx = _function_context({
        "valid": True,
        "code": "save10",
        "discount_type": "percentage",
        "discount_amount": 15.5,
    })
    await GroundingLedgerMiddleware().process(ctx, _noop)
    assert ledger.promos["SAVE10"].discount_amount == 15.5


@pytest.mark.asyncio
async def test_invalid_coupon_result_is_not_recorded() -> None:
    ledger = reset_grounding_ledger()
    ctx = _function_context({"valid": False, "code": "EXPIRED", "error": "Coupon has expired"})
    await GroundingLedgerMiddleware().process(ctx, _noop)
    assert ledger.promos == {}


@pytest.mark.asyncio
async def test_check_stock_result_is_not_recorded_as_product() -> None:
    # check_stock returns product_id/in_stock/total_quantity — a genuinely
    # different shape from search/detail tools, and carries no price/name,
    # so it must not be misfiled as a ProductFact.
    ledger = reset_grounding_ledger()
    ctx = _function_context({"product_id": _PRODUCT_ID, "in_stock": True, "total_quantity": 12})
    await GroundingLedgerMiddleware().process(ctx, _noop)
    assert ledger.products == {}


@pytest.mark.asyncio
async def test_error_shaped_result_is_ignored() -> None:
    ledger = reset_grounding_ledger()
    ctx = _function_context({"error": "Product not found: abc"})
    await GroundingLedgerMiddleware().process(ctx, _noop)
    assert ledger.products == {}
    assert ledger.orders == {}
