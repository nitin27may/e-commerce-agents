"""#298 -- the orchestrator can route to merchant-ops only behind MERCHANT_OPS_AGENT_ENABLED, and with it off every
orchestrator request (prompt, tool schema, registry) is exactly what the recorded fixtures were keyed on."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from orchestrator.agent import call_specialist_agent, routing_description
from orchestrator.prompts import get_system_prompt
from shared.factory import active_agent_registry
from shared.prompt_loader import load_prompt


def _flag(monkeypatch: pytest.MonkeyPatch, value: bool) -> None:
    """Set the flag on the settings object each module actually holds (another test reloads shared.config)."""
    from orchestrator import agent as orchestrator_agent
    from orchestrator import prompts
    from shared import factory

    for holder in {id(m.settings): m.settings for m in (factory, prompts, orchestrator_agent)}.values():
        monkeypatch.setattr(holder, "MERCHANT_OPS_AGENT_ENABLED", value, raising=False)


REGISTRY = json.dumps(
    {
        "review-sentiment": "http://review-sentiment:8084",
        "merchant-ops": "http://merchant-ops:8086",
    }
)


def test_the_registry_drops_merchant_ops_unless_the_flag_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, False)
    assert set(active_agent_registry(REGISTRY)) == {"review-sentiment"}
    _flag(monkeypatch, True)
    assert set(active_agent_registry(REGISTRY)) == {"review-sentiment", "merchant-ops"}


def test_with_the_flag_off_the_prompt_and_the_tool_schema_are_the_recorded_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag(monkeypatch, False)
    for role in ("customer", "seller", "admin"):
        assert get_system_prompt(role) == load_prompt("orchestrator", role)
    assert routing_description(merchant_ops=False) == (
        "Route a request to a specialist agent via A2A protocol. Available agents: product-discovery, "
        "order-management, pricing-promotions, review-sentiment, inventory-fulfillment"
    )
    assert "merchant-ops" not in call_specialist_agent.description


def test_with_the_flag_on_only_sellers_and_admins_are_told_about_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, True)
    assert "merchant-ops" not in get_system_prompt("customer")
    for role in ("seller", "admin"):
        prompt = get_system_prompt(role)
        assert prompt.startswith(load_prompt("orchestrator", role))
        assert "**merchant-ops**" in prompt and "never tell the user a proposal was approved" in prompt
    assert routing_description(merchant_ops=True).endswith("inventory-fulfillment, merchant-ops")


REPO = Path(__file__).resolve().parents[3]


def test_compose_runs_merchant_ops_only_behind_its_own_profile_and_the_flag_defaults_off() -> None:
    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text())["services"]
    service = compose["merchant-ops"]
    assert service["profiles"] == ["merchant-ops"]  # no default run, demo, no-key overlay or gate starts it
    assert service["build"]["args"] == {"AGENT_NAME": "merchant_ops", "AGENT_PORT": 8086}
    assert "http://localhost:8086/ready" in " ".join(service["healthcheck"]["test"])
    orchestrator = compose["orchestrator"]
    assert "merchant-ops" not in orchestrator["depends_on"]  # nothing waits on an opt-in service
    assert orchestrator["environment"]["MERCHANT_OPS_AGENT_ENABLED"] == "${MERCHANT_OPS_AGENT_ENABLED:-false}"
    assert json.loads(orchestrator["environment"]["AGENT_REGISTRY"])["merchant-ops"] == "http://merchant-ops:8086"
    for other in ("docker-compose.dotnet.yml", "docker-compose.demo.yml", "docker-compose.nokey.yml"):
        assert "merchant-ops" not in (REPO / other).read_text(), other  # Python only, not in the demo images
