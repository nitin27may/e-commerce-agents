"""Phase 1.5 — chat.py links a paused workflow:return-replace run to its
usage_logs row and a hitl_requests row.

Real Postgres (clean_db). Exercises the real /api/chat handler end to
end: a high-value return pauses, and afterward the DB has exactly what
POST /api/orchestration/{run_id}/resume will need — a hitl_requests row
carrying request_id + checkpoint_id, and workflow_checkpoints.usage_log_id
pointing back at the run.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from orchestrator.routes import optional_auth, router
from shared.config import settings
from shared.context import current_user_email, current_user_role


async def _eligible(order_id: str) -> dict[str, Any]:
    return {"eligible": True}


async def _initiate_ok(order_id: str, reason: str, refund_method: str) -> dict[str, Any]:
    return {"return_id": "ret-99", "refund_amount": 120.0}


async def _search_ok(max_price: float, min_rating: float, limit: int) -> list[dict[str, Any]]:
    return [{"id": "p-1", "name": "Replacement A"}]


async def _tier_gold() -> dict[str, Any]:
    return {"tier": "gold", "discount_pct": 10.0}


RETURN_TOOLS = {
    "check_return_eligibility": _eligible,
    "initiate_return": _initiate_ok,
    "search_products": _search_ok,
    "get_loyalty_tier": _tier_gold,
}


@pytest.mark.asyncio
async def test_chat_persists_hitl_request_and_links_checkpoint_on_pause(
    clean_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    import orchestrator.modes as modes_module
    import order_management.tools as order_tools
    from orchestrator.modes.workflow_mode import ReturnReplaceMode

    order_id = str(uuid.uuid4())
    high = settings.RETURN_HITL_THRESHOLD + 100.0

    async def _fake_order_details(*, order_id: str) -> dict[str, Any]:
        return {"order_id": order_id, "total": high}

    monkeypatch.setattr(order_tools, "get_order_details", _fake_order_details)
    monkeypatch.setitem(modes_module.MODES, "workflow:return-replace", ReturnReplaceMode(tools=RETURN_TOOLS))
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)

    user_id = uuid.uuid4()
    await clean_db.execute(
        """INSERT INTO users (id, email, password_hash, name, role)
           VALUES ($1, $2, 'hash', 'Test User', 'customer')""",
        user_id,
        "hitl-test@example.com",
    )

    async def _fake_auth():
        current_user_email.set("hitl-test@example.com")
        current_user_role.set("customer")
        return {"sub": "hitl-test@example.com", "role": "customer", "user_id": str(user_id)}

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[optional_auth] = _fake_auth

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        resp = await http.post(
            "/api/chat",
            json={"message": f"return order {order_id}", "mode": "workflow:return-replace"},
            headers={"Authorization": "Bearer fake"},
        )

    assert resp.status_code == 200
    assert "needs approval" in resp.json()["response"]

    hitl_row = await clean_db.fetchrow("SELECT * FROM hitl_requests WHERE user_email = $1", "hitl-test@example.com")
    assert hitl_row is not None
    assert hitl_row["status"] == "pending"
    assert hitl_row["kind"] == "return_approval"
    assert hitl_row["request_id"]
    assert hitl_row["checkpoint_id"] is not None

    usage_log_id = hitl_row["workflow_run_id"]
    usage_row = await clean_db.fetchrow("SELECT id FROM usage_logs WHERE id = $1", usage_log_id)
    assert usage_row is not None

    checkpoint_row = await clean_db.fetchrow(
        "SELECT usage_log_id FROM workflow_checkpoints WHERE checkpoint_id = $1",
        hitl_row["checkpoint_id"],
    )
    assert checkpoint_row is not None
    assert checkpoint_row["usage_log_id"] == usage_log_id


@pytest.mark.asyncio
async def test_chat_does_not_create_hitl_request_for_completed_tool_mode_run(
    clean_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sanity check that _link_run_artifacts is a true no-op for the
    overwhelmingly common case — a plain "tool" mode turn must not leave
    any hitl_requests/checkpoint linkage behind."""
    from agent_framework import (
        Agent,
        BaseChatClient,
        ChatResponse,
        ChatResponseUpdate,
        Content,
        FunctionInvocationLayer,
        Message,
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

    response = ChatResponse(
        messages=[Message(role="assistant", contents=[Content.from_text(text="hi there")])],
        response_id=str(uuid.uuid4()),
        finish_reason="stop",
    )
    fake_agent = Agent(client=_ScriptedClient(response), instructions="test", name="orchestrator")
    monkeypatch.setattr("orchestrator.agent.create_orchestrator_agent", lambda: fake_agent)
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)

    user_id = uuid.uuid4()
    await clean_db.execute(
        """INSERT INTO users (id, email, password_hash, name, role)
           VALUES ($1, $2, 'hash', 'Test User', 'customer')""",
        user_id,
        "tool-mode-test@example.com",
    )

    async def _fake_auth():
        current_user_email.set("tool-mode-test@example.com")
        current_user_role.set("customer")
        return {"sub": "tool-mode-test@example.com", "role": "customer", "user_id": str(user_id)}

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[optional_auth] = _fake_auth

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        resp = await http.post("/api/chat", json={"message": "hello"}, headers={"Authorization": "Bearer fake"})

    assert resp.status_code == 200
    count = await clean_db.fetchval(
        "SELECT COUNT(*) FROM hitl_requests WHERE user_email = $1", "tool-mode-test@example.com"
    )
    assert count == 0


@pytest.mark.asyncio
async def test_stream_names_the_run_and_flags_a_pause_before_done(
    clean_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``/api/chat/stream`` emits ``event: run`` carrying the run's id.

    Without it the chat thread cannot offer an approval at all: resuming
    needs ``POST /api/orchestration/{run_id}/resume``, and until this frame
    existed the only id the client ever learned was ``conversation_id``. The
    user who caused the pause had to know a separate ``/runs`` page existed
    and find their run on it — a pause the pauser cannot see is
    indistinguishable from a hang.

    The frame cannot be folded into ``event: metadata``: the run id *is* the
    ``usage_logs`` row, which is written during persistence, several frames
    after ``metadata`` has already gone out. So this asserts ordering as well
    as content — the frame must land before ``[DONE]``, because ``[DONE]`` is
    when the client stops reading.
    """
    import orchestrator.modes as modes_module
    import order_management.tools as order_tools
    from orchestrator.modes.workflow_mode import ReturnReplaceMode
    from orchestrator.routes.chat import ChatRequest, chat_stream

    order_id = str(uuid.uuid4())
    high = settings.RETURN_HITL_THRESHOLD + 100.0

    async def _fake_order_details(*, order_id: str) -> dict[str, Any]:
        return {"order_id": order_id, "total": high}

    monkeypatch.setattr(order_tools, "get_order_details", _fake_order_details)
    monkeypatch.setitem(modes_module.MODES, "workflow:return-replace", ReturnReplaceMode(tools=RETURN_TOOLS))
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)

    user_id = uuid.uuid4()
    await clean_db.execute(
        """INSERT INTO users (id, email, password_hash, name, role)
           VALUES ($1, $2, 'hash', 'Test User', 'customer')""",
        user_id,
        "run-frame@example.com",
    )
    current_user_email.set("run-frame@example.com")
    current_user_role.set("customer")

    class _FakeRequest:
        async def is_disconnected(self) -> bool:
            return False

    response = await chat_stream(
        ChatRequest(message=f"return order {order_id}", mode="workflow:return-replace"),
        _FakeRequest(),
        user={"sub": "run-frame@example.com", "role": "customer", "user_id": str(user_id)},
    )
    raw = "".join([chunk async for chunk in response.body_iterator])

    run_index = raw.find("event: run\n")
    assert run_index != -1, f"no run frame in stream: {raw[-400:]}"
    assert run_index < raw.find("data: [DONE]"), "run frame must precede [DONE]"

    payload = json.loads(raw[run_index:].split("data: ", 1)[1].split("\n\n", 1)[0])
    assert payload["pending_approval"] is True

    # The id must be the one the resume route will look up, not an invented
    # correlation id — asserting it against the hitl_requests row is what
    # makes this a contract test rather than a shape test.
    hitl_row = await clean_db.fetchrow(
        "SELECT workflow_run_id FROM hitl_requests WHERE user_email = $1", "run-frame@example.com"
    )
    assert hitl_row is not None
    assert payload["run_id"] == str(hitl_row["workflow_run_id"])


@pytest.mark.asyncio
async def test_stream_run_frame_reports_no_pause_for_an_ordinary_turn(
    clean_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every mode emits the frame, but only a real pause sets the flag.

    Emitting it only on pause would make its absence ambiguous — a client
    could not tell "this run did not pause" from "this run's id never
    arrived", and would have to poll /runs to find out.
    """
    from agent_framework import (
        Agent,
        BaseChatClient,
        ChatResponse,
        ChatResponseUpdate,
        Content,
        FunctionInvocationLayer,
        Message,
    )

    from orchestrator.routes.chat import ChatRequest, chat_stream

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

    scripted = ChatResponse(
        messages=[Message(role="assistant", contents=[Content.from_text(text="all good")])],
        response_id=str(uuid.uuid4()),
        finish_reason="stop",
    )
    fake_agent = Agent(client=_ScriptedClient(scripted), instructions="test", name="orchestrator")
    monkeypatch.setattr("orchestrator.agent.create_orchestrator_agent", lambda: fake_agent)
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)

    user_id = uuid.uuid4()
    await clean_db.execute(
        """INSERT INTO users (id, email, password_hash, name, role)
           VALUES ($1, $2, 'hash', 'Test User', 'customer')""",
        user_id,
        "run-frame-ok@example.com",
    )
    current_user_email.set("run-frame-ok@example.com")
    current_user_role.set("customer")

    class _FakeRequest:
        async def is_disconnected(self) -> bool:
            return False

    response = await chat_stream(
        ChatRequest(message="hello"),
        _FakeRequest(),
        user={"sub": "run-frame-ok@example.com", "role": "customer", "user_id": str(user_id)},
    )
    raw = "".join([chunk async for chunk in response.body_iterator])

    run_index = raw.find("event: run\n")
    assert run_index != -1, f"no run frame in stream: {raw[-400:]}"
    payload = json.loads(raw[run_index:].split("data: ", 1)[1].split("\n\n", 1)[0])
    assert payload["pending_approval"] is False
    assert payload["run_id"]
