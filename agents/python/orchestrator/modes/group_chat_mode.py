"""``group-chat`` mode: sequential round-table debate over a shared transcript.

Wraps ``workflows/group_chat.py``'s ``GroupChatWorkflow`` — per the audit,
exercised only with synthetic sync responders in its own tests. This is
the first production caller: two agent-backed panelists (a value/pricing
perspective and a quality/reviews perspective), each seeing what the
previous ones said, followed by a moderator that synthesizes a verdict.
Per that module's own docstring, this is a *distinct* MAF pattern from
the others already wired — not a fan-out (``workflow:pre-purchase``) and
not an LLM tool router (``tool``) or mesh (``handoff``) — useful when a
decision benefits from multiple named perspectives rather than one
specialist's answer.

Required a small change to ``workflows/group_chat.py``: ``Responder`` was
strictly synchronous (every existing test only ever passed a plain
function). A real panelist needs an LLM call, which is async —
``_PanelistExecutor.run()`` now awaits the responder's result when it's
awaitable, so this mode can pass a coroutine-returning closure without
``workflows/group_chat.py`` needing to know anything about MAF ``Agent``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

from orchestrator.events import OrchestrationEvent, adapt_workflow_event
from shared.context import current_conversation_history
from shared.grounding.ledger import reset_grounding_ledger

from .base import ModeCapabilities, RunContext

_PANEL_PROMPTS = {
    "value": (
        "You are the value/pricing panelist on a purchase-decision round-table. "
        "Given the question and what's been said so far, give a short (2-3 sentence) "
        "take from a price-and-value angle only. Don't repeat prior speakers."
    ),
    "quality": (
        "You are the quality/reviews panelist on a purchase-decision round-table. "
        "Given the question and what's been said so far, give a short (2-3 sentence) "
        "take from a build-quality-and-reviews angle only. Don't repeat prior speakers."
    ),
}


def _format_transcript(transcript: list[dict[str, str]]) -> str:
    if not transcript:
        return "(no prior turns)"
    return "\n".join(f"{turn['speaker']}: {turn['text']}" for turn in transcript)


def _make_agent_responder(panel_name: str, instructions: str) -> Callable[[str, list[dict[str, str]]], Awaitable[str]]:
    async def _respond(question: str, transcript: list[dict[str, str]]) -> str:
        from agent_framework import Agent

        from shared.factory import get_chat_client
        from shared.middleware import build_specialist_middleware

        # Panelists are free-form LLM commentary with no tools attached, but
        # they were built with no middleware at all — meaning no PII
        # redaction, no injection detection, and (before this) no grounding
        # check on their own output. Same wiring point every other agent
        # uses; StepRecorderMiddleware/GroundingLedgerMiddleware are no-ops
        # here since there are no tool calls to record.
        agent = Agent(
            client=get_chat_client(),
            instructions=instructions,
            name=panel_name,
            middleware=build_specialist_middleware(),
        )
        prompt = f"Question: {question}\n\nTranscript so far:\n{_format_transcript(transcript)}"
        response = await agent.run(prompt)
        return response.text or f"({panel_name} had nothing to add)"

    return _respond


class GroupChatMode:
    name = "group-chat"
    label = "Group Chat (round-table debate)"
    description = (
        "MAF sequential workflow: named panelists take turns over a shared "
        "transcript — each sees prior turns before speaking — then a moderator "
        "synthesizes a verdict. Distinct from tool routing (one specialist call) "
        "and handoff (control changes hands); every panelist speaks."
    )
    capabilities = ModeCapabilities(streams=True, supports_hitl=False, supports_checkpoints=False, is_graph=True)

    def __init__(self, panelists: list[tuple[str, Callable]] | None = None) -> None:
        self._panelists = panelists

    def _resolve_panelists(self) -> list[tuple[str, Callable]]:
        if self._panelists is not None:
            return self._panelists
        return [(name, _make_agent_responder(name, instructions)) for name, instructions in _PANEL_PROMPTS.items()]

    async def run(self, message: str, ctx: RunContext) -> AsyncIterator[OrchestrationEvent]:
        from workflows.group_chat import GroupChatState, GroupChatWorkflow

        current_conversation_history.set(ctx.history)
        reset_grounding_ledger()

        panelists = self._resolve_panelists()
        maf_workflow = GroupChatWorkflow(panelists=panelists)._build()
        state = GroupChatState(question=message)

        final_state = state
        async for event in maf_workflow.run(state, stream=True):
            adapted = adapt_workflow_event(event)
            if adapted is not None:
                yield adapted
            if getattr(event, "type", None) == "output":
                data = getattr(event, "data", None)
                if isinstance(data, GroupChatState):
                    final_state = data

        # No "grounding" key here: each panelist's own agent.run() call still
        # runs GroundingVerificationMiddleware against its own turn's text
        # (see _make_agent_responder above), but that per-turn report isn't
        # threaded back through workflows/group_chat.py's transcript entries
        # ({"speaker", "text"} only) or GroupChatState. Surfacing it in the
        # UI would mean extending that data model, not the grounding code.
        agents_involved = [name for name, _ in panelists] + ["moderator"]
        yield OrchestrationEvent(
            kind="run_completed",
            payload={
                "text": final_state.verdict,
                "agents_involved": agents_involved,
                "transcript": final_state.transcript,
            },
        )

    def graph_mermaid(self) -> str | None:
        names = [name for name, _ in self._resolve_panelists()]
        if not names:
            return None
        node_ids = [f"panelist_{n}[{n}]" for n in names]
        edges = [f"  {a} --> {b}" for a, b in zip(node_ids, node_ids[1:])]
        edges.append(f"  {node_ids[-1]} --> moderator")
        return "graph LR\n" + "\n".join(edges)
