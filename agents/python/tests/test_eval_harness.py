"""Tests for evals/harness.py::ProductionRunner.

Real MAF Agent driven by a scripted fake chat client (reusing
test_orchestration_modes.py's _ScriptedClient), not a mocked run() — same
standard the rest of this repo holds. Proves ProductionRunner actually
routes through the real dispatch (orchestrator.modes for the orchestrator
case, shared.agent_host._run_agent_native for specialists), not a
hand-rolled loop.
"""

from __future__ import annotations

import uuid

import pytest
from agent_framework import Agent, ChatResponse, Content, Message, tool

from evals.harness import AGENT_FACTORIES, ProductionRunner, _routes, _tools_called
from tests.test_orchestration_modes import _ScriptedClient, _text_response


def test_agent_factories_cover_orchestrator_and_all_five_specialists() -> None:
    assert set(AGENT_FACTORIES) == {
        "orchestrator",
        "product-discovery",
        "order-management",
        "pricing-promotions",
        "review-sentiment",
        "inventory-fulfillment",
    }


def test_tools_called_extracts_names_from_steps() -> None:
    steps = [{"tool_name": "search_products"}, {"tool_name": "check_stock"}, {"status": "success"}]
    assert _tools_called(steps) == ["search_products", "check_stock"]


def test_routes_extracts_agent_name_from_call_specialist_agent_steps() -> None:
    steps = [
        {"tool_name": "call_specialist_agent", "tool_input": {"agent_name": "product-discovery", "message": "hi"}},
        {"tool_name": "search_products"},
    ]
    assert _routes(steps) == ["product-discovery"]


def test_routes_ignores_steps_with_malformed_tool_input() -> None:
    steps = [{"tool_name": "call_specialist_agent", "tool_input": "not-a-dict"}]
    assert _routes(steps) == []


@pytest.mark.asyncio
async def test_orchestrator_run_returns_text_and_tools_called(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _ScriptedClient(_text_response("Paris is the capital of France."))
    fake_agent = Agent(client=fake, instructions="test", name="orchestrator")
    monkeypatch.setattr("orchestrator.agent.create_orchestrator_agent", lambda: fake_agent)

    runner = ProductionRunner("orchestrator")
    outcome = await runner.run("capital of France?")

    assert outcome.text == "Paris is the capital of France."
    assert outcome.error is None
    assert outcome.routes == []


@pytest.mark.asyncio
async def test_orchestrator_run_extracts_route_from_specialist_call(monkeypatch: pytest.MonkeyPatch) -> None:
    @tool(name="call_specialist_agent", description="Route to a specialist")
    async def call_specialist_agent(agent_name: str, message: str) -> str:
        return "specialist said hi"

    call_response = ChatResponse(
        messages=[
            Message(
                role="assistant",
                contents=[
                    Content.from_function_call(
                        call_id="c1",
                        name="call_specialist_agent",
                        arguments={"agent_name": "product-discovery", "message": "hi"},
                    )
                ],
            )
        ],
        response_id=str(uuid.uuid4()),
        finish_reason="tool_calls",
    )
    from shared.agent_observability import STEP_MIDDLEWARE

    fake = _ScriptedClient(call_response, _text_response("Found some headphones."))
    fake_agent = Agent(
        client=fake,
        instructions="test",
        name="orchestrator",
        tools=[call_specialist_agent],
        middleware=STEP_MIDDLEWARE,
    )
    monkeypatch.setattr("orchestrator.agent.create_orchestrator_agent", lambda: fake_agent)

    runner = ProductionRunner("orchestrator")
    outcome = await runner.run("find headphones")

    assert outcome.routes == ["product-discovery"]
    assert "call_specialist_agent" in outcome.tools_called
    assert outcome.text == "Found some headphones."


@pytest.mark.asyncio
async def test_specialist_run_uses_real_agent_host_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _ScriptedClient(_text_response("Here are some products."))
    fake_agent = Agent(client=fake, instructions="test", name="product-discovery")
    monkeypatch.setattr("product_discovery.agent.create_product_discovery_agent", lambda: fake_agent)

    runner = ProductionRunner("product-discovery")
    outcome = await runner.run("find headphones")

    assert outcome.text == "Here are some products."
    assert outcome.error is None


@pytest.mark.asyncio
async def test_specialist_agent_is_cached_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _ScriptedClient(_text_response("first"), _text_response("second"))
    fake_agent = Agent(client=fake, instructions="test", name="product-discovery")
    build_calls = []

    def _build():
        build_calls.append(1)
        return fake_agent

    monkeypatch.setattr("product_discovery.agent.create_product_discovery_agent", _build)

    runner = ProductionRunner("product-discovery")
    await runner.run("first query")
    await runner.run("second query")

    assert len(build_calls) == 1, "the agent factory must only be called once per runner"


@pytest.mark.asyncio
async def test_run_captures_exception_as_error_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom():
        raise RuntimeError("agent construction failed")

    monkeypatch.setattr("product_discovery.agent.create_product_discovery_agent", _boom)

    runner = ProductionRunner("product-discovery")
    outcome = await runner.run("anything")

    assert outcome.error == "agent construction failed"
    assert outcome.text == ""


@pytest.mark.asyncio
async def test_specialist_call_parked_by_maf_approval_is_seen_as_a_park(monkeypatch: pytest.MonkeyPatch) -> None:
    # approval_mode="always_require" is decided by MAF before any function middleware runs: the response has no
    # text, no step and no tool_approval_requests row, only a function_approval_request. The runner must still
    # report the park and the call (name and arguments), or expect_hitl can never hold for a gated tool.
    ran: list[str] = []

    @tool(name="cancel_order", description="Cancel an order", approval_mode="always_require")
    async def cancel_order(order_id: str, reason: str) -> dict:
        ran.append(order_id)
        return {"status": "cancelled"}

    call_response = ChatResponse(
        messages=[
            Message(
                role="assistant",
                contents=[
                    Content.from_function_call(
                        call_id="c1", name="cancel_order", arguments={"order_id": "o-1", "reason": "cheaper elsewhere"}
                    )
                ],
            )
        ],
        response_id=str(uuid.uuid4()),
        finish_reason="tool_calls",
    )
    fake = _ScriptedClient(call_response)
    fake_agent = Agent(client=fake, instructions="test", name="order-management", tools=[cancel_order])
    monkeypatch.setattr("order_management.agent.create_order_management_agent", lambda: fake_agent)

    # The host now makes the park durable (shared.agent_host.park_pending_approvals) and answers in words; stand in
    # for the database write so this stays a unit test of what the runner reports.
    async def _fake_park(**kwargs: object) -> tuple[str, bool]:
        return "req-1", True

    monkeypatch.setattr("shared.hitl.park_gated_call", _fake_park)
    # "o-1" is not a real order; the pre-park checks (shared.park_prechecks) have their own tests.
    monkeypatch.setattr("shared.park_prechecks._PRECHECKS", {})

    outcome = await ProductionRunner("order-management", user_email="jack@example.com").run("cancel order o-1")

    assert ran == [], "a parked call must not execute"
    assert "asked a team member to approve" in outcome.text
    assert outcome.hitl_requested is True
    assert outcome.tools_called == ["cancel_order"]
    assert outcome.tool_calls == [
        {"tool_name": "cancel_order", "tool_input": {"order_id": "o-1", "reason": "cheaper elsewhere"}}
    ]


@pytest.mark.asyncio
async def test_gated_call_refused_before_parking_is_a_denial_not_an_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    # A customer asking for a backorder: the host's pre-park role check refuses it, so no approval is requested.
    # The call still counts as a tool call (name and arguments) and the run reads as denied.
    from shared.guardrails.roles import requires_role

    @tool(name="place_backorder", description="Backorder", approval_mode="always_require")
    @requires_role("seller", "admin")
    async def place_backorder(product_id: str, quantity: int) -> dict:
        raise AssertionError("never executed")

    args = {"product_id": "p-1", "quantity": 5}
    call_response = ChatResponse(
        messages=[
            Message(
                role="assistant",
                contents=[Content.from_function_call(call_id="c1", name="place_backorder", arguments=args)],
            )
        ],
        response_id=str(uuid.uuid4()),
        finish_reason="tool_calls",
    )
    fake_agent = Agent(
        client=_ScriptedClient(call_response),
        instructions="test",
        name="inventory-fulfillment",
        tools=[place_backorder],
    )
    monkeypatch.setattr("inventory_fulfillment.agent.create_inventory_fulfillment_agent", lambda: fake_agent)

    outcome = await ProductionRunner("inventory-fulfillment", user_email="jack@example.com").run("backorder p-1")

    assert "not permitted to place backorders" in outcome.text
    assert outcome.hitl_requested is False
    assert outcome.denied is True
    assert outcome.parked_calls == []
    assert outcome.tool_calls == [{"tool_name": "place_backorder", "tool_input": args}]


@pytest.mark.asyncio
async def test_each_run_starts_with_a_fresh_per_run_token_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """The in-process loop must reset the per-run ceiling per case, or a long suite exhausts it."""
    from shared.guardrails.cost_budget_middleware import current_run_tokens, get_run_tokens

    seen: list[int] = []

    async def fake_attempt(self: ProductionRunner, user_input: str, history: list) -> object:
        seen.append(get_run_tokens())
        current_run_tokens.set(get_run_tokens() + 90_000)
        from evals.harness import RunOutcome

        return RunOutcome(text="ok")

    monkeypatch.setattr(ProductionRunner, "_run_specialist", fake_attempt)
    runner = ProductionRunner("product-discovery")
    current_run_tokens.set(95_000)
    await runner.run("one")
    await runner.run("two")
    assert seen == [0, 0]
