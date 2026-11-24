"""``--mode``: the orchestrator quality suite scored through each orchestration mode. No model, no database."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.baselines import REFERENCE_ROOT
from evals.evaluator import MODE_NAMES, AgentEvaluator, EvalCase, load_dataset, select_for_mode
from evals.records import Setup
from orchestrator.modes import MODES

DATASETS = Path(__file__).resolve().parent.parent / "evals" / "datasets"


def _case(**kw: object) -> EvalCase:
    base: dict[str, object] = {
        "input": "q",
        "expected_tools": ["call_specialist_agent"],
        "expected_fields": [],
        "criteria": {},
    }
    base.update(kw)
    return EvalCase(**base)  # type: ignore[arg-type]


def test_mode_names_match_the_registry() -> None:
    assert set(MODE_NAMES) == set(MODES)


def test_default_key_is_unchanged_and_a_mode_gets_its_own_directory() -> None:
    plain = Setup(chat_provider="replay", chat_model="gpt-4.1")
    assert plain.key == "python-inproc-replay"
    moded = plain.model_copy(update={"orchestration_mode": "workflow:pre-purchase"})
    assert moded.key == "python-inproc-replay-mode-workflow-pre-purchase"
    live = Setup(chat_provider="azure", chat_model="gpt-4.1", orchestration_mode="handoff")
    assert live.key == "python-inproc-gpt-4.1-mode-handoff"


def test_tool_selection_keeps_cases_without_modes_and_those_naming_tool() -> None:
    cases = [_case(), _case(modes=["tool", "handoff"]), _case(modes=["group-chat"])]
    assert [i for i, _ in select_for_mode(cases, None)] == [0, 1]
    assert [i for i, _ in select_for_mode(cases, "tool")] == [0, 1]


def test_other_mode_selects_only_listed_cases_sets_mode_and_applies_overrides() -> None:
    cases = [
        _case(),
        _case(
            modes=["tool", "handoff"],
            mode_overrides={"handoff": {"expected_tools": [], "expected_tool_args": None}},
            expected_tool_args={"call_specialist_agent": {"agent_name": "x"}},
        ),
    ]
    picked = select_for_mode(cases, "handoff")
    assert [i for i, _ in picked] == [1]  # the dataset index survives the filter
    case = picked[0][1]
    assert case.mode == "handoff"
    assert case.expected_tools == [] and case.expected_tool_args is None
    # the original is untouched, so the same dataset still serves the tool run
    assert cases[1].mode is None and cases[1].expected_tools == ["call_specialist_agent"]


def test_loader_rejects_unknown_modes_and_overrides(tmp_path: Path) -> None:
    def write(entry: dict) -> Path:
        path = tmp_path / "d.json"
        path.write_text(
            json.dumps([{"input": "q", "expected_tools": [], "expected_fields": [], "criteria": {}, **entry}])
        )
        return path

    with pytest.raises(ValueError, match="unknown mode"):
        load_dataset(write({"modes": ["magentic"]}))
    with pytest.raises(ValueError, match="not in modes"):
        load_dataset(write({"modes": ["tool"], "mode_overrides": {"handoff": {}}}))
    with pytest.raises(ValueError, match="allowed"):
        load_dataset(write({"modes": ["handoff"], "mode_overrides": {"handoff": {"input": "other"}}}))


def test_mode_is_orchestrator_only() -> None:
    setup = Setup(chat_provider="replay", chat_model="gpt-4.1")
    with pytest.raises(ValueError, match="orchestrator only"):
        AgentEvaluator("product-discovery", setup=setup, orchestration_mode="handoff")
    evaluator = AgentEvaluator("orchestrator", setup=setup, orchestration_mode="handoff")
    assert evaluator.setup.key.endswith("-mode-handoff")
    assert AgentEvaluator("orchestrator", setup=setup, orchestration_mode="tool").setup.key == "python-inproc-replay"


@pytest.mark.parametrize("dataset", ["orchestrator_routing.json", "orchestrator_modes.json"])
def test_every_mode_listed_in_a_replay_dataset_has_a_committed_baseline(dataset: str) -> None:
    cases = load_dataset(DATASETS / dataset)
    modes = {m for c in cases for m in c.modes or [] if m != "tool"}
    assert modes
    for mode in sorted(modes):
        baseline = REFERENCE_ROOT / f"python-inproc-replay-mode-{mode.replace(':', '-')}" / Path(dataset).name
        assert baseline.is_file(), f"{mode}: no baseline at {baseline}"
        stored = json.loads(baseline.read_text())
        assert stored["setup"]["orchestration_mode"] == mode
        assert stored["dataset_cases"] == len(select_for_mode(cases, mode))


def test_live_twins_list_the_same_modes() -> None:
    live = load_dataset(DATASETS / "live" / "orchestrator_modes.live.json")
    replay = load_dataset(DATASETS / "orchestrator_modes.json")
    assert [c.modes for c in live] == [c.modes for c in replay]
    routing = load_dataset(DATASETS / "live" / "orchestrator_routing.live.json")
    assert any(c.modes and "handoff" in c.modes for c in routing)
    assert all(c.why and c.mode_overrides is not None for c in routing if c.modes)
