"""D-4 -- the approval policy engine: golden cases, loader validation, operator semantics.

The golden cases in ``config/policies/testdata/*.yaml`` are shared with the .NET suite
(``ApprovalPolicyTests``): both engines must give the same route and reasons for every case.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from shared.policy import PolicyError, evaluate, get_policy, load_policy
from shared.policy.loader import POLICY_PATH, parse_policy

TESTDATA = POLICY_PATH.parent / "testdata"


def _golden() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in sorted(TESTDATA.glob("*.yaml")):
        for case in yaml.safe_load(path.read_text(encoding="utf-8"))["cases"]:
            cases.append({**case, "file": path.name})
    return cases


GOLDEN = _golden()


def test_there_are_golden_cases() -> None:
    assert len(GOLDEN) >= 50
    assert len({(c["kind"], c["name"]) for c in GOLDEN}) == len(GOLDEN), "golden case names must be unique per kind"


@pytest.mark.parametrize("case", GOLDEN, ids=lambda c: f"{c['kind']}:{c['name']}")
def test_golden_case(case: dict[str, Any]) -> None:
    decision = evaluate(case["kind"], case["facts"])

    assert decision.route == case["expect"]["route"]
    assert list(decision.reasons) == case["expect"]["reasons"]
    assert decision.evidence["rule"] == case["expect"]["rule"]
    assert decision.version == get_policy().version


def test_every_rule_of_every_kind_is_covered_by_a_golden_case() -> None:
    hit = {(c["kind"], c["expect"]["rule"]) for c in GOLDEN}
    for kind in get_policy().kinds.values():
        for rule in kind.rules:
            assert (kind.kind, rule.id) in hit, f"{kind.kind}.{rule.id} has no golden case"


def test_every_gated_tool_has_a_policy() -> None:
    from shared.hitl import HITL_GATED_TOOLS

    assert HITL_GATED_TOOLS <= set(get_policy().kinds)
    assert "return_approval" in get_policy().kinds


def test_the_shipped_policy_carries_the_per_kind_settings() -> None:
    policy = get_policy()
    assert policy.version
    for kind in policy.kinds.values():
        assert kind.sla_hours > 0 and kind.on_timeout in {"reject", "expire"}
    assert policy.kind("return_approval").editable_fields == ("amount", "resolution")


def test_evidence_is_deterministic_and_explains_the_rule() -> None:
    facts = {"amount": 600, "loyalty_tier": "gold"}
    first, second = evaluate("return_approval", facts), evaluate("return_approval", facts)

    assert first == second
    assert first.evidence["conditions"] == [{"fact": "amount", "op": "gt", "value": 500.0, "actual": 600.0}]
    assert first.evidence["facts"]["amount"] == 600.0 and first.evidence["facts"]["returns_90d"] is None


def test_decimal_facts_are_accepted() -> None:
    from decimal import Decimal

    assert evaluate("return_approval", {"amount": Decimal("600.00")}).route == "manager"


# ── engine errors ────────────────────────────────────────────────────────────


def test_unknown_kind_fails_loudly() -> None:
    with pytest.raises(PolicyError, match="no approval policy for kind"):
        evaluate("delete_everything", {})


def test_unknown_fact_name_fails_loudly() -> None:
    with pytest.raises(PolicyError, match="unknown fact"):
        evaluate("return_approval", {"amount": 1, "customer_mood": "grumpy"})


@pytest.mark.parametrize("facts", [{"amount": "lots"}, {"amount": True}, {"loyalty_tier": 3}])
def test_wrong_fact_type_fails_loudly(facts: dict[str, Any]) -> None:
    with pytest.raises(PolicyError):
        evaluate("return_approval", facts)


# ── loader validation ────────────────────────────────────────────────────────


def _doc(**overrides: Any) -> dict[str, Any]:
    rule = {"id": "r", "route": "support", "reason": "because", "when": []}
    kind = {"editable_fields": [], "sla_hours": 4, "on_timeout": "expire", "rules": [rule]}
    return {"policy_version": "v1", "kinds": {"k": kind}} | overrides


def _with_kind(**kind_overrides: Any) -> dict[str, Any]:
    doc = _doc()
    doc["kinds"]["k"].update(kind_overrides)
    return doc


def _rules(*rules: dict[str, Any]) -> dict[str, Any]:
    return _with_kind(rules=list(rules))


def _cond(**kw: Any) -> dict[str, Any]:
    return {"id": "a", "route": "manager", "reason": "x", "when": [kw]}


_DEFAULT = {"id": "default", "route": "support", "reason": "d", "when": []}


def test_a_minimal_valid_document_loads() -> None:
    assert parse_policy(_doc()).kind("k").rules[0].route == "support"


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (_doc(policy_version=2026.11), "policy_version"),
        (_doc(policy_version=""), "policy_version"),
        (_doc(extra=1), "unknown key"),
        (_doc(kinds={}), "kinds"),
        (_with_kind(sla_hours=0), "sla_hours"),
        (_with_kind(sla_hours="4"), "sla_hours"),
        (_with_kind(on_timeout="approve"), "on_timeout"),
        (_with_kind(editable_fields="amount"), "editable_fields"),
        (_with_kind(surprise=True), "unknown key"),
        (_with_kind(rules=[]), "non-empty"),
        (_rules({"id": "a", "route": "boss", "reason": "x", "when": []}), "unknown route"),
        (_rules({"id": "a", "route": "support", "reason": "", "when": []}), "reason"),
        (_rules({"id": "a", "route": "support", "reason": "x"}), "'when'"),
        (_rules({"id": "a", "route": "support", "reason": "x", "when": [], "extra": 1}), "unknown key"),
        (_rules(_cond(fact="amount", op="gt", value=1)), "last rule must be unconditional"),
        (_rules(_cond(fact="mood", op="gt", value=1), _DEFAULT), "unknown fact"),
        (_rules(_cond(fact="amount", op="matches", value=1), _DEFAULT), "unknown operator"),
        (_rules(_cond(fact="amount", op="gt", value="1"), _DEFAULT), "numbers"),
        (_rules(_cond(fact="amount", op="gt", value=True), _DEFAULT), "numbers"),
        (_rules(_cond(fact="amount", op="gt", value=float("nan")), _DEFAULT), "numbers"),
        (_rules(_cond(fact="amount", op="gt", value=[1]), _DEFAULT), "only 'in' takes a list"),
        (_rules(_cond(fact="amount", op="in", value=[]), _DEFAULT), "non-empty list"),
        (_rules(_cond(fact="amount", op="in", value=5), _DEFAULT), "non-empty list"),
        (_rules(_cond(fact="loyalty_tier", op="gt", value="gold"), _DEFAULT), "string fact"),
        (_rules(_cond(fact="loyalty_tier", op="eq", value="platinum"), _DEFAULT), "unknown loyalty tier"),
        (_rules(_cond(fact="loyalty_tier", op="eq", value=1), _DEFAULT), "strings"),
        (_rules(_cond(fact="amount", op="gt"), _DEFAULT), "missing"),
        (_rules(_cond(fact="amount", op="gt", value=1, expr="1+1"), _DEFAULT), "unknown key"),
        (_rules(_DEFAULT, {**_DEFAULT, "id": "after"}), "unreachable"),
        (
            _rules(
                {**_DEFAULT, "id": "same", "when": [{"fact": "amount", "op": "gt", "value": 1}]},
                {**_DEFAULT, "id": "same"},
            ),
            "duplicate rule id",
        ),
    ],
)
def test_invalid_policies_fail_loudly_at_load(document: dict[str, Any], message: str) -> None:
    with pytest.raises(PolicyError, match=message):
        parse_policy(document)


def test_a_missing_or_malformed_file_is_a_policy_error(tmp_path: Path) -> None:
    with pytest.raises(PolicyError, match="cannot read"):
        load_policy(tmp_path / "nope.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("kinds: [unclosed", encoding="utf-8")
    with pytest.raises(PolicyError, match="cannot read"):
        load_policy(bad)


# ── operator semantics on a tiny hand-built policy ───────────────────────────


def _one_rule_policy(op: str, value: Any, fact: str = "amount"):
    return parse_policy(
        _rules(
            {"id": "hit", "route": "manager", "reason": "hit", "when": [{"fact": fact, "op": op, "value": value}]},
            _DEFAULT,
        )
    )


@pytest.mark.parametrize(
    ("op", "value", "actual", "expected"),
    [
        ("lt", 10, 9.99, True),
        ("lt", 10, 10, False),
        ("lte", 10, 10, True),
        ("lte", 10, 10.01, False),
        ("gt", 10, 10.01, True),
        ("gt", 10, 10, False),
        ("gte", 10, 10, True),
        ("gte", 10, 9.99, False),
        ("eq", 10, 10, True),
        ("eq", 10, 11, False),
        ("in", [1, 2, 3], 2, True),
        ("in", [1, 2, 3], 4, False),
    ],
)
def test_operator(op: str, value: Any, actual: float, expected: bool) -> None:
    decision = evaluate("k", {"amount": actual}, policy=_one_rule_policy(op, value))
    assert (decision.route == "manager") is expected


def test_string_operators() -> None:
    eq = _one_rule_policy("eq", "gold", fact="loyalty_tier")
    assert evaluate("k", {"loyalty_tier": "gold"}, policy=eq).route == "manager"
    assert evaluate("k", {"loyalty_tier": "silver"}, policy=eq).route == "support"
    member = _one_rule_policy("in", ["silver", "gold"], fact="loyalty_tier")
    assert evaluate("k", {"loyalty_tier": "silver"}, policy=member).route == "manager"
    assert evaluate("k", {"loyalty_tier": "bronze"}, policy=member).route == "support"


def test_an_unknown_fact_never_satisfies_a_condition_even_a_lt() -> None:
    policy = _one_rule_policy("lt", 1_000_000)
    assert evaluate("k", {}, policy=policy).route == "support"
    assert evaluate("k", {"amount": None}, policy=policy).route == "support"


def test_first_match_wins_over_a_later_match() -> None:
    policy = parse_policy(
        _rules(
            {"id": "first", "route": "auto", "reason": "first", "when": [{"fact": "amount", "op": "lt", "value": 10}]},
            {
                "id": "second",
                "route": "manager",
                "reason": "second",
                "when": [{"fact": "amount", "op": "lt", "value": 100}],
            },
            _DEFAULT,
        )
    )
    assert evaluate("k", {"amount": 5}, policy=policy).evidence["rule"] == "first"
    assert evaluate("k", {"amount": 50}, policy=policy).evidence["rule"] == "second"


def test_the_policy_file_contains_no_expression_syntax() -> None:
    """Conditions are data. Nothing in the policy module evaluates a string."""
    import inspect

    import shared.policy.engine as engine_mod
    import shared.policy.loader as loader_mod

    for module in (engine_mod, loader_mod):
        source = inspect.getsource(module)
        assert "eval(" not in source and "exec(" not in source and "import re" not in source
