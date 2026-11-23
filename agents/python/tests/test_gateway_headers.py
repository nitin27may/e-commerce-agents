"""Gateway attribution headers on every model call (AZ-4): ``x-agent-name``, ``x-run-id``, ``x-eval-run-id``.

Each client shape the factory builds (openai Responses, openai Chat Completions / OpenAI-compatible, azure,
anthropic, and the embeddings clients) is driven against a stub transport that records the request headers.
The replay clients make no HTTP call; a separate test shows the replay key does not move.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import httpx2
import pytest
from agent_framework import Content, Message
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient

from shared import config as config_module
from shared import embedding_config, factory, gateway_headers
from shared.context import current_agent_name, current_eval_run_id, current_run_id
from shared.replay_client import _canonical_request, _request_hash

GATEWAY = ("x-agent-name", "x-run-id", "x-eval-run-id")


def _set(monkeypatch: pytest.MonkeyPatch, name: str, value: Any) -> None:
    for obj in {id(o): o for o in (factory.settings, config_module.settings, gateway_headers.settings)}.values():
        monkeypatch.setattr(obj, name, value)
    monkeypatch.setattr(embedding_config, "settings", factory.settings, raising=False)


@pytest.fixture
def configure(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    defaults = {
        "LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": "sk-test",
        "LLM_MODEL": "gpt-4.1",
        "LLM_BASE_URL": "",
        "LLM_API": "responses",
        "GATEWAY_HEADERS_ENABLED": True,
        "EVAL_RUN_ID": "",
        "EMBEDDING_PROVIDER": "",
        "EMBEDDING_BASE_URL": "",
        "EMBEDDING_API_KEY": "",
    }
    for name, value in defaults.items():
        _set(monkeypatch, name, value)
    for var in (current_agent_name, current_run_id, current_eval_run_id):
        var.set("")

    def apply(**overrides: Any) -> None:
        for name, value in overrides.items():
            _set(monkeypatch, name, value)

    return apply


class _Recorder:
    """Records request headers; answers 400 so the SDKs raise at once instead of retrying."""

    def __init__(self) -> None:
        self.headers: list[dict[str, str]] = []

    def __call__(self, request: Any) -> Any:
        self.headers.append({k.lower(): v for k, v in request.headers.items()})
        return _response(request)


def _response(request: Any) -> Any:
    module = httpx2 if type(request).__module__.startswith("httpx2") else httpx
    return module.Response(400, json={"error": {"message": "stub", "type": "invalid_request_error"}})


def _stub(sdk_client: Any, recorder: _Recorder) -> None:
    http = sdk_client._client
    module = httpx2 if type(http).__module__.startswith("httpx2") else httpx
    http._transport = module.MockTransport(recorder)


async def _chat(client: Any) -> None:
    try:
        await client.get_response([Message(role="user", contents=[Content.from_text(text="hi")])])
    except Exception:  # noqa: BLE001 - the stub answers 400; only the request matters
        pass


def _sdk(client: Any) -> Any:
    return getattr(client, "client", None) or client.anthropic_client


def _use_azure(configure: Callable[..., None]) -> None:
    configure(
        LLM_PROVIDER="azure",
        AZURE_OPENAI_ENDPOINT="https://example.openai.azure.com/",
        AZURE_OPENAI_KEY="az-key",
        AZURE_OPENAI_DEPLOYMENT="gpt-4.1",
        AZURE_OPENAI_AUTH="key",
    )


def _use_anthropic(configure: Callable[..., None]) -> None:
    configure(LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="sk-ant-test", ANTHROPIC_MODEL="claude-sonnet-5-5")


SHAPES: dict[str, Callable[[Callable[..., None]], None]] = {
    "openai-responses": lambda c: c(LLM_API="responses"),
    "openai-chat-completions": lambda c: c(LLM_API="chat_completions"),
    "openai-compatible": lambda c: c(LLM_API="chat_completions", LLM_BASE_URL="http://localhost:11434/v1"),
    "azure": _use_azure,
    "anthropic": _use_anthropic,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", SHAPES)
async def test_chat_call_carries_the_three_headers(shape: str, configure: Callable[..., None]) -> None:
    SHAPES[shape](configure)
    configure(EVAL_RUN_ID="eval-2026-11-21")
    client = factory.get_chat_client("product-discovery")
    current_run_id.set("run-abc123")
    recorder = _Recorder()
    _stub(_sdk(client), recorder)

    await _chat(client)

    assert recorder.headers, "no request reached the stub"
    sent = recorder.headers[0]
    assert sent["x-agent-name"] == "product-discovery"
    assert sent["x-run-id"] == "run-abc123"
    assert sent["x-eval-run-id"] == "eval-2026-11-21"


@pytest.mark.asyncio
async def test_run_id_is_read_per_request_and_agents_differ(configure: Callable[..., None]) -> None:
    first = factory.get_chat_client("order-management")
    second = factory.get_chat_client("pricing-promotions")
    recorder = _Recorder()
    _stub(_sdk(first), recorder)
    _stub(_sdk(second), recorder)

    current_run_id.set("run-1")
    await _chat(first)
    await _chat(second)
    current_run_id.set("run-2")
    await _chat(first)

    names = [h["x-agent-name"] for h in recorder.headers]
    runs = [h["x-run-id"] for h in recorder.headers]
    assert names == ["order-management", "pricing-promotions", "order-management"]
    assert runs == ["run-1", "run-1", "run-2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "azure", "openai_compatible"])
async def test_embeddings_call_carries_the_headers(provider: str, configure: Callable[..., None]) -> None:
    if provider == "azure":
        _use_azure(configure)
        configure(EMBEDDING_PROVIDER="azure")
    elif provider == "openai_compatible":
        configure(EMBEDDING_PROVIDER="openai_compatible", EMBEDDING_BASE_URL="http://localhost:11434/v1")
    else:
        configure(EMBEDDING_PROVIDER="openai")
    factory.get_chat_client("product-discovery")  # a specialist's process: the agent it serves is bound
    current_run_id.set("run-emb")
    configure(EVAL_RUN_ID="eval-emb")
    client = factory.get_embeddings_client()
    recorder = _Recorder()
    _stub(client, recorder)

    with pytest.raises(Exception):  # noqa: B017, PT011 - the stub answers 400
        await client.embeddings.create(model="text-embedding-3-small", input=["hi"])

    sent = recorder.headers[0]
    assert (sent["x-agent-name"], sent["x-run-id"], sent["x-eval-run-id"]) == (
        "product-discovery",
        "run-emb",
        "eval-emb",
    )


@pytest.mark.asyncio
async def test_disabled_sends_none(configure: Callable[..., None]) -> None:
    configure(GATEWAY_HEADERS_ENABLED=False, EVAL_RUN_ID="x")
    current_run_id.set("run-1")
    client = factory.get_chat_client("product-discovery")
    recorder = _Recorder()
    _stub(_sdk(client), recorder)

    await _chat(client)

    assert recorder.headers
    assert not [h for h in GATEWAY if h in recorder.headers[0]]


@pytest.mark.asyncio
async def test_empty_values_are_omitted(configure: Callable[..., None]) -> None:
    client = factory.get_chat_client("product-discovery")  # no run id, no eval run id
    recorder = _Recorder()
    _stub(_sdk(client), recorder)

    await _chat(client)

    sent = recorder.headers[0]
    assert sent["x-agent-name"] == "product-discovery"
    assert "x-run-id" not in sent
    assert "x-eval-run-id" not in sent


@pytest.mark.asyncio
async def test_eval_context_var_beats_the_env_setting(configure: Callable[..., None]) -> None:
    configure(EVAL_RUN_ID="from-env")
    current_eval_run_id.set("from-harness")
    client = factory.get_chat_client("product-discovery")
    recorder = _Recorder()
    _stub(_sdk(client), recorder)

    await _chat(client)

    assert recorder.headers[0]["x-eval-run-id"] == "from-harness"


def test_values_are_sanitised() -> None:
    assert gateway_headers.sanitize_header_value("a b\r\nc\x00d") == "abcd"
    assert gateway_headers.sanitize_header_value("café-bot") == "caf-bot"
    assert gateway_headers.sanitize_header_value("someone@example.com") == "someoneexample.com"
    assert len(gateway_headers.sanitize_header_value("x" * 1000)) == gateway_headers.MAX_VALUE_LENGTH
    assert gateway_headers.sanitize_header_value(None) == ""


@pytest.mark.asyncio
async def test_no_user_identity_or_secret_in_the_headers(configure: Callable[..., None]) -> None:
    from shared.context import current_session_id, current_user_email, current_user_role

    current_user_email.set("shopper@example.com")
    current_user_role.set("admin")
    current_session_id.set("11111111-2222-3333-4444-555555555555")
    client = factory.get_chat_client("product-discovery")
    recorder = _Recorder()
    _stub(_sdk(client), recorder)
    try:
        await _chat(client)
        blob = json.dumps({k: v for k, v in recorder.headers[0].items() if k in GATEWAY})
        for leaked in ("shopper@example.com", "shopper", "admin", "11111111", "sk-test"):
            assert leaked not in blob
    finally:
        for var in (current_user_email, current_user_role, current_session_id):
            var.set("")


@pytest.mark.asyncio
async def test_the_judge_client_is_attributed_to_eval_judge(configure: Callable[..., None]) -> None:
    configure(JUDGE_PROVIDER="openai", JUDGE_MODEL="gpt-4.1-mini", JUDGE_API_KEY="sk-judge", JUDGE_BASE_URL="")
    client = factory.get_judge_chat_client()
    recorder = _Recorder()
    _stub(_sdk(client), recorder)

    await _chat(client)

    assert recorder.headers[0]["x-agent-name"] == "eval-judge"
    assert current_agent_name.get() == ""  # the judge never becomes "the agent"


def test_replay_client_is_untouched_and_the_replay_key_does_not_move(configure: Callable[..., None]) -> None:
    messages = [Message(role="user", contents=[Content.from_text(text="Find me headphones")])]
    options = {"instructions": "Be brief.", "tools": []}

    def key() -> str:
        return _request_hash(_canonical_request(messages, options))

    configure(GATEWAY_HEADERS_ENABLED=False)
    off = key()
    configure(GATEWAY_HEADERS_ENABLED=True, EVAL_RUN_ID="e1")
    current_run_id.set("run-x")
    current_agent_name.set("product-discovery")
    on = key()
    assert on == off

    configure(LLM_PROVIDER="replay")
    from shared.replay_client import ReplayChatClient

    client = factory.get_chat_client("product-discovery")
    assert isinstance(client, ReplayChatClient)
    assert not hasattr(client, "client")  # no SDK client, so nothing to attach a hook to


def test_chat_clients_are_the_expected_shapes(configure: Callable[..., None]) -> None:
    configure(LLM_API="responses")
    assert isinstance(factory.get_chat_client("a"), OpenAIChatClient)
    configure(LLM_API="chat_completions")
    assert isinstance(factory.get_chat_client("a"), OpenAIChatCompletionClient)
