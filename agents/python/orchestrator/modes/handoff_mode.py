"""``handoff`` mode: MAF ``HandoffBuilder`` mesh over the same specialists.

Wraps ``orchestrator/handoff.py::build_orchestrator_handoff_workflow`` —
already built, already tested (``tests/test_handoff_orchestration.py``),
never previously reachable from a live request. This is the first thing
that reaches it: ``ORCHESTRATION_MODE=handoff`` or a per-request
``mode="handoff"`` now actually runs the mesh instead of the tool router.

Final-answer and participant-order extraction mirrors
``tutorials/14-handoff-orchestration/python/main.py::ask()`` — that
tutorial is the verified reference for how to read a handoff workflow's
event stream (confirmed against real Azure calls in Phase 0): "output"
events carry incremental ``AgentResponseUpdate`` text per executor id, and
the final answer is the last participant's fully-assembled turn.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from orchestrator import handoff as handoff_module
from orchestrator.events import OrchestrationEvent, adapt_workflow_event
from shared.context import current_conversation_history

from .base import ModeCapabilities, RunContext


class HandoffMode:
    name = "handoff"
    label = "Handoff Mesh"
    description = (
        "MAF HandoffBuilder mesh: the orchestrator mechanically hands control to a specialist "
        "and back, instead of the LLM deciding per-turn via a tool call."
    )
    capabilities = ModeCapabilities(
        streams=True,
        supports_hitl=False,  # neither shared/hitl.py nor an in-workflow gate is wired into this mesh
        supports_checkpoints=False,
        is_graph=True,
    )

    async def run(self, message: str, ctx: RunContext) -> AsyncIterator[OrchestrationEvent]:
        # Forwarded for parity with the tool router — handoff specialists are
        # RemoteSpecialistChatClient instances that don't currently read this
        # ContextVar (they flatten only the current turn's prompt, see
        # shared/remote_agent.py), but setting it costs nothing and keeps
        # both modes' request setup identical.
        current_conversation_history.set(ctx.history)

        # Module-qualified access (not `from orchestrator.handoff import ...`)
        # deliberately — tests monkeypatch orchestrator.handoff._load_registry
        # / .create_orchestrator_agent, which only takes effect on attribute
        # reads through the module object, not on a name bound at import time
        # into this module's own namespace.
        known_participants = {"orchestrator", *handoff_module._load_registry().keys()}
        workflow = handoff_module.build_orchestrator_handoff_workflow()

        current_speaker: str | None = None
        turns: list[tuple[str, list[str]]] = []

        async for event in workflow.run(message, stream=True):
            etype = getattr(event, "type", None)

            if etype == "output":
                executor_id = getattr(event, "executor_id", None)
                if executor_id in known_participants:
                    if current_speaker != executor_id:
                        current_speaker = executor_id
                        turns.append((executor_id, []))
                    update = getattr(event, "data", None)
                    text = getattr(update, "text", None) if update is not None else None
                    if text:
                        turns[-1][1].append(text)

            adapted = adapt_workflow_event(event)
            if adapted is not None:
                yield adapted

        assembled = [(eid, "".join(parts).strip()) for eid, parts in turns if any(parts)]
        agents_involved = list(dict.fromkeys(eid for eid, _ in assembled)) or ["orchestrator"]
        final_text = assembled[-1][1] if assembled else ""

        yield OrchestrationEvent(
            kind="run_completed",
            payload={"text": final_text, "agents_involved": agents_involved},
        )

    def graph_mermaid(self) -> str | None:
        return None
