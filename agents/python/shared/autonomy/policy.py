"""Load and validate ``config/policies/autonomy.yaml``, and decide a tier / check a budget (pure, no database).

Same stance as the approval policy loader: the file is validated whole at load time and fails loudly
(:class:`AutonomyPolicyError`) on anything it does not recognise -- an unknown key, fact, operator or tier, a rule
naming a budget that does not exist, a duplicate rule id -- and on anything that could let the system act without
a bound:

* the last rule of an action is unconditional **and** ``needs_approval`` (the default is to ask, never to act);
* every ``auto`` / ``auto_notify`` rule carries an ``amount lte N`` condition, and N is at most the budget's
  ``per_action_max_amount``;
* every cap is a positive number.

Money is :class:`~decimal.Decimal` throughout; a number in the file with more than two decimal places is refused
rather than rounded.

:func:`evaluate_tier` and :func:`check_budget` are pure: they read nothing and can be run again later on stored
inputs with the same result. A fact that is ``None`` (could not be determined) never satisfies a condition.
"""

from __future__ import annotations

import functools
import math
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import yaml

POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "policies" / "autonomy.yaml"

OPERATORS = frozenset({"lt", "lte", "gt", "gte", "eq"})
TIERS = frozenset({"auto", "auto_notify", "needs_approval", "no_action"})
#: ``no_action`` is a tier only a risk review may name (a low-risk order is simply left alone); a money or notice rule
#: that "did nothing" is a rule the engine writes itself, never one the policy file picks.
RISK_ONLY_TIERS = frozenset({"no_action"})
#: Tiers that act without a person; they are the ones that draw on a budget.
AUTONOMOUS_TIERS = frozenset({"auto", "auto_notify"})
#: Every fact a rule may test. All are numbers.
FACTS = frozenset(
    {
        "amount",
        "delay_days",
        "order_total",
        "days_since_purchase",
        "drop_percent",
        "recipients",
        # order.risk_flagged: the signals read from the order, and the score they add up to.
        "account_age_days",
        "address_mismatch",
        "items_count",
        "orders_in_window",
        "risk_score",
    }
)
#: What an action does, and so how an event is sized: ``credit`` (a store credit sized by a shipment delay),
#: ``price_adjustment`` (a store credit for a price drop on a recent purchase) and ``notice`` (a notification only,
#: no money, no budget).
#: ``damage_credit`` is a remedy for a customer-reported damaged item (a store credit, or a replacement offer: money
#: either way, so it has a budget) and ``risk_review`` scores an order and may hold it for a person (no money).
KINDS = frozenset({"credit", "price_adjustment", "notice", "damage_credit", "risk_review"})
MONEY_KINDS = frozenset({"credit", "price_adjustment", "damage_credit"})
#: A risk score is capped here so a pile of signals cannot make the number meaningless.
MAX_RISK_SCORE = 100

_CENT = Decimal("0.01")
#: Money columns are NUMERIC(12, 2).
_MAX_MONEY = Decimal("9999999999.99")

_TOP_KEYS = {"policy_version", "budgets", "actions", "events"}
_BUDGET_KEYS = {
    "per_action_max_amount",
    "per_customer_per_day",
    "per_order_max_count",
    "global_per_day",
    "global_per_month",
}
_BUDGET_REQUIRED = _BUDGET_KEYS - {"global_per_month"}
_CAP_KEYS = {"max_count", "max_amount"}
_ACTION_KEYS = {"budget", "reversible", "rules", "kind"}
_ACTION_REQUIRED = {"reversible", "rules"}
_RULE_KEYS = {"id", "tier", "reason", "when", "hold"}
_RULE_REQUIRED = {"id", "tier", "reason", "when"}
_CONDITION_KEYS = {"fact", "op", "value"}
_EVENT_KEYS = {"action", "eligible_order_statuses", "credit_tiers"}
_PRICE_EVENT_KEYS = {"action", "eligible_order_statuses", "lookback_days", "max_lines"}
_NOTICE_EVENT_KEYS = {"action", "cart_max_age_days", "max_recipients", "per_customer_per_day"}
_DAMAGE_EVENT_KEYS = {
    "action",
    "eligible_order_statuses",
    "lookback_days",
    "credit_percent",
    "max_credit",
    "replacement_max_line_value",
}
_RISK_EVENT_KEYS = {"action", "eligible_order_statuses", "velocity_window_hours", "signals"}
_SIGNAL_KEYS = {"id", "fact", "op", "value", "points"}
_CREDIT_TIER_KEYS = {"min_delay_days", "amount"}


class AutonomyPolicyError(ValueError):
    """The autonomy policy is missing, unreadable or invalid, or an action / event asked of it is unknown."""


@dataclass(frozen=True)
class Cap:
    max_count: int
    max_amount: Decimal


@dataclass(frozen=True)
class Budget:
    key: str
    per_action_max_amount: Decimal
    per_customer_per_day: Cap
    per_order_max_count: int
    global_per_day: Cap
    #: Optional: every customer, one UTC calendar month. Checked in the same transaction, under the same lock helper,
    #: as the day cap. None means the budget has no monthly limit.
    global_per_month: Cap | None = None


@dataclass(frozen=True)
class Condition:
    fact: str
    op: str
    value: Decimal


@dataclass(frozen=True)
class Rule:
    id: str
    tier: str
    reason: str
    when: tuple[Condition, ...]
    #: A risk review only: the order is held (reversibly) while the approval waits. Never on an autonomous tier.
    hold: bool = False


@dataclass(frozen=True)
class Action:
    name: str
    budget: str | None  # None for a ``notice``: it spends no money, so it has no budget
    reversible: bool
    rules: tuple[Rule, ...]
    kind: str = "credit"


@dataclass(frozen=True)
class CreditTier:
    min_delay_days: int
    amount: Decimal


@dataclass(frozen=True)
class Signal:
    """One thing about an order that adds ``points`` to its risk score when ``fact op value`` holds."""

    id: str
    condition: Condition
    points: int


@dataclass(frozen=True)
class EventRule:
    topic: str
    action: str
    eligible_order_statuses: frozenset[str]
    credit_tiers: tuple[CreditTier, ...]
    # price_adjustment events: purchases newer than ``lookback_days`` qualify, at most ``max_lines`` lines per event.
    lookback_days: int = 0
    max_lines: int = 0
    # notice events: carts touched within ``cart_max_age_days``, at most ``max_recipients`` per event and
    # ``per_customer_per_day`` notices to one customer per UTC day.
    cart_max_age_days: int = 0
    max_recipients: int = 0
    per_customer_per_day: int = 0
    # damage events: reports newer than ``lookback_days`` qualify. The remedy is ``credit_percent`` of the damaged
    # quantity's value up to ``max_credit``, or a free replacement when the damaged quantity is worth at most
    # ``replacement_max_line_value`` and is in stock.
    credit_percent: Decimal = Decimal(0)
    max_credit: Decimal = Decimal(0)
    replacement_max_line_value: Decimal = Decimal(0)
    # risk events: orders by the same customer within ``velocity_window_hours`` count towards velocity; ``signals``
    # add up to the score.
    velocity_window_hours: int = 0
    signals: tuple[Signal, ...] = ()

    def credit_for(self, delay_days: int) -> Decimal | None:
        """The credit for a delay: the amount of the highest tier the delay reaches, or None below the lowest."""
        earned = [t for t in self.credit_tiers if delay_days >= t.min_delay_days]
        return max(earned, key=lambda t: t.min_delay_days).amount if earned else None


@dataclass(frozen=True)
class AutonomyPolicy:
    version: str
    budgets: dict[str, Budget]
    actions: dict[str, Action]
    events: dict[str, EventRule]

    def action(self, name: str) -> Action:
        try:
            return self.actions[name]
        except KeyError:
            raise AutonomyPolicyError(f"no autonomy action {name!r}") from None

    def budget(self, key: str) -> Budget:
        try:
            return self.budgets[key]
        except KeyError:
            raise AutonomyPolicyError(f"no autonomy budget {key!r}") from None

    def event(self, topic: str) -> EventRule | None:
        return self.events.get(topic)


# ── validation ───────────────────────────────────────────────────────────────


def _mapping(value: Any, where: str, allowed: set[str], *, required: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AutonomyPolicyError(f"{where}: expected a mapping")
    unknown = set(value) - allowed
    if unknown:
        raise AutonomyPolicyError(f"{where}: unknown key(s) {sorted(map(str, unknown))}")
    missing = (allowed if required is None else required) - set(value)
    if missing:
        raise AutonomyPolicyError(f"{where}: missing {sorted(missing)}")
    return value


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _decimal(value: Any, where: str) -> Decimal:
    """A YAML number as an exact Decimal; more than two decimal places is an error, never a silent round."""
    if not _is_number(value):
        raise AutonomyPolicyError(f"{where}: expected a number, got {value!r}")
    number = Decimal(str(value))
    if number != number.quantize(_CENT):
        raise AutonomyPolicyError(f"{where}: at most two decimal places, got {value!r}")
    return number.quantize(_CENT)


def _money(value: Any, where: str) -> Decimal:
    number = _decimal(value, where)
    if not Decimal(0) < number <= _MAX_MONEY:
        raise AutonomyPolicyError(f"{where}: must be above 0 and at most {_MAX_MONEY}, got {value!r}")
    return number


def _count(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise AutonomyPolicyError(f"{where}: expected an integer of at least 1, got {value!r}")
    return value


def _cap(raw: Any, where: str) -> Cap:
    raw = _mapping(raw, where, _CAP_KEYS)
    return Cap(_count(raw["max_count"], f"{where}.max_count"), _money(raw["max_amount"], f"{where}.max_amount"))


def _budget(key: str, raw: Any) -> Budget:
    where = f"budgets.{key}"
    raw = _mapping(raw, where, _BUDGET_KEYS, required=_BUDGET_REQUIRED)
    per_action = _money(raw["per_action_max_amount"], f"{where}.per_action_max_amount")
    customer = _cap(raw["per_customer_per_day"], f"{where}.per_customer_per_day")
    glob = _cap(raw["global_per_day"], f"{where}.global_per_day")
    if customer.max_amount < per_action:
        raise AutonomyPolicyError(f"{where}: per_customer_per_day.max_amount is below per_action_max_amount")
    if glob.max_amount < customer.max_amount:
        raise AutonomyPolicyError(f"{where}: global_per_day.max_amount is below the per-customer cap")
    month: Cap | None = None
    if "global_per_month" in raw:
        month = _cap(raw["global_per_month"], f"{where}.global_per_month")
        if month.max_amount < glob.max_amount or month.max_count < glob.max_count:
            raise AutonomyPolicyError(f"{where}: global_per_month is below global_per_day")
    return Budget(
        key, per_action, customer, _count(raw["per_order_max_count"], f"{where}.per_order_max_count"), glob, month
    )


def _condition(raw: Any, where: str) -> Condition:
    raw = _mapping(raw, where, _CONDITION_KEYS)
    if raw["fact"] not in FACTS:
        raise AutonomyPolicyError(f"{where}: unknown fact {raw['fact']!r}")
    if raw["op"] not in OPERATORS:
        raise AutonomyPolicyError(f"{where}: unknown operator {raw['op']!r}")
    return Condition(raw["fact"], raw["op"], _decimal(raw["value"], f"{where}.value"))


def _rule(raw: Any, where: str) -> Rule:
    raw = _mapping(raw, where, _RULE_KEYS, required=_RULE_REQUIRED)
    for key in ("id", "reason"):
        if not isinstance(raw[key], str) or not raw[key].strip():
            raise AutonomyPolicyError(f"{where}: {key!r} must be a non-empty string")
    if raw["tier"] not in TIERS:
        raise AutonomyPolicyError(f"{where}: unknown tier {raw['tier']!r}")
    if not isinstance(raw["when"], list):
        raise AutonomyPolicyError(f"{where}: 'when' must be a list (empty for an unconditional rule)")
    hold = raw.get("hold", False)
    if not isinstance(hold, bool):
        raise AutonomyPolicyError(f"{where}: 'hold' must be true or false")
    return Rule(
        raw["id"],
        raw["tier"],
        raw["reason"],
        tuple(_condition(c, f"{where}.when[{i}]") for i, c in enumerate(raw["when"])),
        hold,
    )


def _amount_ceiling(rule: Rule) -> Decimal | None:
    """The tightest ``amount lte/lt/eq N`` bound a rule carries, or None if it does not bound the amount."""
    bounds = [c.value for c in rule.when if c.fact == "amount" and c.op in ("lte", "lt", "eq")]
    return min(bounds) if bounds else None


def _action(name: str, raw: Any, budgets: dict[str, Budget]) -> Action:
    where = f"actions.{name}"
    raw = _mapping(raw, where, _ACTION_KEYS, required=_ACTION_REQUIRED)
    kind = raw.get("kind", "credit")
    if kind not in KINDS:
        raise AutonomyPolicyError(f"{where}: unknown kind {kind!r}")
    budget_key = raw.get("budget")
    if kind in MONEY_KINDS:
        if budget_key not in budgets:
            raise AutonomyPolicyError(f"{where}: unknown budget {budget_key!r}")
    elif budget_key is not None:
        raise AutonomyPolicyError(f"{where}: a {kind} spends no money, so it has no budget")
    if not isinstance(raw["reversible"], bool):
        raise AutonomyPolicyError(f"{where}.reversible: expected true or false")
    if kind == "notice" and raw["reversible"]:
        raise AutonomyPolicyError(f"{where}: a sent notice cannot be reversed")
    if kind == "risk_review" and not raw["reversible"]:
        raise AutonomyPolicyError(f"{where}: a hold must be reversible")
    rules_raw = raw["rules"]
    if not isinstance(rules_raw, list) or not rules_raw:
        raise AutonomyPolicyError(f"{where}.rules: expected a non-empty list")
    rules = tuple(_rule(r, f"{where}.rules[{i}]") for i, r in enumerate(rules_raw))
    if len({r.id for r in rules}) != len(rules):
        raise AutonomyPolicyError(f"{where}.rules: duplicate rule id")
    last = rules[-1]
    if last.when or last.tier != "needs_approval":
        raise AutonomyPolicyError(f"{where}.rules: the last rule must be unconditional and needs_approval")
    if any(not r.when for r in rules[:-1]):
        raise AutonomyPolicyError(
            f"{where}.rules: an unconditional rule before the last one makes the rest unreachable"
        )
    for rule in rules:
        if rule.tier in RISK_ONLY_TIERS and kind != "risk_review":
            raise AutonomyPolicyError(f"{where}.rules.{rule.id}: only a risk review may take no action")
        if rule.hold and (kind != "risk_review" or rule.tier != "needs_approval"):
            raise AutonomyPolicyError(f"{where}.rules.{rule.id}: only a risk review's needs_approval rule may hold")
        if kind == "risk_review" and rule.tier in AUTONOMOUS_TIERS:
            # The engine never cancels, refunds or clears an order itself: a review ends in nothing or a person.
            raise AutonomyPolicyError(f"{where}.rules.{rule.id}: a risk review never acts alone")
        if rule.tier not in AUTONOMOUS_TIERS:
            continue
        if kind == "notice":
            # No money to bound, so the bound is how many people one event may reach.
            if not any(c.fact == "recipients" and c.op in ("lte", "lt", "eq") for c in rule.when):
                raise AutonomyPolicyError(
                    f"{where}.rules.{rule.id}: an autonomous notice needs a 'recipients lte N' bound"
                )
            continue
        assert budget_key is not None
        cap = budgets[budget_key].per_action_max_amount
        ceiling = _amount_ceiling(rule)
        if ceiling is None:
            raise AutonomyPolicyError(f"{where}.rules.{rule.id}: an autonomous rule needs an 'amount lte N' ceiling")
        if ceiling > cap:
            raise AutonomyPolicyError(
                f"{where}.rules.{rule.id}: amount ceiling {ceiling} is above the budget's per_action_max_amount {cap}"
            )
    return Action(name, budget_key, raw["reversible"], rules, kind)


def _positive_int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise AutonomyPolicyError(f"{where}: expected an integer of at least 1, got {value!r}")
    return value


def _event(topic: str, raw: Any, actions: dict[str, Action]) -> EventRule:
    where = f"events.{topic}"
    if not isinstance(raw, dict):
        raise AutonomyPolicyError(f"{where}: expected a mapping")
    action_name = raw.get("action")
    if action_name not in actions:
        raise AutonomyPolicyError(f"{where}: unknown action {action_name!r}")
    kind = actions[action_name].kind
    if kind == "damage_credit":
        raw = _mapping(raw, where, _DAMAGE_EVENT_KEYS)
        percent = _decimal(raw["credit_percent"], f"{where}.credit_percent")
        if not Decimal(0) < percent <= Decimal(100):
            raise AutonomyPolicyError(f"{where}.credit_percent: must be above 0 and at most 100")
        return EventRule(
            topic,
            action_name,
            _statuses(raw["eligible_order_statuses"], where),
            (),
            lookback_days=_positive_int(raw["lookback_days"], f"{where}.lookback_days"),
            credit_percent=percent,
            max_credit=_money(raw["max_credit"], f"{where}.max_credit"),
            replacement_max_line_value=_money(raw["replacement_max_line_value"], f"{where}.replacement_max_line_value"),
        )
    if kind == "risk_review":
        raw = _mapping(raw, where, _RISK_EVENT_KEYS)
        signals_raw = raw["signals"]
        if not isinstance(signals_raw, list) or not signals_raw:
            raise AutonomyPolicyError(f"{where}.signals: expected a non-empty list")
        signals: list[Signal] = []
        for i, item in enumerate(signals_raw):
            at = f"{where}.signals[{i}]"
            item = _mapping(item, at, _SIGNAL_KEYS)
            if not isinstance(item["id"], str) or not item["id"].strip():
                raise AutonomyPolicyError(f"{at}.id: must be a non-empty string")
            condition = _condition({k: item[k] for k in ("fact", "op", "value")}, at)
            if condition.fact == "risk_score":
                raise AutonomyPolicyError(f"{at}: a signal cannot test the score it adds to")
            signals.append(Signal(item["id"], condition, _positive_int(item["points"], f"{at}.points")))
        if len({s.id for s in signals}) != len(signals):
            raise AutonomyPolicyError(f"{where}.signals: duplicate signal id")
        return EventRule(
            topic,
            action_name,
            _statuses(raw["eligible_order_statuses"], where),
            (),
            velocity_window_hours=_positive_int(raw["velocity_window_hours"], f"{where}.velocity_window_hours"),
            signals=tuple(signals),
        )
    if kind == "price_adjustment":
        raw = _mapping(raw, where, _PRICE_EVENT_KEYS)
        statuses = _statuses(raw["eligible_order_statuses"], where)
        return EventRule(
            topic,
            action_name,
            statuses,
            (),
            lookback_days=_positive_int(raw["lookback_days"], f"{where}.lookback_days"),
            max_lines=_positive_int(raw["max_lines"], f"{where}.max_lines"),
        )
    if kind == "notice":
        raw = _mapping(raw, where, _NOTICE_EVENT_KEYS)
        return EventRule(
            topic,
            action_name,
            frozenset(),
            (),
            cart_max_age_days=_positive_int(raw["cart_max_age_days"], f"{where}.cart_max_age_days"),
            max_recipients=_positive_int(raw["max_recipients"], f"{where}.max_recipients"),
            per_customer_per_day=_positive_int(raw["per_customer_per_day"], f"{where}.per_customer_per_day"),
        )
    raw = _mapping(raw, where, _EVENT_KEYS)
    statuses = _statuses(raw["eligible_order_statuses"], where)
    tiers_raw = raw["credit_tiers"]
    if not isinstance(tiers_raw, list) or not tiers_raw:
        raise AutonomyPolicyError(f"{where}.credit_tiers: expected a non-empty list")
    tiers: list[CreditTier] = []
    for i, item in enumerate(tiers_raw):
        item = _mapping(item, f"{where}.credit_tiers[{i}]", _CREDIT_TIER_KEYS)
        days = item["min_delay_days"]
        if isinstance(days, bool) or not isinstance(days, int) or days < 1:
            raise AutonomyPolicyError(f"{where}.credit_tiers[{i}].min_delay_days: expected an integer of at least 1")
        tiers.append(CreditTier(days, _money(item["amount"], f"{where}.credit_tiers[{i}].amount")))
    if len({t.min_delay_days for t in tiers}) != len(tiers):
        raise AutonomyPolicyError(f"{where}.credit_tiers: duplicate min_delay_days")
    ordered = sorted(tiers, key=lambda t: t.min_delay_days)
    if any(b.amount < a.amount for a, b in zip(ordered, ordered[1:], strict=False)):
        raise AutonomyPolicyError(f"{where}.credit_tiers: a longer delay may not earn a smaller credit")
    return EventRule(topic, action_name, statuses, tuple(ordered))


def _statuses(value: Any, where: str) -> frozenset[str]:
    if not isinstance(value, list) or not value or not all(isinstance(s, str) and s for s in value):
        raise AutonomyPolicyError(f"{where}.eligible_order_statuses: expected a non-empty list of statuses")
    return frozenset(value)


def parse_policy(document: Any) -> AutonomyPolicy:
    """Validate an already-parsed YAML document and build the policy."""
    doc = _mapping(document, "policy", _TOP_KEYS)
    version = doc["policy_version"]
    if not isinstance(version, str) or not version.strip():
        raise AutonomyPolicyError("policy_version must be a non-empty string (quote it in the YAML)")
    for key in ("budgets", "actions", "events"):
        if not isinstance(doc[key], dict) or not doc[key]:
            raise AutonomyPolicyError(f"{key}: expected a non-empty mapping")
    budgets = {str(k): _budget(str(k), v) for k, v in doc["budgets"].items()}
    actions = {str(k): _action(str(k), v, budgets) for k, v in doc["actions"].items()}
    events = {str(k): _event(str(k), v, actions) for k, v in doc["events"].items()}
    return AutonomyPolicy(version, budgets, actions, events)


def load_policy(path: Path | None = None) -> AutonomyPolicy:
    """Read, parse and validate a policy file (default: the shipped ``autonomy.yaml``)."""
    target = path or POLICY_PATH
    try:
        with open(target, encoding="utf-8") as handle:
            document = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        raise AutonomyPolicyError(f"cannot read autonomy policy {target.name}: {type(exc).__name__}") from exc
    return parse_policy(document)


@functools.lru_cache(maxsize=4)
def _cached(path: str | None) -> AutonomyPolicy:
    return load_policy(Path(path) if path else None)


def get_policy() -> AutonomyPolicy:
    """The active policy: the shipped file, or ``AUTONOMY_POLICY_PATH`` when set. Loaded once per path."""
    from shared.config import settings

    return _cached(settings.AUTONOMY_POLICY_PATH or None)


# ── evaluation (pure) ────────────────────────────────────────────────────────


def to_decimal(value: Any) -> Decimal | None:
    """A fact value as a Decimal, or None when it is absent or not a finite number (unknown)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except ArithmeticError:
        return None
    return number if number.is_finite() else None


def _holds(cond: Condition, actual: Decimal | None) -> bool:
    if actual is None:  # unknown evidence never satisfies a condition
        return False
    return {
        "lt": actual < cond.value,
        "lte": actual <= cond.value,
        "gt": actual > cond.value,
        "gte": actual >= cond.value,
        "eq": actual == cond.value,
    }[cond.op]


@dataclass(frozen=True)
class TierDecision:
    tier: str
    rule_id: str
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)
    hold: bool = False


def evaluate_tier(action: Action, facts: dict[str, Any]) -> TierDecision:
    """The tier for ``action`` given ``facts``: the first rule whose conditions all hold. Pure.

    Raises :class:`AutonomyPolicyError` for an unknown fact name; a missing *value* is just unknown.
    """
    unknown = set(facts) - FACTS
    if unknown:
        raise AutonomyPolicyError(f"unknown fact(s): {sorted(unknown)}")
    known = {name: to_decimal(facts.get(name)) for name in FACTS}
    for rule in action.rules:
        if all(_holds(c, known[c.fact]) for c in rule.when):
            return TierDecision(
                rule.tier,
                rule.id,
                rule.reason,
                evidence={
                    "rule": rule.id,
                    "conditions": [
                        {
                            "fact": c.fact,
                            "op": c.op,
                            "value": str(c.value),
                            "actual": None if known[c.fact] is None else str(known[c.fact]),
                        }
                        for c in rule.when
                    ],
                    "facts": {k: None if v is None else str(v) for k, v in sorted(known.items())},
                },
                hold=rule.hold,
            )
    # Unreachable for a loaded policy: the last rule is unconditional.
    raise AutonomyPolicyError(f"no rule matched for action {action.name!r}")


@dataclass(frozen=True)
class Usage:
    """What a budget has already drawn: today's totals and the customer's and order's own."""

    global_count: int = 0
    global_amount: Decimal = Decimal(0)
    customer_count: int = 0
    customer_amount: Decimal = Decimal(0)
    order_count: int = 0
    #: This UTC month's draws on the budget (every customer), read in the same transaction as the day's.
    month_count: int = 0
    month_amount: Decimal = Decimal(0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "global_count": self.global_count,
            "global_amount": str(self.global_amount),
            "customer_count": self.customer_count,
            "customer_amount": str(self.customer_amount),
            "order_count": self.order_count,
            "month_count": self.month_count,
            "month_amount": str(self.month_amount),
        }


@dataclass(frozen=True)
class BudgetCheck:
    ok: bool
    tripped: tuple[str, ...] = ()


def check_budget(budget: Budget, amount: Decimal | None, usage: Usage) -> BudgetCheck:
    """Whether drawing ``amount`` on top of ``usage`` stays inside every cap. Exactly at a cap is allowed. Pure."""
    if amount is None or amount <= 0:
        return BudgetCheck(False, ("amount_missing",))
    tripped: list[str] = []
    if amount > budget.per_action_max_amount:
        tripped.append("per_action_amount")
    if usage.customer_count + 1 > budget.per_customer_per_day.max_count:
        tripped.append("customer_daily_count")
    if usage.customer_amount + amount > budget.per_customer_per_day.max_amount:
        tripped.append("customer_daily_amount")
    if usage.order_count + 1 > budget.per_order_max_count:
        tripped.append("order_count")
    if usage.global_count + 1 > budget.global_per_day.max_count:
        tripped.append("global_daily_count")
    if usage.global_amount + amount > budget.global_per_day.max_amount:
        tripped.append("global_daily_amount")
    month = budget.global_per_month
    if month is not None:
        if usage.month_count + 1 > month.max_count:
            tripped.append("global_monthly_count")
        if usage.month_amount + amount > month.max_amount:
            tripped.append("global_monthly_amount")
    return BudgetCheck(not tripped, tuple(tripped))


RESOLUTION_CREDIT = "credit"
RESOLUTION_REPLACEMENT = "replacement_offer"


def size_damage_remedy(rule: EventRule, value: Decimal, *, in_stock: bool) -> tuple[str, Decimal] | None:
    """The remedy for damage worth ``value``: ``(resolution, amount)``, or None when it comes to nothing. Pure.

    A free replacement (costed at ``value``) when the product is in stock and ``value`` is at most
    ``replacement_max_line_value``; otherwise a store credit of ``credit_percent`` of ``value``, never above
    ``max_credit``. Cents round half up.
    """
    if in_stock and value <= rule.replacement_max_line_value:
        return (RESOLUTION_REPLACEMENT, value) if value > 0 else None
    credit = min((value * rule.credit_percent / 100).quantize(_CENT, rounding=ROUND_HALF_UP), rule.max_credit)
    return (RESOLUTION_CREDIT, credit) if credit > 0 else None


def month_start(day: date) -> date:
    """The first day of ``day``'s calendar month (a budget's month is the UTC calendar month, not 30 days)."""
    return day.replace(day=1)


def next_month_start(day: date) -> date:
    first = month_start(day)
    return date(first.year + 1, 1, 1) if first.month == 12 else date(first.year, first.month + 1, 1)


@dataclass(frozen=True)
class RiskScore:
    score: int
    triggered: tuple[dict[str, Any], ...]


def score_order(rule: EventRule, facts: dict[str, Any]) -> RiskScore:
    """The risk score of an order: the points of every signal whose condition holds, capped at 100. Pure.

    A fact that could not be determined (``None``) satisfies nothing and adds nothing, so missing evidence can only
    lower the score; the tier rules then read the score, never the raw facts.
    """
    known = {name: to_decimal(facts.get(name)) for name in FACTS}
    total = 0
    triggered: list[dict[str, Any]] = []
    for signal in rule.signals:
        if _holds(signal.condition, known[signal.condition.fact]):
            total += signal.points
            triggered.append(
                {"signal": signal.id, "points": signal.points, "actual": str(known[signal.condition.fact])}
            )
    return RiskScore(min(total, MAX_RISK_SCORE), tuple(triggered))
