"""The public A2A card of each *real* Python specialist hides what the least-privileged role may not call.

``test_a2a_conformance.py`` proves the rule with synthetic tools. This builds the actual ``create_*_agent()`` of every
specialist and asserts, from the tools themselves: every tool whose ``requires_role`` excludes ``customer`` is absent
from the card (the card is served unauthenticated, so an internal tool's name and description must not be on it), and
at least one customer tool is present.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from typing import Any

import pytest
from agent_framework import FunctionTool

from evals.harness import AGENT_FACTORIES
from shared.a2a_server import build_agent_card
from shared.guardrails.roles import available_to_least_privileged, required_roles

SPECIALISTS = [name for name in AGENT_FACTORIES if name != "orchestrator"]
SELLER_ONLY_SPECIALISTS = {"merchant-ops"}


@pytest.fixture
def replay_provider() -> Iterator[None]:
    """Building an agent needs a chat client; replay needs no key and makes no call (same trick as fixture_drift)."""
    importlib.import_module("shared.config")
    switched: dict[int, tuple[Any, str]] = {}
    for module in list(sys.modules.values()):
        candidate = getattr(module, "settings", None)
        if type(candidate).__name__ == "Settings" and hasattr(candidate, "LLM_PROVIDER"):
            switched.setdefault(id(candidate), (candidate, candidate.LLM_PROVIDER))
            candidate.LLM_PROVIDER = "replay"
    try:
        yield
    finally:
        for candidate, previous in switched.values():
            candidate.LLM_PROVIDER = previous


def _agent(name: str) -> Any:
    module_path, factory = AGENT_FACTORIES[name]
    return getattr(importlib.import_module(module_path), factory)()


def _function_tools(agent: Any) -> list[FunctionTool]:
    tools = (getattr(agent, "default_options", None) or {}).get("tools") or []
    return [t for t in tools if isinstance(t, FunctionTool)]


@pytest.mark.parametrize("name", SPECIALISTS)
def test_a_real_specialists_card_hides_every_tool_customers_may_not_call(name: str, replay_provider: None) -> None:
    agent = _agent(name)
    tools = _function_tools(agent)
    restricted = {t.name for t in tools if not available_to_least_privileged(getattr(t, "func", None))}
    customer = {t.name for t in tools} - restricted

    card = build_agent_card(agent=agent, agent_name=name, description="d", base_url="http://x")
    advertised = {s.id for s in card.skills}

    # merchant-ops is for sellers and admins alone: every tool is role-restricted, so its public card has no skill.
    assert customer or name in SELLER_ONLY_SPECIALISTS, f"{name} has no customer tool at all"
    assert advertised == customer
    assert not advertised & restricted
    serialized = card.SerializeToString().decode("utf-8", errors="ignore")
    for tool in tools:
        if tool.name in restricted:
            assert tool.name not in serialized, f"{name}: restricted tool {tool.name} leaks into the public card"


def test_the_real_specialists_do_have_restricted_tools_to_hide(replay_provider: None) -> None:
    """Guard against the check above passing vacuously: some real tools really are role-restricted."""
    restricted = {
        t.name
        for name in SPECIALISTS
        for t in _function_tools(_agent(name))
        if required_roles(getattr(t, "func", None)) is not None
        and not available_to_least_privileged(getattr(t, "func", None))
    }

    assert {"draft_seller_response", "place_backorder"} <= restricted
