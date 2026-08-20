"""Tests for evals/baselines.py — stored eval-score snapshots + regression detection."""

from __future__ import annotations

from pathlib import Path

from evals.baselines import check_regression, load_baseline, write_baseline
from evals.evaluator import EvalSummary


def _summary(**overrides: float) -> EvalSummary:
    defaults = {
        "avg_groundedness": 0.9,
        "avg_correctness": 0.85,
        "avg_completeness": 0.8,
        "overall_score": 0.86,
    }
    defaults.update(overrides)
    return EvalSummary(agent_name="product-discovery", dataset_path="evals/datasets/product_discovery.json", **defaults)


def test_load_baseline_returns_none_when_missing(tmp_path: Path) -> None:
    assert load_baseline(tmp_path / "does-not-exist.json") is None


def test_write_then_load_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    write_baseline(path, _summary())

    loaded = load_baseline(path)
    assert loaded is not None
    assert loaded["overall_score"] == 0.86
    assert loaded["agent_name"] == "product-discovery"


def test_write_baseline_creates_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "baseline.json"
    write_baseline(path, _summary())
    assert path.exists()


def test_check_regression_no_drop_passes() -> None:
    baseline = {"overall_score": 0.86, "avg_groundedness": 0.9, "avg_correctness": 0.85, "avg_completeness": 0.8}
    regressed, _ = check_regression(baseline, _summary(), max_regression=0.05)
    assert regressed is False


def test_check_regression_small_drop_within_tolerance_passes() -> None:
    baseline = {"overall_score": 0.90, "avg_groundedness": 0.9, "avg_correctness": 0.85, "avg_completeness": 0.8}
    # overall_score dropped 0.04, tolerance is 0.05 — not a regression.
    regressed, message = check_regression(baseline, _summary(overall_score=0.86), max_regression=0.05)
    assert regressed is False
    assert "REGRESSION" not in message


def test_check_regression_large_drop_fails() -> None:
    baseline = {"overall_score": 0.95, "avg_groundedness": 0.9, "avg_correctness": 0.85, "avg_completeness": 0.8}
    regressed, message = check_regression(baseline, _summary(overall_score=0.70), max_regression=0.05)
    assert regressed is True
    assert "REGRESSION" in message


def test_check_regression_improvement_never_flagged() -> None:
    baseline = {"overall_score": 0.5, "avg_groundedness": 0.5, "avg_correctness": 0.5, "avg_completeness": 0.5}
    regressed, message = check_regression(baseline, _summary(), max_regression=0.05)
    assert regressed is False
    assert "REGRESSION" not in message
