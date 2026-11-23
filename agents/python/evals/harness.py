"""Runs eval cases through the real production execution path.

Replaces ``evaluator.py``'s old ``_run_agent()``, which hand-rolled its own
OpenAI tool-calling loop and called raw undecorated tool functions directly
— bypassing every ``AgentMiddleware``/``FunctionMiddleware`` a real request
goes through (guardrails, HITL, and Phase 2's grounding verification).

Two paths, because ``orchestrator/modes/`` is an orchestrator-level concept
only — specialists are never mode-dispatched, in evals or in production:

- The ``orchestrator`` case goes through ``orchestrator.modes.get_mode("tool")
  .run(...)`` — the same dispatch a real ``POST /api/chat`` request uses.
- The five specialist cases go through ``shared.agent_host._run_agent_native()``
  — the real A2A entry point each specialist's ``/message:send`` handler calls.

Both paths run the agent's full ``build_specialist_middleware()`` stack, and
both go through ``shared.factory.get_chat_client()`` — so ``LLM_PROVIDER=replay``
now actually works for evals, which it never did through the old loop.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any

from evals.live_limits import run_with_limits
from shared.agent_observability import get_steps, reset_steps
from shared.context import (
    current_session_id,
    current_user_email,
    current_user_role,
)
from shared.grounding.ledger import reset_grounding_ledger
from shared.guardrails.flags import get_guardrail_flags, reset_guardrail_flags
from shared.replay_client import ReplayFixtureMissingError
from shared.run_usage import tokens_from_usage

# Agent factory registry — maps eval agent names to their creation functions.
# Lives here (not in run_evals.py) because ProductionRunner needs it to build
# specialist agents; run_evals.py imports it from here for the CLI's --agent
# choices, avoiding a run_evals <-> harness circular import.
AGENT_FACTORIES: dict[str, tuple[str, str]] = {
    "product-discovery": ("product_discovery.agent", "create_product_discovery_agent"),
    "order-management": ("order_management.agent", "create_order_management_agent"),
    "pricing-promotions": ("pricing_promotions.agent", "create_pricing_promotions_agent"),
    "review-sentiment": ("review_sentiment.agent", "create_review_sentiment_agent"),
    "inventory-fulfillment": ("inventory_fulfillment.agent", "create_inventory_fulfillment_agent"),
    "orchestrator": ("orchestrator.agent", "create_orchestrator_agent"),
}


@dataclass
class RunOutcome:
    """What a production run produced — enough for every existing scorer plus new ones."""

    text: str
    tools_called: list[str] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    grounding: dict[str, Any] | None = None
    guardrail_flags: dict[str, bool] = field(default_factory=dict)
    error: str | None = None
    # A missing replay fixture is an infrastructure failure, not a bad answer.
    # Tracked separately so CI can say "3 fixtures are missing" instead of
    # reporting an agent that scored 0 — the ambiguity that let issue #25 sit
    # open across two PRs.
    fixture_missing: bool = False
    # Schema v2: what each tool was called with (step-shaped: tool_name +
    # tool_input), and whether the run was refused by role gating / parked for
    # human approval. All derived from the recorded steps, never from the text.
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    denied: bool = False
    hitl_requested: bool = False


def _create_agent(agent_name: str) -> Any:
    if agent_name not in AGENT_FACTORIES:
        available = ", ".join(sorted(AGENT_FACTORIES.keys()))
        raise ValueError(f"Unknown agent: {agent_name!r}. Available: {available}")
    module_path, factory_name = AGENT_FACTORIES[agent_name]
    module = importlib.import_module(module_path)
    return getattr(module, factory_name)()


def _tools_called(steps: list[dict[str, Any]]) -> list[str]:
    return [s.get("tool_name", "") for s in steps if s.get("tool_name")]


def _tool_calls(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"tool_name": s["tool_name"], "tool_input": s.get("tool_input")} for s in steps if s.get("tool_name")]


def _output_field(step: dict[str, Any], key: str) -> Any:
    output = step.get("tool_output")
    return output.get(key) if isinstance(output, dict) else None


def _was_denied(steps: list[dict[str, Any]]) -> bool:
    """A tool refused the call for the caller's role (``shared.guardrails.roles._denial``)."""
    return any(_output_field(s, "error") == "permission_denied" for s in steps)


def _hitl_requested(steps: list[dict[str, Any]]) -> bool:
    """A step shows a destructive tool parked for approval (``pending_approval`` result).

    Rarely fires on its own: ``HITLFunctionMiddleware`` sits outside
    ``StepRecorderMiddleware`` and short-circuits before it, so a parked call
    leaves no step. ``_hitl_rows`` below is the signal that actually sees it.
    """
    return any(_output_field(s, "status") == "pending_approval" for s in steps)


async def _hitl_rows() -> int | None:
    """Rows in ``hitl_requests`` (``None`` when there is no database to ask)."""
    try:
        from shared.db import get_pool

        return await get_pool().fetchval("SELECT count(*) FROM hitl_requests")
    except Exception:
        return None


def _routes(steps: list[dict[str, Any]]) -> list[str]:
    routes: list[str] = []
    for s in steps:
        if s.get("tool_name") != "call_specialist_agent":
            continue
        tool_input = s.get("tool_input")
        agent_name = tool_input.get("agent_name") if isinstance(tool_input, dict) else None
        if agent_name:
            routes.append(agent_name)
    return routes


class ProductionRunner:
    """Runs a single eval input through the real production path for one agent.

    A fresh runner per case (or reused across cases for the same agent —
    specialist agents are cached on first use, mirroring how a real process
    builds each agent once and reuses it across requests).
    """

    def __init__(
        self,
        agent_name: str,
        *,
        user_email: str = "eval@example.com",
        user_role: str = "customer",
    ) -> None:
        self.agent_name = agent_name
        self.user_email = user_email
        self.user_role = user_role
        # Agents bake the caller's role into their composed prompt at construction,
        # so a per-case role needs its own agent, not the first one built.
        self._agents: dict[str, Any] = {}
        self._run_role = user_role

    async def run(
        self,
        user_input: str,
        *,
        user_role: str | None = None,
        user_email: str | None = None,
    ) -> RunOutcome:
        """Run one input. ``user_role`` / ``user_email`` override the runner's defaults for this case."""
        self._run_role = user_role or self.user_role
        current_user_email.set(user_email or self.user_email)
        current_user_role.set(self._run_role)
        current_session_id.set("")
        reset_steps()
        reset_grounding_ledger()
        reset_guardrail_flags()

        hitl_rows_before = await _hitl_rows()

        async def _attempt() -> RunOutcome:
            # A retried attempt starts clean, or its steps would be counted twice.
            reset_steps()
            reset_grounding_ledger()
            reset_guardrail_flags()
            if self.agent_name == "orchestrator":
                return await self._run_orchestrator(user_input)
            return await self._run_specialist(user_input)

        try:
            # A plain call unless a live run configured retries (429 / 5xx backoff, evals/live_limits.py).
            outcome = await run_with_limits(_attempt)
        except ReplayFixtureMissingError as exc:
            return RunOutcome(
                text="",
                error=str(exc),
                fixture_missing=True,
                guardrail_flags=get_guardrail_flags(),
            )
        except Exception as exc:
            # A specialist's missing fixture reaches the orchestrator as an A2A
            # failure rather than the original exception type, so fall back to
            # recognising it by message.
            return RunOutcome(
                text="",
                error=str(exc),
                fixture_missing="No replay fixture" in str(exc),
                guardrail_flags=get_guardrail_flags(),
            )

        outcome.guardrail_flags = get_guardrail_flags()
        steps = get_steps()
        outcome.tool_calls = _tool_calls(steps)
        outcome.denied = _was_denied(steps)
        hitl_rows_after = await _hitl_rows()
        outcome.hitl_requested = _hitl_requested(steps) or (
            hitl_rows_before is not None and hitl_rows_after is not None and hitl_rows_after > hitl_rows_before
        )
        return outcome

    async def _run_orchestrator(self, user_input: str) -> RunOutcome:
        from orchestrator.modes import RunContext, get_mode

        mode = get_mode("tool")
        ctx = RunContext(history=[])
        text = ""
        grounding: dict[str, Any] | None = None
        usage: dict[str, Any] = {}

        async for event in mode.run(user_input, ctx):
            if event.kind == "run_completed":
                text = event.payload.get("text", "")
                grounding = event.payload.get("grounding")
                usage = event.payload.get("usage") or {}

        steps = get_steps()
        # run_completed.usage is {total, by_agent}: the orchestrator's calls plus
        # every specialist's, as reported over A2A.
        tokens_in, tokens_out = tokens_from_usage(usage)
        return RunOutcome(
            text=text,
            tools_called=_tools_called(steps),
            routes=_routes(steps),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            grounding=grounding,
        )

    async def _run_specialist(self, user_input: str) -> RunOutcome:
        from shared.agent_host import _run_agent_native

        agent = self._agents.get(self._run_role)
        if agent is None:
            agent = self._agents[self._run_role] = _create_agent(self.agent_name)

        metadata_box: dict[str, Any] = {}
        text = await _run_agent_native(agent, user_input, metadata_box=metadata_box)

        steps = get_steps()
        usage = metadata_box.get("_maf_usage") or {}
        return RunOutcome(
            text=text,
            tools_called=_tools_called(steps),
            tokens_in=usage.get("input_token_count") or 0,
            tokens_out=usage.get("output_token_count") or 0,
            grounding=metadata_box.get("grounding"),
        )
