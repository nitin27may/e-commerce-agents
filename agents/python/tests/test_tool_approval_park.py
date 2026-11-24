"""A call MAF parks for approval (``approval_mode="always_require"``) becomes a durable approval, once.

MAF stops the run before function middleware, so ``HITLFunctionMiddleware`` never sees a gated tool and the
response text is empty (observed live against the real order-management host). The host now records the parked
call (``shared.agent_host.park_pending_approvals``) and answers with a fixed sentence. These tests use the REAL
gated tool declarations' decorator mode (``always_require``) with the production middleware stack's HITL piece,
a scripted model and a real database (testcontainers), through both host run paths.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import asyncpg
import pytest
import pytest_asyncio
from agent_framework import Agent, ChatResponse, Content, Message, tool

import shared.db as shared_db
from shared.agent_host import _run_agent_native, _run_agent_native_stream, park_pending_approvals
from shared.agent_observability import STEP_MIDDLEWARE
from shared.context import current_session_id, current_user_email
from shared.hitl import HITLFunctionMiddleware, park_gated_call, parked_reply
from tests.test_orchestration_modes import _ScriptedClient, _text_response

EMAIL = "jack.anderson@gmail.com"
ORDER_ID = "3e493865-2501-57c8-aa51-6d1cce1ded41"


def _call(name: str, args: dict[str, Any]) -> ChatResponse:
    return ChatResponse(
        messages=[
            Message(role="assistant", contents=[Content.from_function_call(call_id="c1", name=name, arguments=args)])
        ],
        response_id=str(uuid.uuid4()),
        finish_reason="tool_calls",
    )


@pytest_asyncio.fixture
async def pool(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> asyncpg.Pool:
    monkeypatch.setattr(shared_db, "_pool", clean_db)
    # A real, cancellable order of the requester: the pre-park check (shared.park_prechecks) must pass for the
    # park to happen at all.
    user_id = await clean_db.fetchval(
        "INSERT INTO users (email, password_hash, name) VALUES ($1, 'x', 'Jack') RETURNING id", EMAIL
    )
    await clean_db.execute(
        "INSERT INTO orders (id, user_id, status, total, shipping_address) VALUES ($1, $2, 'placed', 10, '{}')",
        ORDER_ID,
        user_id,
    )
    return clean_db


@pytest.fixture(autouse=True)
def _identity() -> Any:
    email_token = current_user_email.set(EMAIL)
    session_token = current_session_id.set("")
    yield
    current_user_email.reset(email_token)
    current_session_id.reset(session_token)


def _agent(tool_name: str, executed: list[str], args: dict[str, Any] | None = None) -> Agent:
    @tool(name=tool_name, description="gated test tool", approval_mode="always_require")
    async def gated(order_id: str) -> dict:
        executed.append(order_id)
        return {"order_id": order_id, "new_status": "cancelled"}

    client = _ScriptedClient(_call(tool_name, args or {"order_id": ORDER_ID}), _text_response("never reached"))
    return Agent(
        client=client,
        instructions="test",
        name="order-management",
        tools=[gated],
        middleware=[HITLFunctionMiddleware(), *STEP_MIDDLEWARE],
    )


async def _rows(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    return await pool.fetch("SELECT * FROM tool_approval_requests ORDER BY created_at")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_blocking_run_parks_the_call_and_answers_in_words(pool: asyncpg.Pool) -> None:
    executed: list[str] = []
    text = await _run_agent_native(_agent("cancel_order", executed), "Cancel my order")

    assert executed == []  # the tool never ran
    assert "approve cancelling order " + ORDER_ID in text
    rows = await _rows(pool)
    assert len(rows) == 1
    row = rows[0]
    assert (row["user_email"], row["tool_name"], row["status"], row["agent_name"]) == (
        EMAIL,
        "cancel_order",
        "pending",
        "order-management",
    )
    assert row["approval_id"] is not None  # the staff inbox row exists and is linked
    approval = await pool.fetchrow("SELECT kind, source, requester_email, status FROM approvals")
    assert (approval["kind"], approval["source"], approval["requester_email"], approval["status"]) == (
        "cancel_order",
        "tool",
        EMAIL,
        "pending_review",
    )
    assert text.startswith("I have asked a team member")
    assert str(row["id"])[:8] in text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_streaming_run_yields_the_same_sentence(pool: asyncpg.Pool) -> None:
    executed: list[str] = []
    box: dict[str, Any] = {}
    chunks: list[str] = []
    async for chunk in _run_agent_native_stream(_agent("cancel_order", executed), "Cancel it", metadata_box=box):
        chunks.append(chunk)

    assert executed == []
    assert "".join(chunks).startswith("I have asked a team member to approve cancelling order")
    assert len(await _rows(pool)) == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_same_call_twice_is_one_row_even_concurrently(pool: asyncpg.Pool) -> None:
    kwargs: dict[str, Any] = {
        "user_email": EMAIL,
        "session_id": None,
        "agent_name": "order-management",
        "tool_name": "cancel_order",
        "tool_input": {"order_id": ORDER_ID, "reason": "changed my mind"},
    }
    results = await asyncio.gather(*(park_gated_call(**kwargs) for _ in range(5)))

    assert len({request_id for request_id, _ in results}) == 1
    assert sum(1 for _, created in results if created) == 1
    assert len(await _rows(pool)) == 1
    assert await pool.fetchval("SELECT count(*) FROM approvals") == 1

    # A different input, or another user, is a different request.
    await park_gated_call(**{**kwargs, "tool_input": {"order_id": str(uuid.uuid4())}})
    await park_gated_call(**{**kwargs, "user_email": "other@example.com"})
    assert len(await _rows(pool)) == 3


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_decided_request_does_not_swallow_a_new_one(pool: asyncpg.Pool) -> None:
    kwargs: dict[str, Any] = {
        "user_email": EMAIL,
        "session_id": None,
        "agent_name": "order-management",
        "tool_name": "cancel_order",
        "tool_input": {"order_id": ORDER_ID},
    }
    first, _ = await park_gated_call(**kwargs)
    await pool.execute("UPDATE tool_approval_requests SET status = 'denied' WHERE id = $1", first)
    second, created = await park_gated_call(**kwargs)
    assert created is True and second != first


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_non_uuid_session_id_does_not_break_parking(pool: asyncpg.Pool) -> None:
    token = current_session_id.set("not-a-uuid")
    try:
        text = await _run_agent_native(_agent("cancel_order", []), "Cancel my order")
    finally:
        current_session_id.reset(token)
    assert "approve cancelling order" in text
    assert (await _rows(pool))[0]["session_id"] is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_no_forwarded_identity_fails_closed(pool: asyncpg.Pool) -> None:
    token = current_user_email.set("")
    try:
        text = await _run_agent_native(_agent("cancel_order", []), "Cancel my order")
    finally:
        current_user_email.reset(token)
    assert "no changes have been made" in text
    assert await _rows(pool) == []


@pytest.mark.asyncio
async def test_a_database_failure_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shared_db, "_pool", None)  # get_pool() raises
    executed: list[str] = []
    text = await _run_agent_native(_agent("cancel_order", executed), "Cancel my order")
    assert executed == []
    assert "no changes have been made" in text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_ungated_tool_runs_and_parks_nothing(pool: asyncpg.Pool) -> None:
    executed: list[str] = []

    @tool(name="get_order_details", description="read only")
    async def lookup(order_id: str) -> dict:
        executed.append(order_id)
        return {"order_id": order_id}

    client = _ScriptedClient(_call("get_order_details", {"order_id": ORDER_ID}), _text_response("It is placed."))
    agent = Agent(client=client, instructions="t", name="order-management", tools=[lookup], middleware=STEP_MIDDLEWARE)
    text = await _run_agent_native(agent, "Where is my order")

    assert executed == [ORDER_ID]
    assert text == "It is placed."
    assert await _rows(pool) == []


@pytest.mark.asyncio
async def test_a_response_without_requests_is_untouched() -> None:
    class _Plain:
        user_input_requests: list[Any] = []

    assert await park_pending_approvals(object(), _Plain()) == ""


def test_parked_reply_names_the_subject_per_tool() -> None:
    assert "cancelling order o-1 (ref: abcdef12)" in parked_reply("cancel_order", {"order_id": "o-1"}, "abcdef123456")
    assert "refund for return r-9" in parked_reply("process_refund", {"return_id": "r-9"}, None)
    assert "approve a custom tool." in parked_reply("a_custom_tool", {}, None)
    assert "(ref" not in parked_reply("cancel_order", {"order_id": "o-1"}, None)
