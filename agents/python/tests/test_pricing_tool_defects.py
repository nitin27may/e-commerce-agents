"""Round-one tool defects in pricing-promotions: validate_coupon cart guard, name-based bundles."""

from __future__ import annotations

import pytest

from shared.context import current_user_email


def _fn(tool_obj):
    return getattr(tool_obj, "func", tool_obj)


@pytest.mark.asyncio
@pytest.mark.parametrize("total", [0, 0.0, -5.0, float("nan")])
async def test_validate_coupon_rejects_non_positive_cart(clean_db, monkeypatch, total) -> None:
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    current_user_email.set("coupon-test@example.com")
    from pricing_promotions.tools import validate_coupon

    result = await _fn(validate_coupon)("WELCOME10", total)

    assert result["valid"] is False
    assert result["error_code"] == "invalid_cart_total"
    assert "greater than 0" in result["error"]


@pytest.mark.asyncio
async def test_validate_coupon_positive_cart_still_validated(clean_db, monkeypatch) -> None:
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    current_user_email.set("coupon-test@example.com")
    await clean_db.execute("INSERT INTO coupons (code, discount_type, discount_value) VALUES ('SAVE5', 'fixed', 5)")
    from pricing_promotions.tools import validate_coupon

    result = await _fn(validate_coupon)("save5", 20.0)

    assert result["valid"] is True
    assert result["new_total"] == 15.0
