"""Live reference baselines: marked as live, keyed apart from replay, and never used by the replay gate."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from evals.baselines import REFERENCE_ROOT, load_baseline, reference_path, write_baseline
from evals.evaluator import EvalSummary
from evals.records import JudgeSetup, Setup

REPO_ROOT = Path(__file__).resolve().parents[3]
REPLAY_KEY = "python-inproc-replay"


def _setup(provider: str, model: str, judge: JudgeSetup | None = None) -> Setup:
    return Setup(chat_provider=provider, chat_model=model, judge=judge or JudgeSetup())


def _summary(setup: Setup) -> EvalSummary:
    return EvalSummary(
        agent_name="pricing-promotions",
        dataset_path="evals/datasets/live/pricing_promotions.live.json",
        setup=setup,
        avg_groundedness=0.8,
        avg_correctness=0.9,
        avg_completeness=0.85,
        overall_score=0.86,
    )


def test_live_and_replay_setups_have_different_baseline_directories() -> None:
    live = _setup("azure", "gpt-4.1")
    replay = _setup("replay", "replay")
    assert replay.key == REPLAY_KEY
    assert live.key == "python-inproc-gpt-4.1"
    assert reference_path(live.key, "x").parent != reference_path(replay.key, "x").parent


def test_a_live_baseline_says_in_the_file_that_it_is_live_and_k1(tmp_path: Path) -> None:
    judge = JudgeSetup(kind="llm", provider="azure", model="gpt-5.4-mini", self_judged=False)
    path = tmp_path / "b.json"
    write_baseline(path, _summary(_setup("azure", "gpt-4.1", judge)))
    data = load_baseline(path)
    assert data is not None
    assert data["live"] is True
    assert data["repeats"] == 1
    assert "recorded_on" in data
    caveat = str(data["caveat"])
    assert "k=1" in caveat and "not a quality guarantee" in caveat and "azure:gpt-5.4-mini" in caveat
    assert "spread" not in data


def test_a_replay_baseline_carries_no_live_marker(tmp_path: Path) -> None:
    path = tmp_path / "b.json"
    write_baseline(path, _summary(_setup("replay", "replay")))
    data = load_baseline(path)
    assert data is not None
    assert "live" not in data and "caveat" not in data


def test_every_committed_live_baseline_is_marked_and_keyed_apart() -> None:
    live_dirs = [d for d in REFERENCE_ROOT.iterdir() if d.is_dir() and d.name != REPLAY_KEY]
    assert live_dirs, "expected at least one live baseline directory"
    for directory in live_dirs:
        for path in directory.glob("*.json"):
            data = json.loads(path.read_text())
            assert data["live"] is True, path
            assert data["setup_key"] == directory.name, path
            assert data["setup"]["chat_provider"] != "replay", path
            assert data["repeats"] >= 1 and "caveat" in data, path
            if data["setup"]["judge"]["self_judged"]:
                assert "self-judged" in data["caveat"], path


def test_the_replay_directory_holds_only_replay_baselines() -> None:
    for path in (REFERENCE_ROOT / REPLAY_KEY).glob("*.json"):
        assert "live" not in json.loads(path.read_text()), path


def test_the_replay_gate_only_names_the_replay_baseline_directory() -> None:
    """scripts/replay-evals.sh and the PR workflow must never point at a live setup's directory."""
    script = (REPO_ROOT / "scripts" / "replay-evals.sh").read_text()
    assert f'BASELINE_DIR="evals/baselines/{REPLAY_KEY}"' in script
    mentioned = set(re.findall(r"evals/baselines/([A-Za-z0-9._-]+)", script))
    assert mentioned <= {REPLAY_KEY}, mentioned
    workflow = (REPO_ROOT / ".github" / "workflows" / "evals.yml").read_text()
    assert set(re.findall(r"evals/baselines/([A-Za-z0-9._-]+)", workflow)) <= {REPLAY_KEY, "prompt_tokens.json"}
    # The live script compares against no baseline at all: the threshold is its gate.
    code = [
        ln
        for ln in (REPO_ROOT / "scripts" / "live-evals.sh").read_text().splitlines()
        if not ln.lstrip().startswith("#")
    ]
    assert not [ln for ln in code if re.search(r"--(baseline|baseline-from)\b", ln) and "echo" not in ln], code


@pytest.mark.parametrize("stem", ["product_discovery", "inventory_fulfillment"])
def test_a_replay_run_resolves_its_baseline_by_setup_key_never_to_a_live_file(stem: str) -> None:
    resolved = reference_path(_setup("replay", "replay").key, stem)
    assert resolved.parent.name == REPLAY_KEY
    assert load_baseline(resolved) is not None
    assert "live" not in (load_baseline(resolved) or {})


def test_all_six_live_suites_have_a_baseline_for_the_reference_setup() -> None:
    stems = {path.stem for path in (REFERENCE_ROOT / "python-inproc-gpt-4.1").glob("*.json")}
    assert stems == {
        "product_discovery",
        "order_management",
        "pricing_promotions",
        "review_sentiment",
        "inventory_fulfillment",
        "orchestrator_routing",
    }
    for stem in stems:
        data = json.loads((REFERENCE_ROOT / "python-inproc-gpt-4.1" / f"{stem}.json").read_text())
        assert data["total_cases"] == data["dataset_cases"], stem  # every case ran: none refused or skipped
