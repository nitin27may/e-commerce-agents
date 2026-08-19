"""Tests for the group-chat / round-table workflow (Track C2).

Runs the real MAF workflow with deterministic responders (no LLM): verifies
turn order, the shared transcript (each panelist sees prior turns), and the
moderator synthesis.
"""

from __future__ import annotations

import pytest

from workflows.group_chat import GroupChatState, GroupChatWorkflow, _default_synthesis


async def test_round_table_runs_turns_in_order() -> None:
    def value(_q: str, _t: list[dict[str, str]]) -> str:
        return "Great price for the feature set."

    def quality(_q: str, transcript: list[dict[str, str]]) -> str:
        return f"Saw {len(transcript)} prior turn(s); build quality is excellent."

    wf = GroupChatWorkflow(panelists=[("value", value), ("quality", quality)])
    state = await wf.execute("Is the Sony WH-1000XM5 worth it?")

    assert [t["speaker"] for t in state.transcript] == ["value", "quality"]
    # The shared transcript means the quality panelist saw the value turn.
    assert "Saw 1 prior turn" in state.transcript[1]["text"]
    assert state.completed_steps == ["value", "quality", "moderator"]
    assert state.verdict


async def test_custom_synthesizer_used() -> None:
    wf = GroupChatWorkflow(
        panelists=[("solo", lambda _q, _t: "one opinion")],
        synthesizer=lambda _s: "FINAL VERDICT",
    )
    state = await wf.execute("worth it?")
    assert state.verdict == "FINAL VERDICT"


async def test_panelist_failure_is_contained() -> None:
    def boom(_q: str, _t: list[dict[str, str]]) -> str:
        raise RuntimeError("model down")

    wf = GroupChatWorkflow(panelists=[("flaky", boom)])
    state = await wf.execute("q?")
    assert "could not respond" in state.transcript[0]["text"]
    assert state.verdict  # moderator still synthesizes


async def test_requires_at_least_one_panelist() -> None:
    with pytest.raises(ValueError):
        await GroupChatWorkflow(panelists=[]).execute("q?")


def test_default_synthesis_mentions_speakers() -> None:
    state = GroupChatState(question="q?", transcript=[{"speaker": "value", "text": "t"}])
    assert "value" in _default_synthesis(state)


async def test_async_responder_is_awaited() -> None:
    """An agent-backed panelist returns a coroutine, not a str — the
    executor must await it rather than threading the coroutine object
    itself into the transcript."""

    async def value(_q: str, _t: list[dict[str, str]]) -> str:
        return "Great price for the feature set."

    def quality(_q: str, transcript: list[dict[str, str]]) -> str:
        return f"Saw {len(transcript)} prior turn(s); build quality is excellent."

    wf = GroupChatWorkflow(panelists=[("value", value), ("quality", quality)])
    state = await wf.execute("Is the Sony WH-1000XM5 worth it?")

    assert state.transcript[0]["text"] == "Great price for the feature set."
    assert "Saw 1 prior turn" in state.transcript[1]["text"]
