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
                contents=[Content.from_function_call(
                    call_id="c1", name="call_specialist_agent",
                    arguments={"agent_name": "product-discovery", "message": "hi"},
                )],
            )
        ],
        response_id=str(uuid.uuid4()),
        finish_reason="tool_calls",
    )
    from shared.agent_observability import STEP_MIDDLEWARE

    fake = _ScriptedClient(call_response, _text_response("Found some headphones."))
    fake_agent = Agent(
        client=fake, instructions="test", name="orchestrator",
        tools=[call_specialist_agent], middleware=STEP_MIDDLEWARE,
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
    monkeypatch.setattr(
        "product_discovery.agent.create_product_discovery_agent", lambda: fake_agent
    )

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
