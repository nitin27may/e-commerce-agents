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


# ─────────────────────── baselines keyed by setup ───────────────────────


def test_reference_baselines_live_under_the_setup_key_and_exist_for_replay() -> None:
    from evals.baselines import REFERENCE_ROOT, reference_path

    path = reference_path("python-inproc-replay", "product_discovery")
    assert path == REFERENCE_ROOT / "python-inproc-replay" / "product_discovery.json"
    assert path.exists()
    assert not (REFERENCE_ROOT / "product_discovery.json").exists()  # nothing left at the unkeyed location


def test_personal_baselines_are_git_ignored_and_kind_follows_location(tmp_path: Path) -> None:
    import subprocess

    from evals.baselines import baseline_kind_for, personal_path, reference_path

    personal = personal_path("python-inproc-gpt-5.1", "order_management")
    assert personal.parts[-4:] == (".local", "baselines", "python-inproc-gpt-5.1", "order_management.json")
    assert baseline_kind_for(personal) == "personal"
    assert baseline_kind_for(reference_path("python-inproc-replay", "order_management")) == "reference"
    assert baseline_kind_for(tmp_path / "x.json") == "reference"
    ignored = subprocess.run(["git", "check-ignore", "-q", str(personal)], cwd=Path(__file__).parent)
    assert ignored.returncode == 0


def test_a_written_baseline_carries_its_setup_repeats_and_spread(tmp_path: Path) -> None:
    from evals.records import Setup

    summary = _summary()
    summary.setup = Setup(chat_provider="openai", chat_model="gpt-5.1")
    summary.repeats = 3
    summary.dataset_cases = 5
    summary.spread = {"overall_score": 0.02}
    path = tmp_path / "b.json"

    write_baseline(path, summary)
    loaded = load_baseline(path)

    assert loaded is not None
    assert loaded["setup_key"] == "python-inproc-gpt-5.1"
    assert (loaded["repeats"], loaded["dataset_cases"], loaded["spread"]) == (3, 5, {"overall_score": 0.02})
