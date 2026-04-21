"""MAF Handoff workflow for the orchestrator → specialist mesh.

This is the Phase 7 step-10 replacement for the ``call_specialist_agent``
tool router. When ``settings.MAF_HANDOFF_MODE == "handoff"`` the routes
layer builds this workflow instead of the tool-backed agent, and MAF
routes turns between the orchestrator and the 5 specialists mechanically
via the Handoff orchestration.

Each specialist is a :class:`~shared.remote_agent.RemoteSpecialistChatClient`
wrapped in an ``Agent``, so handoffs still traverse A2A HTTP on the wire
— the mechanism is Handoff, the transport is A2A.

Default stays tool-based (`MAF_HANDOFF_MODE=tool`), so this module is
additive; nothing in the existing runtime changes until the flag flips.
"""

import json
import logging
from typing import Any

from agent_framework import Agent
from agent_framework_orchestrations import HandoffBuilder

from orchestrator.agent import create_orchestrator_agent
from shared.config import settings
from shared.remote_agent import make_remote_specialist_agent

logger = logging.getLogger(__name__)


def _load_registry() -> dict[str, str]:
    try:
        registry = json.loads(settings.AGENT_REGISTRY)
    except json.JSONDecodeError:
        logger.warning("AGENT_REGISTRY is not valid JSON; handoff workflow will have no specialists")
        return {}
    return {k: v for k, v in registry.items() if v}


def build_remote_specialist_agents(registry: dict[str, str] | None = None) -> list[Agent]:
    """Turn the AGENT_REGISTRY map into a list of Handoff-compatible Agents."""
    reg = registry if registry is not None else _load_registry()
    return [make_remote_specialist_agent(name, url) for name, url in reg.items()]


def build_orchestrator_handoff_workflow(
    *,
    orchestrator: Agent | None = None,
    specialists: list[Agent] | None = None,
    autonomous_mode: bool | None = None,
) -> Any:
    """Build a MAF HandoffBuilder workflow.

    Args:
        orchestrator: Optional pre-built orchestrator agent. When omitted,
            one is created with the standard system prompt.
        specialists: Optional pre-built specialist agents. When omitted,
            they are derived from ``settings.AGENT_REGISTRY``.
        autonomous_mode: Override for ``settings.HANDOFF_AUTONOMOUS_MODE``.
            When ``True``, specialists auto-reply without an intermediate
            user turn. When ``False``, each handoff emits an observable
            event in the workflow stream.
    """
    orchestrator = orchestrator or create_orchestrator_agent()
    specialists = specialists if specialists is not None else build_remote_specialist_agents()
    auto = settings.HANDOFF_AUTONOMOUS_MODE if autonomous_mode is None else autonomous_mode

    builder = HandoffBuilder(name="orchestrator-handoff").participants([orchestrator, *specialists])
    builder = builder.with_start_agent(orchestrator)

    # Mesh topology: orchestrator can hand to any specialist; each specialist
    # can hand back to the orchestrator. We deliberately don't let specialists
    # hand to each other here — that cross-talk path already exists via the
    # orchestrator round-trip and introducing it would make the routing graph
    # much harder to reason about from a support-ops perspective.
    if specialists:
        builder = builder.add_handoff(orchestrator, specialists)
        for specialist in specialists:
            builder = builder.add_handoff(specialist, [orchestrator])

    if auto:
        # Let the orchestrator keep the floor after a specialist replies so
        # it can decide to hand off again (or wrap up) without bouncing the
        # conversation back to the end-user every turn.
        builder = builder.with_autonomous_mode(agents=[orchestrator])

    return builder.build()
