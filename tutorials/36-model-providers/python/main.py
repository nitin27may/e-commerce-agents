"""
MAF v1 - Chapter 36: Model Providers (Python)

How one agent definition runs on different models, and what has and has not been checked.

  Selection:    `LLM_PROVIDER` picks the provider; the app's factory turns it into a MAF chat client.
  Overrides:    an agent's own `model:` block can pick a different model, or provider, for that agent alone.
  Capability:   a stored probe result per model says which wire features it handled. Not every model passes.
  Provenance:   which providers this repo has actually called, and which it has only stubbed.

Steps 1 to 3 call no model and no network. Step 4 runs one turn against a recorded response.

Run:
    python tutorials/36-model-providers/python/main.py
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))
# Appended, not inserted: the application's top-level package names must never shadow a chapter's own.
sys.path.append(str(REPO_ROOT / "agents" / "python"))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import Agent, BaseChatClient  # noqa: E402
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402
from shared.run_usage import served_model  # noqa: E402
from tutorials._shared.llm import build_chat_client, resolve_provider  # noqa: E402

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "tests" / "fixtures" / "replay"
RESULTS_DIR = REPO_ROOT / "agents" / "python" / "evals" / "provider-results"

# ─────────────────── Step 1: the tutorials' key rule ───────────────────

KEY_RULE_SCENARIOS: list[tuple[str, dict[str, str]]] = [
    ("nothing set", {}),
    ("OPENAI_API_KEY only", {"OPENAI_API_KEY": "sk-demo"}),
    ("Azure endpoint and key", {"AZURE_OPENAI_ENDPOINT": "https://example.invalid/", "AZURE_OPENAI_KEY": "demo"}),
    ("Azure key but no endpoint", {"AZURE_OPENAI_KEY": "demo"}),
    ("key set, LLM_PROVIDER=replay", {"OPENAI_API_KEY": "sk-demo", "LLM_PROVIDER": "replay"}),
]
_KEY_RULE_VARS = ("LLM_PROVIDER", "OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_KEY", "AZURE_OPENAI_API_KEY")


@contextmanager
def environment(env: Mapping[str, str]) -> Iterator[None]:
    """Run a block with exactly these credential variables set, and restore the real environment after."""
    saved = {name: os.environ.pop(name, None) for name in _KEY_RULE_VARS}
    os.environ.update(env)
    try:
        yield
    finally:
        for name in _KEY_RULE_VARS:
            os.environ.pop(name, None)
            if saved[name] is not None:
                os.environ[name] = saved[name]  # type: ignore[assignment]


def key_rule(env: Mapping[str, str]) -> str:
    with environment(env):
        return resolve_provider()


# ─────────────────── Step 2: the application's provider selection ───────────────────


@dataclass(frozen=True)
class ModelOverride:
    """A `model:` block in an agent's prompt YAML. `None` falls back to the global setting."""

    provider: str | None = None
    model: str | None = None


@dataclass(frozen=True)
class ClientPlan:
    provider: str
    wire: str
    client_class: str
    model: str
    base_url: str | None
    note: str = ""


PROVIDERS = ("openai", "azure", "anthropic", "replay")


def plan_client(env: Mapping[str, str], override: ModelOverride | None = None) -> ClientPlan:
    """What `_build_provider_client` in agents/python/shared/factory.py would build for these settings.

    A port of its decisions, not of its construction, so it runs without the backend's settings stack. Raises the
    same errors for the same mistakes.
    """
    global_provider = env.get("LLM_PROVIDER", "").strip().lower()
    if global_provider == "replay":
        return ClientPlan(
            "replay", "none (recorded)", "ReplayChatClient", "any", None, "a request with no fixture fails"
        )
    if global_provider not in PROVIDERS:
        raise ValueError(f"Unknown LLM_PROVIDER: {env.get('LLM_PROVIDER')!r}. Must be one of {', '.join(PROVIDERS)}.")

    provider = (override.provider if override and override.provider else None) or global_provider
    override_model = override.model if override else None

    if global_provider == "anthropic" and provider != "anthropic" and not override_model:
        raise ValueError(f"model.model is required when an agent switches from anthropic to {provider}")

    if provider == "anthropic":
        if not env.get("ANTHROPIC_API_KEY", "").strip():
            raise ValueError("ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic.")
        model = override_model or env.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")  # LLM_MODEL is ignored here
        return ClientPlan(
            "anthropic",
            "Messages API",
            "AnthropicClient",
            model,
            None,
            "stub-tested only, never run against the live API",
        )

    if provider == "openai":
        if not env.get("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai.")
        # LLM_BASE_URL belongs to the global openai endpoint. An agent that switches to openai from another
        # provider talks to api.openai.com.
        base_url = (env.get("LLM_BASE_URL") or None) if global_provider == "openai" else None
        chat_completions = env.get("LLM_API", "responses") == "chat_completions"
        return ClientPlan(
            "openai",
            "Chat Completions" if chat_completions else "Responses API",
            "OpenAIChatCompletionClient" if chat_completions else "OpenAIChatClient",
            override_model or env.get("LLM_MODEL", "gpt-4.1"),
            base_url,
        )

    missing = [k for k in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_KEY", "AZURE_OPENAI_DEPLOYMENT") if not env.get(k)]
    if missing:
        raise ValueError(f"Azure OpenAI requires {', '.join(missing)}.")
    return ClientPlan(
        "azure",
        "Chat Completions",
        "OpenAIChatCompletionClient",
        override_model or env["AZURE_OPENAI_DEPLOYMENT"],
        None,
        "the model is a deployment name, not a model id",
    )


def build_client(plan: ClientPlan, env: Mapping[str, str]) -> BaseChatClient | None:
    """Construct the real MAF client for a plan, with no network call. `None` for Anthropic and replay.

    Anthropic's client lives in `agent-framework-anthropic`, which the tutorials project does not install.
    """
    if plan.client_class == "OpenAIChatClient":
        return OpenAIChatClient(model=plan.model, api_key=env["OPENAI_API_KEY"], base_url=plan.base_url)
    if plan.client_class == "OpenAIChatCompletionClient" and plan.provider == "openai":
        return OpenAIChatCompletionClient(model=plan.model, api_key=env["OPENAI_API_KEY"], base_url=plan.base_url)
    if plan.provider == "azure":
        return OpenAIChatCompletionClient(
            model=plan.model, azure_endpoint=env["AZURE_OPENAI_ENDPOINT"], api_key=env["AZURE_OPENAI_KEY"]
        )
    return None


DEMO_KEY = "demo-not-a-secret"
OLLAMA_ENV = {
    "LLM_PROVIDER": "openai",
    "OPENAI_API_KEY": "not-needed",
    "LLM_BASE_URL": "http://localhost:11434/v1",
    "LLM_API": "chat_completions",
    "LLM_MODEL": "qwen2.5:7b",
}
PLAN_SCENARIOS: list[tuple[str, dict[str, str], ModelOverride | None]] = [
    ("replay", {"LLM_PROVIDER": "replay"}, None),
    (
        "azure",
        {
            "LLM_PROVIDER": "azure",
            "AZURE_OPENAI_ENDPOINT": "https://example.invalid/",
            "AZURE_OPENAI_KEY": DEMO_KEY,
            "AZURE_OPENAI_DEPLOYMENT": "gpt-4.1",
        },
        None,
    ),
    ("openai", {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": DEMO_KEY}, None),
    ("openai-compatible (Ollama)", OLLAMA_ENV, None),
    ("anthropic", {"LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": DEMO_KEY, "LLM_MODEL": "ignored"}, None),
    (
        "openai, one agent on a smaller model",
        {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": DEMO_KEY, "LLM_BASE_URL": "http://localhost:11434/v1"},
        ModelOverride(model="gpt-4.1-mini"),
    ),
    (
        "azure global, one agent on api.openai.com",
        {
            "LLM_PROVIDER": "azure",
            "AZURE_OPENAI_ENDPOINT": "https://example.invalid/",
            "AZURE_OPENAI_KEY": DEMO_KEY,
            "AZURE_OPENAI_DEPLOYMENT": "gpt-4.1",
            "OPENAI_API_KEY": DEMO_KEY,
            "LLM_BASE_URL": "http://localhost:11434/v1",
        },
        ModelOverride(provider="openai", model="gpt-4.1-mini"),
    ),
]

# ─────────────────── Step 3: what each model did when probed ───────────────────


@dataclass(frozen=True)
class ProbeRow:
    label: str
    model: str
    date: str
    passed: int
    total: int
    failed: tuple[str, ...]


def load_probe_rows(directory: pathlib.Path = RESULTS_DIR) -> list[ProbeRow]:
    """One row per stored result in agents/python/evals/provider-results/ (the not-run list is separate)."""
    rows = []
    for path in sorted(directory.glob("*__*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        caps = data["capabilities"]
        failed = tuple(name for name, c in caps.items() if c["status"] == "fail")
        passed = sum(1 for c in caps.values() if c["status"] == "pass")
        label = data.get("label") or {"azure": "Azure OpenAI", "openai": "OpenAI"}.get(
            data["provider"], data["provider"]
        )
        rows.append(ProbeRow(label, data["model"], data["date"], passed, len(caps), failed))
    return rows


def not_run(directory: pathlib.Path = RESULTS_DIR) -> list[str]:
    return [item["provider"] for item in json.loads((directory / "not-run.json").read_text(encoding="utf-8"))]


# ─────────────────── Step 4: one turn on a recording ───────────────────

QUESTION = "In one sentence, what does a return window mean for a customer?"


def _default_client() -> BaseChatClient:
    return build_chat_client(FIXTURES_DIR)


def build_agent(client: object | None = None) -> Agent:
    return Agent(client or _default_client(), instructions="You are a concise store assistant.", name="provider-demo")


async def ask(client: object | None = None) -> tuple[str, str]:
    """The answer text, and the model id the recorded response names."""
    response = await build_agent(client).run(QUESTION)
    return response.text, served_model(response) or "not reported"


# ─────────────────── Entry point ───────────────────


async def main() -> None:
    print("Step 1. The tutorials' key rule: which provider does each environment select?")
    for label, env in KEY_RULE_SCENARIOS:
        print(f"  {label:<30} -> {key_rule(env)}")
    print()

    print("Step 2. The application's factory: what each LLM_PROVIDER builds.")
    for label, env, override in PLAN_SCENARIOS:
        plan = plan_client(env, override)
        client = build_client(plan, env)
        built = type(client).__name__ if client is not None else "(not built here)"
        where = plan.base_url or "provider default"
        print(f"  {label}")
        print(f"    {plan.client_class} via {plan.wire}; model {plan.model}; endpoint {where}; constructed: {built}")
        if plan.note:
            print(f"    note: {plan.note}")
    for label, env in (
        ("unknown provider", {"LLM_PROVIDER": "gemini"}),
        ("azure with no deployment", {"LLM_PROVIDER": "azure", "AZURE_OPENAI_ENDPOINT": "x", "AZURE_OPENAI_KEY": "y"}),
        (
            "anthropic global, agent to openai, no model",
            {"LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": DEMO_KEY, "OPENAI_API_KEY": DEMO_KEY},
        ),
    ):
        override = ModelOverride(provider="openai") if "agent" in label else None
        try:
            plan_client(env, override)
        except ValueError as exc:
            print(f"  error [{label}]: {exc}")
    print()

    print("Step 3. Capability probes stored in the repo (one small request per capability, not a quality score).")
    for row in load_probe_rows():
        verdict = "all pass" if not row.failed else "failed: " + ", ".join(row.failed)
        print(f"  {row.label:<14} {row.model:<20} {row.date}  {row.passed}/{row.total} pass  {verdict}")
    print("  never probed: " + "; ".join(not_run()))
    print()

    print(f"Step 4. One turn on a recording. Q: {QUESTION}")
    text, model = await ask()
    print(f"  A: {text}")
    print(f"  the recording was made on: {model}")


if __name__ == "__main__":
    asyncio.run(main())
