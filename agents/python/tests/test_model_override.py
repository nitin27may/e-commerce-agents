"""Per-agent model selection (P-2, #96).

An agent's ``config/prompts/<agent>.yaml`` may carry an optional ``model:`` block. No shipped file
does, so the tests build temporary YAML. What must hold:

* the block is validated at load, and an error names the file;
* it never reaches the composed prompt (fixture keys hash the instructions);
* ``get_chat_client(agent_name)`` resolves the override and falls back to global settings;
* the model in effect is recorded for cost pricing.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient

from shared import factory, prompt_loader
from shared.config import settings
from shared.context import current_agent_model
from shared.prompt_loader import PROMPTS_DIR, load_model_override, load_prompt, validate_model_overrides
from shared.run_usage import configured_model_name


@pytest.fixture
def prompts_dir(tmp_path, monkeypatch) -> Path:
    """A private copy of the real prompt tree, so tests may add ``model:`` blocks to it."""
    target = tmp_path / "prompts"
    shutil.copytree(PROMPTS_DIR, target)
    monkeypatch.setattr(prompt_loader, "PROMPTS_DIR", target)
    monkeypatch.setattr(prompt_loader, "SHARED_DIR", target / "_shared")
    prompt_loader._load_shared_file.cache_clear()
    return target


def _with_model_block(prompts_dir: Path, agent: str, block: object) -> None:
    path = prompts_dir / f"{agent}.yaml"
    data = yaml.safe_load(path.read_text())
    data["model"] = block
    path.write_text(yaml.safe_dump(data, sort_keys=False))


@pytest.fixture
def openai_settings(monkeypatch) -> None:
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "LLM_MODEL", "gpt-4.1")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "")
    monkeypatch.setattr(settings, "LLM_API", "responses")
    current_agent_model.set("")


# ─────────────────────── shipped files ───────────────────────


def test_no_shipped_prompt_file_carries_a_model_block() -> None:
    """The mechanism ships off: behaviour and replay fixtures stay exactly as recorded."""
    for path in sorted(PROMPTS_DIR.glob("*.yaml")):
        assert "model" not in (yaml.safe_load(path.read_text()) or {}), path.name
        assert load_model_override(path.stem) is None
    validate_model_overrides()


# ─────────────────────── prompt loader ───────────────────────


@pytest.mark.parametrize("agent", ["orchestrator", "product-discovery", "handoff-triage"])
@pytest.mark.parametrize("role", ["customer", "admin"])
def test_model_block_never_changes_the_composed_prompt(prompts_dir, agent, role) -> None:
    before = load_prompt(agent, role)
    assert before  # the comparison below must not be vacuous
    _with_model_block(prompts_dir, agent, {"provider": "openai", "model": "gpt-4.1-mini", "temperature": 0.1})
    prompt_loader._load_yaml.cache_clear()
    after = load_prompt(agent, role)
    assert after.encode() == before.encode()
    assert "gpt-4.1-mini" not in after
    assert load_model_override(agent) is not None


def test_valid_block_round_trips(prompts_dir) -> None:
    _with_model_block(prompts_dir, "orchestrator", {"provider": "Azure", "model": " mini-dep ", "temperature": 0})
    override = load_model_override("orchestrator")
    assert (override.provider, override.model, override.temperature) == ("azure", "mini-dep", 0.0)


def test_partial_block_is_allowed(prompts_dir) -> None:
    _with_model_block(prompts_dir, "orchestrator", {"temperature": 0.2})
    override = load_model_override("orchestrator")
    assert override.provider is None and override.model is None and override.temperature == 0.2


@pytest.mark.parametrize(
    ("block", "fragment"),
    [
        ({"provider": "gemini", "model": "x"}, "unknown model.provider"),
        ({"provider": "anthropic", "model": "x"}, ".NET stack only"),
        ({"model": ""}, "non-empty string"),
        ({"model": 7}, "non-empty string"),
        ({"temperature": 3}, "between 0 and 2"),
        ({"temperature": "hot"}, "between 0 and 2"),
        ({"temperature": True}, "between 0 and 2"),
        ({"modle": "x"}, "unknown key"),
        (["openai"], "must be a mapping"),
    ],
)
def test_invalid_block_raises_naming_the_file(prompts_dir, block, fragment) -> None:
    _with_model_block(prompts_dir, "pricing-promotions", block)
    with pytest.raises(ValueError, match="pricing-promotions.yaml") as exc:
        load_model_override("pricing-promotions")
    assert fragment in str(exc.value)
    with pytest.raises(ValueError, match="pricing-promotions.yaml"):
        validate_model_overrides()


def test_missing_agent_file_has_no_override(prompts_dir) -> None:
    assert load_model_override("no-such-agent") is None


# ─────────────────────── factory ───────────────────────


def test_no_override_uses_global_settings(prompts_dir, openai_settings) -> None:
    client = factory.get_chat_client("orchestrator")
    assert isinstance(client, OpenAIChatClient)
    assert client.model == "gpt-4.1"
    assert current_agent_model.get() == ""
    assert factory.agent_default_options("orchestrator") is None
    assert factory.agent_default_options(None) is None


def test_override_model_and_temperature(prompts_dir, openai_settings) -> None:
    _with_model_block(prompts_dir, "orchestrator", {"model": "gpt-4.1-nano", "temperature": 0.0})
    client = factory.get_chat_client("orchestrator")
    assert client.model == "gpt-4.1-nano"
    assert current_agent_model.get() == "gpt-4.1-nano"
    assert factory.agent_default_options("orchestrator") == {"temperature": 0.0}
    # Another agent is untouched.
    other = factory.get_chat_client("product-discovery")
    assert other.model == "gpt-4.1"
    assert current_agent_model.get() == ""


def test_calling_without_a_name_ignores_overrides(prompts_dir, openai_settings) -> None:
    _with_model_block(prompts_dir, "orchestrator", {"model": "gpt-4.1-nano"})
    assert factory.get_chat_client().model == "gpt-4.1"


def test_override_can_switch_provider(prompts_dir, monkeypatch) -> None:
    """Global azure, one agent on public OpenAI: needs OPENAI_API_KEY, and never inherits Azure's base URL."""
    monkeypatch.setattr(settings, "LLM_PROVIDER", "azure")
    monkeypatch.setattr(settings, "AZURE_OPENAI_ENDPOINT", "https://x.openai.azure.com/")
    monkeypatch.setattr(settings, "AZURE_OPENAI_KEY", "k")
    monkeypatch.setattr(settings, "AZURE_OPENAI_DEPLOYMENT", "gpt41-prod")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_API", "responses")
    _with_model_block(prompts_dir, "orchestrator", {"provider": "openai", "model": "gpt-4.1-mini"})

    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        factory.get_chat_client("orchestrator")

    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    client = factory.get_chat_client("orchestrator")
    assert isinstance(client, OpenAIChatClient)
    assert client.model == "gpt-4.1-mini"
    # Specialists with no block still use the Azure deployment.
    assert isinstance(factory.get_chat_client("product-discovery"), OpenAIChatCompletionClient)


def test_azure_override_names_the_deployment(prompts_dir, monkeypatch) -> None:
    monkeypatch.setattr(settings, "LLM_PROVIDER", "azure")
    monkeypatch.setattr(settings, "AZURE_OPENAI_ENDPOINT", "https://x.openai.azure.com/")
    monkeypatch.setattr(settings, "AZURE_OPENAI_KEY", "k")
    monkeypatch.setattr(settings, "AZURE_OPENAI_DEPLOYMENT", "gpt41-prod")
    _with_model_block(prompts_dir, "orchestrator", {"model": "mini-deployment"})
    client = factory.get_chat_client("orchestrator")
    assert client.model == "mini-deployment"
    assert current_agent_model.get() == "mini-deployment"


def test_invalid_block_fails_client_construction_naming_the_file(prompts_dir, openai_settings) -> None:
    _with_model_block(prompts_dir, "orchestrator", {"provider": "bogus"})
    with pytest.raises(ValueError, match="orchestrator.yaml"):
        factory.get_chat_client("orchestrator")


def test_anthropic_provider_points_at_the_dotnet_stack(monkeypatch) -> None:
    monkeypatch.setattr(settings, "LLM_PROVIDER", "anthropic")
    with pytest.raises(ValueError, match=r"\.NET stack only"):
        factory.get_chat_client()


def test_replay_ignores_overrides(prompts_dir, monkeypatch) -> None:
    from shared.replay_client import ReplayChatClient

    monkeypatch.setattr(settings, "LLM_PROVIDER", "replay")
    _with_model_block(prompts_dir, "orchestrator", {"provider": "openai", "model": "gpt-4.1-nano"})
    current_agent_model.set("")
    assert isinstance(factory.get_chat_client("orchestrator"), ReplayChatClient)
    assert current_agent_model.get() == ""


# ─────────────────────── cost ───────────────────────


def test_cost_falls_back_to_the_model_in_effect(monkeypatch) -> None:
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_MODEL", "gpt-4.1")
    current_agent_model.set("")
    assert configured_model_name() == "gpt-4.1"
    token = current_agent_model.set("gpt-4.1-nano")
    try:
        assert configured_model_name() == "gpt-4.1-nano"
        from shared.guardrails.cost_budget_middleware import _current_model

        assert _current_model() == "gpt-4.1-nano"
    finally:
        current_agent_model.reset(token)


def test_run_options_apply_the_agents_temperature(prompts_dir, monkeypatch) -> None:
    """Run-level options beat ``default_options``, so the host's per-run options must carry it."""
    from types import SimpleNamespace

    from shared.agent_host import _run_options

    monkeypatch.setattr(settings, "LLM_TEMPERATURE", 0.2)
    _with_model_block(prompts_dir, "pricing-promotions", {"temperature": 0.9})
    assert _run_options(SimpleNamespace(name="pricing-promotions")) == {"temperature": 0.9}
    assert _run_options(SimpleNamespace(name="order-management")) == {"temperature": 0.2}
    assert _run_options() == {"temperature": 0.2}
