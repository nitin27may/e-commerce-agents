"""LLM_API selects the OpenAI wire API for the ``openai`` provider (#96, P-1).

The default (``responses``) must keep posting to ``/responses``; ``chat_completions``
must post to ``/chat/completions`` so OpenAI-compatible endpoints with no Responses
route (Gemini's compatible endpoint, some vLLM / Ollama versions) work. Asserted on
the real HTTP request path, through the client's own ``AsyncOpenAI`` with a
``httpx.MockTransport`` swapped in — no network.
"""

from __future__ import annotations

import httpx
import openai
import pytest
from agent_framework import Message
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient
from pydantic import ValidationError

from shared import factory
from shared.config import Settings

BASE_URL = "http://compat.test/v1"


def _configure(monkeypatch: pytest.MonkeyPatch, llm_api: str) -> None:
    # Other tests reload shared.config, so factory.settings and the settings object a
    # lazy `from shared.config import settings` sees can be different instances. Set both.
    import shared.config as config_mod

    for target in {id(o): o for o in (factory.settings, config_mod.settings)}.values():
        monkeypatch.setattr(target, "LLM_PROVIDER", "openai")
        monkeypatch.setattr(target, "OPENAI_API_KEY", "sk-test")
        monkeypatch.setattr(target, "LLM_BASE_URL", BASE_URL)
        monkeypatch.setattr(target, "LLM_API", llm_api)


async def _request_path(client) -> str:
    """Send one message through *client* and return the URL path it POSTed to."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(400, json={"error": {"message": "stop here", "type": "invalid_request_error"}})

    client.client = openai.AsyncOpenAI(
        api_key="sk-test",
        base_url=BASE_URL,
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(Exception):  # noqa: B017, PT011 — the 400 above is the expected stop
        await client.get_response([Message(role="user", contents=["hi"])])
    assert len(seen) == 1
    return seen[0]


def test_default_is_responses(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings.model_fields["LLM_API"].default == "responses"


def test_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        Settings(LLM_API="completions")


def test_responses_selects_chat_client(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, "responses")
    client = factory.get_chat_client()
    assert isinstance(client, OpenAIChatClient)
    assert not isinstance(client, OpenAIChatCompletionClient)


def test_chat_completions_selects_completion_client(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, "chat_completions")
    assert isinstance(factory.get_chat_client(), OpenAIChatCompletionClient)


async def test_responses_posts_to_responses_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, "responses")
    assert await _request_path(factory.get_chat_client()) == "/v1/responses"


async def test_chat_completions_posts_to_chat_completions_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, "chat_completions")
    assert await _request_path(factory.get_chat_client()) == "/v1/chat/completions"


def test_azure_ignores_llm_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(factory.settings, "LLM_PROVIDER", "azure")
    monkeypatch.setattr(factory.settings, "AZURE_OPENAI_ENDPOINT", "https://x.openai.azure.com/")
    monkeypatch.setattr(factory.settings, "AZURE_OPENAI_KEY", "k")
    monkeypatch.setattr(factory.settings, "AZURE_OPENAI_DEPLOYMENT", "d")
    monkeypatch.setattr(factory.settings, "LLM_API", "responses")
    assert isinstance(factory.get_chat_client(), OpenAIChatCompletionClient)


def test_replay_recorder_follows_the_switch(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from shared.replay_client import ReplayChatClient

    _configure(monkeypatch, "chat_completions")
    recorder = ReplayChatClient(fixtures_dir=str(tmp_path), record=True, record_provider="openai")
    assert isinstance(recorder._build_record_client(), OpenAIChatCompletionClient)

    _configure(monkeypatch, "responses")
    recorder = ReplayChatClient(fixtures_dir=str(tmp_path), record=True, record_provider="openai")
    inner = recorder._build_record_client()
    assert isinstance(inner, OpenAIChatClient) and not isinstance(inner, OpenAIChatCompletionClient)
