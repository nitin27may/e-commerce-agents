"""Tests for orchestrator/modes/group_chat_mode.py.

GroupChatMode wraps the already-tested workflows/group_chat.py round-table
graph. Functional tests inject synthetic (sync and async) panelists via
the constructor override — mirroring test_workflow_group_chat.py's own
stubbing — so no LLM call is needed to prove the mode's event stream and
run_completed contract. A separate structural test proves the *default*
(no override) panelist wiring — the actual production path — builds real
callables for the expected names without invoking them (invoking would
need a live chat client).
"""

from __future__ import annotations

import pytest

from orchestrator.modes.base import RunContext
from orchestrator.modes.group_chat_mode import GroupChatMode, _format_transcript


def _value(_q: str, _t: list[dict[str, str]]) -> str:
    return "Great price for the feature set."


async def _quality(_q: str, transcript: list[dict[str, str]]) -> str:
    return f"Saw {len(transcript)} prior turn(s); build quality is excellent."


@pytest.mark.asyncio
async def test_group_chat_mode_runs_panelists_in_order_then_moderator() -> None:
    mode = GroupChatMode(panelists=[("value", _value), ("quality", _quality)])
    events = [e async for e in mode.run("Is the Sony WH-1000XM5 worth it?", RunContext(history=[]))]

    final = events[-1]
    assert final.kind == "run_completed"
    assert final.payload["agents_involved"] == ["value", "quality", "moderator"]
    assert [t["speaker"] for t in final.payload["transcript"]] == ["value", "quality"]
    assert "Saw 1 prior turn" in final.payload["transcript"][1]["text"]
    assert final.payload["text"]  # moderator verdict is non-empty


@pytest.mark.asyncio
async def test_group_chat_mode_supports_async_panelists() -> None:
    """The whole point of wiring this mode: real panelists are LLM calls,
    which are async — workflows/group_chat.py's Responder had to learn to
    await these (see that module's docstring)."""
    mode = GroupChatMode(panelists=[("quality", _quality)])
    events = [e async for e in mode.run("worth it?", RunContext(history=[]))]

    final = events[-1]
    assert final.payload["transcript"][0]["text"] == "Saw 0 prior turn(s); build quality is excellent."


@pytest.mark.asyncio
async def test_group_chat_mode_emits_node_events() -> None:
    mode = GroupChatMode(panelists=[("value", _value)])
    events = [e async for e in mode.run("worth it?", RunContext(history=[]))]

    assert any(e.kind == "node_enter" for e in events)
    assert any(e.kind == "node_exit" for e in events)


def test_group_chat_mode_graph_mermaid_reflects_panel_order() -> None:
    mode = GroupChatMode(panelists=[("value", _value), ("quality", _quality)])
    graph = mode.graph_mermaid()
    assert graph is not None
    assert "panelist_value" in graph
    assert "panelist_quality" in graph
    assert "moderator" in graph
    # value must precede quality in the edge list (turn order matters).
    assert graph.index("panelist_value") < graph.index("panelist_quality")


def test_group_chat_mode_default_panel_has_value_and_quality_names() -> None:
    """Structural check on the real production wiring (no override) — does
    not invoke the panelists, which would need a live chat client."""
    mode = GroupChatMode()
    panelists = mode._resolve_panelists()
    names = [name for name, _ in panelists]
    assert names == ["value", "quality"]
    assert all(callable(responder) for _, responder in panelists)


def test_format_transcript_handles_empty_and_populated() -> None:
    assert _format_transcript([]) == "(no prior turns)"
    formatted = _format_transcript([{"speaker": "value", "text": "cheap"}])
    assert formatted == "value: cheap"
