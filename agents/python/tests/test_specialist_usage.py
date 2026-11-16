"""#97 / E-2a — a specialist's token usage reaches the orchestrator's accounting.

Covers, in order:

* the wire shape (one literal, asserted identically by the .NET tests);
* the accumulator (``shared/run_usage.py``);
* both specialist endpoints emitting ``usage`` (``/message:send`` field,
  ``/message:stream`` terminal ``event: usage`` frame);
* ``call_specialist_agent`` consuming it on both paths **without** the usage
  entering the text it returns to the orchestrator's model;
* ``usage_logs.tokens_in/out`` including specialist tokens through
  ``/api/chat`` and ``/api/chat/stream`` (real Postgres via ``clean_db``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import orchestrator.agent as orch_mod
from orchestrator.agent import call_specialist_agent
from orchestrator.routes import optional_auth, router
from shared.agent_host import create_agent_app
from shared.config import settings
from shared.context import (
    current_session_id,
    current_steps,
    current_stream_queue,
    current_user_email,
    current_user_role,
)
from shared.run_usage import RunUsage, begin_run_usage, current_run_usage, to_wire_usage, tokens_from_usage

# The wire shape, as literal JSON. The .NET tests assert the same string
# (agents/dotnet/tests/.../SpecialistUsageTests.cs) — change both or neither.
WIRE_USAGE_JSON = '{"input_token_count":120,"output_token_count":30,"cached_input_token_count":100,"model":"gpt-4.1"}'
WIRE_USAGE = json.loads(WIRE_USAGE_JSON)

# What MAF's UsageDetails looks like for the same call (cached count is named
# cache_read_input_token_count there).
MAF_USAGE = {
    "input_token_count": 120,
    "output_token_count": 30,
    "total_token_count": 150,
    "cache_read_input_token_count": 100,
}

SPECIALIST_TEXT = "The Sony WH-1000XM5 is $79.99."
ORCHESTRATOR_USAGE = {"input_token_count": 1000, "output_token_count": 50, "cache_read_input_token_count": 0}


@pytest.fixture(autouse=True)
def _model_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai", raising=False)
    monkeypatch.setattr(settings, "LLM_MODEL", "gpt-4.1", raising=False)


@contextlib.contextmanager
def _noop_span(*args, **kwargs):
    yield


# ─────────────────────── Wire shape + accumulator ───────────────────────


def test_wire_shape_is_the_shared_literal() -> None:
    wire = to_wire_usage(MAF_USAGE)
    assert wire == WIRE_USAGE
    # Key order is part of the contract the literal pins down.
    assert json.dumps(wire, separators=(",", ":")) == WIRE_USAGE_JSON


def test_wire_conversion_is_idempotent_and_drops_empty_usage() -> None:
    assert to_wire_usage(WIRE_USAGE) == WIRE_USAGE
    assert to_wire_usage(None) is None
    assert to_wire_usage({}) is None
    assert to_wire_usage({"input_token_count": 0, "output_token_count": 0}) is None


def test_accumulator_sums_per_agent_and_in_total() -> None:
    acc = RunUsage()
    acc.add("orchestrator", ORCHESTRATOR_USAGE)
    acc.add("product-discovery", MAF_USAGE)
    acc.add("product-discovery", MAF_USAGE)  # two calls to the same specialist in one turn
    acc.add("order-management", None)  # no usage reported: not an entry

    snap = acc.snapshot()
    assert snap["by_agent"]["product-discovery"]["input_token_count"] == 240
    assert snap["by_agent"]["product-discovery"]["cached_input_token_count"] == 200
    assert set(snap["by_agent"]) == {"orchestrator", "product-discovery"}
    assert snap["total"] == {
        "input_token_count": 1240,
        "output_token_count": 110,
        "cached_input_token_count": 200,
    }
    assert tokens_from_usage(snap) == (1240, 110)
    # A bare UsageDetails-shaped mapping is still understood.
    assert tokens_from_usage(MAF_USAGE) == (120, 30)
    assert tokens_from_usage(None) == (0, 0)


def test_snapshot_with_does_not_mutate() -> None:
    acc = RunUsage()
    acc.add("product-discovery", MAF_USAGE)
    first = acc.snapshot_with("orchestrator", ORCHESTRATOR_USAGE)
    second = acc.snapshot_with("orchestrator", ORCHESTRATOR_USAGE)
    assert first == second
    assert first["total"]["input_token_count"] == 1120
    assert set(acc.by_agent) == {"product-discovery"}


# ─────────────────────── The specialist host ───────────────────────


class _Update:
    def __init__(self, text: str) -> None:
        self.text = text


class _Response:
    def __init__(self, text: str, usage) -> None:
        self.text = text
        self.additional_properties: dict = {}
        self.usage_details = usage


class _Stream:
    def __init__(self, text: str, usage) -> None:
        self._text, self._usage = text, usage

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        half = len(self._text) // 2
        yield _Update(self._text[:half])
        yield _Update(self._text[half:])

    async def get_final_response(self) -> _Response:
        return _Response(self._text, self._usage)


class _SpecialistAgent:
    """Stand-in for a MAF Agent: fixed answer, fixed MAF ``usage_details``."""

    def __init__(self, text: str = SPECIALIST_TEXT, usage=MAF_USAGE) -> None:
        self._text, self._usage = text, usage

    def run(self, messages=None, *, stream: bool = False, options=None, **_kwargs):
        if stream:
            return _Stream(self._text, self._usage)

        async def _return() -> _Response:
            return _Response(self._text, self._usage)

        return _return()


def _specialist_app(usage=MAF_USAGE) -> FastAPI:
    return create_agent_app(agent=_SpecialistAgent(usage=usage), agent_name="product-discovery", port=8081)


@pytest.mark.asyncio
async def test_message_send_returns_usage_beside_the_response_not_inside_it() -> None:
    async with AsyncClient(transport=ASGITransport(app=_specialist_app()), base_url="http://t") as http:
        body = (await http.post("/message:send", json={"message": "hi"})).json()

    assert body["usage"] == WIRE_USAGE
    assert body["response"] == SPECIALIST_TEXT
    assert "token" not in body["response"]


@pytest.mark.asyncio
async def test_message_send_omits_usage_when_the_model_reported_none() -> None:
    async with AsyncClient(transport=ASGITransport(app=_specialist_app(usage=None)), base_url="http://t") as http:
        body = (await http.post("/message:send", json={"message": "hi"})).json()
    assert "usage" not in body


@pytest.mark.asyncio
async def test_message_stream_ends_with_a_usage_frame_before_done() -> None:
    from shared.sse import aiter_sse_events

    async with AsyncClient(transport=ASGITransport(app=_specialist_app()), base_url="http://t") as http:
        resp = await http.post("/message:stream", json={"message": "hi"})

        async def _chunks():
            yield resp.text

        events = [e async for e in aiter_sse_events(_chunks())]

    assert events[-2] == ("usage", WIRE_USAGE_JSON)
    assert events[-1] == ("data", "[DONE]")
    text_frames = [data for name, data in events if name == "data" and data != "[DONE]"]
    assert "".join(text_frames) == SPECIALIST_TEXT


# ─────────────────────── call_specialist_agent consumes it ───────────────────────


def _bind_request(monkeypatch: pytest.MonkeyPatch, transport: httpx.AsyncBaseTransport, *, streaming: bool):
    monkeypatch.setattr(orch_mod, "AGENT_REGISTRY", {"product-discovery": "http://pd:8081"})
    monkeypatch.setattr(orch_mod, "_A2A_TRANSPORT", transport)
    monkeypatch.setattr(orch_mod, "a2a_call_span", _noop_span)
    monkeypatch.setattr(settings, "AGENT_SHARED_SECRET", "test-secret", raising=False)
    current_user_email.set("u@example.com")
    current_user_role.set("customer")
    current_session_id.set("")
    current_steps.set([])
    queue: asyncio.Queue | None = asyncio.Queue() if streaming else None
    current_stream_queue.set(queue)
    return begin_run_usage(), queue


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [True, False], ids=["stream", "blocking"])
async def test_tool_result_and_browser_queue_never_carry_usage(
    monkeypatch: pytest.MonkeyPatch, streaming: bool
) -> None:
    acc, queue = _bind_request(monkeypatch, ASGITransport(app=_specialist_app()), streaming=streaming)

    result = await call_specialist_agent(agent_name="product-discovery", message="price of the XM5?")

    # The string the orchestrator's model sees is exactly the specialist's
    # answer. Anything else changes every replay-fixture key.
    assert result == SPECIALIST_TEXT
    assert "token" not in result and "usage" not in result and "gpt-4.1" not in result

    if queue is not None:
        queued = []
        while not queue.empty():
            queued.append(queue.get_nowait())
        assert queued, "the stream path should have forwarded the specialist's text"
        assert all(item[0] in ("delta", "frame") for item in queued)
        assert "token" not in json.dumps(queued, default=str)

    assert acc.by_agent["product-discovery"] == {
        "input_token_count": 120,
        "output_token_count": 30,
        "cached_input_token_count": 100,
        "model": "gpt-4.1",
    }


@pytest.mark.asyncio
async def test_a_specialist_that_sends_no_usage_is_still_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    acc, _ = _bind_request(monkeypatch, ASGITransport(app=_specialist_app(usage=None)), streaming=True)
    assert await call_specialist_agent(agent_name="product-discovery", message="hi") == SPECIALIST_TEXT
    assert acc.by_agent == {}


# ─────────────────────── usage_logs through the real routes ───────────────────────


class _Connected:
    async def is_disconnected(self) -> bool:
        return False


async def _seed_user(pool, email: str) -> uuid.UUID:
    user_id = uuid.uuid4()
    await pool.execute(
        """INSERT INTO users (id, email, password_hash, name, role)
           VALUES ($1, $2, 'hash', 'Test User', 'customer')""",
        user_id,
        email,
    )
    return user_id


def _chat_app(email: str, user_id: uuid.UUID) -> FastAPI:
    async def _fake_auth():
        current_user_email.set(email)
        current_user_role.set("customer")
        return {"sub": email, "role": "customer", "user_id": str(user_id)}

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[optional_auth] = _fake_auth
    return app


async def _wait_for_persist_tasks() -> None:
    from orchestrator.routes import chat as chat_module

    loop = asyncio.get_event_loop()
    deadline = loop.time() + 2.0
    while chat_module._PENDING_PERSIST_TASKS and loop.time() < deadline:
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/api/chat", "/api/chat/stream"])
async def test_usage_logs_include_specialist_tokens(clean_db, monkeypatch: pytest.MonkeyPatch, endpoint: str) -> None:
    """The orchestrator's own 1000/50 tokens plus the specialist's 120/30 — through both routes."""
    import orchestrator.agent as orch_agent_mod

    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    monkeypatch.setattr(orch_mod, "AGENT_REGISTRY", {"product-discovery": "http://pd:8081"})
    monkeypatch.setattr(orch_mod, "_A2A_TRANSPORT", ASGITransport(app=_specialist_app()))
    monkeypatch.setattr(orch_mod, "a2a_call_span", _noop_span)
    monkeypatch.setattr(settings, "AGENT_SHARED_SECRET", "test-secret", raising=False)
    orchestrator_agent = object()
    monkeypatch.setattr(orch_agent_mod, "create_orchestrator_agent", lambda: orchestrator_agent)

    # The specialist app runs in this process and reaches the same module
    # functions, so the fakes below only take over for the orchestrator's agent.
    import shared.agent_host as host

    real_native, real_stream = host._run_agent_native, host._run_agent_native_stream

    email = f"usage-{endpoint.rsplit('/', 1)[-1]}@example.com"
    user_id = await _seed_user(clean_db, email)

    tool_results: list[str] = []

    async def _fake_run_agent_native(agent, message, history=None, metadata_box=None):
        if agent is not orchestrator_agent:
            return await real_native(agent, message, history=history, metadata_box=metadata_box)
        # The orchestrator "model" calls its one tool, then answers.
        tool_results.append(await call_specialist_agent(agent_name="product-discovery", message=message))
        if metadata_box is not None:
            metadata_box["_maf_usage"] = dict(ORCHESTRATOR_USAGE)
        return "Here you go: " + tool_results[-1]

    async def _fake_run_agent_native_stream(agent, message, history=None, metadata_box=None):
        if agent is not orchestrator_agent:
            async for chunk in real_stream(agent, message, history=history, metadata_box=metadata_box):
                yield chunk
            return
        tool_results.append(await call_specialist_agent(agent_name="product-discovery", message=message))
        yield "Here you go: " + tool_results[-1]
        if metadata_box is not None:
            metadata_box["_maf_usage"] = dict(ORCHESTRATOR_USAGE)

    monkeypatch.setattr("shared.agent_host._run_agent_native", _fake_run_agent_native)
    monkeypatch.setattr("shared.agent_host._run_agent_native_stream", _fake_run_agent_native_stream)

    async with AsyncClient(transport=ASGITransport(app=_chat_app(email, user_id)), base_url="http://test") as http:
        resp = await http.post(endpoint, json={"message": "price of the XM5?", "mode": "tool"})
    assert resp.status_code == 200
    await _wait_for_persist_tasks()

    row = await clean_db.fetchrow(
        "SELECT tokens_in, tokens_out FROM usage_logs "
        "WHERE agent_name = 'orchestrator' ORDER BY created_at DESC LIMIT 1"
    )
    assert row is not None
    assert (row["tokens_in"], row["tokens_out"]) == (1000 + 120, 50 + 30)

    # Hard constraint: the tool result the model saw, and everything the browser
    # received, is free of the usage object.
    assert tool_results == [SPECIALIST_TEXT]
    assert "input_token_count" not in resp.text
    assert "event: usage" not in resp.text
    if endpoint.endswith("/stream"):
        assert "gpt-4.1" not in resp.text


@pytest.mark.asyncio
async def test_run_completed_usage_is_total_and_by_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.modes import RunContext, get_mode

    monkeypatch.setattr(orch_mod, "AGENT_REGISTRY", {"product-discovery": "http://pd:8081"})
    monkeypatch.setattr(orch_mod, "_A2A_TRANSPORT", ASGITransport(app=_specialist_app()))
    monkeypatch.setattr(orch_mod, "a2a_call_span", _noop_span)
    monkeypatch.setattr(settings, "AGENT_SHARED_SECRET", "test-secret", raising=False)
    orchestrator_agent = object()
    monkeypatch.setattr(orch_mod, "create_orchestrator_agent", lambda: orchestrator_agent)
    import shared.agent_host as host

    real_native = host._run_agent_native
    current_user_email.set("u@example.com")
    current_user_role.set("customer")
    current_stream_queue.set(None)

    async def _fake_run_agent_native(agent, message, history=None, metadata_box=None):
        if agent is not orchestrator_agent:
            return await real_native(agent, message, history=history, metadata_box=metadata_box)
        await call_specialist_agent(agent_name="product-discovery", message=message)
        metadata_box["_maf_usage"] = dict(ORCHESTRATOR_USAGE)
        return "answer"

    monkeypatch.setattr("shared.agent_host._run_agent_native", _fake_run_agent_native)

    events = [e async for e in get_mode("tool").run("hi", RunContext(history=[]))]
    usage = next(e for e in events if e.kind == "run_completed").payload["usage"]

    assert usage["total"] == {"input_token_count": 1120, "output_token_count": 80, "cached_input_token_count": 100}
    assert set(usage["by_agent"]) == {"orchestrator", "product-discovery"}
    assert current_run_usage.get() is not None
