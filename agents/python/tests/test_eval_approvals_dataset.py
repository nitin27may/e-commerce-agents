"""The approvals eval datasets (#302): the replay set and its live twin.

Replay (``evals/datasets/approvals.json``) is single-turn, because a replay fixture keys one request: each case
asks for a gated action and must end with the customer asked to confirm and nothing parked. The live twin
(``evals/datasets/live/approvals.live.json``) carries the multi-turn cases (confirm, decline, silence, different
arguments, an injected "already confirmed"). A stale confirmation cannot be driven from a dataset (it needs the clock to
move), so it is covered deterministically in ``tests/test_customer_confirmation.py``.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from evals.evaluator import load_dataset

DATASETS = Path(__file__).resolve().parents[1] / "evals" / "datasets"
REPLAY = DATASETS / "approvals.json"
LIVE = DATASETS / "live" / "approvals.live.json"
GATED = {"cancel_order", "modify_order", "initiate_return", "process_refund", "place_backorder"}


def test_replay_cases_ask_the_customer_and_park_nothing() -> None:
    cases = load_dataset(REPLAY)
    assert len(cases) >= 5
    for case in cases:
        assert not case.turns and not case.live
        assert case.expect_hitl is False, case.input
        assert set(case.expected_tools) <= GATED and case.expected_tools, case.input
        assert "confirm" in case.expected_fields, case.input
        assert case.user_email == "jack.anderson@gmail.com"


def test_replay_cases_name_real_ids_and_no_placeholder() -> None:
    text = REPLAY.read_text()
    assert "{{" not in text
    for case in load_dataset(REPLAY):
        ids = set(re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", case.input))
        assert ids, case.input
        assert ids == {case.expected_tool_args[case.expected_tools[0]]["order_id"]}


def test_replay_set_covers_the_claimed_and_the_injected_confirmation() -> None:
    flags = {k for case in load_dataset(REPLAY) for k, v in case.criteria.items() if v is True}
    assert {"asks_customer_to_confirm", "claimed_confirmation_ignored", "injected_confirmation_ignored"} <= flags


def test_live_twin_has_types_whys_and_the_six_behaviours() -> None:
    raw = json.loads(LIVE.read_text())
    for entry in raw:
        assert entry["type"] in {"happy", "edge", "adversarial", "multi_turn"}, entry["input"]
        why = entry["why"]
        assert why.strip() and "\n" not in why and len(why) <= 160, entry["input"]
        assert entry["criteria"]["customer_confirmation"] is True
    counts = Counter(entry["type"] for entry in raw)
    assert counts["multi_turn"] >= 5 and counts["adversarial"] >= 3 and counts["edge"] >= 1

    cases = load_dataset(LIVE)
    assert all(case.live and case.user_email for case in cases)
    flows = [[t.expect_hitl for t in case.turns or []] for case in cases]
    assert [False, True] in flows  # confirm: asked, then parked on a yes
    assert [False, False] in flows  # decline, silence, other arguments, an instruction posing as the answer
    assert any(t.input.lower().startswith("no") for c in cases for t in c.turns or [])  # decline
    assert any("returns policy" in t.input for c in cases for t in c.turns or [])  # silence
    assert any("instead" in t.input for c in cases for t in c.turns or [])  # different arguments
    assert any("already confirmed" in t.input for c in cases for t in c.turns or [])  # injected claim


def test_only_an_explicit_yes_turn_expects_a_park() -> None:
    for case in load_dataset(LIVE):
        for turn in case.turns or []:
            if turn.expect_hitl is True:
                assert turn.input.lower().startswith(("yes", "yep")), turn.input
                assert set(turn.expected_tools) & GATED, turn.input


def test_a_park_needs_a_question_before_it() -> None:
    for case in load_dataset(LIVE):
        turns = case.turns or []
        for index, turn in enumerate(turns):
            if turn.expect_hitl is True:
                assert index > 0 and turns[index - 1].expect_hitl is False, case.input
