"""In-process orchestrator multi-turn eval cases write the ``messages`` table like the real chat route.

Before, only the orchestrator saw earlier turns (as ``RunContext.history``); a specialist reached over A2A got the
session id but found no rows to rehydrate. These tests run a scripted two-turn conversation through
``ProductionRunner("orchestrator")`` with a fake A2A hop that runs the specialist's real
``_rehydrate_history_from_session`` on the forwarded ``x-session-id``, and check what it finds.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

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

import orchestrator.agent as orch_mod
from evals.harness import Conversation, ProductionRunner
from orchestrator.agent import ORCHESTRATOR_TOOLS
from shared.agent_host import _rehydrate_history_from_session
from shared.context import current_user_email

pytestmark = pytest.mark.asyncio


def _call(name: str, message: str) -> ChatResponse:
    return ChatResponse(
        messages=[
            Message(
                role="assistant",
                contents=[
                    Content.from_function_call(
                        call_id=f"c-{uuid.uuid4().hex[:6]}",
                        name="call_specialist_agent",
                        arguments={"agent_name": name, "message": message},
                    )
                ],
            )
        ],
        response_id=str(uuid.uuid4()),
        finish_reason="tool_calls",
    )


def _text(text: str) -> ChatResponse:
    return ChatResponse(
        messages=[Message(role="assistant", contents=[Content.from_text(text=text)])],
        response_id=str(uuid.uuid4()),
        finish_reason="stop",
    )


class _ScriptedClient(FunctionInvocationLayer, BaseChatClient):
    """Plays a fixed list of responses in order (route, answer, route, answer, ...)."""

    def __init__(self, responses: list[ChatResponse]) -> None:
        super().__init__()
        self._responses = list(responses)

    def _inner_get_response(self, *, messages, stream: bool, options=None, **_):
        if stream:

            async def _gen():
                for msg in self._responses.pop(0).messages:
                    yield ChatResponseUpdate(role=msg.role, contents=msg.contents, author_name=msg.author_name)

            return self._build_response_stream(_gen())

        async def _one() -> ChatResponse:
            return self._responses.pop(0)

        return _one()


class _Specialist:
    """The A2A hop: records the forwarded session id and what the specialist would rehydrate from it."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def patch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        owner = self

        async def _post(url, *, json=None, headers=None, **_kw):
            session = (headers or {}).get("x-session-id", "")
            rehydrated = await _rehydrate_history_from_session(session)
            owner.calls.append({"session": session, "history": rehydrated, "message": (json or {}).get("message")})
            resp = MagicMock()
            resp.json.return_value = {"response": f"specialist answer {len(owner.calls)}"}
            resp.raise_for_status = MagicMock()
            return resp

        instance = AsyncMock()
        instance.__aenter__.return_value = instance
        instance.__aexit__.return_value = None
        instance.post = _post
        monkeypatch.setattr("orchestrator.agent.httpx.AsyncClient", MagicMock(return_value=instance))


async def _user(db: Any, email: str) -> uuid.UUID:
    user_id = uuid.uuid4()
    await db.execute(
        "INSERT INTO users (id, email, password_hash, name, role) VALUES ($1, $2, 'hash', 'Eval User', 'customer')",
        user_id,
        email,
    )
    return user_id


def _wire(monkeypatch: pytest.MonkeyPatch, db: Any, turns: int) -> _Specialist:
    # test_config_loader reloads shared.config with a bogus backend and leaves it behind; pin the one under test.
    monkeypatch.setattr("shared.config.settings.MAF_SESSION_BACKEND", "postgres")
    monkeypatch.setattr("shared.db._pool", db, raising=False)
    monkeypatch.setattr("shared.db.get_pool", lambda: db)
    monkeypatch.setattr(orch_mod, "AGENT_REGISTRY", {"order-management": "http://om:8082"})
    script: list[ChatResponse] = []
    for n in range(1, turns + 1):
        script += [_call("order-management", f"forwarded {n}"), _text(f"orchestrator answer {n}")]
    agent = Agent(client=_ScriptedClient(script), instructions="test", name="orchestrator", tools=ORCHESTRATOR_TOOLS)
    monkeypatch.setattr("orchestrator.agent.create_orchestrator_agent", lambda: agent)
    specialist = _Specialist()
    specialist.patch(monkeypatch)
    return specialist


async def _two_turns(runner: ProductionRunner, email: str, session: str) -> Conversation:
    conversation = Conversation(session_id=session)
    for text in ("status of my latest order", "and when will it arrive"):
        outcome = await runner.run(text, user_email=email, conversation=conversation)
        assert outcome.error is None, outcome.error
        conversation.record_turn(text, outcome)
    return conversation


async def test_second_turns_specialist_rehydrates_the_first_turn(clean_db, monkeypatch, sample_env) -> None:
    email = "inproc-mt@example.com"
    await _user(clean_db, email)
    specialist = _wire(monkeypatch, clean_db, turns=2)
    session = str(uuid.uuid4())

    await _two_turns(ProductionRunner("orchestrator"), email, session)

    assert [c["session"] for c in specialist.calls] == [session, session]
    second = specialist.calls[1]["history"] or []
    contents = [h["content"] for h in second]
    # the earlier user message and the orchestrator's earlier answer, in order, before the current message
    assert contents[:2] == ["status of my latest order", "orchestrator answer 1"]
    assert [h["role"] for h in second[:2]] == ["user", "assistant"]
    first = specialist.calls[0]["history"] or []
    assert "orchestrator answer 1" not in [h["content"] for h in first]


async def test_rows_match_what_the_chat_route_writes(clean_db, monkeypatch, sample_env) -> None:
    email = "inproc-rows@example.com"
    user_id = await _user(clean_db, email)
    _wire(monkeypatch, clean_db, turns=2)
    session = str(uuid.uuid4())

    await _two_turns(ProductionRunner("orchestrator"), email, session)

    conv = await clean_db.fetchrow("SELECT user_id, last_message_at FROM conversations WHERE id = $1", session)
    assert conv["user_id"] == user_id and conv["last_message_at"] is not None
    rows = await clean_db.fetch(
        "SELECT role, content, agent_name, agents_involved, metadata FROM messages "
        "WHERE conversation_id = $1 ORDER BY created_at, id",
        session,
    )
    # one user and one assistant row per turn: no double write from the in-memory history
    assert [r["role"] for r in rows] == ["user", "assistant", "user", "assistant"]
    assert rows[0]["agent_name"] is None and rows[1]["agent_name"] == "orchestrator"
    assert rows[1]["agents_involved"] and "orchestrator" in rows[1]["agents_involved"]
    assert '"steps"' in rows[1]["metadata"] and '"agents_involved"' in rows[1]["metadata"]
    assert [r["content"] for r in rows if r["role"] == "assistant"] == [
        "orchestrator answer 1",
        "orchestrator answer 2",
    ]


async def test_rows_are_scoped_to_the_callers_email(clean_db, monkeypatch, sample_env) -> None:
    email = "inproc-owner@example.com"
    await _user(clean_db, email)
    await _user(clean_db, "inproc-other@example.com")
    _wire(monkeypatch, clean_db, turns=2)
    session = str(uuid.uuid4())
    await _two_turns(ProductionRunner("orchestrator"), email, session)

    current_user_email.set("inproc-other@example.com")
    assert not await _rehydrate_history_from_session(session)
    current_user_email.set(email)
    assert await _rehydrate_history_from_session(session)


async def test_a_conversation_owned_by_someone_else_is_never_joined(clean_db, monkeypatch, sample_env) -> None:
    await _user(clean_db, "inproc-a@example.com")
    owner = await _user(clean_db, "inproc-b@example.com")
    session = uuid.uuid4()
    await clean_db.execute("INSERT INTO conversations (id, user_id, title) VALUES ($1, $2, 't')", session, owner)
    _wire(monkeypatch, clean_db, turns=1)

    outcome = await ProductionRunner("orchestrator").run(
        "hi", user_email="inproc-a@example.com", conversation=Conversation(session_id=str(session))
    )

    assert outcome.error and "another user" in outcome.error
    assert await clean_db.fetchval("SELECT count(*) FROM messages") == 0


async def test_a_rerun_gets_its_own_conversation(clean_db, monkeypatch, sample_env) -> None:
    email = "inproc-rerun@example.com"
    await _user(clean_db, email)
    specialist = _wire(monkeypatch, clean_db, turns=4)
    runner = ProductionRunner("orchestrator")

    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    await _two_turns(runner, email, first)
    await _two_turns(runner, email, second)

    assert first != second
    assert await clean_db.fetchval("SELECT count(*) FROM conversations") == 2
    third_call = specialist.calls[2]["history"] or []  # run two, turn one: nothing from run one
    assert [h["content"] for h in third_call] == ["status of my latest order"]
    assert await clean_db.fetchval("SELECT count(*) FROM messages WHERE conversation_id = $1", second) == 4


async def test_single_turn_and_unknown_user_write_nothing(clean_db, monkeypatch, sample_env) -> None:
    email = "inproc-single@example.com"
    await _user(clean_db, email)
    _wire(monkeypatch, clean_db, turns=2)
    runner = ProductionRunner("orchestrator")

    single = await runner.run("status of my latest order", user_email=email)
    assert single.error is None
    assert await clean_db.fetchval("SELECT count(*) FROM conversations") == 0
    assert await clean_db.fetchval("SELECT count(*) FROM messages") == 0

    # a caller with no user row keeps the old in-memory-only behaviour instead of failing
    ghost = await runner.run(
        "and when will it arrive",
        user_email="ghost@example.com",
        conversation=Conversation(session_id=str(uuid.uuid4())),
    )
    assert ghost.error is None
    assert await clean_db.fetchval("SELECT count(*) FROM messages") == 0
