"""The stack-parity comparison (B2): two runs of one dataset agree, or the comparison names what diverged.

``test_removing_a_capability_from_one_stack_fails_the_comparison`` is the unit-level proof of B2's acceptance
criterion; the CI job runs the same function over two real stacks (``scripts/stack-parity.sh``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.records import CaseScores, RunRecord, Setup, ToolCall, write_records
from evals.stack_compare import compare_records, format_comparison, main


def _setup(stack: str) -> Setup:
    return Setup(stack=stack, target="http", chat_provider="replay", chat_model="gpt-4.1")


def _record(stack: str, index: int = 0, **kw) -> RunRecord:
    base = {
        "run_id": "r",
        "recorded_at": "2026-01-01T00:00:00+00:00",
        "agent": "orchestrator",
        "dataset": "stack_parity",
        "case_id": f"orchestrator:stack_parity:{index}",
        "case_index": index,
        "setup": _setup(stack),
        "query": "research the Sony WH-1000XM5",
        "response": "Reviews: positive | Stock: 120 units available",
        "nodes": ["fan-out", "reviews", "stock", "price-history", "merge-and-ship", "synthesis"],
        "scores": CaseScores(groundedness=1.0, correctness=1.0, completeness=1.0, overall=1.0),
        "passed": True,
    }
    return RunRecord(**{**base, **kw})


def _pair(**kw_b) -> tuple[list[RunRecord], list[RunRecord]]:
    return [_record("python", 0), _record("python", 1)], [_record("dotnet", 0, **kw_b), _record("dotnet", 1)]


def test_identical_runs_agree_and_wording_is_not_compared() -> None:
    a, b = _pair(response="A different sentence, same facts. Stock: 120")
    result = compare_records(a, b, label_a="python", label_b="dotnet")

    assert result.ok and result.cases == 2
    assert "OK: no divergence" in format_comparison(result)


def test_parallel_workflow_branches_in_a_different_order_are_not_a_divergence() -> None:
    a, b = _pair(nodes=["fan-out", "price-history", "stock", "reviews", "merge-and-ship", "synthesis"])

    assert compare_records(a, b).ok


def test_removing_a_capability_from_one_stack_fails_the_comparison() -> None:
    """A workflow node the .NET stack no longer runs, and a tool it no longer calls: both are divergences."""
    a, b = _pair(
        nodes=["fan-out", "reviews", "stock", "synthesis"],
        tool_calls=[ToolCall(name="check_stock")],
    )
    a[0] = a[0].model_copy(update={"tool_calls": [ToolCall(name="check_stock"), ToolCall(name="get_price_history")]})

    result = compare_records(a, b, label_a="python", label_b="dotnet")

    assert not result.ok
    text = format_comparison(result)
    assert "workflow nodes differ" in text and "price_history" in text
    assert "tool calls differ: only python: ['get_price_history']" in text


def test_a_route_a_denial_and_a_pause_that_differ_are_divergences() -> None:
    a, b = _pair(routes=["order-management"], denied=True, hitl_requested=True)
    a[0] = a[0].model_copy(update={"routes": ["product-discovery"]})

    text = format_comparison(compare_records(a, b, label_a="python", label_b="dotnet"))

    assert "routes differ" in text and "denial differs" in text and "approval pause differs" in text


def test_a_fact_one_side_could_not_observe_is_not_compared() -> None:
    a, b = _pair(hitl_requested=True, not_observable=["hitl"])

    assert compare_records(a, b).ok


def test_mean_scores_may_differ_within_the_tolerance_and_not_beyond_it() -> None:
    a, b = _pair()
    near = CaseScores(groundedness=1.0, correctness=1.0, completeness=0.9, overall=0.98)
    far = CaseScores(groundedness=1.0, correctness=0.4, completeness=1.0, overall=0.76)

    b_near = [b[0].model_copy(update={"scores": near}), b[1]]
    b_far = [b[0].model_copy(update={"scores": far}), b[1]]

    assert compare_records(a, b_near, score_tolerance=0.05).ok  # mean overall moved by 0.01
    out = format_comparison(compare_records(a, b_far, score_tolerance=0.05))
    assert "mean correctness differs by 0.300" in out and "mean overall differs by 0.120" in out


def test_a_pass_that_flips_is_a_divergence_even_with_equal_scores() -> None:
    a, b = _pair(passed=False)

    assert "python passed but dotnet failed" in format_comparison(
        compare_records(a, b, label_a="python", label_b="dotnet")
    )


def test_a_case_missing_on_one_side_and_an_empty_side_diverge() -> None:
    a, b = _pair()

    assert "ran on python but not on dotnet" in format_comparison(
        compare_records(a, b[:1], label_a="python", label_b="dotnet")
    )
    assert "nothing to compare" in format_comparison(compare_records(a, []))


def test_two_stacks_failing_identically_prove_nothing_unless_allowed() -> None:
    a = [_record("python", 0, error="replay miss", fixture_missing=True, passed=False)]
    b = [_record("dotnet", 0, error="replay miss", fixture_missing=True, passed=False)]

    assert "both stacks failed" in format_comparison(compare_records(a, b))
    assert compare_records(a, b, allow_errors=True).ok


def test_an_error_on_one_stack_only_diverges() -> None:
    a, b = _pair(error="stream dropped after 3 frame(s)", passed=False)

    assert "python answered" in format_comparison(compare_records(a, b, label_a="python", label_b="dotnet"))


def test_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    a, b = _pair()
    pa, pb = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    write_records(pa, a)
    write_records(pb, b)
    assert main([str(pa), str(pb), "--label-a", "python", "--label-b", "dotnet"]) == 0

    write_records(pb, [b[0].model_copy(update={"nodes": []}), b[1]])
    assert main([str(pa), str(pb)]) == 1
    assert "workflow nodes differ" in capsys.readouterr().out

    assert main([str(pa), str(tmp_path / "missing.jsonl")]) == 2


def test_node_ids_compare_by_normalised_set() -> None:
    a, b = _pair(
        nodes=["fan_out", "reviews", "stock", "price_history", "merge_and_ship", "merge_and_ship", "synthesis"]
    )
    assert compare_records(a, b, label_a="python", label_b="dotnet").ok


def test_a_missing_node_still_diverges() -> None:
    a, b = _pair(nodes=["fan_out", "reviews", "stock", "synthesis"])
    result = compare_records(a, b, label_a="python", label_b="dotnet")
    assert not result.ok and "workflow nodes differ" in format_comparison(result)


def test_unreported_grounding_skips_groundedness_and_overall_only() -> None:
    a = [_record("python", 0, not_observable=["grounding"])]
    low = CaseScores(groundedness=0.0, correctness=1.0, completeness=1.0, overall=0.6)
    assert compare_records(a, [_record("dotnet", 0, scores=low)], label_a="python", label_b="dotnet").ok
    worse = CaseScores(groundedness=0.0, correctness=0.0, completeness=1.0, overall=0.3)
    result = compare_records(a, [_record("dotnet", 0, scores=worse)], label_a="python", label_b="dotnet")
    assert not result.ok and "correctness" in format_comparison(result)
