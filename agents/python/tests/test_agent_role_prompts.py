"""Regression test for the dead role-aware-prompt bug (Phase 0.3 Task A).

Previously every ``agent.py`` imported a module-level ``SYSTEM_PROMPT``
constant that ``prompts.py`` evaluated once at import time with the default
role "customer" baked in — sellers and admins were served customer
instructions no matter what ``current_user_role`` held. The fix makes each
``create_*_agent()`` call ``get_system_prompt(current_user_role.get())`` at
construction time, and agents are already rebuilt per request, so the
composed prompt must vary with the ContextVar. No LLM/DB.
"""

from __future__ import annotations

import pytest

from shared.config import settings
from shared.context import current_user_role


@pytest.fixture(autouse=True)
def _openai_dummy(monkeypatch: pytest.MonkeyPatch) -> None:
    # create_chat_client only checks the key is non-empty; no network at build time.
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai", raising=False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key", raising=False)


def _instructions(agent: object) -> str:
    opts = getattr(agent, "default_options", None)
    assert isinstance(opts, dict), f"default_options is {type(opts)!r}, expected dict"
    instructions = opts.get("instructions")
    assert instructions, "agent was built with no instructions"
    return instructions


def test_order_management_agent_uses_seller_prompt_for_seller_role() -> None:
    from order_management.agent import create_order_management_agent

    current_user_role.set("seller")
    instructions = _instructions(create_order_management_agent())

    assert "When a seller asks about orders, show orders containing products they sell" in instructions
    assert "This user is a customer" not in instructions
    assert "an admin with full access to all data and agents" not in instructions


def test_order_management_agent_uses_admin_prompt_for_admin_role() -> None:
    from order_management.agent import create_order_management_agent

    current_user_role.set("admin")
    instructions = _instructions(create_order_management_agent())

    assert "an admin with full access to all data and agents" in instructions
    assert "This user is a customer" not in instructions
    assert "When a seller asks about orders" not in instructions


def test_order_management_agent_falls_back_to_customer_prompt_when_role_unset() -> None:
    from order_management.agent import create_order_management_agent

    current_user_role.set("")
    instructions = _instructions(create_order_management_agent())

    assert "This user is a customer" in instructions

    current_user_role.set("customer")
