"""The group-chat panelist and eval-judge prompts moved to YAML; the text must not have changed.

Replay fixtures are keyed by the exact instruction text, so a drift here is a fixture miss.
``data/group_chat_prompt_snapshots.json`` is the pre-move literal, copied from git.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.scorers.llm_judge import _JUDGE_INSTRUCTIONS
from orchestrator.modes.group_chat_mode import _PANEL_PROMPTS
from shared.prompt_loader import load_raw_prompt

_SNAPSHOTS: dict[str, str] = json.loads(
    (Path(__file__).parent / "data" / "group_chat_prompt_snapshots.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("name", sorted(_SNAPSHOTS))
def test_raw_prompt_matches_snapshot(name: str) -> None:
    assert load_raw_prompt(name) == _SNAPSHOTS[name]


def test_modules_use_the_yaml_text() -> None:
    assert _PANEL_PROMPTS["value"] == _SNAPSHOTS["group-chat-value"]
    assert _PANEL_PROMPTS["quality"] == _SNAPSHOTS["group-chat-quality"]
    assert _JUDGE_INSTRUCTIONS == _SNAPSHOTS["_eval/completeness-judge"]


def test_missing_raw_prompt_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_raw_prompt("does-not-exist")
