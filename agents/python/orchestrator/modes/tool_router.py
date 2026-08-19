"""The default mode: orchestrator LLM calls ``call_specialist_agent`` per turn.

Wraps exactly what ``orchestrator/routes/chat.py``'s ``chat()`` did directly
before this module existed — ``create_orchestrator_agent()`` +
``_run_agent_native()`` — with no behavior change. The extraction is what
makes it swappable for ``handoff`` (or, in later steps, a workflow) behind
the same ``run()`` contract.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from orchestrator.events import OrchestrationEvent, adapt_step
from shared.agent_observability import get_steps, reset_steps
from shared.context import current_conversation_history

from .base import ModeCapabilities, RunContext


class ToolRouterMode:
    name = "tool"
    label = "Tool Router"
    description = (
        "The orchestrator LLM calls call_specialist_agent to route to a specialist. "
        "Single-hop: one specialist per turn, decided by the model."
    )
    capabilities = ModeCapabilities(
        streams=True,
        supports_hitl=True,  # via shared/hitl.py's FunctionMiddleware, not in-workflow
        supports_checkpoints=False,
        is_graph=False,
    )

    async def run(self, message: str, ctx: RunContext) -> AsyncIterator[OrchestrationEvent]:
        from orchestrator.agent import create_orchestrator_agent
        from shared.agent_host import _run_agent_native

        agent = create_orchestrator_agent()
        current_conversation_history.set(ctx.history)
        reset_steps()

        response_text = await _run_agent_native(agent, message, history=ctx.history)

        steps = get_steps()
        for step in steps:
            step.setdefault("agent", "orchestrator")
            yield adapt_step(step)

        agents_involved = list(dict.fromkeys(["orchestrator", *(s.get("agent", "orchestrator") for s in steps)]))
        yield OrchestrationEvent(
            kind="run_completed",
            payload={"text": response_text, "agents_involved": agents_involved, "steps": steps},
        )

    def graph_mermaid(self) -> str | None:
        return None
