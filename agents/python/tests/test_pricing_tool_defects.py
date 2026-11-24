"""Round-one tool defects in pricing-promotions: validate_coupon cart guard, name-based bundles."""

from __future__ import annotations

import pytest

from shared.context import current_user_email
from tests.test_promotion_rules import _product, _promotion


def _fn(tool_obj):
    return getattr(tool_obj, "func", tool_obj)


@pytest.mark.asyncio
@pytest.mark.parametrize("total", [-5.0, -0.01, float("nan"), float("inf")])
async def test_validate_coupon_rejects_a_negative_or_non_finite_cart(clean_db, monkeypatch, total) -> None:
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    current_user_email.set("coupon-test@example.com")
    from pricing_promotions.tools import validate_coupon

    result = await _fn(validate_coupon)("WELCOME10", total)

    assert result["valid"] is False
    assert result["error_code"] == "invalid_cart_total"
    assert "negative" in result["error"]


@pytest.mark.asyncio
async def test_validate_coupon_positive_cart_still_validated(clean_db, monkeypatch) -> None:
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    current_user_email.set("coupon-test@example.com")
    await clean_db.execute("INSERT INTO coupons (code, discount_type, discount_value) VALUES ('SAVE5', 'fixed', 5)")
    from pricing_promotions.tools import validate_coupon

    result = await _fn(validate_coupon)("save5", 20.0)

    assert result["valid"] is True
    assert result["new_total"] == 15.0


@pytest.mark.asyncio
async def test_bundle_eligibility_matches_seeded_names_case_insensitively(clean_db, monkeypatch) -> None:
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    sony = await _product(clean_db, "Sony WH-1000XM5", "Electronics", 300.00)
    ssd = await _product(clean_db, "Samsung T7 Shield SSD 2TB", "Electronics", 100.00)
    await _promotion(
        clean_db,
        "Tech Bundle",
        "bundle",
        {"products": ["sony wh-1000xm5", "Samsung T7 Shield SSD 2TB"], "discount_pct": 10},
    )
    from pricing_promotions.tools import check_bundle_eligibility

    result = await _fn(check_bundle_eligibility)([sony, ssd])
    bundles = result["bundle_deals"]

    assert [b["promotion_name"] for b in bundles] == ["Tech Bundle"]
    assert bundles[0]["bundle_total"] == 400.0
    assert bundles[0]["savings"] == 40.0

    partial = await _fn(check_bundle_eligibility)([sony])
    assert partial["bundle_deals"] == []
