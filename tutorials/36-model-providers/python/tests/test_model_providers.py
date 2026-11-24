"""
Chapter 36 - Model Providers: tests.

Steps 1 to 3 of main.py call no model and make no network call. The replay test plays back the recorded
response (tests/fixtures/replay/), so nothing here needs a key.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402
from main import (  # noqa: E402
    DEMO_KEY,
    KEY_RULE_SCENARIOS,
    OLLAMA_ENV,
    PLAN_SCENARIOS,
    ModelOverride,
    ask,
    build_client,
    environment,
    key_rule,
    load_probe_rows,
    not_run,
    plan_client,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]

# ─────────── The tutorials' key rule ───────────


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("nothing set", "replay"),
        ("OPENAI_API_KEY only", "openai"),
        ("Azure endpoint and key", "azure"),
        ("Azure key but no endpoint", "replay"),
        ("key set, LLM_PROVIDER=replay", "replay"),
    ],
)
def test_key_rule(label: str, expected: str) -> None:
    env = dict(KEY_RULE_SCENARIOS)[label]
    assert key_rule(env) == expected


def test_environment_helper_restores_the_real_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "real-looking")
    with environment({}):
        assert "OPENAI_API_KEY" not in os.environ
    assert os.environ["OPENAI_API_KEY"] == "real-looking"


def test_an_unknown_explicit_provider_is_an_error_not_a_fallback() -> None:
    with pytest.raises(ValueError, match="LLM_PROVIDER must be one of"):
        key_rule({"LLM_PROVIDER": "gemini", "OPENAI_API_KEY": "sk-demo"})


# ─────────── The application's provider selection ───────────


def test_openai_defaults_to_the_responses_api_and_azure_to_chat_completions() -> None:
    openai = plan_client({"LLM_PROVIDER": "openai", "OPENAI_API_KEY": DEMO_KEY})
    assert (openai.client_class, openai.wire, openai.model) == ("OpenAIChatClient", "Responses API", "gpt-4.1")
    azure = plan_client(next(e for lbl, e, _ in PLAN_SCENARIOS if lbl == "azure"))
    assert (azure.client_class, azure.wire) == ("OpenAIChatCompletionClient", "Chat Completions")


def test_a_compatible_endpoint_needs_chat_completions_and_keeps_its_base_url() -> None:
    plan = plan_client(OLLAMA_ENV)
    assert plan.wire == "Chat Completions"
    assert plan.base_url == "http://localhost:11434/v1"
    assert plan.model == "qwen2.5:7b"


def test_anthropic_ignores_llm_model_and_is_marked_stub_tested() -> None:
    plan = plan_client({"LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": DEMO_KEY, "LLM_MODEL": "ignored"})
    assert plan.model == "claude-sonnet-5-5"
    assert "never run against the live API" in plan.note


def test_replay_names_no_model() -> None:
    assert plan_client({"LLM_PROVIDER": "replay"}).model == "any"


def test_an_agent_override_changes_the_model_and_keeps_the_endpoint() -> None:
    env = {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": DEMO_KEY, "LLM_BASE_URL": "http://localhost:11434/v1"}
    plan = plan_client(env, ModelOverride(model="gpt-4.1-mini"))
    assert (plan.model, plan.base_url) == ("gpt-4.1-mini", "http://localhost:11434/v1")


def test_an_agent_that_switches_provider_does_not_inherit_the_global_base_url() -> None:
    env = next(e for lbl, e, _ in PLAN_SCENARIOS if lbl == "azure global, one agent on api.openai.com")
    plan = plan_client(env, ModelOverride(provider="openai", model="gpt-4.1-mini"))
    assert plan.base_url is None


@pytest.mark.parametrize(
    ("env", "override", "message"),
    [
        ({"LLM_PROVIDER": "gemini"}, None, "Unknown LLM_PROVIDER"),
        ({"LLM_PROVIDER": "openai"}, None, "OPENAI_API_KEY is required"),
        ({"LLM_PROVIDER": "anthropic"}, None, "ANTHROPIC_API_KEY is required"),
        (
            {"LLM_PROVIDER": "azure", "AZURE_OPENAI_ENDPOINT": "x", "AZURE_OPENAI_KEY": "y"},
            None,
            "AZURE_OPENAI_DEPLOYMENT",
        ),
        (
            {"LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": DEMO_KEY, "OPENAI_API_KEY": DEMO_KEY},
            ModelOverride(provider="openai"),
            "model.model is required",
        ),
    ],
)
def test_misconfiguration_fails_with_a_message_that_names_the_fix(
    env: dict[str, str], override: ModelOverride | None, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        plan_client(env, override)


def test_every_planned_openai_family_client_can_be_constructed_offline() -> None:
    for label, env, override in PLAN_SCENARIOS:
        plan = plan_client(env, override)
        client = build_client(plan, env)
        if plan.provider in ("openai", "azure"):
            assert isinstance(client, OpenAIChatClient | OpenAIChatCompletionClient), label
        else:
            assert client is None, label


def test_the_plan_still_matches_the_factory_it_ports() -> None:
    """A drift guard, not a proof: the decisions this chapter ports must still be in the application's factory."""
    source = (REPO_ROOT / "agents" / "python" / "shared" / "factory.py").read_text(encoding="utf-8")
    for snippet in (
        '(cfg if cfg is not None else settings).LLM_API == "chat_completions"',
        '(cfg.LLM_BASE_URL or None) if global_provider == "openai" else None',
        "model = override_model or cfg.ANTHROPIC_MODEL",
        "OpenAIChatCompletionClient(",
    ):
        assert snippet in source, snippet


# ─────────── Stored capability probes ───────────


def test_probe_rows_come_from_the_stored_results() -> None:
    rows = {(r.label, r.model): r for r in load_probe_rows()}
    gpt41 = rows[("Azure OpenAI", "gpt-4.1")]
    assert gpt41.failed == () and gpt41.passed == gpt41.total
    # The gpt-5.6 deployments rejected temperature 0.2 and nothing else.
    assert rows[("Azure OpenAI", "gpt-5.6-luna")].failed == ("temperature_param",)
    # A 7B local model ignored a forced tool choice.
    assert "tool_choice" in rows[("Ollama (local)", "qwen2.5:7b")].failed


def test_providers_never_probed_are_listed_and_anthropic_is_among_them() -> None:
    never = not_run()
    assert any("Anthropic" in item for item in never)
    assert any(item.startswith("OpenAI direct") for item in never)  # a label, not a URL
    rows = load_probe_rows()
    assert not any("anthropic" in r.label.lower() for r in rows)


# ─────────── Replay ───────────


@pytest.mark.asyncio
async def test_replay_answer_names_the_model_it_was_recorded_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    text, model = await ask()
    assert "return window" in text.lower()
    assert model.startswith("gpt-4.1")
