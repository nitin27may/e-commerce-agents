"""Issue #51 — promotion rules never matched the code that read them.

Filed as "optimize_cart raises ZeroDivisionError on an eval case". The crash was
real, but it was one symptom of a wider problem: `promotions.rules` is untyped
JSONB with no documented schema, and `scripts/seed.py` writes different key
names than `optimize_cart` reads. Every promotion type was affected, and only
one of the three failed loudly:

- `bundle`: seed writes `products` (names), the code read `product_ids`. So
  `required` was empty — and `all([])` is True, so every bundle matched every
  cart and then contributed £0.
- `buy_x_get_y`: seed writes `category`/`min_quantity`/`discount_pct`, the code
  read `categories`/`buy_quantity`/`free_quantity`. Both defaulted to 0, so
  `qty >= 0 + 0` was always true and the next line divided by zero — the crash.
- `flash_sale`: seed writes `categories`, the code read `product_ids`. Nothing
  ever matched: a silent no-op.

So no promotion had ever applied correctly in any environment. These tests use
the real seeded rule shapes, so they fail if the seed/code contract drifts
again.
"""

from __future__ import annotations

import json
import uuid

import pytest

from shared.context import current_user_email


async def _product(db, name: str, category: str, price: float) -> str:
    product_id = uuid.uuid4()
    await db.execute(
        """INSERT INTO products (id, name, description, category, brand, price, is_active)
           VALUES ($1, $2, 'test product', $3, 'test', $4, TRUE)""",
        product_id,
        name,
        category,
        price,
    )
    return str(product_id)


async def _promotion(db, name: str, type_: str, rules: dict) -> None:
    await db.execute(
        """INSERT INTO promotions (name, type, rules, start_date, end_date, is_active)
           VALUES ($1, $2, $3, NOW() - INTERVAL '1 day', NOW() + INTERVAL '30 days', TRUE)""",
        name,
        type_,
        json.dumps(rules),
    )


async def _optimize(db, monkeypatch, cart):
    monkeypatch.setattr("shared.db._pool", db, raising=False)
    current_user_email.set("promo-test@example.com")
    from pricing_promotions.tools import optimize_cart

    fn = getattr(optimize_cart, "func", optimize_cart)
    return await fn(cart)


@pytest.mark.asyncio
async def test_buy_x_get_y_with_seeded_rule_shape_does_not_crash(clean_db, monkeypatch) -> None:
    """The reported bug. `min_quantity` + `discount_pct` is what the seed writes.

    Before the fix this raised ZeroDivisionError("integer division or modulo by
    zero") — MAF caught it and handed the model an error result, so the turn
    completed and the eval still scored. The tool was simply never contributing.
    """
    book = await _product(clean_db, "Deep Work", "Books", 20.00)
    await _promotion(
        clean_db,
        "Buy 2 Books Get 10% Off",
        "buy_x_get_y",
        {"category": "Books", "min_quantity": 2, "discount_pct": 10},
    )

    result = await _optimize(clean_db, monkeypatch, [{"product_id": book, "quantity": 3}])

    bogo = [s for s in result["savings"] if s["type"] == "buy_x_get_y"]
    assert bogo, f"promotion did not apply: {result['savings']}"
    assert bogo[0]["amount"] == pytest.approx(6.00)  # 10% of 3 x $20


@pytest.mark.asyncio
async def test_buy_x_get_y_still_supports_real_free_units(clean_db, monkeypatch) -> None:
    """The other rule shape must keep working — this is a genuine BOGO."""
    item = await _product(clean_db, "Socks", "Clothing", 10.00)
    await _promotion(
        clean_db,
        "Buy 2 Get 1 Free",
        "buy_x_get_y",
        {"categories": ["Clothing"], "buy_quantity": 2, "free_quantity": 1},
    )

    result = await _optimize(clean_db, monkeypatch, [{"product_id": item, "quantity": 6}])

    bogo = [s for s in result["savings"] if s["type"] == "buy_x_get_y"]
    assert bogo and bogo[0]["amount"] == pytest.approx(20.00)  # two free units


@pytest.mark.asyncio
async def test_a_bundle_requiring_nothing_never_matches(clean_db, monkeypatch) -> None:
    """`all([])` is True — the trap that made every bundle fire on every cart."""
    item = await _product(clean_db, "Random Thing", "Home", 50.00)
    await _promotion(clean_db, "Malformed Bundle", "bundle", {"discount_pct": 25})

    result = await _optimize(clean_db, monkeypatch, [{"product_id": item, "quantity": 1}])

    assert not [s for s in result["savings"] if s["type"] == "bundle_promotion"]


@pytest.mark.asyncio
async def test_bundle_matches_on_product_names(clean_db, monkeypatch) -> None:
    """The seeded shape: bundles are authored by name, not by generated UUID."""
    a = await _product(clean_db, "Sony WH-1000XM5", "Electronics", 300.00)
    b = await _product(clean_db, "Samsung T7 Shield SSD 2TB", "Electronics", 100.00)
    await _promotion(
        clean_db,
        "Tech Bundle Deal",
        "bundle",
        {"products": ["Sony WH-1000XM5", "Samsung T7 Shield SSD 2TB"], "discount_pct": 10},
    )

    result = await _optimize(
        clean_db, monkeypatch, [{"product_id": a, "quantity": 1}, {"product_id": b, "quantity": 1}]
    )

    bundle = [s for s in result["savings"] if s["type"] == "bundle_promotion"]
    assert bundle and bundle[0]["amount"] == pytest.approx(40.00)  # 10% of $400


@pytest.mark.asyncio
async def test_flash_sale_scoped_by_category_applies(clean_db, monkeypatch) -> None:
    """Previously a silent no-op: the seed scopes by category, the code read ids."""
    shirt = await _product(clean_db, "Shirt", "Clothing", 80.00)
    book = await _product(clean_db, "A Book", "Books", 20.00)
    await _promotion(
        clean_db,
        "Spring Flash Sale",
        "flash_sale",
        {"categories": ["Clothing", "Sports"], "discount_pct": 15},
    )

    result = await _optimize(
        clean_db, monkeypatch, [{"product_id": shirt, "quantity": 1}, {"product_id": book, "quantity": 1}]
    )

    flash = [s for s in result["savings"] if s["type"] == "flash_sale"]
    assert len(flash) == 1, "the flash sale must not apply to the out-of-category item"
    assert flash[0]["amount"] == pytest.approx(12.00)  # 15% of $80


@pytest.mark.asyncio
async def test_malformed_rules_are_skipped_not_guessed_at(clean_db, monkeypatch) -> None:
    """Unparseable rules must contribute nothing, and must never raise.

    `rules` is untyped JSONB written by hand, so a typo is a data error rather
    than an exception — but it must not silently become a discount either.
    """
    item = await _product(clean_db, "Widget", "Home", 100.00)
    await _promotion(clean_db, "Nonsense BOGO", "buy_x_get_y", {"category": "Home"})
    await _promotion(clean_db, "Negative Sale", "flash_sale", {"categories": ["Home"], "discount_pct": -50})
    await _promotion(clean_db, "Impossible Sale", "flash_sale", {"categories": ["Home"], "discount_pct": 900})

    result = await _optimize(clean_db, monkeypatch, [{"product_id": item, "quantity": 5}])

    assert result["total_savings"] == 0
    assert result["final_total"] == pytest.approx(500.00)
