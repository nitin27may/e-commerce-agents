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
from shared.grounding.ledger import reset_grounding_ledger
from shared.run_usage import ORCHESTRATOR, begin_run_usage

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
        reset_steps()
        reset_grounding_ledger()
        run_usage = begin_run_usage()

        run_metadata: dict = {}
        response_text = await _run_agent_native(agent, message, history=ctx.history, metadata_box=run_metadata)

        run_usage.add(ORCHESTRATOR, run_metadata.get("_maf_usage"), run_metadata.get("_maf_model"))

        steps = get_steps()
        for step in steps:
            step.setdefault("agent", "orchestrator")
            yield adapt_step(step)

        agents_involved = list(dict.fromkeys(["orchestrator", *(s.get("agent", "orchestrator") for s in steps)]))
        yield OrchestrationEvent(
            kind="run_completed",
            payload={
                "text": response_text,
                "agents_involved": agents_involved,
                "steps": steps,
                "grounding": run_metadata.get("grounding"),
                # {total, by_agent}: the orchestrator's own LLM calls plus every
                # specialist's, as reported over A2A (shared/run_usage.py).
                "usage": run_usage.snapshot(),
            },
        )

    def graph_mermaid(self) -> str | None:
        return None
