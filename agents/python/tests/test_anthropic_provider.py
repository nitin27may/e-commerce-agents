"""``LLM_PROVIDER=anthropic`` on the Python stack (P-3, #96), proved with a stubbed transport.

What these tests prove, because it is easy to over-read: that MAF's own connector
(``agent-framework-anthropic``), built by ``shared.factory`` and driven by a real MAF ``Agent``, (a) sends
requests in the Messages API shape (model, ``max_tokens``, system prompt, the tool definition), (b) turns a
canned ``tool_use`` into a call of the Python tool through MAF's tool loop, (c) sends the follow-up request with
a ``tool_result`` for the same ``tool_use_id``, and (d) maps ``usage`` into MAF's usage details -- blocking and
streaming. It mirrors the .NET ``AnthropicChatClientTests``.

What they do **not** prove: that Claude picks the right tool or arguments, prompt caching, the shape of real
error responses (rate limits, overload, a bad key), streaming edge cases the canned events do not include, or
that the canned JSON matches api.anthropic.com byte for byte (it follows the published schema). No live call has
ever been made with this provider; a smoke run with a real key is still owed (R2 in the plan).

The Anthropic SDK (2.x) is built on ``httpx2``, so the stub transport is an ``httpx2.MockTransport``.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Annotated, Any

import httpx2
import pytest
from agent_framework import Agent, Content, Message, tool
from agent_framework.anthropic import AnthropicClient
from anthropic import AsyncAnthropic

from shared import config as config_module
from shared import embedding_config, factory
from shared.anthropic_client import (
    ANTHROPIC_DEFAULT_MAX_OUTPUT_TOKENS,
    ECommerceAnthropicClient,
    build_anthropic_client,
)
from shared.config import Settings
from shared.context import current_agent_model

MODEL = "claude-sonnet-5-5"
KEY = "sk-ant-test-not-a-real-key"

TOOL_USE = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": MODEL,
    "content": [{"type": "tool_use", "id": "toolu_abc", "name": "get_stock", "input": {"sku": "HDPH-01"}}],
    "stop_reason": "tool_use",
    "stop_sequence": None,
    "usage": {"input_tokens": 12, "output_tokens": 7},
}
FINAL = {
    "id": "msg_2",
    "type": "message",
    "role": "assistant",
    "model": MODEL,
    "content": [{"type": "text", "text": "There are 42 units in stock."}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 30, "output_tokens": 9},
}


def _sse(events: list[tuple[str, dict[str, Any]]]) -> bytes:
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode()


def _stream_events(message: dict[str, Any]) -> bytes:
    """The Messages API streaming form of a canned message (one content block)."""
    block = message["content"][0]
    start_block = {**block, "input": {}} if block["type"] == "tool_use" else {**block, "text": ""}
    if block["type"] == "tool_use":
        delta = {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
    else:
        delta = {"type": "text_delta", "text": block["text"]}
    usage = message["usage"]
    return _sse(
        [
            (
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        **{k: message[k] for k in ("id", "type", "role", "model")},
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": usage["input_tokens"], "output_tokens": 1},
                    },
                },
            ),
            ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": start_block}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": delta}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": message["stop_reason"], "stop_sequence": None},
                    "usage": {"output_tokens": usage["output_tokens"]},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        ]
    )


class _Stub:
    """Records every request and answers with the queued canned messages, as JSON or as an SSE stream."""

    def __init__(self, *messages: dict[str, Any]) -> None:
        self._queue = list(messages)
        self.seen: list[tuple[httpx2.Request, dict[str, Any]]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        self.seen.append((request, body))
        message = self._queue.pop(0)
        if body.get("stream"):
            return httpx2.Response(200, content=_stream_events(message), headers={"content-type": "text/event-stream"})
        return httpx2.Response(200, json=message)

    def sdk_client(self) -> AsyncAnthropic:
        return AsyncAnthropic(api_key=KEY, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(self)))


def _agent(stub: _Stub, calls: list[str], **agent_kwargs: Any) -> Agent:
    @tool
    async def get_stock(sku: Annotated[str, "The SKU"]) -> str:
        """Units in stock for a SKU."""
        calls.append(sku)
        return "42"

    client = build_anthropic_client(model=MODEL, api_key=KEY, anthropic_client=stub.sdk_client())
    return Agent(client=client, instructions="Be brief.", tools=[get_stock], **agent_kwargs)


def _blocks(body: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        block for message in body["messages"] if isinstance(message["content"], list) for block in message["content"]
    ]


def _assert_first_request(request: httpx2.Request, body: dict[str, Any], max_tokens: int) -> None:
    assert request.method == "POST"
    assert request.url.path.endswith("/v1/messages")
    # The SDK (2.x) sends the key as ``Authorization: Bearer``, as the .NET package does.
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert body["model"] == MODEL
    assert body["max_tokens"] == max_tokens
    assert body["system"] == "Be brief."
    assert body["tools"][0]["name"] == "get_stock"
    assert "sku" in body["tools"][0]["input_schema"]["properties"]


def _assert_tool_result_follow_up(body: dict[str, Any]) -> None:
    blocks = _blocks(body)
    assert any(b["type"] == "tool_use" and b["id"] == "toolu_abc" for b in blocks)
    (result,) = [b for b in blocks if b["type"] == "tool_result"]
    assert result["tool_use_id"] == "toolu_abc"
    assert "42" in json.dumps(result["content"])


# ─────────────────────── the tool loop through a stubbed transport ───────────────────────


async def test_blocking_tool_use_round_trip_sends_tool_result_and_maps_usage() -> None:
    stub = _Stub(TOOL_USE, FINAL)
    calls: list[str] = []

    response = await _agent(stub, calls).run("How many HDPH-01 are in stock?")

    assert calls == ["HDPH-01"], "the tool the model asked for ran, with the model's arguments"
    assert response.text == "There are 42 units in stock."
    assert len(stub.seen) == 2
    _assert_first_request(*stub.seen[0], max_tokens=ANTHROPIC_DEFAULT_MAX_OUTPUT_TOKENS)
    _assert_tool_result_follow_up(stub.seen[1][1])
    # Usage is summed across both model calls (12 + 30 in, 7 + 9 out).
    assert response.usage_details is not None
    assert response.usage_details["input_token_count"] == 42
    assert response.usage_details["output_token_count"] == 16


async def test_streaming_tool_use_round_trip_sends_tool_result_and_yields_text() -> None:
    stub = _Stub(TOOL_USE, FINAL)
    calls: list[str] = []

    stream = _agent(stub, calls).run("How many HDPH-01 are in stock?", stream=True)
    text = ""
    async for update in stream:
        text += update.text or ""
    final = await stream.get_final_response()

    assert calls == ["HDPH-01"]
    assert text == "There are 42 units in stock."
    assert final.text == "There are 42 units in stock."
    assert len(stub.seen) == 2
    assert all(body["stream"] is True for _, body in stub.seen)
    _assert_first_request(*stub.seen[0], max_tokens=ANTHROPIC_DEFAULT_MAX_OUTPUT_TOKENS)
    _assert_tool_result_follow_up(stub.seen[1][1])
    assert final.usage_details is not None
    assert final.usage_details["output_token_count"] == 16


async def test_an_explicit_max_tokens_beats_the_default() -> None:
    stub = _Stub(FINAL)
    client = build_anthropic_client(model=MODEL, api_key=KEY, anthropic_client=stub.sdk_client())
    agent = Agent(client=client, instructions="Be brief.", default_options={"max_tokens": 321})

    await agent.run("hi")

    assert stub.seen[0][1]["max_tokens"] == 321


async def test_default_max_tokens_applies_to_a_bare_client_call() -> None:
    """Claude requires max_tokens; the connector alone would send 1024. This stack matches .NET's 4096."""
    stub = _Stub(FINAL)
    client = build_anthropic_client(model=MODEL, api_key=KEY, anthropic_client=stub.sdk_client())

    await client.get_response([Message(role="user", contents=[Content.from_text(text="hi")])])

    assert stub.seen[0][1]["max_tokens"] == 4096


# ─────────────────────── the factory ───────────────────────


def _set(monkeypatch: pytest.MonkeyPatch, name: str, value: Any) -> None:
    """Patch ``name`` on every live settings object (other tests reload ``shared.config``)."""
    for obj in {id(o): o for o in (factory.settings, config_module.settings)}.values():
        monkeypatch.setattr(obj, name, value)


@pytest.fixture
def anthropic_settings(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    defaults = {
        "LLM_PROVIDER": "anthropic",
        "ANTHROPIC_API_KEY": KEY,
        "ANTHROPIC_MODEL": MODEL,
        "LLM_MODEL": "gpt-4.1",
        "OPENAI_API_KEY": "",
        "EMBEDDING_PROVIDER": "",
        "EMBEDDING_BASE_URL": "",
        "EMBEDDING_API_KEY": "",
    }
    for name, value in defaults.items():
        _set(monkeypatch, name, value)
    monkeypatch.setattr(factory, "_warned_anthropic_embeddings", False)
    current_agent_model.set("")

    def apply(**overrides: Any) -> None:
        for name, value in overrides.items():
            _set(monkeypatch, name, value)

    return apply


def test_settings_default_to_the_dotnet_model_and_read_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """``ANTHROPIC_MODEL`` defaults to the id the .NET stack defaults to (one default across stacks)."""
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert Settings(_env_file=None).ANTHROPIC_MODEL == "claude-sonnet-5-5"  # type: ignore[call-arg]
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-haiku-5-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    configured = Settings(_env_file=None)  # type: ignore[call-arg]
    assert (configured.ANTHROPIC_MODEL, configured.ANTHROPIC_API_KEY) == ("claude-haiku-5-5", "k")


def test_get_chat_client_builds_the_connector_with_anthropic_model_not_llm_model(anthropic_settings) -> None:
    client = factory.get_chat_client()
    assert isinstance(client, ECommerceAnthropicClient)
    assert isinstance(client, AnthropicClient)  # MAF's own connector, not a hand-rolled client
    assert client.model == MODEL, "LLM_MODEL (gpt-4.1 in the compose files) must never reach Claude"
    assert current_agent_model.get() == ""


def test_anthropic_model_setting_is_used(anthropic_settings) -> None:
    anthropic_settings(ANTHROPIC_MODEL="claude-opus-5-5")
    assert factory.get_chat_client().model == "claude-opus-5-5"


@pytest.mark.parametrize("key", ["", "   "])
def test_missing_key_names_the_variable_and_never_a_value(anthropic_settings, key) -> None:
    anthropic_settings(ANTHROPIC_API_KEY=key)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic") as err:
        factory.get_chat_client()
    assert KEY not in str(err.value)


def test_unknown_provider_message_lists_anthropic(anthropic_settings) -> None:
    anthropic_settings(LLM_PROVIDER="bogus")
    with pytest.raises(ValueError, match="'anthropic'"):
        factory.get_chat_client()


def test_other_providers_still_build_the_openai_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default path is untouched: no Anthropic client, no Anthropic import."""
    from agent_framework.openai import OpenAIChatClient

    _set(monkeypatch, "LLM_PROVIDER", "openai")
    _set(monkeypatch, "OPENAI_API_KEY", "sk-test")
    assert isinstance(factory.get_chat_client(), OpenAIChatClient)


# ── per-agent ``model:`` block ──


@pytest.fixture
def with_block(tmp_path, monkeypatch):
    import shutil

    import yaml

    from shared import prompt_loader

    target = tmp_path / "prompts"
    shutil.copytree(prompt_loader.PROMPTS_DIR, target)
    monkeypatch.setattr(prompt_loader, "PROMPTS_DIR", target)
    monkeypatch.setattr(prompt_loader, "SHARED_DIR", target / "_shared")
    prompt_loader._load_shared_file.cache_clear()

    def write(agent: str, block: dict[str, Any]) -> None:
        path = target / f"{agent}.yaml"
        data = yaml.safe_load(path.read_text())
        data["model"] = block
        path.write_text(yaml.safe_dump(data, sort_keys=False))

    return write


def test_agent_block_can_pick_a_claude_model_under_anthropic(anthropic_settings, with_block) -> None:
    with_block("orchestrator", {"model": "claude-haiku-5-5"})
    client = factory.get_chat_client("orchestrator")
    assert isinstance(client, ECommerceAnthropicClient)
    assert client.model == "claude-haiku-5-5"
    assert current_agent_model.get() == "claude-haiku-5-5"
    assert factory.get_chat_client("product-discovery").model == MODEL


def test_agent_block_can_switch_an_openai_install_to_claude(monkeypatch, with_block) -> None:
    _set(monkeypatch, "LLM_PROVIDER", "openai")
    _set(monkeypatch, "OPENAI_API_KEY", "sk-test")
    _set(monkeypatch, "ANTHROPIC_API_KEY", KEY)
    with_block("orchestrator", {"provider": "anthropic"})
    client = factory.get_chat_client("orchestrator")
    assert isinstance(client, ECommerceAnthropicClient)
    assert client.model == "claude-sonnet-5-5"


def test_agent_block_switching_away_from_claude_must_name_its_model(anthropic_settings, with_block) -> None:
    anthropic_settings(OPENAI_API_KEY="sk-test")
    with_block("orchestrator", {"provider": "openai"})
    with pytest.raises(ValueError, match="model.model is required when an agent switches from LLM_PROVIDER=anthropic"):
        factory.get_chat_client("orchestrator")


def test_agent_block_switching_away_from_claude_works_when_it_names_a_model(anthropic_settings, with_block) -> None:
    from agent_framework.openai import OpenAIChatClient

    anthropic_settings(OPENAI_API_KEY="sk-test")
    with_block("orchestrator", {"provider": "openai", "model": "gpt-4.1-mini"})
    assert isinstance(factory.get_chat_client("orchestrator"), OpenAIChatClient)


# ─────────────────────── embeddings: Anthropic has none ───────────────────────


def test_embedding_provider_is_the_unavailable_marker_when_nothing_else_is_chosen(anthropic_settings) -> None:
    assert embedding_config.embedding_provider() == embedding_config.NO_EMBEDDINGS


def test_embeddings_client_says_what_to_set_instead_of_demanding_an_openai_key(anthropic_settings) -> None:
    with pytest.raises(ValueError, match="no embeddings API; set EMBEDDING_PROVIDER"):
        factory.get_embeddings_client()


async def test_embed_texts_fails_with_the_same_message(anthropic_settings) -> None:
    with pytest.raises(ValueError, match="no embeddings API"):
        await factory.embed_texts(["x"])


def test_a_separate_embedding_provider_makes_embeddings_work_under_claude(anthropic_settings) -> None:
    anthropic_settings(EMBEDDING_PROVIDER="openai_compatible", EMBEDDING_BASE_URL="http://localhost:11434/v1")
    assert embedding_config.embedding_provider() == "openai_compatible"
    assert factory.get_embeddings_client() is not None


def test_the_chat_client_warns_once_that_semantic_search_is_unavailable(anthropic_settings, caplog) -> None:
    with caplog.at_level("WARNING", logger="shared.factory"):
        factory.get_chat_client()
        factory.get_chat_client()
    warnings = [r for r in caplog.records if "no embeddings API" in r.getMessage()]
    assert len(warnings) == 1


def test_no_warning_when_embeddings_are_configured_separately(anthropic_settings, caplog) -> None:
    anthropic_settings(EMBEDDING_PROVIDER="openai", EMBEDDING_API_KEY="sk-embed")
    with caplog.at_level("WARNING", logger="shared.factory"):
        factory.get_chat_client()
    assert not [r for r in caplog.records if "no embeddings API" in r.getMessage()]


async def test_semantic_search_degrades_to_full_text_under_claude(anthropic_settings, monkeypatch, caplog) -> None:
    """The tool catches the embedding failure and runs its full-text arm; the stack does not stop."""
    from product_discovery import tools as pd_tools

    captured: dict[str, Any] = {}

    class _Conn:
        async def execute(self, sql: str, *args: Any) -> None:
            return None

        async def fetch(self, sql: str, *args: Any) -> list[Any]:
            captured["sql"] = sql
            return []

    class _Acquire:
        async def __aenter__(self) -> _Conn:
            return _Conn()

        async def __aexit__(self, *exc: Any) -> None:
            return None

    class _Pool:
        def acquire(self) -> _Acquire:
            return _Acquire()

    monkeypatch.setattr(pd_tools, "get_pool", lambda: _Pool())
    with caplog.at_level("WARNING"):
        result = await pd_tools.semantic_search.func(query="cozy winter gift", limit=3)  # type: ignore[attr-defined]
    assert result == []
    assert "NULL::float8 AS similarity" in captured["sql"], "no embedding was produced, so only the full-text arm ran"
    assert any("falling back to full-text only" in r.getMessage() for r in caplog.records)


async def test_seeder_skips_embeddings_under_claude_without_touching_the_database(anthropic_settings) -> None:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # scripts/ is at the repo root
    from scripts import seed as seed_mod

    class _NoDb:
        def __getattr__(self, name: str) -> Any:  # any call would fail the test
            raise AssertionError(f"the seeder touched the database ({name})")

    assert await seed_mod.seed_product_embeddings(_NoDb()) == 0  # type: ignore[arg-type]


# ─────────────────────── the replay recorder ───────────────────────


async def test_the_recorder_records_through_the_anthropic_connector_and_replays_it(
    anthropic_settings, monkeypatch, tmp_path
) -> None:
    """RECORD=true with REPLAY_RECORD_PROVIDER=anthropic writes a fixture; a plain replay then serves it."""
    from shared import anthropic_client
    from shared.replay_client import ReplayChatClient

    stub = _Stub(FINAL)
    real_build = anthropic_client.build_anthropic_client
    monkeypatch.setattr(
        anthropic_client,
        "build_anthropic_client",
        lambda **kw: real_build(**{**kw, "anthropic_client": stub.sdk_client()}),
    )
    messages = [Message(role="user", contents=[Content.from_text(text=f"hello {uuid.uuid4()}")])]

    recorder = ReplayChatClient(fixtures_dir=tmp_path, record=True, record_provider="anthropic")
    recorded = await recorder.get_response(messages)
    assert recorded.text == "There are 42 units in stock."
    assert len(stub.seen) == 1 and stub.seen[0][1]["model"] == MODEL
    assert len(list(tmp_path.glob("*.json"))) == 1

    replayed = await ReplayChatClient(fixtures_dir=tmp_path, record=False).get_response(messages)
    assert replayed.text == recorded.text
    assert len(stub.seen) == 1, "the replay made no model call"


def test_the_recorder_needs_the_key(anthropic_settings) -> None:
    from shared.replay_client import ReplayChatClient

    anthropic_settings(ANTHROPIC_API_KEY="")
    with pytest.raises(ValueError, match="REPLAY_RECORD_PROVIDER=anthropic requires ANTHROPIC_API_KEY"):
        ReplayChatClient(fixtures_dir="unused", record=True, record_provider="anthropic")._build_record_client()
