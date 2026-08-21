"""Issue #9 — a specialist must receive the conversation's prior turns.

The bug this file exists to prevent is subtle enough that every *other*
session-related test in this repo passed while production was broken:

    web/src/lib/api.ts never sends an ``x-session-id`` header
      -> orchestrator/routes/chat.py never sets ``current_session_id``
        -> build_a2a_headers() forwards ``x-session-id: ""``
          -> shared/agent_host.py::_rehydrate_history_from_session returns
             None at its ``if not session_id`` guard, before touching the DB
            -> the specialist answers every follow-up with no prior context

Nothing failed. The specialist just silently started from a blank slate, so
"which one has the longest battery life?" reached product-discovery as a bare
comparative with no antecedent. It looked like LLM nondeterminism because the
orchestrator *does* hold the history and its prompt asks it to inline context
into the specialist message — a non-deterministic instruction that sometimes
worked.

Every existing test that appears to cover this sets the ContextVar or the
header by hand (``test_orchestrator_intent.py::test_session_id_forwarded_in_headers``,
``test_service_token.py``, ``test_streaming_steps.py``), which is precisely
why they kept passing. These two drive a real HTTP request instead, and assert
the two halves of the chain that production actually runs.
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

import orchestrator.agent as orch_mod
from orchestrator.agent import ORCHESTRATOR_TOOLS
from orchestrator.routes import optional_auth, router
from shared.context import current_user_email, current_user_role


def _text(text: str) -> ChatResponse:
    return ChatResponse(
        messages=[Message(role="assistant", contents=[Content.from_text(text=text)])],
        response_id=str(uuid.uuid4()),
        finish_reason="stop",
    )


class _RoutingClient(FunctionInvocationLayer, BaseChatClient):
    """Turn 1 routes to a specialist; turn 2 answers. Mirrors real tool use."""

    def __init__(self, specialist: str, forwarded: str) -> None:
        super().__init__()
        self._responses = [
            ChatResponse(
                messages=[
                    Message(
                        role="assistant",
                        contents=[
                            Content.from_function_call(
                                call_id="c1",
                                name="call_specialist_agent",
                                arguments={"agent_name": specialist, "message": forwarded},
                            )
                        ],
                    )
                ],
                response_id=str(uuid.uuid4()),
                finish_reason="tool_calls",
            ),
            _text("The Sony WH-1000XM5 lasts about 30 hours."),
        ]

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


def _capture_a2a() -> tuple[object, dict]:
    """Intercept the outbound A2A POST and record its headers and body."""
    from unittest.mock import AsyncMock, MagicMock

    resp = MagicMock()
    resp.json.return_value = {"response": "About 30 hours."}
    resp.raise_for_status = MagicMock()

    captured: dict = {}

    async def _post(url, *, json=None, headers=None, **_kw):
        captured["headers"] = headers or {}
        captured["json"] = json
        return resp

    instance = AsyncMock()
    instance.__aenter__.return_value = instance
    instance.__aexit__.return_value = None
    instance.post = _post
    return MagicMock(return_value=instance), captured


async def _seed_prior_turn(db, user_id: uuid.UUID, conversation_id: uuid.UUID, email: str) -> None:
    await db.execute(
        """INSERT INTO users (id, email, password_hash, name, role)
           VALUES ($1, $2, 'hash', 'Test User', 'customer')""",
        user_id,
        email,
    )
    await db.execute(
        "INSERT INTO conversations (id, user_id, title) VALUES ($1, $2, 'headphones')",
        conversation_id,
        user_id,
    )
    await db.execute(
        """INSERT INTO messages (conversation_id, role, content)
           VALUES ($1, 'user', 'show me noise cancelling headphones')""",
        conversation_id,
    )
    await db.execute(
        """INSERT INTO messages (conversation_id, role, content)
           VALUES ($1, 'assistant', 'The Sony WH-1000XM5 is a great option.')""",
        conversation_id,
    )


@pytest.mark.asyncio
async def test_specialist_call_carries_the_conversation_id_as_session_id(
    clean_db, monkeypatch: pytest.MonkeyPatch, sample_env: dict
) -> None:
    """The root cause: a browser-driven turn must forward a real session id.

    Asserts against the *outbound A2A headers* rather than the ContextVar,
    because the ContextVar being set is only interesting if it survives all
    the way onto the wire — and in the streaming path it crosses an
    ``asyncio.create_task`` boundary to get there.
    """
    user_id, conversation_id = uuid.uuid4(), uuid.uuid4()
    email = "issue9@example.com"
    await _seed_prior_turn(clean_db, user_id, conversation_id, email)

    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    monkeypatch.setattr(orch_mod, "AGENT_REGISTRY", {"product-discovery": "http://pd:8081"})

    client = _RoutingClient("product-discovery", "battery life of the Sony WH-1000XM5")
    agent = Agent(client=client, instructions="test", name="orchestrator", tools=ORCHESTRATOR_TOOLS)
    monkeypatch.setattr("orchestrator.agent.create_orchestrator_agent", lambda: agent)

    async def _fake_auth():
        current_user_email.set(email)
        current_user_role.set("customer")
        return {"sub": email, "role": "customer", "user_id": str(user_id)}

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[optional_auth] = _fake_auth

    mock_class, captured = _capture_a2a()
    monkeypatch.setattr("orchestrator.agent.httpx.AsyncClient", mock_class)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        resp = await http.post(
            "/api/chat",
            json={"message": "which one has the longest battery life?", "conversation_id": str(conversation_id)},
            headers={"Authorization": "Bearer fake"},
        )

    assert resp.status_code == 200
    assert captured, "the orchestrator never called a specialist"

    sent = captured["headers"].get("x-session-id", "")
    assert sent == str(conversation_id), (
        f"the specialist got x-session-id={sent!r}, so it cannot rehydrate anything. "
        "The browser never sends this header, so chat.py must set "
        "current_session_id from the conversation it just resolved."
    )


@pytest.mark.asyncio
async def test_specialist_rehydrates_the_prior_turn_from_that_session_id(
    clean_db, monkeypatch: pytest.MonkeyPatch, sample_env: dict
) -> None:
    """The other half: that id must actually produce the prior turn.

    Split from the test above deliberately. The first proves the id reaches
    the specialist; this proves the id is one the specialist can use. Either
    half passing alone still leaves follow-ups broken.
    """
    from shared.agent_host import _rehydrate_history_from_session

    user_id, conversation_id = uuid.uuid4(), uuid.uuid4()
    await _seed_prior_turn(clean_db, user_id, conversation_id, "issue9-rehydrate@example.com")
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)

    history = await _rehydrate_history_from_session(str(conversation_id))

    assert history, "a real conversation id returned no history"
    assert [h["role"] for h in history] == ["user", "assistant"]
    assert "Sony WH-1000XM5" in history[-1]["content"]


@pytest.mark.asyncio
async def test_streaming_turn_also_carries_the_session_id(
    clean_db, monkeypatch: pytest.MonkeyPatch, sample_env: dict
) -> None:
    """The streaming path must not lose the id across its task boundary.

    ``/api/chat/stream`` runs the orchestrator inside an
    ``asyncio.create_task`` spawned from within the SSE generator, and a
    ``create_task`` snapshots whatever context is active at creation time.
    Setting a ContextVar in the endpoint body is therefore not, on its own,
    proof that the specialist sees it — so this asserts on the wire again
    rather than trusting the blocking test to generalise.
    """
    user_id, conversation_id = uuid.uuid4(), uuid.uuid4()
    email = "issue9-stream@example.com"
    await _seed_prior_turn(clean_db, user_id, conversation_id, email)

    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    monkeypatch.setattr(orch_mod, "AGENT_REGISTRY", {"product-discovery": "http://pd:8081"})

    client = _RoutingClient("product-discovery", "battery life of the Sony WH-1000XM5")
    agent = Agent(client=client, instructions="test", name="orchestrator", tools=ORCHESTRATOR_TOOLS)
    monkeypatch.setattr("orchestrator.agent.create_orchestrator_agent", lambda: agent)

    async def _fake_auth():
        current_user_email.set(email)
        current_user_role.set("customer")
        return {"sub": email, "role": "customer", "user_id": str(user_id)}

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[optional_auth] = _fake_auth

    # The streaming branch of call_specialist_agent uses client.stream(), not
    # post() — mock that instead, and make it fail after recording, so the
    # call falls through to the blocking path rather than needing a full SSE
    # transcript. The headers are already captured by then, which is all this
    # test is about.
    from unittest.mock import AsyncMock, MagicMock

    captured: dict = {}

    def _stream(_method, _url, *, json=None, headers=None, **_kw):
        captured["headers"] = headers or {}
        raise RuntimeError("recorded")

    instance = AsyncMock()
    instance.__aenter__.return_value = instance
    instance.__aexit__.return_value = None
    instance.stream = _stream
    instance.post = AsyncMock(side_effect=RuntimeError("recorded"))
    monkeypatch.setattr("orchestrator.agent.httpx.AsyncClient", MagicMock(return_value=instance))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        async with http.stream(
            "POST",
            "/api/chat/stream",
            json={"message": "which one has the longest battery life?", "conversation_id": str(conversation_id)},
            headers={"Authorization": "Bearer fake"},
        ) as resp:
            assert resp.status_code == 200
            async for _ in resp.aiter_lines():
                pass

    assert captured, "the orchestrator never called a specialist on the streaming path"
    assert captured["headers"].get("x-session-id", "") == str(conversation_id)


@pytest.mark.asyncio
async def test_assistant_turn_is_persisted_before_done_is_yielded(
    clean_db, monkeypatch: pytest.MonkeyPatch, sample_env: dict
) -> None:
    """A follow-up sent the instant [DONE] lands must still see this turn.

    The assistant message used to be written by a task spawned *after*
    ``[DONE]`` was already on the wire. ``[DONE]`` is what re-enables the
    composer, so a fast follow-up — a script, an impatient user, an e2e
    test — could read history before that INSERT committed and lose the very
    turn it was following up on. Distinct from the session-id bug above and
    able to break follow-ups on its own, so it gets its own test.

    Asserted on the *server-side* ordering rather than by racing a read from
    the client, for two reasons found by trying the obvious version first:
    locally the detached INSERT usually wins anyway (so a timing assertion
    passed against the bug), and httpx's ASGITransport buffers the whole body
    (so the client cannot observe [DONE] before the generator ends, no matter
    how it is written). Both make a client-side test structurally incapable
    of catching this regression.
    """
    user_id, conversation_id = uuid.uuid4(), uuid.uuid4()
    email = "issue9-durable@example.com"
    await _seed_prior_turn(clean_db, user_id, conversation_id, email)

    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    monkeypatch.setattr(orch_mod, "AGENT_REGISTRY", {})

    client = _RoutingClient("product-discovery", "unused")
    client._responses = [_text("About 30 hours on a full charge.")]
    agent = Agent(client=client, instructions="test", name="orchestrator")
    monkeypatch.setattr("orchestrator.agent.create_orchestrator_agent", lambda: agent)

    import orchestrator.routes.chat as chat_mod

    order: list[str] = []

    real_persist = chat_mod._persist_assistant_turn

    async def _recording_persist(**kwargs):
        await real_persist(**kwargs)
        order.append("persisted")

    monkeypatch.setattr(chat_mod, "_persist_assistant_turn", _recording_persist)

    real_streaming_response = chat_mod.StreamingResponse

    def _recording_response(content, **kwargs):
        async def _wrapped():
            async for chunk in content:
                if "[DONE]" in chunk:
                    order.append("done")
                yield chunk

        return real_streaming_response(_wrapped(), **kwargs)

    monkeypatch.setattr(chat_mod, "StreamingResponse", _recording_response)

    async def _fake_auth():
        current_user_email.set(email)
        current_user_role.set("customer")
        return {"sub": email, "role": "customer", "user_id": str(user_id)}

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[optional_auth] = _fake_auth

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        async with http.stream(
            "POST",
            "/api/chat/stream",
            json={"message": "how long does its battery last?", "conversation_id": str(conversation_id)},
            headers={"Authorization": "Bearer fake"},
        ) as resp:
            assert resp.status_code == 200
            async for _ in resp.aiter_lines():
                pass

    assert order == ["persisted", "done"], f"expected the turn to be durable before [DONE] was yielded, got {order}"

    rows = await clean_db.fetch(
        """SELECT content FROM messages
           WHERE conversation_id = $1 AND role = 'assistant'
           ORDER BY created_at""",
        conversation_id,
    )
    assert [r["content"] for r in rows][-1] == "About 30 hours on a full charge."
