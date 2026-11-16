"""E-2c: every orchestration mode reports usage in ``run_completed.usage``.

``tool`` already did (``tests/test_specialist_usage.py``). These cover the rest —
handoff (triage agent's own usage + the specialists' reported over A2A),
group-chat (the panelists' calls), the two workflow modes (an explicit zero, since
they make no chat-model call) — and ``POST /compare`` surfacing it. Usage is
accounting only: nothing here may reach text a model reads or the chat bubble.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest
from agent_framework import Agent, ChatResponse, Content, Message

from orchestrator.modes.base import RunContext
from orchestrator.modes.group_chat_mode import GroupChatMode
from orchestrator.modes.handoff_mode import HandoffMode
from orchestrator.modes.workflow_mode import PrePurchaseMode, ReturnReplaceMode
from orchestrator.routes.orchestration import CompareRequest, compare_modes
from shared.run_usage import tokens_from_usage, zero_usage
from tests.test_handoff_orchestration import _StubTransport, stub_transport  # noqa: F401
from tests.test_orchestration_modes import _ScriptedClient, _text_response
from tests.test_orchestration_routes import PRE_PURCHASE_TOOLS, PRODUCT_UUID

USER = {"sub": "test@example.com", "role": "customer"}


def _with_usage(response: ChatResponse, tokens_in: int, tokens_out: int, cached: int = 0) -> ChatResponse:
    details = {
        "input_token_count": tokens_in,
        "output_token_count": tokens_out,
        "cache_read_input_token_count": cached,
    }
    response.usage_details = details
    # A streaming client reports usage as a `usage` content item on an update; the
    # scripted client replays message contents as updates, so mirror that here.
    response.messages[-1].contents.append(Content.from_usage(details))
    return response


async def _events(mode: Any, message: str) -> list[Any]:
    return [e async for e in mode.run(message, RunContext(history=[]))]


# ─────────────────────── workflow modes: explicit zero ───────────────────────


@pytest.mark.asyncio
async def test_pre_purchase_reports_an_explicit_zero_usage() -> None:
    final = (await _events(PrePurchaseMode(tools=PRE_PURCHASE_TOOLS), PRODUCT_UUID))[-1]
    assert final.payload["usage"] == zero_usage()
    assert final.payload["usage"]["total"] == {
        "input_token_count": 0,
        "output_token_count": 0,
        "cached_input_token_count": 0,
    }
    assert final.payload["usage"]["by_agent"] == {}


@pytest.mark.asyncio
async def test_return_replace_error_path_also_reports_zero_usage() -> None:
    # No signed-in user -> the early-exit run_completed; still a measured zero.
    final = (await _events(ReturnReplaceMode(tools={}), "return it"))[-1]
    assert final.kind == "run_completed"
    assert final.payload["usage"] == zero_usage()


# ─────────────────────── group chat ───────────────────────


@pytest.mark.asyncio
async def test_group_chat_sums_panelist_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    clients = iter(
        [
            _ScriptedClient(_with_usage(_text_response("Fair price."), 100, 10, cached=40)),
            _ScriptedClient(_with_usage(_text_response("Well built."), 200, 20)),
        ]
    )
    monkeypatch.setattr("shared.factory.get_chat_client", lambda: next(clients))
    events = await _events(GroupChatMode(), "worth it?")

    final = events[-1]
    usage = final.payload["usage"]
    assert usage["by_agent"]["value"]["input_token_count"] == 100
    assert usage["by_agent"]["value"]["cached_input_token_count"] == 40
    assert usage["by_agent"]["quality"]["output_token_count"] == 20
    assert tokens_from_usage(usage) == (300, 30)
    # Accounting never enters the transcript or the verdict a reader sees.
    rendered = json.dumps([final.payload["text"], final.payload["transcript"]])
    assert "input_token_count" not in rendered


# ─────────────────────── handoff ───────────────────────


@pytest.mark.asyncio
async def test_handoff_sums_triage_and_specialist_usage(
    monkeypatch: pytest.MonkeyPatch,
    stub_transport: _StubTransport,  # noqa: F811
) -> None:
    stub_transport.reply = "1554"
    original = stub_transport.handle_async_request

    async def _with_wire_usage(request: Any) -> Any:
        body = json.loads((await original(request)).content)
        body["usage"] = {
            "input_token_count": 500,
            "output_token_count": 50,
            "cached_input_token_count": 400,
            "model": "gpt-4.1",
        }
        return httpx.Response(200, content=json.dumps(body).encode(), request=request)

    monkeypatch.setattr(stub_transport, "handle_async_request", _with_wire_usage)

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
    _with_usage(handoff_call, 70, 7)
    fake_orchestrator = Agent(
        client=_ScriptedClient(handoff_call),
        instructions="test",
        name="orchestrator",
        require_per_service_call_history_persistence=True,
    )
    monkeypatch.setattr("orchestrator.handoff._load_registry", lambda: {"math": "http://math-specialist:9999/a2a"})
    monkeypatch.setattr("orchestrator.handoff.create_handoff_triage_agent", lambda: fake_orchestrator)

    final = (await _events(HandoffMode(), "What is 37 * 42?"))[-1]

    usage = final.payload["usage"]
    assert usage["by_agent"]["math"]["input_token_count"] == 500
    assert usage["by_agent"]["math"]["model"] == "gpt-4.1"
    assert usage["by_agent"]["orchestrator"]["input_token_count"] == 70
    assert tokens_from_usage(usage) == (570, 57)
    assert "input_token_count" not in final.payload["text"]


# ─────────────────────── /compare ───────────────────────


@pytest.mark.asyncio
async def test_compare_reports_tokens_and_cost_per_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    import orchestrator.modes as modes_module

    fake_agent = Agent(
        client=_ScriptedClient(_with_usage(_text_response("A great deal."), 1000, 100)),
        instructions="test",
        name="orchestrator",
    )
    monkeypatch.setattr("orchestrator.agent.create_orchestrator_agent", lambda: fake_agent)
    monkeypatch.setitem(modes_module.MODES, "workflow:pre-purchase", PrePurchaseMode(tools=PRE_PURCHASE_TOOLS))

    body = CompareRequest(message=PRODUCT_UUID, modes=["tool", "workflow:pre-purchase"])
    tool_result, workflow_result = (await compare_modes(body, user=USER)).results

    assert (tool_result.tokens_in, tool_result.tokens_out) == (1000, 100)
    assert tool_result.est_cost_usd is not None and tool_result.est_cost_usd > 0
    # A measured zero, not "not captured" (which would be None).
    assert (workflow_result.tokens_in, workflow_result.tokens_out, workflow_result.est_cost_usd) == (0, 0, 0.0)
    assert "input_token_count" not in tool_result.text
