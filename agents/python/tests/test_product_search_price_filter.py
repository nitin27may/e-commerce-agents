"""Exact-price filters in search_products: float 14.99 must match a stored NUMERIC 14.99."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from product_discovery.tools import _money, search_products
from shared.context import current_user_email


def _fn(tool_obj):
    return getattr(tool_obj, "func", tool_obj)


async def _product(db, name: str, price: str) -> str:
    product_id = uuid.uuid4()
    await db.execute(
        """INSERT INTO products (id, name, description, category, brand, price, is_active)
           VALUES ($1, $2, 'test product', 'Sports', 'test', $3, TRUE)""",
        product_id,
        name,
        Decimal(price),
    )
    return str(product_id)


def test_money_rounds_to_exact_cents() -> None:
    assert _money(14.99) == Decimal("14.99")
    assert _money(0.1 + 0.2) == Decimal("0.30")
    assert _money(15) == Decimal("15.00")


@pytest.mark.asyncio
async def test_exact_price_range_finds_the_product(clean_db, monkeypatch) -> None:
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    current_user_email.set("price-test@example.com")
    exact = await _product(clean_db, "Water Bottle 32oz", "14.99")
    await _product(clean_db, "Cheaper Bottle", "9.99")
    await _product(clean_db, "Pricier Bottle", "19.99")

    exact_only = await _fn(search_products)(min_price=14.99, max_price=14.99)
    assert [p["id"] for p in exact_only] == [exact]

    only_max = await _fn(search_products)(max_price=14.99, sort_by="price_desc")
    assert exact in [p["id"] for p in only_max]

    only_min = await _fn(search_products)(min_price=14.99, sort_by="price_asc")
    assert [p["price"] for p in only_min] == [14.99, 19.99]
