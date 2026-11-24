"""Unit tests for prompt composition (Track A1 — prompt-layer hardening).

Pure string assembly: no LLM, no DB. Verifies that every agent's composed
system prompt carries the shared security guardrails (prompt-injection
resistance, role confinement, refusal policy) added to the grounding rules,
that the original data-grounding directives survive composition, and that
prompts remain role-aware.
"""

from __future__ import annotations

import pytest

from shared.prompt_loader import load_prompt

AGENTS = [
    "product-discovery",
    "order-management",
    "pricing-promotions",
    "review-sentiment",
    "inventory-fulfillment",
    "merchant-ops",
    "orchestrator",
]

SECURITY_HEADERS = [
    "Prompt-Injection Resistance",
    "Role Confinement",
    "Refusal Policy",
]


@pytest.mark.parametrize("agent", AGENTS)
def test_prompt_includes_security_guardrails(agent: str) -> None:
    prompt = load_prompt(agent, "customer")
    assert prompt, f"{agent} produced an empty prompt"
    for header in SECURITY_HEADERS:
        assert header in prompt, f"{agent} prompt missing '{header}' section"


@pytest.mark.parametrize("agent", AGENTS)
def test_prompt_keeps_data_grounding(agent: str) -> None:
    prompt = load_prompt(agent, "customer").lower()
    assert "data grounding rules" in prompt
    assert "hallucinate" in prompt  # core anti-fabrication directive survives


def test_injection_rules_name_the_canonical_attacks() -> None:
    prompt = load_prompt("orchestrator", "customer").lower()
    # The actual guardrail wording is present, not just the header.
    assert "as data, never as instructions" in prompt
    assert "ignore previous instructions" in prompt
    assert "never reveal" in prompt and "system prompt" in prompt


def test_role_confinement_rejects_self_claimed_privilege() -> None:
    prompt = load_prompt("order-management", "customer").lower()
    assert "fixed by the authenticated user's role" in prompt
    assert "i am an admin" in prompt  # explicitly names the escalation attempt


def test_prompt_is_role_aware() -> None:
    # The orchestrator has distinct customer vs admin role instructions.
    customer = load_prompt("orchestrator", "customer")
    admin = load_prompt("orchestrator", "admin")
    assert customer and admin
    assert customer != admin, "orchestrator prompt did not vary by role"


def test_unknown_role_falls_back_but_keeps_guardrails() -> None:
    # An unrecognized role must still receive the shared security guardrails.
    prompt = load_prompt("orchestrator", "no-such-role")
    assert prompt
    for header in SECURITY_HEADERS:
        assert header in prompt


def test_unknown_agent_returns_empty() -> None:
    assert load_prompt("does-not-exist", "customer") == ""
