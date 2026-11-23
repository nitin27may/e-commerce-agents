"""A parked approval-gated tool call is visible to the eval harness (``tool_approval_requests``).

``HITLFunctionMiddleware`` writes ``tool_approval_requests`` and short-circuits before the step recorder, so a
parked ``cancel_order`` leaves no step and no ``hitl_requests`` row. The harness counts a pending row for the case
user inside the run window as an approval pause. Real middleware and a real database (testcontainers).
"""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg
import pytest
from agent_framework import Agent, ChatResponse, Content, Message, tool

import shared.db as shared_db
from evals import http_target
from evals.evaluator import AgentEvaluator, EvalCase
from evals.harness import ProductionRunner, db_clock, pending_tool_approvals
from shared.agent_observability import STEP_MIDDLEWARE
from shared.hitl import HITLFunctionMiddleware
from tests.test_eval_http_target import REPLAY, FakeOrchestrator, _frames_for, _scripted_outcome
from tests.test_orchestration_modes import _ScriptedClient, _text_response

EMAIL = "jack.anderson@gmail.com"


def _call(name: str, args: dict[str, Any]) -> ChatResponse:
    return ChatResponse(
        messages=[
            Message(role="assistant", contents=[Content.from_function_call(call_id="c1", name=name, arguments=args)])
        ],
        response_id=str(uuid.uuid4()),
        finish_reason="tool_calls",
    )


@pytest.fixture
def pool(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> asyncpg.Pool:
    monkeypatch.setattr(shared_db, "_pool", clean_db)
    return clean_db


async def _insert(pool: asyncpg.Pool, email: str, status: str = "pending") -> None:
    await pool.execute(
        "INSERT INTO tool_approval_requests (user_email, agent_name, tool_name, tool_input, status) "
        "VALUES ($1, 'a', 'cancel_order', '{}'::jsonb, $2)",
        email,
        status,
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_pending_rows_are_scoped_to_user_status_and_window(pool: asyncpg.Pool) -> None:
    await _insert(pool, EMAIL)  # parked before the window opens
    since = await db_clock()
    assert since is not None
    assert await pending_tool_approvals(EMAIL, since) == 0
    await _insert(pool, EMAIL)
    await _insert(pool, EMAIL, status="approved")
    await _insert(pool, "someone.else@example.com")
    assert await pending_tool_approvals(EMAIL, since) == 1
    assert await pending_tool_approvals(EMAIL, None) is None  # unknown is never "none"


@pytest.mark.asyncio
async def test_no_database_is_unknown_not_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shared_db, "_pool", None)
    assert await db_clock() is None
    assert await pending_tool_approvals(EMAIL, None) is None


def _runner(monkeypatch: pytest.MonkeyPatch, tool_name: str) -> tuple[ProductionRunner, list[str]]:
    executed: list[str] = []

    @tool(name=tool_name, description="test tool")
    async def the_tool(order_id: str) -> dict:
        executed.append(order_id)
        return {"order_id": order_id, "new_status": "cancelled"}

    client = _ScriptedClient(_call(tool_name, {"order_id": "o-1"}), _text_response("done"))
    agent = Agent(
        client=client,
        instructions="test",
        name="order-management",
        tools=[the_tool],
        middleware=[HITLFunctionMiddleware(), *STEP_MIDDLEWARE],
    )
    monkeypatch.setattr("order_management.agent.create_order_management_agent", lambda: agent)
    return ProductionRunner("order-management"), executed


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_parked_cancel_order_is_an_approval_pause(pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> None:
    runner, executed = _runner(monkeypatch, "cancel_order")
    outcome = await runner.run("Cancel my order", user_email=EMAIL)

    assert executed == []  # parked, not executed
    assert outcome.tools_called == []  # and, as documented, no step: the middleware short-circuits first
    assert outcome.hitl_requested is True
    assert await pool.fetchval("SELECT count(*) FROM hitl_requests") == 0  # the old signal could never see it

    runner, _ = _runner(monkeypatch, "cancel_order")  # the scripted model is spent; a fresh one for the evaluator
    evaluator = AgentEvaluator("order-management", pass_threshold=0.0, runner=runner)
    case = EvalCase(
        input="Cancel my order",
        expected_tools=[],
        expected_fields=[],
        criteria={},
        expect_hitl=True,
        user_email=EMAIL,
    )
    result = await evaluator._evaluate_case(case, "order-management:d:0")
    assert result.assertion_failures == []
    assert result.record is not None and result.record.hitl_requested is True


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_ungated_tool_is_not_a_pause_and_a_pre_existing_row_is_not_counted(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _insert(pool, EMAIL)  # parked by an earlier run: outside this run's window
    runner, executed = _runner(monkeypatch, "get_order_details")
    outcome = await runner.run("Where is my order", user_email=EMAIL)

    assert executed == ["o-1"]
    assert outcome.hitl_requested is False


# ─────────────────────── the http target ───────────────────────


async def _http_record(monkeypatch: pytest.MonkeyPatch, found: int | None) -> Any:
    async def _clock() -> str:
        return "window"

    async def _count(email: str, since: Any) -> int | None:
        assert since == "window" and email == EMAIL
        return found

    monkeypatch.setattr(http_target, "db_clock", _clock)
    monkeypatch.setattr(http_target, "pending_tool_approvals", _count)
    fake = FakeOrchestrator(_frames_for(_scripted_outcome()))
    runner = fake.runner()
    case = EvalCase(
        input="cancel my order",
        expected_tools=["search_products", "call_specialist_agent"],
        expected_fields=[],
        criteria={},
        expect_hitl=True,
        user_email=EMAIL,
    )
    evaluator = AgentEvaluator("orchestrator", pass_threshold=0.0, setup=REPLAY, runner=runner)
    try:
        return (await evaluator._evaluate_case(case, "orchestrator:d:0")).record
    finally:
        await runner.aclose()


@pytest.mark.asyncio
async def test_http_target_sees_a_parked_tool_call_through_the_database(monkeypatch: pytest.MonkeyPatch) -> None:
    record = await _http_record(monkeypatch, 1)
    assert record.hitl_requested is True
    assert "hitl" not in record.not_observable
    assert record.assertion_failures == []


@pytest.mark.asyncio
@pytest.mark.parametrize("found", [0, None])
async def test_http_target_keeps_hitl_not_observable_without_a_positive_finding(
    monkeypatch: pytest.MonkeyPatch, found: int | None
) -> None:
    record = await _http_record(monkeypatch, found)
    assert record.hitl_requested is False
    assert "hitl" in record.not_observable
    assert record.assertion_failures == []  # not applicable, neither pass nor fail
