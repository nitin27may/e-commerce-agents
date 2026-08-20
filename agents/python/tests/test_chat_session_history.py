"""Phase 1.5 — session-backed history read in chat.py.

Real Postgres (clean_db, via testcontainers — never mock the DB, per
this repo's test policy). Proves two things about the read side that
replaced the hand-rolled SELECT:

1. History reaches the LLM correctly (prior turns, oldest first).
2. The current turn is NOT duplicated — reading history before inserting
   the new user message (the fix; the old order inserted first, so the
   just-added row came back in `history` AND got appended a second time
   by shared/agent_host.py::_history_as_maf_messages).

Write-side (agent_name/agents_involved/metadata persistence) is
untouched by 1.5 and already covered elsewhere; not re-tested here.
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
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from orchestrator.routes import optional_auth, router
from shared.context import current_user_email, current_user_role


def _text_response(text: str) -> ChatResponse:
    return ChatResponse(
        messages=[Message(role="assistant", contents=[Content.from_text(text=text)])],
        response_id=str(uuid.uuid4()),
        finish_reason="stop",
    )


class _RecordingClient(FunctionInvocationLayer, BaseChatClient):
    """Records every messages list it's asked to respond to."""

    def __init__(self, *responses: ChatResponse) -> None:
        super().__init__()
        self._responses = list(responses)
        self.seen_message_texts: list[list[str]] = []

    async def _next(self) -> ChatResponse:
        return self._responses.pop(0)

    def _inner_get_response(self, *, messages, stream: bool, options=None, **_):
        self.seen_message_texts.append([m.text for m in messages if m.text])
        if stream:

            async def _gen():
                response = await self._next()
                for msg in response.messages:
                    yield ChatResponseUpdate(role=msg.role, contents=msg.contents, author_name=msg.author_name)

            return self._build_response_stream(_gen())
        return self._next()


@pytest.mark.asyncio
async def test_chat_history_excludes_current_turn_and_includes_prior_ones(
    clean_db, monkeypatch: pytest.MonkeyPatch, sample_env: dict
) -> None:
    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    await clean_db.execute(
        """INSERT INTO users (id, email, password_hash, name, role)
           VALUES ($1, $2, 'hash', 'Test User', 'customer')""",
        user_id,
        "history-test@example.com",
    )
    await clean_db.execute(
        """INSERT INTO conversations (id, user_id, title) VALUES ($1, $2, 'prior chat')""",
        conversation_id,
        user_id,
    )
    await clean_db.execute(
        """INSERT INTO messages (conversation_id, role, content)
           VALUES ($1, 'user', 'what is the capital of France?')""",
        conversation_id,
    )
    await clean_db.execute(
        """INSERT INTO messages (conversation_id, role, content)
           VALUES ($1, 'assistant', 'Paris.')""",
        conversation_id,
    )

    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)

    client = _RecordingClient(_text_response("It has about 2.1 million people."))
    fake_agent = Agent(client=client, instructions="test", name="orchestrator")
    monkeypatch.setattr("orchestrator.agent.create_orchestrator_agent", lambda: fake_agent)

    async def _fake_auth():
        current_user_email.set("history-test@example.com")
        current_user_role.set("customer")
        return {"sub": "history-test@example.com", "role": "customer", "user_id": str(user_id)}

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[optional_auth] = _fake_auth

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        resp = await http.post(
            "/api/chat",
            json={"message": "how big is it?", "conversation_id": str(conversation_id)},
            headers={"Authorization": "Bearer fake"},
        )

    assert resp.status_code == 200
    assert resp.json()["response"] == "It has about 2.1 million people."

    sent_texts = client.seen_message_texts[-1]
    assert sent_texts == [
        "what is the capital of France?",
        "Paris.",
        "how big is it?",
    ], f"expected exactly one copy of the current turn, got: {sent_texts}"
