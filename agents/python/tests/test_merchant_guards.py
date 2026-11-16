"""M-1 (#93) -- the merchant guards and the limits file: every boundary, no database.

Numbers used throughout: limits are the shipped ones unless a test builds its own -- margin floor 15 %,
10 % a day, promotions block, 25 lines a batch.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from shared.merchant import (
    GuardResult,
    MerchantLimits,
    MerchantLimitsError,
    PromotionFact,
    ReasonCode,
    check_batch,
    check_price_change,
    check_restock_line,
    get_limits,
    load_limits,
)
from shared.merchant.facts import daily_change_pcts, utc_day_start
from shared.merchant.limits import LIMITS_PATH, parse_limits

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
D = Decimal

LIMITS = MerchantLimits(
    policy_version="test.1",
    margin_floor_pct=D("15"),
    max_change_pct_per_day=D("10"),
    block_during_active_promotion=True,
    max_lines_per_batch=25,
    max_restock_quantity=5000,
    max_restock_order_value=D("50000"),
)


def _price(
    proposed: str,
    *,
    current: str = "100.00",
    cost: str | None = "50.00",
    prior: tuple[str, ...] = (),
    promotions: tuple[PromotionFact, ...] = (),
    limits: MerchantLimits = LIMITS,
) -> GuardResult:
    return check_price_change(
        current_price=D(current),
        proposed_price=D(proposed),
        unit_cost=None if cost is None else D(cost),
        prior_changes_today=tuple(D(p) for p in prior),
        promotions=promotions,
        now=NOW,
        limits=limits,
    )


def _promo(*, starts: timedelta, ends: timedelta, is_active: bool = True) -> PromotionFact:
    return PromotionFact(id="p1", name="Sale", starts_at=NOW + starts, ends_at=NOW + ends, is_active=is_active)


# ── the happy path and the result shape ───────────────────────────────────────


def test_an_ordinary_change_is_ok_and_reports_the_numbers_it_used() -> None:
    result = _price("95.00")
    assert result.ok and not result.blocked
    assert result.reasons == ()
    assert result.policy_version == "test.1"
    assert result.numbers["margin_pct"] == D("47.36842105263157894736842105")
    assert result.numbers["this_change_pct"] == D("5")
    assert result.numbers["total_change_pct_today"] == D("5")


def test_to_dict_is_json_safe() -> None:
    import json

    payload = _price("50.00", cost=None).to_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert payload["ok"] is False
    assert payload["reasons"] == ["missing_cost", "daily_change_cap_exceeded"]
    assert payload["numbers"]["unit_cost"] is None
    assert payload["policy_version"] == "test.1"


def test_reason_codes_are_fixed() -> None:
    assert {code.value for code in ReasonCode} == {
        "invalid_price",
        "missing_cost",
        "margin_below_floor",
        "daily_change_cap_exceeded",
        "active_promotion",
        "invalid_quantity",
        "batch_too_large",
        "restock_quantity_exceeded",
        "restock_value_exceeded",
        "edit_beyond_proposal",
        "price_moved",
        "restock_already_scheduled",
    }


# ── margin floor ──────────────────────────────────────────────────────────────


def test_margin_exactly_at_the_floor_is_allowed() -> None:
    # 100 -> 94.12 is not exact; use a price where (p - cost) / p is exactly 15 %: cost 85, price 100.
    result = _price("100.00", current="100.00", cost="85.00")
    assert result.numbers["margin_pct"] == D("15")
    assert result.ok


def test_margin_a_cent_below_the_floor_is_blocked() -> None:
    result = _price("100.00", current="100.00", cost="85.01")
    assert result.reasons == (ReasonCode.MARGIN_BELOW_FLOOR,)
    assert result.blocked


def test_a_price_below_cost_is_blocked_on_margin() -> None:
    result = _price("95.00", cost="96.00")
    assert ReasonCode.MARGIN_BELOW_FLOOR in result.reasons
    assert result.numbers["margin_pct"] < 0


def test_the_floor_follows_the_limits_given() -> None:
    strict = MerchantLimits("t", D("50"), D("10"), True, 25, 5000, D("50000"))
    assert _price("100.00", cost="50.00", limits=strict).ok
    assert _price("100.00", cost="50.01", limits=strict).reasons == (ReasonCode.MARGIN_BELOW_FLOOR,)


def test_missing_cost_is_blocked_never_waved_through() -> None:
    result = _price("95.00", cost=None)
    assert result.reasons == (ReasonCode.MISSING_COST,)
    assert result.numbers["margin_pct"] is None


def test_a_zero_cost_is_a_known_cost() -> None:
    assert _price("95.00", cost="0.00").ok


# ── daily cap ─────────────────────────────────────────────────────────────────


def test_a_change_exactly_at_the_daily_cap_is_allowed() -> None:
    assert _price("90.00").ok
    assert _price("110.00").ok  # an increase counts the same as a decrease


def test_a_change_just_past_the_daily_cap_is_blocked() -> None:
    assert _price("89.99").reasons == (ReasonCode.DAILY_CHANGE_CAP_EXCEEDED,)
    assert _price("110.01").reasons == (ReasonCode.DAILY_CHANGE_CAP_EXCEEDED,)


def test_prior_changes_today_accumulate_toward_the_cap() -> None:
    # 4 % + 3 % already today; a further 3 % lands exactly on 10 %, a further 3.01 % does not.
    assert _price("97.00", prior=("4", "3")).ok
    over = _price("96.99", prior=("4", "3"))
    assert over.reasons == (ReasonCode.DAILY_CHANGE_CAP_EXCEEDED,)
    assert over.numbers["total_change_pct_today"] == D("10.01")


def test_prior_changes_count_by_size_not_direction() -> None:
    assert _price("97.00", prior=("-4", "3")).ok
    assert _price("96.00", prior=("-4", "3")).reasons == (ReasonCode.DAILY_CHANGE_CAP_EXCEEDED,)


def test_an_unchanged_price_uses_none_of_the_cap() -> None:
    result = _price("100.00", prior=("10",))
    assert result.ok and result.numbers["this_change_pct"] == D("0")


# ── promotions ────────────────────────────────────────────────────────────────


def test_an_active_promotion_blocks_a_price_change() -> None:
    promo = _promo(starts=-timedelta(days=1), ends=timedelta(days=1))
    assert _price("95.00", promotions=(promo,)).reasons == (ReasonCode.ACTIVE_PROMOTION,)


def test_an_expired_promotion_does_not() -> None:
    promo = _promo(starts=-timedelta(days=5), ends=-timedelta(seconds=1))
    assert _price("95.00", promotions=(promo,)).ok


def test_a_promotion_that_has_not_started_does_not() -> None:
    promo = _promo(starts=timedelta(seconds=1), ends=timedelta(days=5))
    assert _price("95.00", promotions=(promo,)).ok


def test_promotion_window_boundaries_are_inclusive() -> None:
    assert _price("95.00", promotions=(_promo(starts=timedelta(0), ends=timedelta(days=1)),)).blocked
    assert _price("95.00", promotions=(_promo(starts=-timedelta(days=1), ends=timedelta(0)),)).blocked


def test_a_switched_off_promotion_does_not_count_even_inside_its_dates() -> None:
    promo = _promo(starts=-timedelta(days=1), ends=timedelta(days=1), is_active=False)
    assert _price("95.00", promotions=(promo,)).ok


def test_the_promotion_rule_can_be_turned_off() -> None:
    relaxed = MerchantLimits("t", D("15"), D("10"), False, 25, 5000, D("50000"))
    promo = _promo(starts=-timedelta(days=1), ends=timedelta(days=1))
    assert _price("95.00", promotions=(promo,), limits=relaxed).ok


def test_one_active_among_expired_still_blocks() -> None:
    expired = _promo(starts=-timedelta(days=9), ends=-timedelta(days=2))
    live = _promo(starts=-timedelta(days=1), ends=timedelta(days=1))
    result = _price("95.00", promotions=(expired, live))
    assert result.reasons == (ReasonCode.ACTIVE_PROMOTION,)
    assert result.numbers["active_promotions"] == 1


# ── several reasons at once, invalid prices ───────────────────────────────────


def test_every_applicable_reason_is_reported_in_the_fixed_order() -> None:
    promo = _promo(starts=-timedelta(days=1), ends=timedelta(days=1))
    result = _price("60.00", cost="59.00", promotions=(promo,))
    assert result.reasons == (
        ReasonCode.MARGIN_BELOW_FLOOR,
        ReasonCode.DAILY_CHANGE_CAP_EXCEEDED,
        ReasonCode.ACTIVE_PROMOTION,
    )


@pytest.mark.parametrize("proposed", ["0.00", "-1.00"])
def test_a_non_positive_proposed_price_is_invalid_and_nothing_else(proposed: str) -> None:
    result = _price(proposed, cost=None)
    assert result.reasons == (ReasonCode.INVALID_PRICE,)


def test_a_non_positive_current_price_is_invalid() -> None:
    assert _price("5.00", current="0.00").reasons == (ReasonCode.INVALID_PRICE,)


# ── restock lines ─────────────────────────────────────────────────────────────


def test_a_restock_with_a_positive_quantity_and_a_cost_is_ok() -> None:
    assert check_restock_line(quantity=1, unit_cost=D("3.20"), limits=LIMITS).ok


@pytest.mark.parametrize("quantity", [0, -5])
def test_a_restock_quantity_must_be_positive(quantity: int) -> None:
    result = check_restock_line(quantity=quantity, unit_cost=D("3.20"), limits=LIMITS)
    assert result.reasons == (ReasonCode.INVALID_QUANTITY,)


def test_a_restock_quantity_must_be_an_integer_not_a_bool() -> None:
    result = check_restock_line(quantity=True, unit_cost=D("3.20"), limits=LIMITS)
    assert result.reasons == (ReasonCode.INVALID_QUANTITY,)


def test_a_restock_with_no_cost_is_blocked() -> None:
    assert check_restock_line(quantity=10, unit_cost=None, limits=LIMITS).reasons == (ReasonCode.MISSING_COST,)


def test_a_restock_can_be_blocked_twice() -> None:
    result = check_restock_line(quantity=0, unit_cost=None, limits=LIMITS)
    assert result.reasons == (ReasonCode.MISSING_COST, ReasonCode.INVALID_QUANTITY)


# ── batch size ────────────────────────────────────────────────────────────────


def test_a_batch_exactly_at_the_limit_is_ok_and_one_over_is_blocked() -> None:
    assert check_batch(line_count=25, limits=LIMITS).ok
    over = check_batch(line_count=26, limits=LIMITS)
    assert over.reasons == (ReasonCode.BATCH_TOO_LARGE,)
    assert over.numbers == {"line_count": 26, "max_lines_per_batch": 25}


def test_an_empty_batch_is_not_too_large() -> None:
    assert check_batch(line_count=0, limits=LIMITS).ok


# ── today's price changes, from price_history rows ────────────────────────────


def test_daily_change_pcts_compares_each_price_with_the_one_before() -> None:
    steps = daily_change_pcts(D("100"), [D("96"), D("96"), D("98.88")])
    # 100 -> 96 is 4 %, 96 -> 96 is no change, 96 -> 98.88 is 3 %.
    assert steps == (D("4"), D("3"))


def test_daily_change_pcts_without_an_opening_price_ignores_the_first_row() -> None:
    assert daily_change_pcts(None, [D("100"), D("95")]) == (D("5"),)
    assert daily_change_pcts(None, []) == ()
    assert daily_change_pcts(D("100"), []) == ()


def test_the_day_boundary_is_midnight_utc() -> None:
    assert utc_day_start(datetime(2026, 11, 16, 23, 59, 59, tzinfo=UTC)) == datetime(2026, 11, 16, tzinfo=UTC)
    # 01:00 on the 17th in UTC+2 is still the 16th in UTC.
    plus_two = datetime(2026, 11, 17, 1, 0, tzinfo=timezone(timedelta(hours=2)))
    assert utc_day_start(plus_two) == datetime(2026, 11, 16, tzinfo=UTC)


# ── the limits file ───────────────────────────────────────────────────────────

_VALID = {
    "policy_version": "v1",
    "margin_floor_pct": 15,
    "max_change_pct_per_day": 10,
    "block_during_active_promotion": True,
    "max_lines_per_batch": 25,
    "max_restock_quantity": 5000,
    "max_restock_order_value": 50000,
}


def test_the_shipped_limits_file_loads_and_is_cached() -> None:
    limits = load_limits()
    assert limits.policy_version
    assert limits.margin_floor_pct == D("15")
    assert limits.max_change_pct_per_day == D("10")
    assert limits.block_during_active_promotion is True
    assert limits.max_lines_per_batch == 25
    assert limits.max_restock_quantity == 5000
    assert limits.max_restock_order_value == D("50000")
    assert get_limits() is get_limits()
    assert LIMITS_PATH.name == "merchant_limits.yaml"


def test_a_valid_document_parses() -> None:
    assert parse_limits(dict(_VALID)).max_lines_per_batch == 25
    assert parse_limits({**_VALID, "margin_floor_pct": 12.5}).margin_floor_pct == D("12.5")


@pytest.mark.parametrize(
    "override",
    [
        {"margin_floor_pct": -1},
        {"margin_floor_pct": 100},
        {"margin_floor_pct": "15"},
        {"margin_floor_pct": True},
        {"max_change_pct_per_day": 0},
        {"max_change_pct_per_day": 101},
        {"max_change_pct_per_day": float("nan")},
        {"block_during_active_promotion": "yes"},
        {"block_during_active_promotion": 1},
        {"max_restock_quantity": 0},
        {"max_restock_quantity": 2_147_483_648},
        {"max_restock_quantity": 1.5},
        {"max_restock_quantity": True},
        {"max_restock_order_value": 0},
        {"max_restock_order_value": 10_000_000_000},
        {"max_restock_order_value": "lots"},
        {"max_lines_per_batch": 0},
        {"max_lines_per_batch": 2.5},
        {"max_lines_per_batch": True},
        {"policy_version": ""},
        {"policy_version": 3},
    ],
)
def test_an_out_of_range_or_mistyped_value_fails_loudly(override: dict[str, object]) -> None:
    with pytest.raises(MerchantLimitsError):
        parse_limits({**_VALID, **override})


def test_unknown_and_missing_keys_fail_loudly() -> None:
    with pytest.raises(MerchantLimitsError, match="unknown"):
        parse_limits({**_VALID, "margin_floor": 1})
    missing = dict(_VALID)
    del missing["max_lines_per_batch"]
    with pytest.raises(MerchantLimitsError, match="missing"):
        parse_limits(missing)
    with pytest.raises(MerchantLimitsError):
        parse_limits(["not", "a", "mapping"])


def test_a_missing_or_unparseable_file_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(MerchantLimitsError, match="cannot read"):
        load_limits(tmp_path / "nope.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("margin_floor_pct: [unclosed\n", encoding="utf-8")
    with pytest.raises(MerchantLimitsError, match="cannot read"):
        load_limits(bad)
