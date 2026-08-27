"""Tests for orchestrator/routes/orchestration.py.

``GET /modes`` and ``GET /modes/{name}/graph`` previously served a
hardcoded, long-stale list containing only "tool" even though five modes
were registered and reachable via /api/chat — a doc/code gap exactly like
the ones this project's audit exists to catch. These tests prove they now
read the real registry. Resume is real-DB tested separately in
test_orchestration_resume.py (needs a genuine paused run to resume).

``POST /compare`` tests run real modes (tool mode via a scripted chat
client, workflow:pre-purchase via stub tools — no DB/LLM needed) through
the actual route function, same real-machinery standard as the rest of
Phase 1.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from agent_framework import (
    Agent,
    BaseChatClient,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    FunctionInvocationLayer,
    Message,
)
from fastapi import HTTPException

from orchestrator.routes.orchestration import CompareRequest, compare_modes, get_mode_graph, list_modes

ANON_USER = {"sub": "test@example.com", "role": "customer"}


@pytest.mark.asyncio
async def test_list_modes_reports_all_five_registered_modes() -> None:
    modes = await list_modes()
    names = {m["name"] for m in modes}
    assert names == {"tool", "handoff", "workflow:pre-purchase", "workflow:return-replace", "group-chat"}
    tool_entry = next(m for m in modes if m["name"] == "tool")
    assert tool_entry["default"] is True


@pytest.mark.asyncio
async def test_get_mode_graph_returns_mermaid_for_a_graph_mode() -> None:
    result = await get_mode_graph("workflow:pre-purchase")
    assert result["name"] == "workflow:pre-purchase"
    assert result["mermaid"] is not None
    assert "fan_out" in result["mermaid"]


@pytest.mark.asyncio
async def test_get_mode_graph_returns_none_for_a_non_graph_mode() -> None:
    result = await get_mode_graph("tool")
    assert result["mermaid"] is None


@pytest.mark.asyncio
async def test_get_mode_graph_404s_for_unknown_mode() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await get_mode_graph("not-a-real-mode")
    assert exc_info.value.status_code == 404


# ─────────────────────── compare_modes ───────────────────────


def _text_response(text: str) -> ChatResponse:
    return ChatResponse(
        messages=[Message(role="assistant", contents=[Content.from_text(text=text)])],
        response_id=str(uuid.uuid4()),
        finish_reason="stop",
    )


class _ScriptedClient(FunctionInvocationLayer, BaseChatClient):
    def __init__(self, *responses: ChatResponse) -> None:
        super().__init__()
        self._responses = list(responses)

    async def _next(self) -> ChatResponse:
        return self._responses.pop(0)

    def _inner_get_response(self, *, messages, stream: bool, options=None, **_):
        if stream:

            async def _gen():
                response = await self._next()
                for msg in response.messages:
                    yield ChatResponseUpdate(role=msg.role, contents=msg.contents, author_name=msg.author_name)

            return self._build_response_stream(_gen())
        return self._next()


PRODUCT_UUID = "11111111-1111-1111-1111-111111111111"


async def _sentiment_ok(product_id: str) -> dict[str, Any]:
    return {"overall_sentiment": "positive", "average_rating": 4.4}


async def _stock_ok(product_id: str) -> dict[str, Any]:
    return {"in_stock": True, "total_quantity": 17}


async def _price_good(product_id: str, days: int) -> dict[str, Any]:
    return {"is_good_deal": True, "average_price": 120.5, "trend": "flat"}


async def _shipping_fast(product_id: str, destination_region: str) -> dict[str, Any]:
    return {"shipping_options": [{"price": 4.99, "delivery_window": "2 business days"}]}


PRE_PURCHASE_TOOLS = {
    "analyze_sentiment": _sentiment_ok,
    "check_stock": _stock_ok,
    "get_price_history": _price_good,
    "estimate_shipping": _shipping_fast,
}


@pytest.mark.asyncio
async def test_compare_modes_runs_tool_and_workflow_side_by_side(monkeypatch: pytest.MonkeyPatch) -> None:
    import orchestrator.modes as modes_module
    from orchestrator.modes.workflow_mode import PrePurchaseMode

    fake_agent = Agent(
        client=_ScriptedClient(_text_response("It's a great deal.")), instructions="test", name="orchestrator"
    )
    monkeypatch.setattr("orchestrator.agent.create_orchestrator_agent", lambda: fake_agent)
    monkeypatch.setitem(modes_module.MODES, "workflow:pre-purchase", PrePurchaseMode(tools=PRE_PURCHASE_TOOLS))

    body = CompareRequest(message=PRODUCT_UUID, modes=["tool", "workflow:pre-purchase"])
    response = await compare_modes(body, user=ANON_USER)

    assert response.message == PRODUCT_UUID
    assert [r.mode for r in response.results] == ["tool", "workflow:pre-purchase"]

    tool_result = response.results[0]
    assert tool_result.label == "Tool Router"
    assert tool_result.text == "It's a great deal."
    assert tool_result.error is None
    assert tool_result.graph_mermaid is None  # tool routes per-turn, no fixed graph
    assert tool_result.latency_ms >= 0

    workflow_result = response.results[1]
    assert "Reviews: positive" in workflow_result.text
    assert workflow_result.error is None
    assert workflow_result.graph_mermaid is not None
    # No tool_call events for a workflow mode — falls back to node_enter count.
    assert workflow_result.step_count > 0
    assert set(workflow_result.agents_involved) >= {"reviews", "stock"}


@pytest.mark.asyncio
async def test_compare_modes_reports_unknown_mode_without_failing_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_agent = Agent(client=_ScriptedClient(_text_response("hi")), instructions="test", name="orchestrator")
    monkeypatch.setattr("orchestrator.agent.create_orchestrator_agent", lambda: fake_agent)

    body = CompareRequest(message="hello", modes=["tool", "not-a-real-mode"])
    response = await compare_modes(body, user=ANON_USER)

    assert response.results[0].error is None
    assert response.results[0].text == "hi"
    assert response.results[1].mode == "not-a-real-mode"
    assert response.results[1].error is not None


@pytest.mark.asyncio
async def test_compare_modes_reports_a_mode_error_without_failing_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import orchestrator.modes as modes_module
    from orchestrator.modes.workflow_mode import PrePurchaseMode

    fake_agent = Agent(client=_ScriptedClient(_text_response("hi")), instructions="test", name="orchestrator")
    monkeypatch.setattr("orchestrator.agent.create_orchestrator_agent", lambda: fake_agent)
    # No UUID in the message and no real search_products/DB available in
    # this unit test — PrePurchaseMode's ID resolution will raise.
    monkeypatch.setitem(modes_module.MODES, "workflow:pre-purchase", PrePurchaseMode(tools=PRE_PURCHASE_TOOLS))

    body = CompareRequest(message="not a uuid and no db", modes=["tool", "workflow:pre-purchase"])
    response = await compare_modes(body, user=ANON_USER)

    assert response.results[0].error is None
    assert response.results[1].mode == "workflow:pre-purchase"
    assert response.results[1].error is not None


@pytest.mark.asyncio
async def test_compare_modes_rejects_empty_mode_list() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await compare_modes(CompareRequest(message="hi", modes=[]), user=ANON_USER)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_compare_modes_rejects_too_many_modes() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await compare_modes(CompareRequest(message="hi", modes=["tool"] * 6), user=ANON_USER)
    assert exc_info.value.status_code == 400
