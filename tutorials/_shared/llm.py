"""The one rule for keys: every chapter runs with no key; a key is only for live runs.

Every chapter's ``main.py`` builds its chat client through
:func:`build_chat_client`, which resolves the provider like this:

1. An explicit ``LLM_PROVIDER`` wins: ``openai``, ``azure`` or ``replay``.
   Anything else is an error, not a silent fallback.
2. Otherwise, if a key is present, use it: ``OPENAI_API_KEY`` selects OpenAI
   (or any OpenAI-compatible endpoint via ``LLM_BASE_URL``); an Azure endpoint
   plus ``AZURE_OPENAI_KEY`` / ``AZURE_OPENAI_API_KEY`` selects Azure OpenAI.
3. Otherwise fall back to ``replay`` — the committed fixtures in the chapter's
   ``tests/fixtures/replay`` directory, no network, no credentials — and print
   one line saying so.

``RECORD=true`` (with ``REPLAY_RECORD_PROVIDER``) is unchanged; see
``tutorials/_shared/replay_client.py``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from agent_framework import BaseChatClient
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient

from tutorials._shared.replay_client import ReplayChatClient

_PROVIDERS = ("openai", "azure", "replay")
_notice_printed = False


def _azure_key() -> str | None:
    return os.environ.get("AZURE_OPENAI_KEY") or os.environ.get("AZURE_OPENAI_API_KEY")


def resolve_provider() -> str:
    """Return ``"openai"``, ``"azure"`` or ``"replay"`` per the rule in the module docstring."""
    explicit = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if explicit:
        if explicit not in _PROVIDERS:
            raise ValueError(f"LLM_PROVIDER must be one of {', '.join(_PROVIDERS)}; got {explicit!r}")
        return explicit
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if _azure_key() and os.environ.get("AZURE_OPENAI_ENDPOINT"):
        return "azure"
    return "replay"


def build_chat_client(fixtures_dir: str | Path) -> BaseChatClient:
    """Build the chapter's chat client; ``fixtures_dir`` is where its replay cassette lives."""
    global _notice_printed
    explicit = bool(os.environ.get("LLM_PROVIDER", "").strip())
    provider = resolve_provider()

    if provider == "replay":
        if not explicit and not _notice_printed:
            _notice_printed = True
            print(
                "No LLM key found - replaying the recorded fixture "
                "(set OPENAI_API_KEY or the AZURE_OPENAI_* variables for a live run).",
                file=sys.stderr,
            )
        return ReplayChatClient(
            fixtures_dir=fixtures_dir,
            record=os.environ.get("RECORD", "").lower() in ("1", "true", "yes"),
            record_provider=os.environ.get("REPLAY_RECORD_PROVIDER", "openai"),
        )
    if provider == "azure":
        # Chat Completions, not Responses: not every Azure deployment exposes
        # the Responses API, so this keeps a chapter portable across regions.
        return OpenAIChatCompletionClient(
            model=os.environ["AZURE_OPENAI_DEPLOYMENT"],
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=_azure_key(),
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        )
    return OpenAIChatClient(
        model=os.environ.get("LLM_MODEL", "gpt-4.1"),
        api_key=os.environ["OPENAI_API_KEY"],
        # Any OpenAI-compatible endpoint (GitHub Models, OpenRouter, vLLM,
        # LM Studio, Ollama) instead of api.openai.com; see
        # tutorials/00-setup/README.md's "Don't have a paid API key?" section.
        base_url=os.environ.get("LLM_BASE_URL") or None,
    )
