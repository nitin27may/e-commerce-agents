"""Eval schema v2 (#97 / E-3a): per-case identity, ``expected_tool_args``, ``expect_denial``, ``expect_hitl``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.evaluator import AgentEvaluator, EvalCase, load_dataset
from evals.harness import ProductionRunner, RunOutcome, _tool_calls, _was_denied
from evals.scorers.tool_args import args_match, score_tool_args

DATASETS = Path(__file__).resolve().parents[1] / "evals" / "datasets"


# ─────────────────────── the scorer ───────────────────────


def _call(name: str, **tool_input) -> dict:
    return {"tool_name": name, "tool_input": tool_input}


def test_subset_match_ignores_extra_arguments() -> None:
    calls = [_call("search_products", query="wireless headphones", max_price=300.0, sort_by="rating", limit=10)]
    verdict = score_tool_args(calls, {"search_products": {"query": "wireless headphones", "max_price": 300}})
    assert verdict.passed and verdict.score == 1.0
    assert verdict.matched == ["search_products"] and verdict.missing == []


def test_wrong_value_missing_key_and_uncalled_tool_all_miss() -> None:
    calls = [_call("search_products", query="speakers", max_price=500.0)]
    expected = {
        "search_products": {"max_price": 300},  # called, but with another value
        "get_product_details": {"product_id": "x"},  # never called
    }
    verdict = score_tool_args(calls, expected)
    assert not verdict.passed
    assert verdict.missing == ["search_products", "get_product_details"]
    assert verdict.score == 0.0
    assert not args_match({"category": "Electronics"}, {"query": "q"})  # key absent


def test_any_call_to_the_tool_may_satisfy_the_expectation() -> None:
    calls = [_call("search_products", query="a"), _call("search_products", query="b", limit=1)]
    assert score_tool_args(calls, {"search_products": {"query": "b"}}).passed


def test_partial_credit_is_matched_over_expected() -> None:
    calls = [_call("check_stock", product_id="p1")]
    verdict = score_tool_args(calls, {"check_stock": {"product_id": "p1"}, "estimate_shipping": {"days": 3}})
    assert verdict.score == 0.5


def test_value_rules() -> None:
    assert args_match({"limit": 5}, {"limit": 5.0})  # numbers by value
    assert args_match({"category": "electronics"}, {"category": " Electronics "})  # strings: case/space-insensitive
    assert not args_match({"limit": 1}, {"limit": True})  # a bool is not a number
    assert args_match({"filter": {"brand": "sony"}}, {"filter": {"brand": "Sony", "color": "black"}})  # nested subset
    assert args_match({"ids": [1, 2]}, {"ids": [1.0, 2]})
    assert not args_match({"ids": [1, 2]}, {"ids": [1, 2, 3]})  # lists are exact


def test_no_expectation_scores_one_and_non_dict_inputs_never_match() -> None:
    assert score_tool_args([], None).passed
    assert score_tool_args([], {}).score == 1.0
    assert not score_tool_args([{"tool_name": "t", "tool_input": "a string"}], {"t": {"k": 1}}).passed


# ─────────────────────── dataset loading ───────────────────────


def _write(tmp_path: Path, entry: dict) -> Path:
    base = {"input": "hi", "expected_tools": [], "expected_fields": [], "criteria": {}}
    path = tmp_path / "d.json"
    path.write_text(json.dumps([{**base, **entry}]))
    return path


def test_v1_cases_load_with_every_v2_field_unset(tmp_path: Path) -> None:
    case = load_dataset(_write(tmp_path, {}))[0]
    assert (case.user_role, case.user_email, case.expected_tool_args) == (None, None, None)
    assert (case.expect_denial, case.expect_hitl) == (None, None)


def test_v2_fields_are_parsed(tmp_path: Path) -> None:
    case = load_dataset(
        _write(
            tmp_path,
            {
                "user_role": "seller",
                "user_email": "seller@example.com",
                "expected_tool_args": {"get_my_products": {"limit": 5}},
                "expect_denial": True,
                "expect_hitl": False,
            },
        )
    )[0]
    assert case.user_role == "seller" and case.user_email == "seller@example.com"
    assert case.expected_tool_args == {"get_my_products": {"limit": 5}}
    assert case.expect_denial is True and case.expect_hitl is False


def test_malformed_expected_tool_args_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="expected_tool_args"):
        load_dataset(_write(tmp_path, {"expected_tool_args": {"search_products": "query"}}))


def test_committed_datasets_still_load_and_args_target_expected_tools() -> None:
    cases_with_args = 0
    for path in sorted(DATASETS.glob("*.json")):
        if path.name == "red_team.json":
            continue
        for case in load_dataset(path):
            if case.expected_tool_args:
                cases_with_args += 1
                # An argument expectation on a tool the case does not expect would be a typo.
                assert set(case.expected_tool_args) <= set(case.expected_tools), (path.name, case.input)
    assert cases_with_args > 0


# ─────────────────────── the evaluator ───────────────────────


class _StubRunner:
    def __init__(self, outcome: RunOutcome) -> None:
        self.outcome = outcome
        self.seen: dict | None = None

    async def run(self, user_input: str, *, user_role=None, user_email=None) -> RunOutcome:
        self.seen = {"role": user_role, "email": user_email}
        return self.outcome


def _evaluator(outcome: RunOutcome) -> tuple[AgentEvaluator, _StubRunner]:
    evaluator = AgentEvaluator("product-discovery", pass_threshold=0.0)
    stub = _StubRunner(outcome)
    evaluator._runner = stub  # type: ignore[assignment]
    return evaluator, stub


def _outcome(**kw) -> RunOutcome:
    base = {"text": "ok $5", "tools_called": ["search_products"], "grounding": {"verified": 1, "total": 1}}
    return RunOutcome(**{**base, **kw})


def _case(**kw) -> EvalCase:
    return EvalCase(input="q", expected_tools=["search_products"], expected_fields=[], criteria={}, **kw)


@pytest.mark.asyncio
async def test_case_identity_is_passed_to_the_runner() -> None:
    evaluator, stub = _evaluator(_outcome())
    await evaluator._evaluate_case(_case(user_role="seller", user_email="s@example.com"), "id")
    assert stub.seen == {"role": "seller", "email": "s@example.com"}


@pytest.mark.asyncio
async def test_tool_arg_assertion_fails_the_case_but_not_the_score() -> None:
    expected = {"search_products": {"max_price": 300}}
    good, _ = _evaluator(_outcome(tool_calls=[_call("search_products", max_price=300.0)]))
    bad, _ = _evaluator(_outcome(tool_calls=[_call("search_products", max_price=900.0)]))

    ok = await good._evaluate_case(_case(expected_tool_args=expected), "id")
    ko = await bad._evaluate_case(_case(expected_tool_args=expected), "id")

    assert ok.passed and ok.assertion_failures == []
    assert not ko.passed and "search_products" in ko.assertion_failures[0]
    assert ko.overall_score == ok.overall_score  # assertions never move a score


@pytest.mark.asyncio
async def test_denial_and_hitl_expectations() -> None:
    denied, _ = _evaluator(_outcome(denied=True))
    parked, _ = _evaluator(_outcome(hitl_requested=True))
    plain, _ = _evaluator(_outcome())

    assert (await denied._evaluate_case(_case(expect_denial=True), "id")).passed
    assert not (await plain._evaluate_case(_case(expect_denial=True), "id")).passed
    assert not (await denied._evaluate_case(_case(expect_denial=False), "id")).passed
    assert (await parked._evaluate_case(_case(expect_hitl=True), "id")).passed
    assert not (await plain._evaluate_case(_case(expect_hitl=True), "id")).passed
    assert (await plain._evaluate_case(_case(), "id")).passed  # unasserted: nothing to fail


# ─────────────────────── the harness ───────────────────────


def test_denial_is_read_from_the_recorded_step_output() -> None:
    denied_step = {"tool_name": "get_my_products", "tool_output": {"error": "permission_denied"}}
    fine_step = {"tool_name": "search_products", "tool_output": {"count": 3}}
    assert _was_denied([fine_step, denied_step])
    assert not _was_denied([fine_step])
    assert not _was_denied([{"tool_name": "t", "tool_output": "permission_denied"}])  # truncated string output
    assert _tool_calls([fine_step, {"no": "name"}]) == [{"tool_name": "search_products", "tool_input": None}]


@pytest.mark.asyncio
async def test_runner_builds_one_agent_per_role(monkeypatch: pytest.MonkeyPatch) -> None:
    import evals.harness as harness
    from shared.context import current_user_role

    built: list[str] = []

    def _fake_create(agent_name: str) -> object:
        built.append(current_user_role.get())
        return object()

    async def _fake_native(agent, user_input, metadata_box=None, **_kw):
        return "text"

    monkeypatch.setattr(harness, "_create_agent", _fake_create)
    monkeypatch.setattr("shared.agent_host._run_agent_native", _fake_native)

    runner = ProductionRunner("product-discovery")
    await runner.run("a")
    await runner.run("b")
    await runner.run("c", user_role="seller")
    await runner.run("d", user_role="seller")

    assert built == ["customer", "seller"]
