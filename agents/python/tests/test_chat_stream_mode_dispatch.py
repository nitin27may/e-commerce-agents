"""``/api/chat/stream``'s ``chat_stream()`` branches on the resolved mode.

``tool`` mode keeps its original direct-agent SSE path (covered
elsewhere); this file proves the *other* branch — non-``tool`` modes
streamed through ``get_mode(...).run()`` — actually reaches a real
``HandoffBuilder`` workflow end to end (real A2A HTTP call against a
stubbed transport, same as ``test_handoff_orchestration.py``) and emits
the new structured SSE frames (``event: node``, ``event: handoff``,
``event: metadata``) alongside real streamed display text extracted from
workflow "delta" events, not a single end-of-run dump.

Calls ``chat_stream()`` directly (bypassing FastAPI's dependency
injection, same pattern as ``test_chat_route_mode_dispatch.py``) so a
duck-typed fake ``Request`` (no ASGI receive channel) is enough — no
TestClient, no live server.
"""

from __future__ import annotations

import json
import uuid

import pytest
from agent_framework import Agent, ChatResponse, Content, Message
from fastapi import HTTPException

from orchestrator.routes.chat import ChatRequest, chat_stream
from tests.test_handoff_orchestration import _StubTransport, stub_transport  # noqa: F401

ANON_USER = {"sub": "", "role": "anonymous", "user_id": "", "anonymous": True}


class _FakeRequest:
    """Duck-types Starlette's ``Request.is_disconnected()`` — never disconnects."""

    async def is_disconnected(self) -> bool:
        return False


async def _drain(chat_stream_response) -> str:
    chunks = [chunk async for chunk in chat_stream_response.body_iterator]
    return "".join(chunks)


def _parse_sse(raw: str) -> list[tuple[str, str]]:
    """Return ``(event_name, data)`` pairs, defaulting event_name to "" for plain ``data:`` frames."""
    frames = []
    for block in raw.split("\n\n"):
        if not block.strip():
            continue
        event_name = ""
        data_lines = []
        for line in block.split("\n"):
            if line.startswith("event: "):
                event_name = line[len("event: ") :]
            elif line.startswith("data: "):
                data_lines.append(line[len("data: ") :])
        if data_lines:
            frames.append((event_name, "\n".join(data_lines)))
    return frames


@pytest.mark.asyncio
async def test_chat_stream_handoff_mode_emits_structured_frames_and_real_text(
    monkeypatch: pytest.MonkeyPatch,
    stub_transport: _StubTransport,  # noqa: F811 — pytest fixture param, not a redefinition
) -> None:
    monkeypatch.setattr("shared.db._pool", object(), raising=False)
    stub_transport.reply = "1554"

    handoff_call = ChatResponse(
        messages=[
            Message(
                role="assistant",
                contents=[Content.from_function_call(call_id="c1", name="handoff_to_math", arguments={})],
            )
        ],
        response_id=str(uuid.uuid4()),
        finish_reason="tool_calls",
    )

    from tests.test_orchestration_modes import _ScriptedClient

    orchestrator_client = _ScriptedClient(handoff_call)
    fake_orchestrator = Agent(
        client=orchestrator_client,
        instructions="test",
        name="orchestrator",
        require_per_service_call_history_persistence=True,
    )
    monkeypatch.setattr(
        "orchestrator.handoff._load_registry",
        lambda: {"math": "http://math-specialist:9999/a2a"},
    )
    monkeypatch.setattr("orchestrator.handoff.create_orchestrator_agent", lambda: fake_orchestrator)

    response = await chat_stream(
        ChatRequest(message="What is 37 * 42?", mode="handoff"),
        _FakeRequest(),
        user=ANON_USER,
    )
    raw = await _drain(response)
    frames = _parse_sse(raw)

    handoff_frames = [d for name, d in frames if name == "handoff"]
    assert handoff_frames, f"expected an event: handoff frame, got: {frames}"
    assert json.loads(handoff_frames[0])["data"]["target"] == "math"

    node_frames = [d for name, d in frames if name == "node"]
    assert any(json.loads(d)["node_id"] == "math" for d in node_frames)

    metadata_frames = [d for name, d in frames if name == "metadata"]
    assert metadata_frames, f"expected an event: metadata frame, got: {frames}"
    metadata = json.loads(metadata_frames[0])
    assert "math" in metadata["agents_involved"]

    # Real streamed display text — extracted from the workflow's "delta"
    # events, not a synthesized end-of-run dump.
    text_chunks = [d for name, d in frames if name == ""]
    assert any("1554" in chunk for chunk in text_chunks)


@pytest.mark.asyncio
async def test_chat_stream_falls_back_to_end_of_run_dump_for_non_streaming_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """workflow:pre-purchase never emits a delta with readable .contents (its
    one ctx.yield_output() carries the raw ResearchState dataclass, not
    AgentResponseUpdate content — verified empirically, see chat.py's module
    docstring). Without the run_completed fallback this regresses to an
    empty chat bubble; this proves the fallback actually fires.
    """
    import orchestrator.modes as modes_module
    from orchestrator.modes.workflow_mode import PrePurchaseMode

    async def _sentiment_ok(product_id: str) -> dict:
        return {"sentiment": "positive", "total_reviews": 42}

    async def _stock_ok(product_id: str) -> dict:
        return {"in_stock": True, "total_quantity": 17}

    async def _price_good(product_id: str, days: int) -> dict:
        return {"is_good_deal": True, "average_price": 120.5, "trend": "flat"}

    async def _shipping_fast(product_id: str, destination_region: str) -> dict:
        return {"options": [{"price": 4.99, "days": 2}]}

    stub_tools = {
        "analyze_sentiment": _sentiment_ok,
        "check_stock": _stock_ok,
        "get_price_history": _price_good,
        "estimate_shipping": _shipping_fast,
    }
    monkeypatch.setitem(modes_module.MODES, "workflow:pre-purchase", PrePurchaseMode(tools=stub_tools))
    monkeypatch.setattr("shared.db._pool", object(), raising=False)

    response = await chat_stream(
        ChatRequest(message="11111111-1111-1111-1111-111111111111", mode="workflow:pre-purchase"),
        _FakeRequest(),
        user=ANON_USER,
    )
    raw = await _drain(response)
    frames = _parse_sse(raw)

    text_chunks = [d for name, d in frames if name == ""]
    assert any("Reviews: positive" in chunk for chunk in text_chunks), (
        f"expected the final recommendation to reach the display as text, got: {frames}"
    )

    node_frames = [d for name, d in frames if name == "node"]
    assert node_frames, "expected node frames from the fan-out/fan-in graph"


@pytest.mark.asyncio
async def test_chat_stream_rejects_unknown_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shared.db._pool", object(), raising=False)

    with pytest.raises(HTTPException) as exc_info:
        await chat_stream(
            ChatRequest(message="hello", mode="not-a-real-mode"),
            _FakeRequest(),
            user=ANON_USER,
        )

    assert exc_info.value.status_code == 400
