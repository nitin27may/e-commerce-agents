"""A replay miss is only ever acted on in replay mode (C2).

``HTTP 424`` and ``event: replay_miss`` are how a replay-mode specialist says "I have no recording". An
orchestrator that is *not* in replay mode has no business honouring them: a proxy's 424, a misconfigured or
hostile specialist must not make a production orchestrator delete the user's message, drop the answer and show
the demo text. Outside replay every one of those signals is an ordinary specialist failure.
"""

# ruff: noqa: F811

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

import orchestrator.agent as orch_mod
from orchestrator.routes.chat import ChatRequest, chat, chat_stream
from shared.a2a_client import _raise_if_miss
from shared.a2a_server import REPLAY_MISS_EXTENSION_URI
from shared.context import current_steps, current_stream_queue, current_user_email, current_user_role
from shared.remote_agent import RemoteSpecialistChatClient
from shared.replay_miss import (
    REPLAY_MISS_STATUS,
    SpecialistReplayMissError,
    is_replay_miss_event,
    is_replay_miss_status,
)
from tests.test_orchestration_resume import _seed_user
from tests.test_replay_miss import (
    _FakeRequest,
    _miss_transport,
    _ScriptedReplayClient,
    _set,
    _text,
    _tool_call,
)

QUESTION = "show me headphones"


def _specialist_registry(monkeypatch: pytest.MonkeyPatch, transport: httpx.AsyncBaseTransport) -> None:
    monkeypatch.setattr(orch_mod, "AGENT_REGISTRY", {"product-discovery": "http://pd:8081"})
    monkeypatch.setattr(orch_mod, "_A2A_TRANSPORT", transport)
    _set(monkeypatch, "AGENT_SHARED_SECRET", "test-secret")
    current_user_email.set("u@example.com")
    current_user_role.set("customer")
    current_steps.set([])


@pytest.fixture
def not_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, "LLM_PROVIDER", "openai")


# ── the predicates ───────────────────────────────────────────────────────────


def test_the_miss_predicates_are_false_outside_replay_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, "LLM_PROVIDER", "openai")
    assert not is_replay_miss_status(REPLAY_MISS_STATUS)
    assert not is_replay_miss_event({"data": {"error_type": "SpecialistReplayMissError"}})
    _raise_if_miss({REPLAY_MISS_EXTENSION_URI: {"error": "replay_miss"}})  # no raise

    _set(monkeypatch, "LLM_PROVIDER", "replay")
    assert is_replay_miss_status(REPLAY_MISS_STATUS) and not is_replay_miss_status(500)
    assert is_replay_miss_event({"data": {"error_type": "SpecialistReplayMissError"}})
    with pytest.raises(SpecialistReplayMissError):
        _raise_if_miss({REPLAY_MISS_EXTENSION_URI: {"error": "replay_miss"}})


# ── call_specialist_agent: an ordinary failure, never a miss ─────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [True, False])
async def test_a_424_from_a_specialist_is_an_ordinary_error_outside_replay(
    not_replay: None, monkeypatch: pytest.MonkeyPatch, streaming: bool
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(REPLAY_MISS_STATUS, json={"error": "replay_miss"})

    _specialist_registry(monkeypatch, httpx.MockTransport(handler))
    current_stream_queue.set(asyncio.Queue() if streaming else None)

    result = await orch_mod.call_specialist_agent(agent_name="product-discovery", message="hi")

    assert "returned an error (status 424)" in result


@pytest.mark.asyncio
async def test_a_replay_miss_frame_from_a_specialist_is_an_interrupted_stream_outside_replay(
    not_replay: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths: list[str] = []
    _specialist_registry(monkeypatch, _miss_transport(paths))
    queue: asyncio.Queue[Any] = asyncio.Queue()
    current_stream_queue.set(queue)

    result = await orch_mod.call_specialist_agent(agent_name="product-discovery", message="hi")

    assert "interrupted" in result and paths == ["/message:stream"]  # reported as a failure, never re-sent
    assert "replay_miss" not in result
    assert queue.empty(), "the marker is not shown to the user as if the specialist had said it"


@pytest.mark.asyncio
async def test_the_handoff_wire_treats_a_424_as_an_http_error_outside_replay(
    not_replay: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(REPLAY_MISS_STATUS, json={"error": "replay_miss"})

    monkeypatch.setattr("shared.remote_agent._A2A_TRANSPORT", httpx.MockTransport(handler))
    _set(monkeypatch, "AGENT_SHARED_SECRET", "test-secret")
    current_user_email.set("u@example.com")
    current_user_role.set("customer")
    agent = RemoteSpecialistChatClient(name="product-discovery", url="http://pd:8081")

    with pytest.raises(httpx.HTTPStatusError):
        await agent._post("hi")  # noqa: SLF001


# ── the chat routes: the user's message is kept ──────────────────────────────


async def _scripted_orchestrator(monkeypatch: pytest.MonkeyPatch) -> tuple[_ScriptedReplayClient, list[str]]:
    client = _ScriptedReplayClient(
        _tool_call("call_specialist_agent", {"agent_name": "product-discovery", "message": "headphones"}),
        _text("The specialist was unavailable, please try again."),
    )
    paths: list[str] = []
    monkeypatch.setattr(orch_mod, "create_chat_client", lambda *_a, **_k: client)
    monkeypatch.setattr(orch_mod, "AGENT_REGISTRY", {"product-discovery": "http://pd:8081"})
    monkeypatch.setattr(orch_mod, "_A2A_TRANSPORT", _miss_transport(paths))
    _set(monkeypatch, "AGENT_SHARED_SECRET", "test-secret")
    return client, paths


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_non_replay_orchestrator_keeps_the_users_message_on_a_specialist_miss_signal_blocking(
    clean_db, not_replay: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = await _seed_user(clean_db, "gate@example.com")
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    user = {"sub": "gate@example.com", "role": "customer", "user_id": str(user_id)}
    client, paths = await _scripted_orchestrator(monkeypatch)

    result = await chat(ChatRequest(message=QUESTION, mode="tool"), user=user, _rate_limit=None)

    assert result.replay_miss is None
    assert result.response == "The specialist was unavailable, please try again."
    assert client.model_calls == 2  # the model was asked again, as after any failed tool call
    assert await clean_db.fetchval("SELECT count(*) FROM messages WHERE content = $1", QUESTION) == 1
    assert await clean_db.fetchval("SELECT count(*) FROM messages WHERE role = 'assistant'") == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_non_replay_orchestrator_keeps_the_users_message_on_a_specialist_miss_signal_streaming(
    clean_db, not_replay: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = await _seed_user(clean_db, "gate2@example.com")
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    user = {"sub": "gate2@example.com", "role": "customer", "user_id": str(user_id)}
    await _scripted_orchestrator(monkeypatch)

    response = await chat_stream(ChatRequest(message=QUESTION, mode="tool"), _FakeRequest(), user=user)
    raw = "".join([chunk async for chunk in response.body_iterator])

    assert "replay_miss" not in raw
    assert await clean_db.fetchval("SELECT count(*) FROM messages WHERE content = $1", QUESTION) == 1


# ── the A2A 1.0 wire ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("streaming", [True, False], ids=["stream", "blocking"])
@pytest.mark.asyncio
async def test_a_replay_miss_task_on_the_a2a_wire_is_an_ordinary_failure_outside_replay(
    monkeypatch: pytest.MonkeyPatch, streaming: bool
) -> None:
    from httpx import ASGITransport

    from tests.a2a_support import build_app
    from tests.test_a2a_transport import _bind

    app, _ = build_app("miss")  # a replay-mode specialist that has no recording
    _bind(monkeypatch, ASGITransport(app=app), transport_mode="a2a", streaming=streaming)
    _set(monkeypatch, "LLM_PROVIDER", "openai")  # the caller is not replaying

    result = await orch_mod.call_specialist_agent(agent_name="product-discovery", message="hi")

    assert isinstance(result, str) and "replay" not in result.lower()  # a plain failure text, not a miss


@pytest.mark.asyncio
async def test_the_handoff_participant_on_the_a2a_wire_fails_plainly_outside_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from httpx import ASGITransport

    import shared.a2a_client as a2a_client
    from tests.a2a_support import build_app
    from tests.test_a2a_transport import _bind

    app, _ = build_app("miss")
    _bind(monkeypatch, ASGITransport(app=app), transport_mode="a2a", streaming=False)
    _set(monkeypatch, "LLM_PROVIDER", "openai")
    client = RemoteSpecialistChatClient(name="product-discovery", url="http://pd:8081")

    with pytest.raises(a2a_client.A2AClientError):  # an ordinary failed task, never a SpecialistReplayMissError
        await client._post("hi")  # noqa: SLF001


# ── and in replay mode nothing changed ───────────────────────────────────────


@pytest.mark.asyncio
async def test_in_replay_mode_a_424_is_still_a_miss(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(REPLAY_MISS_STATUS, json={"error": "replay_miss"})

    _set(monkeypatch, "LLM_PROVIDER", "replay")
    _specialist_registry(monkeypatch, httpx.MockTransport(handler))
    current_stream_queue.set(None)

    with pytest.raises(SpecialistReplayMissError):
        await orch_mod.call_specialist_agent(agent_name="product-discovery", message="hi")
