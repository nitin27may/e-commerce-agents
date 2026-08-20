"""``OrchestrationMode`` protocol + the request-scoped context every mode's ``run()`` takes.

Identity (user email/role/session) is deliberately *not* on ``RunContext`` —
this repo's convention is ContextVars (``shared/context.py``), read directly
by whatever needs them (tools, ``call_specialist_agent``, A2A header
building), never passed as function arguments. ``RunContext`` carries only
what a mode needs that isn't already ambient: conversation history for this
turn, and the resolved conversation id for anything a mode wants to persist
mid-run (none do yet — Phase 1.5's checkpointing is the first consumer).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol

from orchestrator.events import OrchestrationEvent


@dataclass(frozen=True)
class ModeCapabilities:
    """What a mode supports — surfaced via ``GET /api/orchestration/modes``
    so the web UI (Phase 1.6a) can show capability chips without hardcoding
    per-mode knowledge, and so ``chat.py`` can make a "does this mode support
    what this request needs" decision generically instead of a chain of
    ``if mode_name == "x"`` checks.
    """

    streams: bool = True
    supports_hitl: bool = False
    supports_checkpoints: bool = False
    is_graph: bool = False


@dataclass
class RunContext:
    """Request-scoped, non-identity state passed into every mode's ``run()``."""

    history: list[dict[str, str]] = field(default_factory=list)
    conversation_id: str | None = None


class OrchestrationMode(Protocol):
    """One way of turning a user message into a response.

    This is a ``typing.Protocol`` (structural, not a base class to inherit
    from) — each mode is a plain class that happens to match this shape.
    ``graph_mermaid()`` returns ``None`` for the tool router and handoff mesh
    below, since neither is a fixed graph — they're an LLM (or MAF) making a
    per-turn routing decision. Workflow-backed modes (later Phase 1 steps)
    return real Mermaid source there.
    """

    name: str
    label: str
    description: str
    capabilities: ModeCapabilities

    async def run(self, message: str, ctx: RunContext) -> AsyncIterator[OrchestrationEvent]:
        """Run one turn. Must yield exactly one ``kind="run_completed"`` event
        as its last item, with the full response text in ``payload["text"]``
        and the ordered list of agents involved in ``payload["agents_involved"]``
        — this is the one place every caller (blocking ``/api/chat``,
        streaming ``/api/chat/stream``) looks for the final answer, so a mode
        doesn't need its own bespoke "how do I get the text back out"
        contract per caller.
        """
        ...

    def graph_mermaid(self) -> str | None:
        """Static Mermaid source for this mode's structure, or None if the
        mode has no fixed graph (the tool router and handoff mesh route
        per-turn, not along a fixed topology — see the class docstring).
        """
        ...
