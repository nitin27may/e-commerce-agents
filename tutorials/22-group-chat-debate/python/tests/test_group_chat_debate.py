"""
Chapter 22 — Group-Chat Debate: tests.

No LLM — the panelists are plain callables, so every assertion is exact
(same precedent as Chapter 09's and Chapter 30's LLM-free workflow tests).

This chapter is unusual in the series: it imports the *production*
`workflows.group_chat` module out of `agents/python` rather than shipping a
self-contained example, so the sys.path setup below reaches into the backend
package the way the chapter's own README tells readers to run it.

That import is also why this file exists at all. The production module has its
own tests in `agents/python/tests/test_workflow_group_chat.py`, and for a long
time that was treated as covering this chapter too. It does not: it covers the
module, not the chapter. The demo's own panelists, its synthesizer, and the
claim the chapter is built on — that a later panelist can see earlier turns —
were untested. That claim is the one thing a reader takes away, and it is
invisible from the demo's output: two panelists who happen not to reference
each other produce the same transcript whether the state was shared or not.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

# The chapter runs from agents/python so `workflows` resolves — see its README.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4] / "agents" / "python"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from main import quality_voice, synthesize, value_voice  # noqa: E402
from workflows.group_chat import GroupChatState, GroupChatWorkflow  # noqa: E402

QUESTION = "Is the Sony WH-1000XM5 worth it?"


def _says(text: str):
    return lambda question, transcript: text


def _reports_prior_speakers():
    def responder(question, transcript):
        if not transcript:
            return "I spoke first"
        return "I heard: " + ", ".join(turn["speaker"] for turn in transcript)

    return responder


# ─────────────── Sequencing ───────────────


async def test_every_panelist_speaks_once_in_declared_order() -> None:
    workflow = GroupChatWorkflow(
        panelists=[("first", _says("a")), ("second", _says("b")), ("third", _says("c"))]
    )

    state = await workflow.execute(QUESTION)

    assert [turn["speaker"] for turn in state.transcript] == ["first", "second", "third"]
    assert [turn["text"] for turn in state.transcript] == ["a", "b", "c"]


async def test_a_later_panelist_sees_every_earlier_turn() -> None:
    # The assertion the pattern lives on. Without a shared transcript the third
    # panelist reports "I spoke first" — and nothing else in the run looks wrong.
    workflow = GroupChatWorkflow(
        panelists=[
            ("first", _reports_prior_speakers()),
            ("second", _reports_prior_speakers()),
            ("third", _reports_prior_speakers()),
        ]
    )

    state = await workflow.execute(QUESTION)

    assert state.transcript[0]["text"] == "I spoke first"
    assert state.transcript[1]["text"] == "I heard: first"
    assert state.transcript[2]["text"] == "I heard: first, second"


async def test_a_panelist_cannot_see_turns_that_have_not_happened_yet() -> None:
    # The other half of the ordering guarantee, and not implied by the test
    # above: a transcript shared by reference could expose later turns if the
    # panel ran concurrently.
    seen: list[int] = []

    def counting(question, transcript):
        seen.append(len(transcript))
        return "ok"

    await GroupChatWorkflow(
        panelists=[("a", counting), ("b", counting), ("c", counting)]
    ).execute(QUESTION)

    assert seen == [0, 1, 2]


async def test_the_question_reaches_every_panelist() -> None:
    questions: list[str] = []

    def capture(question, transcript):
        questions.append(question)
        return "ok"

    await GroupChatWorkflow(panelists=[("a", capture), ("b", capture)]).execute(QUESTION)

    assert questions == [QUESTION, QUESTION]


# ─────────────── The moderator ───────────────


async def test_the_moderator_runs_last_and_is_recorded() -> None:
    # completed_steps is the audit trail. A moderator that ran before the last
    # panelist would still produce a plausible verdict — from a short transcript.
    state = await GroupChatWorkflow(
        panelists=[("value", _says("cheap")), ("quality", _says("solid"))]
    ).execute(QUESTION)

    assert state.completed_steps == ["value", "quality", "moderator"]


async def test_the_default_synthesis_names_every_speaker() -> None:
    state = await GroupChatWorkflow(
        panelists=[("value", _says("cheap")), ("quality", _says("solid"))]
    ).execute(QUESTION)

    assert "2 perspective(s)" in state.verdict
    assert "value, quality" in state.verdict
    assert QUESTION in state.verdict


async def test_a_custom_synthesizer_replaces_the_default() -> None:
    state = await GroupChatWorkflow(
        panelists=[("value", _says("cheap"))],
        synthesizer=lambda _state: "MY VERDICT",
    ).execute(QUESTION)

    assert state.verdict == "MY VERDICT"


async def test_the_synthesizer_sees_the_complete_transcript() -> None:
    # Handed a copy taken before the last turn, this would read 1 — and the
    # verdict would be confidently wrong rather than obviously broken.
    seen = -1

    def synthesizer(state: GroupChatState) -> str:
        nonlocal seen
        seen = len(state.transcript)
        return "done"

    await GroupChatWorkflow(
        panelists=[("a", _says("x")), ("b", _says("y"))], synthesizer=synthesizer
    ).execute(QUESTION)

    assert seen == 2


# ─────────────── Failure handling ───────────────


async def test_a_failing_panelist_becomes_a_visible_turn() -> None:
    # A round-table that dies because one specialist timed out is worse than one
    # that reports a missing voice: the moderator can reconcile around a gap it
    # can see.
    def boom(question, transcript):
        raise RuntimeError("provider down")

    state = await GroupChatWorkflow(
        panelists=[("value", _says("cheap")), ("quality", boom)]
    ).execute(QUESTION)

    assert len(state.transcript) == 2
    assert "quality could not respond" in state.transcript[1]["text"]
    assert "provider down" in state.transcript[1]["text"]
    assert state.verdict


async def test_a_panelist_after_a_failure_still_runs_and_sees_the_failed_turn() -> None:
    def boom(question, transcript):
        raise RuntimeError("nope")

    state = await GroupChatWorkflow(
        panelists=[("a", boom), ("b", _reports_prior_speakers())]
    ).execute(QUESTION)

    assert state.transcript[1]["text"] == "I heard: a"
    assert state.completed_steps == ["a", "b", "moderator"]


async def test_an_empty_panel_is_rejected() -> None:
    # A moderator summarizing silence would emit a confident verdict about
    # nothing at all.
    import pytest

    with pytest.raises(ValueError, match="at least one panelist"):
        await GroupChatWorkflow(panelists=[]).execute(QUESTION)


async def test_a_single_panelist_panel_still_reaches_the_moderator() -> None:
    state = await GroupChatWorkflow(panelists=[("solo", _says("x"))]).execute(QUESTION)

    assert len(state.transcript) == 1
    assert state.completed_steps == ["solo", "moderator"]
    assert state.verdict


# ─────────────── The chapter's own demo ───────────────


async def test_the_demo_panel_produces_a_two_turn_debate_and_a_verdict() -> None:
    state = await GroupChatWorkflow(
        panelists=[("value", value_voice), ("quality", quality_voice)],
        synthesizer=synthesize,
    ).execute(QUESTION)

    assert [turn["speaker"] for turn in state.transcript] == ["value", "quality"]
    assert "recommended" in state.verdict


async def test_the_quality_panelist_demonstrably_reads_the_transcript() -> None:
    # The demo's own proof that this is a debate. If the quality voice ever
    # stopped counting prior turns, the chapter would keep running and stop
    # demonstrating its own subject.
    state = await GroupChatWorkflow(
        panelists=[("value", value_voice), ("quality", quality_voice)],
        synthesizer=synthesize,
    ).execute(QUESTION)

    assert "1 prior point(s)" in state.transcript[1]["text"]


async def test_an_async_panelist_is_awaited() -> None:
    # The Responder contract allows a coroutine so a real agent-backed panelist
    # fits without changing the signature — this is the path
    # orchestrator/modes/group_chat_mode.py takes in production.
    async def async_voice(question, transcript):
        return "async take"

    state = await GroupChatWorkflow(panelists=[("async", async_voice)]).execute(QUESTION)

    assert state.transcript[0]["text"] == "async take"
