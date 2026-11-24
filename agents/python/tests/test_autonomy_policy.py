"""V-1 (#94) -- the autonomy policy: golden cases, strict loading, and the guarantees it is built on.

Pure tests, no database: the policy file is validated whole, an unknown fact never satisfies a condition, and a rule
that could act without an amount ceiling is refused at load time.
"""

from __future__ import annotations

import copy
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from shared.autonomy.policy import (
    POLICY_PATH,
    AutonomyPolicyError,
    Usage,
    check_budget,
    evaluate_tier,
    get_policy,
    load_policy,
    month_start,
    next_month_start,
    parse_policy,
    score_order,
    size_damage_remedy,
)

CASES = yaml.safe_load((POLICY_PATH.parent / "testdata-autonomy" / "cases.yaml").read_text(encoding="utf-8"))
SHIPPED: dict[str, Any] = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))


def _broken(mutate: Any) -> dict[str, Any]:
    doc = copy.deepcopy(SHIPPED)
    mutate(doc)
    return doc


# ── golden cases ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("case", CASES["tier_cases"], ids=lambda c: c["name"])
def test_tier_golden_case(case: dict[str, Any]) -> None:
    decision = evaluate_tier(get_policy().action(case["action"]), case["facts"])

    assert (decision.tier, decision.rule_id) == (case["expect"]["tier"], case["expect"]["rule"])
    if "hold" in case["expect"]:
        assert decision.hold is case["expect"]["hold"]


def test_every_rule_of_every_action_has_a_golden_case() -> None:
    hit = {(c["action"], c["expect"]["rule"]) for c in CASES["tier_cases"]}
    for action in get_policy().actions.values():
        for rule in action.rules:
            assert (action.name, rule.id) in hit, f"{action.name}.{rule.id} has no golden case"


@pytest.mark.parametrize("case", CASES["budget_cases"], ids=lambda c: c["name"])
def test_budget_golden_case(case: dict[str, Any]) -> None:
    usage = Usage(
        global_count=case["usage"].get("global_count", 0),
        global_amount=Decimal(str(case["usage"].get("global_amount", 0))),
        customer_count=case["usage"].get("customer_count", 0),
        customer_amount=Decimal(str(case["usage"].get("customer_amount", 0))),
        order_count=case["usage"].get("order_count", 0),
        month_count=case["usage"].get("month_count", 0),
        month_amount=Decimal(str(case["usage"].get("month_amount", 0))),
    )
    amount = None if case["amount"] is None else Decimal(str(case["amount"]))

    checked = check_budget(get_policy().budget(case["budget"]), amount, usage)

    assert checked.ok is case["expect"]["ok"]
    assert list(checked.tripped) == case["expect"]["tripped"]


@pytest.mark.parametrize("case", CASES["credit_cases"], ids=lambda c: f"{c['delay_days']}d")
def test_credit_for_a_delay(case: dict[str, Any]) -> None:
    rule = get_policy().event("order.shipment_delayed")
    assert rule is not None

    credit = rule.credit_for(case["delay_days"])

    assert credit == (None if case["credit"] is None else Decimal(str(case["credit"])))


# ── a missing fact never satisfies a condition ───────────────────────────────


@pytest.mark.parametrize("missing", ["amount", "delay_days"])
def test_a_missing_fact_can_only_fall_through_to_needs_approval(missing: str) -> None:
    facts = {"amount": 5, "delay_days": 3}
    del facts[missing]

    decision = evaluate_tier(get_policy().action("goodwill_credit"), facts)

    assert decision.tier == "needs_approval"
    assert decision.evidence["facts"][missing] is None


@pytest.mark.parametrize("bad", [None, "", "five", float("nan"), float("inf"), True])
def test_an_unusable_fact_value_counts_as_missing(bad: Any) -> None:
    decision = evaluate_tier(get_policy().action("goodwill_credit"), {"amount": bad, "delay_days": 9})

    assert decision.tier == "needs_approval"


def test_an_unknown_fact_name_is_an_error_not_ignored() -> None:
    with pytest.raises(AutonomyPolicyError, match="unknown fact"):
        evaluate_tier(get_policy().action("goodwill_credit"), {"amount": 5, "mood": "sad"})


def test_the_budget_refuses_an_amount_it_cannot_see() -> None:
    budget = get_policy().budget("goodwill_credit")

    for amount in (None, Decimal(0), Decimal("-1")):
        assert check_budget(budget, amount, Usage()).tripped == ("amount_missing",)


# ── the shipped policy ───────────────────────────────────────────────────────


def test_the_shipped_policy_loads_and_its_three_delays_land_in_three_tiers() -> None:
    policy = get_policy()
    rule = policy.event("order.shipment_delayed")
    assert rule is not None
    action = policy.action(rule.action)
    tiers = []
    for days in (3, 6, 12):
        credit = rule.credit_for(days)
        tiers.append(evaluate_tier(action, {"amount": credit, "delay_days": days}).tier)

    assert tiers == ["auto", "auto_notify", "needs_approval"]


def test_money_is_decimal_everywhere() -> None:
    policy = get_policy()
    budget = policy.budget("goodwill_credit")

    assert isinstance(budget.per_action_max_amount, Decimal)
    assert isinstance(budget.global_per_day.max_amount, Decimal)
    for tier in policy.event("order.shipment_delayed").credit_tiers:  # type: ignore[union-attr]
        assert isinstance(tier.amount, Decimal)
    for action in policy.actions.values():
        for rule in action.rules:
            assert all(isinstance(c.value, Decimal) for c in rule.when)


def test_the_policy_override_path_is_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    doc = _broken(lambda d: d["budgets"]["goodwill_credit"]["global_per_day"].update(max_count=2))
    path = tmp_path / "small.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    monkeypatch.setattr("shared.config.settings.AUTONOMY_POLICY_PATH", str(path))

    assert get_policy().budget("goodwill_credit").global_per_day.max_count == 2


# ── strict loading ───────────────────────────────────────────────────────────


def test_the_shipped_file_round_trips_through_the_loader() -> None:
    assert load_policy() == parse_policy(SHIPPED)


@pytest.mark.parametrize(
    ("name", "mutate", "message"),
    [
        ("unknown top key", lambda d: d.update(surprise=1), "unknown key"),
        ("missing version", lambda d: d.pop("policy_version"), "missing"),
        ("blank version", lambda d: d.update(policy_version=" "), "policy_version"),
        ("unknown budget key", lambda d: d["budgets"]["goodwill_credit"].update(extra=1), "unknown key"),
        ("zero cap", lambda d: d["budgets"]["goodwill_credit"]["global_per_day"].update(max_count=0), "at least 1"),
        (
            "money with three decimals",
            lambda d: d["budgets"]["goodwill_credit"].update(per_action_max_amount=25.001),
            "two decimal",
        ),
        (
            "negative money",
            lambda d: d["budgets"]["goodwill_credit"].update(per_action_max_amount=-1),
            "above 0",
        ),
        (
            "customer cap below the per-action cap",
            lambda d: d["budgets"]["goodwill_credit"]["per_customer_per_day"].update(max_amount=1),
            "below per_action_max_amount",
        ),
        (
            "global cap below the customer cap",
            lambda d: d["budgets"]["goodwill_credit"]["global_per_day"].update(max_amount=1),
            "below the per-customer",
        ),
        (
            "action on a missing budget",
            lambda d: d["actions"]["goodwill_credit"].update(budget="nope"),
            "unknown budget",
        ),
        (
            "unknown tier",
            lambda d: d["actions"]["goodwill_credit"]["rules"][0].update(tier="free_for_all"),
            "unknown tier",
        ),
        (
            "unknown fact",
            lambda d: d["actions"]["goodwill_credit"]["rules"][0]["when"][0].update(fact="mood"),
            "unknown fact",
        ),
        (
            "unknown operator",
            lambda d: d["actions"]["goodwill_credit"]["rules"][0]["when"][0].update(op="like"),
            "unknown operator",
        ),
        (
            "duplicate rule id",
            lambda d: d["actions"]["goodwill_credit"]["rules"][1].update(id="auto-small-credit"),
            "duplicate rule id",
        ),
        (
            "last rule conditional",
            lambda d: d["actions"]["goodwill_credit"]["rules"][-1].update(
                when=[{"fact": "amount", "op": "gt", "value": 1}]
            ),
            "last rule must be unconditional",
        ),
        (
            "last rule acts alone",
            lambda d: d["actions"]["goodwill_credit"]["rules"][-1].update(tier="auto"),
            "last rule must be unconditional and needs_approval",
        ),
        (
            "unconditional rule before the last",
            lambda d: d["actions"]["goodwill_credit"]["rules"].insert(
                0, {"id": "always", "tier": "needs_approval", "reason": "x", "when": []}
            ),
            "unreachable",
        ),
        (
            "autonomous rule without an amount ceiling",
            lambda d: d["actions"]["goodwill_credit"]["rules"][0].update(
                when=[{"fact": "delay_days", "op": "gte", "value": 2}]
            ),
            "needs an 'amount lte N' ceiling",
        ),
        (
            "amount ceiling above the per-action cap",
            lambda d: d["actions"]["goodwill_credit"]["rules"][1]["when"][0].update(value=26),
            "above the budget's per_action_max_amount",
        ),
        (
            "event on a missing action",
            lambda d: d["events"]["order.shipment_delayed"].update(action="nope"),
            "unknown action",
        ),
        (
            "no credit tiers",
            lambda d: d["events"]["order.shipment_delayed"].update(credit_tiers=[]),
            "non-empty list",
        ),
        (
            "a longer delay earning less",
            lambda d: d["events"]["order.shipment_delayed"]["credit_tiers"][2].update(amount=1),
            "may not earn a smaller credit",
        ),
        (
            "duplicate delay tier",
            lambda d: d["events"]["order.shipment_delayed"]["credit_tiers"][1].update(min_delay_days=2),
            "duplicate min_delay_days",
        ),
        ("bool as a count", lambda d: d["budgets"]["goodwill_credit"].update(per_order_max_count=True), "integer"),
        ("empty events", lambda d: d.update(events={}), "non-empty mapping"),
    ],
)
def test_a_bad_policy_fails_loudly(name: str, mutate: Any, message: str) -> None:
    with pytest.raises(AutonomyPolicyError, match=message):
        parse_policy(_broken(mutate))


def test_an_unreadable_file_is_a_policy_error(tmp_path: Path) -> None:
    with pytest.raises(AutonomyPolicyError, match="cannot read"):
        load_policy(tmp_path / "missing.yaml")
    (tmp_path / "bad.yaml").write_text("a: [unclosed", encoding="utf-8")
    with pytest.raises(AutonomyPolicyError, match="cannot read"):
        load_policy(tmp_path / "bad.yaml")


def test_the_top_level_must_be_a_mapping() -> None:
    with pytest.raises(AutonomyPolicyError, match="expected a mapping"):
        parse_policy(["not", "a", "mapping"])


# ── #299: risk scores, damage remedies, monthly caps ─────────────────────────


@pytest.mark.parametrize("case", CASES["risk_score_cases"], ids=lambda c: c["name"])
def test_risk_score_golden_case(case: dict[str, Any]) -> None:
    rule = get_policy().event("order.risk_flagged")
    assert rule is not None

    assert score_order(rule, case["facts"]).score == case["score"]


def test_the_score_names_the_signals_that_fired() -> None:
    rule = get_policy().event("order.risk_flagged")
    assert rule is not None

    scored = score_order(rule, {"order_total": 1500, "account_age_days": 1})

    assert [t["signal"] for t in scored.triggered] == ["large-order", "very-large-order", "new-account"]
    assert sum(t["points"] for t in scored.triggered) == scored.score == 65


@pytest.mark.parametrize("case", CASES["damage_cases"], ids=lambda c: c["name"])
def test_damage_remedy_golden_case(case: dict[str, Any]) -> None:
    rule = get_policy().event("order.item_damaged")
    assert rule is not None

    sized = size_damage_remedy(rule, Decimal(str(case["value"])), in_stock=case["in_stock"])

    if case["resolution"] is None:
        assert sized is None
    else:
        assert sized == (case["resolution"], Decimal(str(case["amount"])))


@pytest.mark.parametrize("case", CASES["month_cases"], ids=lambda c: c["day"])
def test_the_month_window_is_the_utc_calendar_month(case: dict[str, Any]) -> None:
    day = date.fromisoformat(case["day"])

    assert month_start(day) == date.fromisoformat(case["start"])
    assert next_month_start(day) == date.fromisoformat(case["next"])


def test_every_shipped_budget_has_a_month_cap_at_least_the_day_cap() -> None:
    for budget in get_policy().budgets.values():
        assert budget.global_per_month is not None, budget.key
        assert budget.global_per_month.max_count >= budget.global_per_day.max_count
        assert budget.global_per_month.max_amount >= budget.global_per_day.max_amount


def test_a_budget_without_a_month_cap_is_still_valid_and_never_trips_one() -> None:
    doc = _broken(lambda d: d["budgets"]["goodwill_credit"].pop("global_per_month"))
    budget = parse_policy(doc).budget("goodwill_credit")

    assert budget.global_per_month is None
    assert check_budget(budget, Decimal(5), Usage(month_count=10**6, month_amount=Decimal(10**9))).ok


def test_the_risk_review_never_acts_alone_and_the_hold_is_only_on_needs_approval() -> None:
    action = get_policy().action("order_risk_review")

    assert {r.tier for r in action.rules} == {"no_action", "needs_approval"}
    assert [r.id for r in action.rules if r.hold] == ["risk-high-hold"]


@pytest.mark.parametrize(
    ("name", "mutate", "message"),
    [
        (
            "month cap below the day count",
            lambda d: d["budgets"]["goodwill_credit"]["global_per_month"].update(max_count=19),
            "global_per_month is below global_per_day",
        ),
        (
            "month cap below the day amount",
            lambda d: d["budgets"]["goodwill_credit"]["global_per_month"].update(max_amount=199),
            "global_per_month is below global_per_day",
        ),
        (
            "unknown month cap key",
            lambda d: d["budgets"]["goodwill_credit"]["global_per_month"].update(per_week=1),
            "unknown key",
        ),
        (
            "an autonomous risk rule",
            lambda d: d["actions"]["order_risk_review"]["rules"][1].update(tier="auto", hold=False),
            "never acts alone",
        ),
        (
            "no_action on a money action",
            lambda d: d["actions"]["goodwill_credit"]["rules"][0].update(tier="no_action"),
            "only a risk review may take no action",
        ),
        (
            "hold on a money action",
            lambda d: d["actions"]["goodwill_credit"]["rules"][-1].update(hold=True),
            "may hold",
        ),
        (
            "hold on a no_action rule",
            lambda d: d["actions"]["order_risk_review"]["rules"][0].update(hold=True),
            "may hold",
        ),
        (
            "an irreversible hold",
            lambda d: d["actions"]["order_risk_review"].update(reversible=False),
            "must be reversible",
        ),
        (
            "a signal on the score itself",
            lambda d: d["events"]["order.risk_flagged"]["signals"][0].update(fact="risk_score"),
            "cannot test the score",
        ),
        (
            "a signal worth no points",
            lambda d: d["events"]["order.risk_flagged"]["signals"][0].update(points=0),
            "at least 1",
        ),
        (
            "duplicate signal ids",
            lambda d: d["events"]["order.risk_flagged"]["signals"][1].update(id="large-order"),
            "duplicate signal id",
        ),
        (
            "a risk review with a budget",
            lambda d: d["actions"]["order_risk_review"].update(budget="goodwill_credit"),
            "spends no money",
        ),
        (
            "a damage credit above 100 percent",
            lambda d: d["events"]["order.item_damaged"].update(credit_percent=101),
            "at most 100",
        ),
        (
            "a damage autonomous rule above the per-action cap",
            lambda d: d["actions"]["damage_goodwill"]["rules"][1]["when"][0].update(value=16),
            "above the budget's per_action_max_amount",
        ),
    ],
)
def test_the_loader_refuses_an_unsafe_new_shape(name: str, mutate: Any, message: str) -> None:
    with pytest.raises(AutonomyPolicyError, match=message):
        parse_policy(_broken(mutate))
