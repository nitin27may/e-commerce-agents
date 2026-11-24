"""Judge calibration, offline path: labels, stored verdicts and the decision rule, with no model call."""

from __future__ import annotations

from typing import Any

import pytest

from evals.judge_calibration import run as cal


def _row(rid: str, present: list[bool], category: str = "clear_pass") -> dict[str, Any]:
    fields = [f"f{i}" for i in range(len(present))]
    return {
        "id": rid,
        "category": category,
        "agent": "a",
        "expected_fields": fields,
        "labels": {f: {"present": p, "reason": "r"} for f, p in zip(fields, present, strict=True)},
    }


def _verdict(rid: str, score: float, mode: str | None = None, tin: int = 400, tout: int = 40) -> dict[str, Any]:
    return {"id": rid, "score": score, "failure_mode": mode, "tokens_in": tin, "tokens_out": tout, "latency_ms": 100}


def test_shipped_labels_are_well_formed_and_marked_unreviewed() -> None:
    rows = cal.load_labels()
    assert len(rows) == 30
    assert len({r["id"] for r in rows}) == 30
    assert all(r["reviewed"] is False for r in rows)
    assert all(info["reason"].strip() for r in rows for info in r["labels"].values())
    assert {"clear_pass", "clear_miss", "partial", "empty", "refusal", "adversarial"} <= {r["category"] for r in rows}
    assert sum(1 for r in rows if r["category"] == "adversarial") == 5


def test_kappa_perfect_chance_and_degenerate() -> None:
    assert cal.cohens_kappa([(True, True), (False, False), (True, True), (False, False)]) == pytest.approx(1.0)
    assert cal.cohens_kappa([(True, False), (False, True)]) == pytest.approx(-1.0)
    assert cal.cohens_kappa([(True, True), (True, True)]) is None
    assert cal.cohens_kappa([]) is None


def test_wilson_interval_bounds() -> None:
    low, high = cal.wilson_interval(27, 30) or (0, 0)
    assert 0.7 < low < 0.8 < 0.9 < high <= 1.0
    assert cal.wilson_interval(0, 0) is None


def test_score_judge_counts_false_pass_false_fail_and_failures() -> None:
    labels = [
        _row("a", [True]),  # label pass, judge pass: agree
        _row("b", [True, True]),  # label pass, judge 0.5: false fail
        _row("c", [False]),  # label fail, judge 1.0: false pass
        _row("d", [False]),  # label fail, judge 0.0: agree
        _row("e", [True]),  # content filter: left out
    ]
    verdicts = [
        _verdict("a", 1.0),
        _verdict("b", 0.5),
        _verdict("c", 1.0),
        _verdict("d", 0.0),
        _verdict("e", 0.0, "judge_unavailable"),
    ]
    scored = cal.score_judge("gpt-4.1-mini", labels, verdicts)
    assert scored["answered"] == 4 and scored["calls"] == 5
    assert scored["agreement"] == 0.5
    assert (scored["false_pass"], scored["false_fail"]) == (1, 1)
    assert scored["false_pass_ids"] == ["c"] and scored["false_fail_ids"] == ["b"]
    assert scored["failures"] == {"judge_unavailable": ["e"]}
    # b carries two fields, both judged failed: fields a(1) + b(2) + c(1) + d(1) = 5, agree on a and d = 2.
    assert scored["fields_compared"] == 5
    assert scored["field_agreement"] == pytest.approx(2 / 5)
    assert scored["cost_per_100_usd"] is not None  # gpt-4.1-mini is priced


def test_missing_verdict_is_an_error() -> None:
    with pytest.raises(ValueError, match="no stored verdict"):
        cal.score_judge("gpt-4.1-mini", [_row("a", [True])], [])


def test_unpriced_model_has_no_cost_never_a_guess() -> None:
    scored = cal.score_judge("not-a-priced-model", [_row("a", [True])], [_verdict("a", 1.0)])
    assert scored["cost_per_100_usd"] is None


def _scored(agree: float, cost: float | None) -> dict[str, Any]:
    return {"agreement": agree, "cost_per_100_usd": cost, "on_common_cases": {"agreement": agree}}


def test_decision_keeps_default_when_cost_leg_fails() -> None:
    decision = cal.decide({"gpt-4.1-mini": _scored(0.6, 0.02), "gpt-4.1": _scored(0.9, 0.104)})
    assert decision["changed"] is False and decision["chosen"] == "gpt-4.1-mini"
    row = decision["candidates"][0]
    assert row["beats_by_margin"] and not row["under_cost_cap"] and not row["qualifies"]


def test_decision_keeps_default_when_candidate_is_unpriced() -> None:
    decision = cal.decide({"gpt-4.1-mini": _scored(0.6, 0.02), "x": _scored(0.9, None)})
    assert decision["changed"] is False
    assert decision["candidates"][0]["cost_known"] is False


def test_decision_keeps_default_inside_the_margin() -> None:
    decision = cal.decide({"gpt-4.1-mini": _scored(0.60, 0.02), "x": _scored(0.64, 0.03)})
    assert decision["changed"] is False


def test_decision_switches_only_on_both_legs_and_picks_the_best() -> None:
    decision = cal.decide(
        {
            "gpt-4.1-mini": _scored(0.60, 0.02),
            "a": _scored(0.70, 0.05),
            "b": _scored(0.80, 0.059),
            "c": _scored(0.95, 0.07),
        }
    )
    assert decision["changed"] is True and decision["chosen"] == "b"


def test_stored_results_are_current_with_labels_and_verdicts() -> None:
    results = cal.build_results(cal.load_labels(), cal.load_verdicts())
    assert cal.RESULTS_PATH.read_text(encoding="utf-8") == cal.dump_results(results)
    assert results["labels"]["reviewed"] == 0
    assert results["decision"]["changed"] is False
    assert results["decision"]["default"] == "gpt-4.1-mini"
    assert set(results["judges"]) >= {"gpt-4.1-mini", "gpt-4.1"}


def test_rescore_check_cli_passes_on_the_shipped_files(capsys: pytest.CaptureFixture[str]) -> None:
    assert cal.main(["--rescore", "--check"]) == 0
    assert "current" in capsys.readouterr().out


def test_estimate_calls_no_model_and_flags_unpriced(capsys: pytest.CaptureFixture[str]) -> None:
    plan = cal.estimate(cal.load_labels(), ["gpt-4.1-mini", "gpt-5.4-mini"])
    assert plan["judges"]["gpt-4.1-mini"]["priced"] is True
    assert plan["judges"]["gpt-5.4-mini"]["priced"] is False
    assert plan["judges"]["gpt-5.4-mini"]["guard_cost_usd"] is not None
