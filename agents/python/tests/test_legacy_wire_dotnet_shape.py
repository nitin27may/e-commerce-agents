"""The Python orchestrator reads a .NET specialist's legacy-wire response, steps and usage included.

The shapes below are what ``AgentHost`` (``agents/dotnet/.../A2A/AgentHost.cs``) writes, not what the Python specialist
writes: a blocking ``/message:send`` body is serialised by the Minimal API's camelCase defaults (``toolName``,
``durationMs``, a null ``agent``), the ``event: steps`` frame of ``/message:stream`` by ``JsonSerializer.Serialize``
with no options (PascalCase), and usage by its explicit snake_case names. The mirror defect on the .NET side was
``TolerantStepConverter``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

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
from shared.legacy_wire import normalize_step, normalize_steps
from shared.run_usage import begin_run_usage

USAGE = {"input_token_count": 120, "output_token_count": 30, "cached_input_token_count": 100, "model": "gpt-4.1"}

# Minimal API default (camelCase) as written into a blocking /message:send body.
CAMEL_STEP = {
    "toolName": "search_products",
    "toolInput": {"query": "tent"},
    "toolOutput": {"count": 3},
    "status": "success",
    "durationMs": 12,
    "agent": None,
}
# JsonSerializer.Serialize(steps) with no options, as written into the `event: steps` frame.
PASCAL_STEP = {
    "ToolName": "get_stock",
    "ToolInput": {"sku": "A1"},
    "ToolOutput": {"qty": 4},
    "Status": "success",
    "DurationMs": 7,
    "Agent": None,
}


@contextlib.contextmanager
def _noop_span(*args, **kwargs):
    yield


def _setup(monkeypatch: pytest.MonkeyPatch, handler) -> asyncio.Queue:
    monkeypatch.setattr(orch_mod, "AGENT_REGISTRY", {"product-discovery": "http://pd:8081"})
    monkeypatch.setattr(orch_mod, "_A2A_TRANSPORT", httpx.MockTransport(handler))
    monkeypatch.setattr(orch_mod, "a2a_call_span", _noop_span)
    monkeypatch.setattr(settings, "AGENT_SHARED_SECRET", "test-secret", raising=False)
    monkeypatch.setattr(settings, "A2A_TRANSPORT", "legacy", raising=False)
    current_user_email.set("u@example.com")
    current_user_role.set("customer")
    current_session_id.set("sess-1")
    queue: asyncio.Queue = asyncio.Queue()
    current_stream_queue.set(queue)
    current_steps.set([])
    return queue


def test_normalize_step_accepts_every_spelling() -> None:
    expected_keys = {"tool_name", "tool_input", "tool_output", "status", "duration_ms", "agent"}
    for raw in (CAMEL_STEP, PASCAL_STEP, {"tool_name": "t", "tool_input": {}, "tool_output": {}, "status": "s"}):
        step = normalize_step(raw, "inventory")
        assert step is not None
        assert expected_keys >= set(step)
        assert step["agent"] == "inventory"
        assert step["tool_name"] == raw.get("toolName") or step["tool_name"] in ("get_stock", "t")
    assert normalize_step(CAMEL_STEP, "x")["duration_ms"] == 12
    # A specialist's own agent tag wins; unknown keys survive; non-objects are skipped.
    assert normalize_step({"toolName": "t", "agent": "pricing", "extra": 1}, "x") == {
        "tool_name": "t",
        "agent": "pricing",
        "extra": 1,
    }
    assert normalize_steps(["junk", CAMEL_STEP, None], "x") == [normalize_step(CAMEL_STEP, "x")]


@pytest.mark.asyncio
async def test_blocking_response_shaped_like_dotnet_yields_steps_and_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"response": "Found 3 tents.", "steps": [CAMEL_STEP], "usage": USAGE}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/message:stream":
            raise httpx.ConnectError("refused")  # provably never ran: take the blocking path
        return httpx.Response(200, json=body)

    _setup(monkeypatch, handler)
    run_usage = begin_run_usage()
    result = await call_specialist_agent(agent_name="product-discovery", message="tents?")

    assert result == "Found 3 tents."
    assert current_steps.get() == [
        {
            "tool_name": "search_products",
            "tool_input": {"query": "tent"},
            "tool_output": {"count": 3},
            "status": "success",
            "duration_ms": 12,
            "agent": "product-discovery",
        }
    ]
    assert run_usage.by_agent["product-discovery"]["input_token_count"] == 120
    assert run_usage.by_agent["product-discovery"]["cached_input_token_count"] == 100


@pytest.mark.asyncio
async def test_stream_shaped_like_dotnet_yields_steps_usage_and_only_the_answer_as_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sse = (
        f"event: steps\ndata: {json.dumps([PASCAL_STEP])}\n\n"
        "data: In stock: 4\n\n"
        f"event: steps\ndata: {json.dumps([{**PASCAL_STEP, 'ToolName': 'get_price'}])}\n\n"
        f"event: usage\ndata: {json.dumps(USAGE)}\n\n"
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})

    queue = _setup(monkeypatch, handler)
    run_usage = begin_run_usage()
    result = await call_specialist_agent(agent_name="product-discovery", message="stock?")

    assert result == "In stock: 4"  # the steps JSON must never be read as answer text
    assert [s["tool_name"] for s in current_steps.get()] == ["get_stock", "get_price"]
    assert {s["agent"] for s in current_steps.get()} == {"product-discovery"}
    assert all(s["_live"] for s in current_steps.get())  # forwarded live, so the drain must not resend them
    frames = []
    while not queue.empty():
        frames.append(queue.get_nowait())
    assert [f[1] for f in frames if f[0] == "frame"] == ["step", "step"]
    assert [f[2] for f in frames if f[0] == "delta"] == ["In stock: 4"]
    assert run_usage.by_agent["product-discovery"]["output_token_count"] == 30
