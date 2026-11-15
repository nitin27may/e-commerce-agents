"""#98: the stream -> /message:send fallback only applies before the first frame."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator

import httpx
import pytest

import orchestrator.agent as orch_mod
from orchestrator.agent import call_specialist_agent
from shared.config import settings
from shared.context import (
    current_session_id,
    current_steps,
    current_stream_queue,
    current_user_email,
    current_user_role,
)


@contextlib.contextmanager
def _noop_span(*args, **kwargs):
    yield


class _BrokenStream(httpx.AsyncByteStream):
    """Yields the given bytes, then raises as a dropped connection would."""

    def __init__(self, head: bytes) -> None:
        self._head = head

    async def __aiter__(self) -> AsyncIterator[bytes]:
        if self._head:
            yield self._head
        raise httpx.ReadError("connection dropped")


def _setup(monkeypatch: pytest.MonkeyPatch, handler) -> tuple[list[str], asyncio.Queue]:
    paths: list[str] = []

    def recording(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return handler(request)

    monkeypatch.setattr(orch_mod, "AGENT_REGISTRY", {"product-discovery": "http://pd:8081"})
    monkeypatch.setattr(orch_mod, "_A2A_TRANSPORT", httpx.MockTransport(recording))
    monkeypatch.setattr(orch_mod, "a2a_call_span", _noop_span)
    monkeypatch.setattr(settings, "AGENT_SHARED_SECRET", "test-secret", raising=False)
    current_user_email.set("u@example.com")
    current_user_role.set("customer")
    current_session_id.set("sess-1")
    queue: asyncio.Queue = asyncio.Queue()
    current_stream_queue.set(queue)
    current_steps.set([])
    return paths, queue


@pytest.mark.asyncio
async def test_failure_after_a_step_frame_never_reposts(monkeypatch: pytest.MonkeyPatch) -> None:
    step = {"tool_name": "search_products", "tool_input": {}, "tool_output": {}, "status": "success", "duration_ms": 1}
    head = f"event: step\ndata: {json.dumps(step)}\n\n".encode()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/message:stream":
            return httpx.Response(200, stream=_BrokenStream(head))
        return httpx.Response(200, json={"response": "SHOULD NOT BE USED"})

    paths, _queue = _setup(monkeypatch, handler)
    result = await call_specialist_agent(agent_name="product-discovery", message="hi")

    assert paths == ["/message:stream"]
    assert "SHOULD NOT BE USED" not in result
    assert "interrupted" in result
    assert len(current_steps.get()) == 1


@pytest.mark.asyncio
async def test_failure_after_text_returns_partial_text_with_note(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/message:stream":
            return httpx.Response(200, stream=_BrokenStream(b"data: Here are\n\ndata:  some results\n\n"))
        return httpx.Response(200, json={"response": "SHOULD NOT BE USED"})

    paths, _queue = _setup(monkeypatch, handler)
    result = await call_specialist_agent(agent_name="product-discovery", message="hi")

    assert paths == ["/message:stream"]
    assert result.startswith("Here are some results")
    assert "interrupted" in result


@pytest.mark.asyncio
async def test_connect_error_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/message:stream":
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json={"response": "blocking answer"})

    paths, _queue = _setup(monkeypatch, handler)
    result = await call_specialist_agent(agent_name="product-discovery", message="hi")

    assert paths == ["/message:stream", "/message:send"]
    assert result == "blocking answer"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [httpx.ReadTimeout("slow"), httpx.ReadError("dropped"), httpx.RemoteProtocolError("bad")],
)
async def test_ambiguous_failure_before_first_frame_never_reposts(
    monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/message:stream":
            raise exc
        return httpx.Response(200, json={"response": "SHOULD NOT BE USED"})

    paths, _queue = _setup(monkeypatch, handler)
    result = await call_specialist_agent(agent_name="product-discovery", message="hi")

    assert paths == ["/message:stream"]
    assert "SHOULD NOT BE USED" not in result
    assert "could not be confirmed" in result


@pytest.mark.asyncio
async def test_dropped_connection_before_first_frame_never_reposts(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/message:stream":
            return httpx.Response(200, stream=_BrokenStream(b""))
        return httpx.Response(200, json={"response": "SHOULD NOT BE USED"})

    paths, _queue = _setup(monkeypatch, handler)
    result = await call_specialist_agent(agent_name="product-discovery", message="hi")

    assert paths == ["/message:stream"]
    assert "could not be confirmed" in result


@pytest.mark.asyncio
async def test_gateway_timeout_status_never_reposts(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/message:stream":
            return httpx.Response(504)
        return httpx.Response(200, json={"response": "SHOULD NOT BE USED"})

    paths, _queue = _setup(monkeypatch, handler)
    result = await call_specialist_agent(agent_name="product-discovery", message="hi")

    assert paths == ["/message:stream"]
    assert "could not be confirmed" in result


@pytest.mark.asyncio
async def test_503_before_stream_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/message:stream":
            return httpx.Response(503)
        return httpx.Response(200, json={"response": "blocking answer"})

    paths, _queue = _setup(monkeypatch, handler)
    result = await call_specialist_agent(agent_name="product-discovery", message="hi")

    assert paths == ["/message:stream", "/message:send"]
    assert result == "blocking answer"
