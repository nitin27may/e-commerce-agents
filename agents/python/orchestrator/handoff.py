"""MAF Handoff workflow for the orchestrator → specialist mesh.

This is the ``handoff`` alternative to the ``tool``-mode
``call_specialist_agent`` tool router. ``orchestrator/modes/handoff_mode.py``
wraps :func:`build_orchestrator_handoff_workflow` and is what makes this
reachable from a live request — via ``mode="handoff"`` on ``/api/chat``, or
``ORCHESTRATION_MODE=handoff`` as the deployment-wide default. MAF routes
turns between the orchestrator and the specialists mechanically via the
Handoff orchestration.

Each specialist is a :class:`~shared.remote_agent.RemoteSpecialistChatClient`
wrapped in an ``Agent``, so handoffs still traverse A2A HTTP on the wire
— the mechanism is Handoff, the transport is A2A.

Default stays tool-based (``ORCHESTRATION_MODE=tool``), so this module is
additive; nothing in the existing runtime changes unless a request or the
deployment config selects ``handoff``.
"""

import json
import logging
from typing import Any

from agent_framework import Agent
from agent_framework_orchestrations import HandoffBuilder

from shared.agent_factory import create_chat_client
from shared.config import settings
from shared.context import current_user_role
from shared.prompt_loader import load_prompt
from shared.remote_agent import make_remote_specialist_agent

logger = logging.getLogger(__name__)


def _load_registry() -> dict[str, str]:
    try:
        registry = json.loads(settings.AGENT_REGISTRY)
    except json.JSONDecodeError:
        logger.warning("AGENT_REGISTRY is not valid JSON; handoff workflow will have no specialists")
        return {}
    return {k: v for k, v in registry.items() if v}


def create_handoff_triage_agent() -> Agent:
    """The start agent for the handoff mesh — deliberately tool-free.

    This is NOT ``create_orchestrator_agent()``, and that distinction is the
    whole reason this mode works. The two modes route by opposite mechanisms:
    ``tool`` calls ``call_specialist_agent`` and keeps ownership of the turn;
    ``handoff`` calls a MAF-synthesised handoff tool and *transfers* ownership.

    Handing the tool-router orchestrator to ``HandoffBuilder`` gives it two
    competing routing mechanisms, and it uses the one its system prompt names —
    so it never calls a handoff tool. Microsoft's guidance is explicit that this
    is fatal here: an agent that responds instead of handing off leaves the
    workflow with nowhere to go but back to the user. With autonomous mode on,
    that becomes an unbounded self-continuation loop.

    Measured against a live stack before this existed: 5,403 streamed updates,
    23,637 characters, 100-200 s, and no specialist ever invoked.
    """
    return Agent(
        client=create_chat_client(),
        name="orchestrator",
        description="Triage agent that routes the conversation to a specialist.",
        instructions=load_prompt("handoff-triage", current_user_role.get() or "customer"),
        # No tools, and no ECommerceContextProvider. Both exist to help the
        # orchestrator *answer*; this agent's only job is to choose a specialist,
        # and every tool it carries is one more thing it can do instead of
        # handing off.
        require_per_service_call_history_persistence=True,
    )


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
    orchestrator = orchestrator or create_handoff_triage_agent()
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
        # Let the triage agent keep the floor after a specialist replies so it
        # can decide to hand off again (or wrap up) without bouncing the
        # conversation back to the end-user every turn.
        #
        # The turn limit is the safety net, and it is not optional. Autonomous
        # mode's contract is "when the agent does not hand off, feed it a
        # continuation prompt and run it again" — so an agent that *cannot*
        # hand off runs until something stops it. The default is 50 turns,
        # which at ~450 characters a turn is the 23,000-character monologue
        # this mode used to produce. Three is enough for hand-off, hand-back,
        # wrap-up.
        builder = builder.with_autonomous_mode(
            agents=[orchestrator],
            turn_limits={orchestrator.name: settings.HANDOFF_MAX_TURNS},
        )

    return builder.build()
