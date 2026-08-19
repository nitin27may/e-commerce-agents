"""``/api/chat``'s ``chat()`` handler dispatches through the mode registry.

Anonymous (storefront) path only — it skips every DB call except the
initial ``get_pool()`` existence check, so this exercises the real
``chat()`` function (not a mock of it) without needing a live Postgres.
Verifies both the default mode and an explicit ``mode`` in the request
body reach the right :class:`OrchestrationMode`, and that an unknown mode
name surfaces as a 400 rather than an unhandled ``UnknownModeError``.
"""

from __future__ import annotations

import uuid

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

from orchestrator.routes.chat import ChatRequest, chat


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


ANON_USER = {"sub": "", "role": "anonymous", "user_id": "", "anonymous": True}


@pytest.mark.asyncio
async def test_chat_route_defaults_to_tool_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shared.db._pool", object(), raising=False)
    fake_agent = Agent(client=_ScriptedClient(_text_response("hi there")), instructions="test", name="orchestrator")
    monkeypatch.setattr("orchestrator.agent.create_orchestrator_agent", lambda: fake_agent)

    response = await chat(ChatRequest(message="hello"), user=ANON_USER)

    assert response.response == "hi there"
    assert response.agents_involved == ["orchestrator"]


@pytest.mark.asyncio
async def test_chat_route_honors_explicit_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shared.db._pool", object(), raising=False)
    fake_agent = Agent(client=_ScriptedClient(_text_response("hi there")), instructions="test", name="orchestrator")
    monkeypatch.setattr("orchestrator.agent.create_orchestrator_agent", lambda: fake_agent)

    response = await chat(ChatRequest(message="hello", mode="tool"), user=ANON_USER)

    assert response.response == "hi there"


@pytest.mark.asyncio
async def test_chat_route_rejects_unknown_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shared.db._pool", object(), raising=False)

    with pytest.raises(HTTPException) as exc_info:
        await chat(ChatRequest(message="hello", mode="not-a-real-mode"), user=ANON_USER)

    assert exc_info.value.status_code == 400
