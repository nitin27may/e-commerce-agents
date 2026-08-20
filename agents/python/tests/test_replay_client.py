"""Tests for shared/replay_client.py.

Unit tests stub the "real" client the record path would otherwise call, so
they run with no network and no credentials. One integration test hits a
real provider (gated the same way the rest of the suite gates real-LLM
tests) to prove record -> replay works against an actual model, not just the
mechanism.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import pytest
from agent_framework import Agent, ChatResponse, Content, Message, tool

from shared.replay_client import ReplayChatClient, ReplayFixtureMissingError, _canonical_request, _request_hash


def _text_response(text: str) -> ChatResponse:
    return ChatResponse(
        messages=[Message(role="assistant", contents=[Content.from_text(text=text)])],
        response_id=str(uuid.uuid4()),
        finish_reason="stop",
    )


class _StubRecordClient:
    """Stand-in for the real client ReplayChatClient._build_record_client() returns."""

    def __init__(self, *responses: ChatResponse) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def _inner_get_response(self, *, messages: Any, stream: bool, options: dict[str, Any]) -> ChatResponse:
        self.calls += 1
        if not self._responses:
            raise AssertionError("stub record client ran out of canned responses")
        return self._responses.pop(0)


# ─────────────────────── hashing ───────────────────────


def test_canonical_request_excludes_sampling_params() -> None:
    messages = [Message(role="user", contents=["hello"])]
    canonical = _canonical_request(messages, {"temperature": 0.9, "max_tokens": 100})
    assert "temperature" not in canonical
    assert "max_tokens" not in canonical
    assert canonical["messages"][0]["contents"][0]["text"] == "hello"


def test_request_hash_is_stable_and_order_sensitive() -> None:
    m1 = [Message(role="user", contents=["a"])]
    m2 = [Message(role="user", contents=["a"])]
    m3 = [Message(role="user", contents=["b"])]
    assert _request_hash(_canonical_request(m1, {})) == _request_hash(_canonical_request(m2, {}))
    assert _request_hash(_canonical_request(m1, {})) != _request_hash(_canonical_request(m3, {}))


# ─────────────────────── record / replay mechanics ───────────────────────


@pytest.mark.asyncio
async def test_replay_raises_when_no_fixture_and_not_recording(tmp_path) -> None:
    client = ReplayChatClient(fixtures_dir=tmp_path, record=False)
    with pytest.raises(ReplayFixtureMissingError, match="RECORD=true"):
        await client._inner_get_response(messages=[Message(role="user", contents=["hi"])], stream=False, options={})


@pytest.mark.asyncio
async def test_record_writes_fixture_and_replay_reads_it_back(tmp_path) -> None:
    stub = _StubRecordClient(_text_response("Paris"))
    recorder = ReplayChatClient(fixtures_dir=tmp_path, record=True)
    recorder._record_client = stub  # bypass real client construction

    messages = [Message(role="user", contents=["capital of France?"])]
    recorded_response = await recorder._inner_get_response(messages=messages, stream=False, options={})
    assert recorded_response.text == "Paris"
    assert stub.calls == 1

    fixtures = list(tmp_path.glob("*.json"))
    assert len(fixtures) == 1
    saved = json.loads(fixtures[0].read_text())
    assert saved["response"]["messages"][0]["contents"][0]["text"] == "Paris"

    replayer = ReplayChatClient(fixtures_dir=tmp_path, record=False)
    replayed_response = await replayer._inner_get_response(messages=messages, stream=False, options={})
    assert replayed_response.text == "Paris"


@pytest.mark.asyncio
async def test_replay_never_calls_the_record_client_when_fixture_exists(tmp_path) -> None:
    messages = [Message(role="user", contents=["hi"])]
    stub = _StubRecordClient(_text_response("first"), _text_response("SHOULD NOT BE USED"))
    recorder = ReplayChatClient(fixtures_dir=tmp_path, record=True)
    recorder._record_client = stub
    await recorder._inner_get_response(messages=messages, stream=False, options={})
    assert stub.calls == 1

    replayer = ReplayChatClient(fixtures_dir=tmp_path, record=True)  # record=True but fixture already exists
    replayer._record_client = stub
    result = await replayer._inner_get_response(messages=messages, stream=False, options={})
    assert result.text == "first"
    assert stub.calls == 1, "existing fixture must short-circuit before touching the record client"


@pytest.mark.asyncio
async def test_streaming_replays_as_a_single_chunk_per_message(tmp_path) -> None:
    stub = _StubRecordClient(_text_response("streamed answer"))
    recorder = ReplayChatClient(fixtures_dir=tmp_path, record=True)
    recorder._record_client = stub
    await recorder._inner_get_response(messages=[Message(role="user", contents=["x"])], stream=False, options={})

    replayer = ReplayChatClient(fixtures_dir=tmp_path, record=False)
    stream = replayer._inner_get_response(messages=[Message(role="user", contents=["x"])], stream=True, options={})
    chunks = [c async for c in stream]
    assert len(chunks) == 1
    assert chunks[0].text == "streamed answer"


@pytest.mark.asyncio
async def test_streaming_response_supports_get_final_response(tmp_path) -> None:
    """Regression test: MAF's own streaming call sites (e.g. an AgentExecutor
    inside a WorkflowBuilder, which is how workflow-level `stream=True` reaches
    a chat client) don't just iterate updates — they call
    ResponseStream.get_final_response() to collapse the stream back into a
    ChatResponse. A bare ResponseStream(_gen()) has no finalizer wired, so this
    raised instead of returning. Caught wiring chapter 11 (agents-in-workflows),
    which drives its agents via workflow.run(text, stream=True).
    """
    stub = _StubRecordClient(_text_response("collapsed answer"))
    recorder = ReplayChatClient(fixtures_dir=tmp_path, record=True)
    recorder._record_client = stub
    await recorder._inner_get_response(messages=[Message(role="user", contents=["x"])], stream=False, options={})

    replayer = ReplayChatClient(fixtures_dir=tmp_path, record=False)
    stream = replayer._inner_get_response(messages=[Message(role="user", contents=["x"])], stream=True, options={})
    final = await stream.get_final_response()
    assert final.text == "collapsed answer"


# ─────────────────────── tool-calling loop, end to end via Agent ───────────────────────


@pytest.mark.asyncio
async def test_agent_replays_a_tool_calling_loop(tmp_path) -> None:
    """Two recorded turns: a function_call, then the final text answer.

    Proves ReplayChatClient's FunctionInvocationLayer composition actually
    drives real local tool execution during replay, not just text playback.
    """

    @tool(name="get_weather", description="Get weather for a city")
    async def get_weather(city: str) -> str:
        return f"sunny in {city}"

    call_response = ChatResponse(
        messages=[
            Message(
                role="assistant",
                contents=[
                    Content.from_function_call(call_id="call_1", name="get_weather", arguments={"city": "Paris"})
                ],
            )
        ],
        response_id=str(uuid.uuid4()),
        finish_reason="tool_calls",
    )
    final_response = _text_response("It's sunny in Paris!")

    stub = _StubRecordClient(call_response, final_response)
    recorder = ReplayChatClient(fixtures_dir=tmp_path, record=True)
    recorder._record_client = stub
    agent = Agent(client=recorder, instructions="test", tools=[get_weather])
    recorded_result = await agent.run("What's the weather in Paris?")
    assert "sunny" in recorded_result.text.lower()
    assert stub.calls == 2
    assert len(list(tmp_path.glob("*.json"))) == 2

    replayer = ReplayChatClient(fixtures_dir=tmp_path, record=False)
    replay_agent = Agent(client=replayer, instructions="test", tools=[get_weather])
    replayed_result = await replay_agent.run("What's the weather in Paris?")
    assert replayed_result.text == recorded_result.text


# ─────────────────────── real provider, gated ───────────────────────


def _available_provider() -> str | None:
    """Which real provider has usable credentials, checked directly rather than via
    settings.LLM_PROVIDER — that's a mutable module-level singleton other tests in this
    suite reassign without reverting, so it can't be trusted as a signal here."""
    from shared.config import settings

    if settings.AZURE_OPENAI_KEY and settings.AZURE_OPENAI_ENDPOINT:
        return "azure"
    if settings.OPENAI_API_KEY and not settings.OPENAI_API_KEY.startswith("sk-your-"):
        return "openai"
    return None


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(_available_provider() is None, reason="no LLM credentials in .env")
async def test_record_against_real_provider_then_replay_offline(tmp_path) -> None:
    provider = _available_provider()
    assert provider is not None  # skipif already guarantees this; narrows the type for mypy/readers
    recorder = ReplayChatClient(fixtures_dir=tmp_path, record=True, record_provider=provider)
    agent = Agent(client=recorder, instructions="Answer with exactly one word.")
    real_answer = await agent.run("What is the capital of France?")
    assert "paris" in real_answer.text.lower()
    assert len(list(tmp_path.glob("*.json"))) == 1

    # Blank the credentials the record path would need — replay must not touch them.
    saved_key, saved_endpoint = os.environ.get("AZURE_OPENAI_KEY"), os.environ.get("AZURE_OPENAI_ENDPOINT")
    saved_openai_key = os.environ.get("OPENAI_API_KEY")
    for var in ("AZURE_OPENAI_KEY", "AZURE_OPENAI_ENDPOINT", "OPENAI_API_KEY"):
        os.environ.pop(var, None)
    try:
        replayer = ReplayChatClient(fixtures_dir=tmp_path, record=False)
        replay_agent = Agent(client=replayer, instructions="Answer with exactly one word.")
        replayed_answer = await replay_agent.run("What is the capital of France?")
        assert replayed_answer.text == real_answer.text
    finally:
        if saved_key is not None:
            os.environ["AZURE_OPENAI_KEY"] = saved_key
        if saved_endpoint is not None:
            os.environ["AZURE_OPENAI_ENDPOINT"] = saved_endpoint
        if saved_openai_key is not None:
            os.environ["OPENAI_API_KEY"] = saved_openai_key
