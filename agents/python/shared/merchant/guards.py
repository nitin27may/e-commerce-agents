"""Pure guards for merchant price changes and restock lines (#93, step M-1 -- groundwork).

Every function here takes the proposed change plus the facts it needs and returns a
:class:`GuardResult`. None of them reads the database, the clock or the environment: the facts come
from :mod:`shared.merchant.facts` (or a test) and the limits from :mod:`shared.merchant.limits`, so
each boundary is unit-testable. Nothing calls them yet -- no agent, route or UI proposes a price
change in this step.

A guard never raises on a bad proposal and never short-circuits: it collects *every* reason that
applies, in the fixed order of :class:`ReasonCode`, plus the numbers it used, so a reviewer can see
exactly why a line was blocked.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from shared.merchant.limits import MerchantLimits

_HUNDRED = Decimal(100)


class ReasonCode(StrEnum):
    """Fixed reason codes. They are stored in ``proposal_lines.guard_result`` -- never rename one."""

    INVALID_PRICE = "invalid_price"
    MISSING_COST = "missing_cost"
    MARGIN_BELOW_FLOOR = "margin_below_floor"
    DAILY_CHANGE_CAP_EXCEEDED = "daily_change_cap_exceeded"
    ACTIVE_PROMOTION = "active_promotion"
    INVALID_QUANTITY = "invalid_quantity"
    BATCH_TOO_LARGE = "batch_too_large"
    RESTOCK_QUANTITY_EXCEEDED = "restock_quantity_exceeded"
    RESTOCK_VALUE_EXCEEDED = "restock_value_exceeded"
    #: A reviewer's value is past what was proposed (a restock raised, a price deeper or above where it started).
    EDIT_BEYOND_PROPOSAL = "edit_beyond_proposal"
    #: The product's price is no longer the one the line was proposed against.
    PRICE_MOVED = "price_moved"
    #: A restock for the product is already on order, so this line would order the same stock twice.
    RESTOCK_ALREADY_SCHEDULED = "restock_already_scheduled"


#: The order reasons are reported in, and so the order of ``GuardResult.reasons``.
_ORDER = {code: index for index, code in enumerate(ReasonCode)}


@dataclass(frozen=True)
class PromotionFact:
    """A promotion that covers the product, as stored. Whether it is *active* is the guard's call."""

    id: str
    name: str
    starts_at: datetime
    ends_at: datetime
    is_active: bool = True

    def is_active_at(self, now: datetime) -> bool:
        return self.is_active and self.starts_at <= now <= self.ends_at


@dataclass(frozen=True)
class GuardResult:
    """Verdict for one line: ``ok``, or blocked with ``reasons`` and the ``numbers`` that decided it."""

    ok: bool
    reasons: tuple[ReasonCode, ...]
    numbers: dict[str, Decimal | int | str | None] = field(default_factory=dict)
    policy_version: str = ""

    @property
    def blocked(self) -> bool:
        return not self.ok

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe form for ``proposal_lines.guard_result`` (decimals become strings)."""
        return {
            "ok": self.ok,
            "reasons": [reason.value for reason in self.reasons],
            "numbers": {key: None if val is None else str(val) for key, val in self.numbers.items()},
            "policy_version": self.policy_version,
        }


def _result(reasons: set[ReasonCode], numbers: dict[str, Any], limits: MerchantLimits) -> GuardResult:
    ordered = tuple(sorted(reasons, key=_ORDER.__getitem__))
    return GuardResult(ok=not ordered, reasons=ordered, numbers=numbers, policy_version=limits.policy_version)


def margin_pct(price: Decimal, unit_cost: Decimal) -> Decimal:
    """``(price - unit_cost) / price * 100``. ``price`` must be positive."""
    return (price - unit_cost) / price * _HUNDRED


def change_pct(old: Decimal, new: Decimal) -> Decimal:
    """``|new - old| / old * 100``. ``old`` must be positive."""
    return abs(new - old) / old * _HUNDRED


def check_price_change(
    *,
    current_price: Decimal,
    proposed_price: Decimal,
    unit_cost: Decimal | None,
    prior_changes_today: Sequence[Decimal],
    promotions: Sequence[PromotionFact],
    now: datetime,
    limits: MerchantLimits,
) -> GuardResult:
    """Judge one proposed price.

    ``prior_changes_today`` is the size, in percent, of each price change already recorded for the
    product today (see :func:`shared.merchant.facts.daily_change_pcts`); the proposed change is added
    to their sum and the total must not pass ``max_change_pct_per_day``. ``promotions`` are the
    promotions covering the product; only those active at ``now`` count.

    A price that is not positive is reported as ``INVALID_PRICE`` alone: the numbers that follow
    from it (margin, percentage) would be meaningless.
    """
    numbers: dict[str, Any] = {
        "current_price": current_price,
        "proposed_price": proposed_price,
        "unit_cost": unit_cost,
        "margin_floor_pct": limits.margin_floor_pct,
        "max_change_pct_per_day": limits.max_change_pct_per_day,
    }
    if proposed_price <= 0 or current_price <= 0:
        return _result({ReasonCode.INVALID_PRICE}, numbers, limits)

    reasons: set[ReasonCode] = set()

    if unit_cost is None or unit_cost < 0:
        reasons.add(ReasonCode.MISSING_COST)
        numbers["margin_pct"] = None
    else:
        margin = margin_pct(proposed_price, unit_cost)
        numbers["margin_pct"] = margin
        if margin < limits.margin_floor_pct:
            reasons.add(ReasonCode.MARGIN_BELOW_FLOOR)

    this_change = change_pct(current_price, proposed_price)
    total_change = sum((abs(pct) for pct in prior_changes_today), Decimal(0)) + this_change
    numbers["this_change_pct"] = this_change
    numbers["total_change_pct_today"] = total_change
    if total_change > limits.max_change_pct_per_day:
        reasons.add(ReasonCode.DAILY_CHANGE_CAP_EXCEEDED)

    active = [promo for promo in promotions if promo.is_active_at(now)]
    numbers["active_promotions"] = len(active)
    if limits.block_during_active_promotion and active:
        reasons.add(ReasonCode.ACTIVE_PROMOTION)

    return _result(reasons, numbers, limits)


def check_restock_line(*, quantity: int, unit_cost: Decimal | None, limits: MerchantLimits) -> GuardResult:
    """Judge one restock line: a positive whole quantity of a product whose cost is known, within the limits.

    The cost is required because a restock commits money at that cost; with none recorded the line
    cannot be valued and is blocked (``MISSING_COST``) rather than assumed free. The quantity may not pass
    ``max_restock_quantity`` and ``quantity x unit_cost`` may not pass ``max_restock_order_value``.
    """
    numbers: dict[str, Any] = {
        "quantity": quantity,
        "unit_cost": unit_cost,
        "max_restock_quantity": limits.max_restock_quantity,
        "max_restock_order_value": limits.max_restock_order_value,
    }
    reasons: set[ReasonCode] = set()
    valid_quantity = not isinstance(quantity, bool) and isinstance(quantity, int) and quantity > 0
    if not valid_quantity:
        reasons.add(ReasonCode.INVALID_QUANTITY)
    elif quantity > limits.max_restock_quantity:
        reasons.add(ReasonCode.RESTOCK_QUANTITY_EXCEEDED)
    if unit_cost is None or unit_cost < 0:
        reasons.add(ReasonCode.MISSING_COST)
        numbers["order_value"] = None
    elif valid_quantity:
        order_value = unit_cost * quantity
        numbers["order_value"] = order_value
        if order_value > limits.max_restock_order_value:
            reasons.add(ReasonCode.RESTOCK_VALUE_EXCEEDED)
    return _result(reasons, numbers, limits)


def check_edit_bounds(
    *,
    field: str,
    current_value: Decimal | None,
    proposed_value: Decimal,
    final_value: Decimal,
    limits: MerchantLimits,
) -> GuardResult:
    """A reviewer's value may never raise a line past what the system proposed.

    A restock quantity may be lowered, never raised. A price must stay between the proposed price and the price
    it started from (``current_value``): a markdown may be shallower than proposed, never deeper, and never above
    where it began. With no recorded starting price only the proposed price bounds it.
    """
    numbers: dict[str, Any] = {
        "current_value": current_value,
        "proposed_value": proposed_value,
        "final_value": final_value,
    }
    if field == "restock_quantity":
        beyond = final_value > proposed_value
    else:
        anchor = proposed_value if current_value is None else current_value
        low, high = min(anchor, proposed_value), max(anchor, proposed_value)
        beyond = not low <= final_value <= high
    return _result({ReasonCode.EDIT_BEYOND_PROPOSAL} if beyond else set(), numbers, limits)


def check_batch(*, line_count: int, limits: MerchantLimits) -> GuardResult:
    """Judge a whole batch: it may hold at most ``max_lines_per_batch`` lines."""
    numbers: dict[str, Any] = {"line_count": line_count, "max_lines_per_batch": limits.max_lines_per_batch}
    reasons = {ReasonCode.BATCH_TOO_LARGE} if line_count > limits.max_lines_per_batch else set()
    return _result(reasons, numbers, limits)
