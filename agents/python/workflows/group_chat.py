"""Group-chat / round-table debate workflow — MAF sequential multi-perspective pattern.

A *distinct* MAF pattern from the others in this package:

- ``pre_purchase`` is concurrent fan-out/fan-in (independent probes, merged once).
- ``return_replace`` is sequential with a human-in-the-loop gate.
- ``group_chat`` (here) is a sequential **round-table**: panelists take turns over
  a *shared transcript* — each panelist sees what the previous ones said — and a
  moderator synthesizes a final verdict. It models a "is this worth buying?"
  debate between, e.g., a value/pricing perspective and a quality/reviews one.

The panelist behavior is injected as a ``Responder`` callable so the workflow is
deterministic and unit-testable without an LLM; production code can pass
agent-backed responders.

Imports use the ``agent_framework._workflows`` submodules (not the package root):
the agent-framework v1.0 beta ships an empty top-level ``__init__`` in a plain
checkout, so the stable submodule paths are what the other workflows use too.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from agent_framework._workflows._executor import Executor, handler
from agent_framework._workflows._workflow_builder import WorkflowBuilder
from agent_framework._workflows._workflow_context import WorkflowContext

logger = logging.getLogger(__name__)

# Given the question and the running transcript (prior turns), return this
# panelist's contribution — either directly (sync, what every test in this
# module uses) or as an awaitable (async, for a real agent-backed panelist —
# see orchestrator/modes/group_chat_mode.py, the first production caller).
Responder = Callable[[str, list[dict[str, str]]], "str | Awaitable[str]"]


@dataclass
class GroupChatState:
    """Shared state threaded through the round-table."""

    question: str
    transcript: list[dict[str, str]] = field(default_factory=list)
    verdict: str = ""
    completed_steps: list[str] = field(default_factory=list)


def _default_synthesis(state: GroupChatState) -> str:
    speakers = ", ".join(turn["speaker"] for turn in state.transcript)
    return f"Synthesized {len(state.transcript)} perspective(s) ({speakers}) on: {state.question}"


# ─────────────────────────── Executors ───────────────────────────


class _PanelistExecutor(Executor):
    """One panelist's turn: append a contribution to the shared transcript."""

    def __init__(self, name: str, responder: Responder) -> None:
        super().__init__(id=f"panelist-{name}")
        self._name = name
        self._responder = responder

    @handler
    async def run(self, state: GroupChatState, ctx: WorkflowContext[GroupChatState, GroupChatState]) -> None:
        try:
            result = self._responder(state.question, list(state.transcript))
            text = await result if inspect.isawaitable(result) else result
        except Exception as exc:  # pragma: no cover - defensive
            text = f"({self._name} could not respond: {exc})"
            logger.exception("group_chat.panelist_failed name=%s", self._name)
        state.transcript.append({"speaker": self._name, "text": text})
        state.completed_steps.append(self._name)
        await ctx.send_message(state)


class _ModeratorExecutor(Executor):
    """Final turn: synthesize the transcript into a verdict and emit it."""

    def __init__(self, synthesizer: Callable[[GroupChatState], str] | None = None) -> None:
        super().__init__(id="moderator")
        self._synthesizer = synthesizer or _default_synthesis

    @handler
    async def run(self, state: GroupChatState, ctx: WorkflowContext[None, GroupChatState]) -> None:
        state.verdict = self._synthesizer(state)
        state.completed_steps.append("moderator")
        await ctx.yield_output(state)


# ─────────────────────────── Workflow ───────────────────────────


@dataclass
class GroupChatWorkflow:
    """A sequential round-table of panelists followed by a moderator.

    Args:
        panelists: ordered ``(name, responder)`` pairs; each runs once, in order,
            seeing the transcript accumulated by earlier panelists.
        synthesizer: optional verdict builder over the final state.
    """

    panelists: list[tuple[str, Responder]]
    synthesizer: Callable[[GroupChatState], str] | None = None

    def _build(self) -> Any:
        if not self.panelists:
            raise ValueError("group chat needs at least one panelist")
        execs = [_PanelistExecutor(name, responder) for name, responder in self.panelists]
        moderator = _ModeratorExecutor(self.synthesizer)
        builder = WorkflowBuilder(start_executor=execs[0], name="group-chat-debate")
        for upstream, downstream in zip(execs, execs[1:]):
            builder = builder.add_edge(upstream, downstream)
        builder = builder.add_edge(execs[-1], moderator)
        return builder.build()

    async def execute(self, question: str) -> GroupChatState:
        """Run the round-table and return the final populated state."""
        workflow = self._build()
        final = GroupChatState(question=question)
        async for event in workflow.run(final, stream=True):
            if getattr(event, "type", None) == "output":
                data = getattr(event, "data", None)
                if isinstance(data, GroupChatState):
                    final = data
        return final
