"""The scorecard generator: pure function of the committed baselines, and the committed page is current."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pytest

# scripts/ lives at the repo root, outside the agents/python project.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts import build_eval_scorecard as sc  # noqa: E402


def _baselines(root: Path) -> Path:
    return root / "agents" / "python" / "evals" / "baselines" / "python-inproc-replay"


def _write_suite(root: Path, name: str, agent: str, cases: int, **extra: object) -> None:
    datasets = root / "agents" / "python" / "evals" / "datasets"
    datasets.mkdir(parents=True, exist_ok=True)
    _baselines(root).mkdir(parents=True, exist_ok=True)
    (datasets / f"{name}.json").write_text(json.dumps([{}] * cases))
    snapshot = {
        "avg_groundedness": 0.5,
        "avg_correctness": 1.0,
        "avg_completeness": 0.25,
        "overall_score": 0.7,
        "agent_name": agent,
        "dataset_path": f"evals/datasets/{name}.json",
        **extra,
    }
    (_baselines(root) / f"{name}.json").write_text(json.dumps(snapshot))


def test_load_rows_counts_cases_and_skips_prompt_tokens(tmp_path: Path) -> None:
    _write_suite(tmp_path, "b_suite", "zeta", 3)
    _write_suite(tmp_path, "a_suite", "alpha", 5, total_tokens_in=1200, total_tokens_out=80)
    (_baselines(tmp_path) / "prompt_tokens.json").write_text("{}")

    rows = sc.load_rows(_baselines(tmp_path), tmp_path)

    assert [r.agent for r in rows] == ["alpha", "zeta"]
    assert rows[0].cases == 5
    assert rows[0].tokens_in == 1200
    assert rows[1].tokens_in is None


def test_render_states_replay_caveat_and_omits_missing_cost(tmp_path: Path) -> None:
    _write_suite(tmp_path, "a_suite", "alpha", 5)
    rows = sc.load_rows(_baselines(tmp_path), tmp_path)

    text = sc.render(rows, Counter({"model-x": 2}), "2026-01-01", "2026-01-02", {})

    assert "regression signal, not a quality measurement" in text
    assert "`model-x` (2 fixtures)" in text
    assert "| alpha | `a_suite.json` | 5 | 0.50 | 1.00 | 0.25 | 0.70 |" in text
    assert "None of the committed baselines carries token" in text


def test_render_shows_tokens_when_present(tmp_path: Path) -> None:
    _write_suite(tmp_path, "a_suite", "alpha", 5, total_tokens_in=1200, total_tokens_out=80)
    rows = sc.load_rows(_baselines(tmp_path), tmp_path)

    text = sc.render(rows, Counter(), "unknown", "unknown", {})

    assert "| alpha | 1,200 | 80 |" in text


def test_committed_page_is_current() -> None:
    assert sc.OUTPUT.read_text() == sc.build(), (
        "run: uv run --project agents/python python -m scripts.build_eval_scorecard"
    )


def test_check_flag_fails_on_stale_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stale = tmp_path / "eval-scorecard.md"
    stale.write_text("old")
    monkeypatch.setattr(sc, "OUTPUT", stale)

    assert sc.main(["--check"]) == 1
    stale.write_text(sc.build())
    assert sc.main(["--check"]) == 0


def test_live_baselines_get_their_own_labelled_table_and_never_join_the_replay_rows(tmp_path: Path) -> None:
    root = tmp_path / "baselines"
    (root / "python-inproc-replay").mkdir(parents=True)
    (root / "python-inproc-replay" / "a.json").write_text(json.dumps({"agent_name": "a", "overall_score": 1.0}))
    live_dir = root / "python-inproc-gpt-4.1"
    live_dir.mkdir()
    (live_dir / "a.json").write_text(
        json.dumps(
            {
                "agent_name": "a",
                "dataset_path": "evals/datasets/live/a.live.json",
                "overall_score": 0.5,
                "avg_groundedness": 0.4,
                "live": True,
                "repeats": 1,
                "recorded_on": "2026-11-24",
                "setup_key": "python-inproc-gpt-4.1",
                "setup": {"chat_model": "gpt-4.1", "judge": {"kind": "llm", "provider": "azure", "model": "j"}},
                "passed_cases": 3,
                "estimated_cost_usd": 0.5,
            }
        )
    )
    (live_dir / "not-live.json").write_text(json.dumps({"agent_name": "b", "overall_score": 0.1}))
    rows = sc.load_live(root, tmp_path)
    assert [r.agent for r in rows] == ["a"]
    text = "\n".join(sc.render_live(rows))
    assert "Live reference baselines (not a quality guarantee)" in text
    assert "azure:j" in text and "| 1 |" in text and "2026-11-24" in text
    assert sc.render_live([]) == []
