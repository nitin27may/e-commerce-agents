"""#86 C-4: a prompt with no recording is a ``replay_miss``, end to end, and never an apology.

Three layers, each tested against the real code rather than a double of it:

* the orchestrator's own model call misses -- the *real* ``ReplayChatClient`` pointed
  at an empty fixtures dir, through both chat routes and the three kinds of mode task;
* a SPECIALIST's model call misses -- the A2A hop carries it as HTTP 424 (blocking) or
  an ``event: replay_miss`` frame (stream), and both consumers re-raise it instead of
  handing the model a string; the orchestrator's model is then never called again;
* the specialist host itself produces the 424 / the frame.

Nothing the user sees may carry the exception text, a fixture hash or a path.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from agent_framework import Agent, ChatResponse, Content, Message
from fastapi import FastAPI
from starlette.testclient import TestClient

import orchestrator.agent as orch_mod
from orchestrator.routes.chat import ChatRequest, chat, chat_stream
from shared.agent_host import create_agent_app
from shared.config import settings
from shared.context import (
    current_session_id,
    current_steps,
    current_stream_queue,
    current_user_email,
    current_user_role,
)
from shared.demo_manifest import load_manifest
from shared.replay_client import ReplayChatClient, ReplayFixtureMissingError
from shared.replay_miss import (
    DEFAULT_MISS_MESSAGE,
    REPLAY_MISS_STATUS,
    SpecialistReplayMissError,
    is_replay_miss_event,
)
from shared.sse import aiter_sse_events
from tests.test_handoff_orchestration import _StubTransport, stub_transport  # noqa: F401

ANON_USER = {"sub": "", "role": "anonymous", "user_id": "", "anonymous": True}


class _FakeRequest:
    async def is_disconnected(self) -> bool:
        return False


def _set(monkeypatch: pytest.MonkeyPatch, name: str, value: Any) -> None:
    """Set a setting on every ``settings`` object in play.

    Other test modules ``importlib.reload`` ``shared.config`` / ``shared.factory``, after which
    modules imported earlier hold a different ``settings`` instance than ``shared.config.settings``.
    """
    import shared.config as config_mod
    import shared.factory as factory_mod
    import shared.middleware as middleware_mod
    import shared.oauth.service_client as client_mod

    seen: list[Any] = []
    for obj in (
        settings,
        config_mod.settings,
        factory_mod.settings,
        middleware_mod.settings,
        orch_mod.settings,
        client_mod.settings,
    ):
        if not any(obj is s for s in seen):
            seen.append(obj)
            monkeypatch.setattr(obj, name, value, raising=False)


@pytest.fixture
def replay_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Replay provider over an empty fixtures dir: every model call is a miss."""
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    _set(monkeypatch, "LLM_PROVIDER", "replay")
    _set(monkeypatch, "REPLAY_FIXTURES_DIR", str(fixtures))
    _set(monkeypatch, "RECORD", False)
    # The routes are called directly, so no auth dependency set the request identity; pin it
    # rather than inherit whatever an earlier test left in this context.
    current_user_email.set("")
    current_user_role.set("anonymous")
    current_session_id.set("")
    monkeypatch.setattr("shared.db._pool", object(), raising=False)
    return fixtures


async def _stream_frames(mode: str, message: str = "tell me about headphones") -> tuple[list[tuple[str, str]], str]:
    response = await chat_stream(ChatRequest(message=message, mode=mode), _FakeRequest(), user=ANON_USER)
    raw = "".join([chunk async for chunk in response.body_iterator])

    async def _chunks():
        yield raw

    return [pair async for pair in aiter_sse_events(_chunks())], raw


def _assert_no_leak(raw: str) -> None:
    lowered = raw.lower()
    for needle in ("hash=", "no replay fixture", ".json", "record=true", "traceback"):
        assert needle not in lowered, f"{needle!r} leaked to the client: {raw[:300]}"


def _miss_frames(frames: list[tuple[str, str]]) -> list[dict[str, Any]]:
    return [json.loads(data) for name, data in frames if name == "replay_miss"]


# ─────────────────────── the orchestrator's own miss ───────────────────────


@pytest.mark.asyncio
async def test_blocking_route_returns_a_structured_miss(replay_empty: Path) -> None:
    result = await chat(ChatRequest(message="show me headphones"), user=ANON_USER, _rate_limit=None)

    assert result.replay_miss is not None
    manifest = load_manifest()
    expected = manifest.miss_message if manifest else DEFAULT_MISS_MESSAGE
    assert result.replay_miss == {"message": expected}
    assert result.response == expected
    assert "apologize" not in result.response
    _assert_no_leak(result.model_dump_json())


@pytest.mark.asyncio
async def test_stream_route_tool_mode_emits_replay_miss(replay_empty: Path) -> None:
    frames, raw = await _stream_frames("tool")

    misses = _miss_frames(frames)
    assert len(misses) == 1 and misses[0]["message"]
    assert not any(name == "data" and "apologize" in data for name, data in frames)
    assert frames[-2][0] == "metadata" and frames[-1] == ("data", "[DONE]")
    _assert_no_leak(raw)


@pytest.mark.asyncio
async def test_stream_route_handoff_mode_emits_replay_miss(replay_empty: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("orchestrator.handoff._load_registry", lambda: {"order-management": "http://om:8082"})

    frames, raw = await _stream_frames("handoff")

    assert len(_miss_frames(frames)) == 1
    # The failed executor's own `error` frame names a fixture hash; it must not be forwarded.
    assert not any(name == "error" for name, _ in frames)
    assert not any(name == "data" and data.strip() not in ("[DONE]", "") for name, data in frames)
    _assert_no_leak(raw)


@pytest.mark.asyncio
async def test_stream_route_group_chat_emits_replay_miss(replay_empty: Path) -> None:
    frames, raw = await _stream_frames("group-chat", "is the Sony WH-1000XM5 worth it?")

    assert len(_miss_frames(frames)) == 1
    assert not any(name == "error" for name, _ in frames)
    # The panelist's miss used to be written into the transcript as "(value could not respond: <hash>)".
    _assert_no_leak(raw)


# ─────────────────────── a specialist's miss ───────────────────────


class _ScriptedReplayClient(ReplayChatClient):
    """The real client class (so MAF layers it exactly as in production) with scripted answers.

    Counts model calls, which is the whole point: after a specialist's miss there must be none.
    """

    def __init__(self, *responses: ChatResponse) -> None:
        super().__init__(fixtures_dir="unused")
        self._script = list(responses)
        self.model_calls = 0

    async def _load_or_record(self, messages: Any, options: dict[str, Any] | None) -> ChatResponse:
        self.model_calls += 1
        if not self._script:
            raise AssertionError("the model was called again after a specialist's replay miss")
        return self._script.pop(0)


def _tool_call(name: str, arguments: dict[str, Any]) -> ChatResponse:
    return ChatResponse(
        messages=[
            Message(
                role="assistant", contents=[Content.from_function_call(call_id="c1", name=name, arguments=arguments)]
            )
        ],
        response_id=str(uuid.uuid4()),
        finish_reason="tool_calls",
    )


def _text(text: str) -> ChatResponse:
    return ChatResponse(
        messages=[Message(role="assistant", contents=[text])], response_id=str(uuid.uuid4()), finish_reason="stop"
    )


def _miss_transport(paths: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/message:stream":
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=b'event: replay_miss\ndata: {"error": "replay_miss"}\n\ndata: [DONE]\n\n',
            )
        return httpx.Response(REPLAY_MISS_STATUS, json={"error": "replay_miss"})

    return httpx.MockTransport(handler)


@pytest.fixture
def tool_mode_with_missing_specialist(
    replay_empty: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[_ScriptedReplayClient, list[str]]:
    client = _ScriptedReplayClient(
        _tool_call("call_specialist_agent", {"agent_name": "product-discovery", "message": "headphones"}),
        # The follow-up call is exactly what a miss must prevent: if the model were asked again it would
        # "answer" from the tool result, and this text would reach the user as if it were real.
        _text("SHOULD NEVER BE GENERATED"),
    )
    paths: list[str] = []
    monkeypatch.setattr(orch_mod, "create_chat_client", lambda *_a, **_k: client)
    monkeypatch.setattr(orch_mod, "AGENT_REGISTRY", {"product-discovery": "http://pd:8081"})
    monkeypatch.setattr(orch_mod, "_A2A_TRANSPORT", _miss_transport(paths))
    _set(monkeypatch, "AGENT_SHARED_SECRET", "test-secret")
    return client, paths


@pytest.mark.asyncio
async def test_specialist_miss_in_tool_mode_reaches_the_stream(
    tool_mode_with_missing_specialist: tuple[_ScriptedReplayClient, list[str]],
) -> None:
    client, paths = tool_mode_with_missing_specialist

    frames, raw = await _stream_frames("tool")

    assert len(_miss_frames(frames)) == 1
    assert paths == ["/message:stream"]
    assert client.model_calls == 1, "a second model call was made after the specialist's miss"
    assert "SHOULD NEVER BE GENERATED" not in raw
    assert not any(name == "data" and "no recorded response" in data for name, data in frames)
    _assert_no_leak(raw)


@pytest.mark.asyncio
async def test_specialist_miss_in_tool_mode_blocking_route(
    tool_mode_with_missing_specialist: tuple[_ScriptedReplayClient, list[str]],
) -> None:
    client, paths = tool_mode_with_missing_specialist

    result = await chat(ChatRequest(message="show me headphones", mode="tool"), user=ANON_USER, _rate_limit=None)

    assert result.replay_miss is not None
    assert paths == ["/message:send"]
    assert client.model_calls == 1
    assert "SHOULD NEVER BE GENERATED" not in result.response


@pytest.mark.asyncio
async def test_specialist_miss_in_handoff_mode(
    replay_empty: Path,
    monkeypatch: pytest.MonkeyPatch,
    stub_transport: _StubTransport,  # noqa: F811
) -> None:
    stub_transport.status = REPLAY_MISS_STATUS
    triage = _ScriptedReplayClient(
        _tool_call("handoff_to_math", {}),
        _text("SHOULD NEVER BE GENERATED"),
    )
    monkeypatch.setattr("orchestrator.handoff._load_registry", lambda: {"math": "http://math:9999/a2a"})
    monkeypatch.setattr(
        "orchestrator.handoff.create_handoff_triage_agent",
        lambda: Agent(
            client=triage, instructions="test", name="orchestrator", require_per_service_call_history_persistence=True
        ),
    )

    frames, raw = await _stream_frames("handoff", "what is 2+2")

    assert len(_miss_frames(frames)) == 1
    assert [c.url.path for c in stub_transport.calls] == ["/a2a/message:send"]
    assert triage.model_calls == 1, "a second model call was made after the specialist's miss"
    assert "SHOULD NEVER BE GENERATED" not in raw
    _assert_no_leak(raw)


@pytest.mark.asyncio
async def test_call_specialist_raises_on_a_424_from_the_stream_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(REPLAY_MISS_STATUS, json={"error": "replay_miss"})

    monkeypatch.setattr(orch_mod, "AGENT_REGISTRY", {"product-discovery": "http://pd:8081"})
    monkeypatch.setattr(orch_mod, "_A2A_TRANSPORT", httpx.MockTransport(handler))
    _set(monkeypatch, "AGENT_SHARED_SECRET", "test-secret")
    current_user_email.set("u@example.com")
    current_user_role.set("customer")
    current_stream_queue.set(asyncio.Queue())
    current_steps.set([])

    with pytest.raises(SpecialistReplayMissError):
        await orch_mod.call_specialist_agent(agent_name="product-discovery", message="hi")


@pytest.mark.asyncio
async def test_other_a2a_failures_are_still_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a 424 is a miss: an ordinary 500 keeps its old behaviour."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "Agent processing failed"})

    monkeypatch.setattr(orch_mod, "AGENT_REGISTRY", {"product-discovery": "http://pd:8081"})
    monkeypatch.setattr(orch_mod, "_A2A_TRANSPORT", httpx.MockTransport(handler))
    _set(monkeypatch, "AGENT_SHARED_SECRET", "test-secret")
    current_user_email.set("u@example.com")
    current_user_role.set("customer")
    current_stream_queue.set(None)
    current_steps.set([])

    result = await orch_mod.call_specialist_agent(agent_name="product-discovery", message="hi")
    assert "returned an error" in result


def test_the_guard_is_absent_outside_replay_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    from shared.replay_miss import replay_miss_middleware

    _set(monkeypatch, "LLM_PROVIDER", "openai")
    assert replay_miss_middleware() == []
    _set(monkeypatch, "LLM_PROVIDER", "replay")
    assert len(replay_miss_middleware()) == 1


# ─────────────────────── the specialist host ───────────────────────


def _specialist_app(fixtures: Path) -> FastAPI:
    agent = Agent(
        client=ReplayChatClient(fixtures_dir=fixtures),
        name="product-discovery",
        instructions="test",
    )
    return create_agent_app(agent=agent, agent_name="product-discovery", port=8081)


def test_specialist_host_send_returns_424_with_a_marker(replay_empty: Path) -> None:
    client = TestClient(_specialist_app(replay_empty))

    resp = client.post("/message:send", json={"message": "hello"})

    assert resp.status_code == 424
    assert resp.json() == {"error": "replay_miss"}
    _assert_no_leak(resp.text)


@pytest.mark.asyncio
async def test_specialist_host_stream_emits_a_replay_miss_frame(replay_empty: Path) -> None:
    app = _specialist_app(replay_empty)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        async with client.stream("POST", "/message:stream", json={"message": "hello"}) as resp:
            assert resp.status_code == 200
            events = [pair async for pair in aiter_sse_events(resp.aiter_text())]

    assert events[0] == ("replay_miss", json.dumps({"error": "replay_miss"}))
    assert events[-1] == ("data", "[DONE]")
    assert not any(name == "data" and data != "[DONE]" for name, data in events)
    _assert_no_leak(json.dumps(events))


def test_a_workflow_executor_miss_event_is_recognised() -> None:
    assert is_replay_miss_event({"data": {"error_type": ReplayFixtureMissingError.__name__, "message": "x"}})
    assert is_replay_miss_event({"data": {"error_type": SpecialistReplayMissError.__name__}})
    assert not is_replay_miss_event({"data": {"error_type": "ValueError"}})
    assert not is_replay_miss_event({"data": None})
    assert not is_replay_miss_event({})
