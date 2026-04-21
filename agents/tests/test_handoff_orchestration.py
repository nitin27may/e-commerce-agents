"""
Phase 7 Refactor 10 — Orchestrator Handoff workflow tests.

Covers the ``RemoteSpecialistChatClient`` adapter and the ``HandoffBuilder``
wiring in ``orchestrator/handoff.py``. HTTP calls are stubbed so the tests
never touch the network or the LLM — we only assert on the shape of the
A2A request, the ``ChatResponse`` shape, and the executors wired into the
built workflow.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from agent_framework import Agent, ChatResponse, Message

from orchestrator.handoff import (
    _load_registry,
    build_orchestrator_handoff_workflow,
    build_remote_specialist_agents,
)
from shared.remote_agent import RemoteSpecialistChatClient, make_remote_specialist_agent


def _stub_orchestrator() -> Agent:
    """Build an orchestrator-shaped Agent that never actually calls an LLM.

    ``build_orchestrator_handoff_workflow`` only needs an Agent instance
    with a name of ``orchestrator``; assembly doesn't invoke the client.
    Using a ``RemoteSpecialistChatClient`` here lets us side-step
    ``shared.factory`` validation in tests.
    """
    return Agent(
        client=RemoteSpecialistChatClient(name="orchestrator", url="http://local-stub"),
        name="orchestrator",
        description="Stub orchestrator used only for handoff wiring tests.",
        instructions="You are a test stub.",
    )


# ─────────────────────── Helpers ───────────────────────


class _StubTransport(httpx.AsyncBaseTransport):
    """Captures A2A requests so we can assert on headers and payload."""

    def __init__(self, reply: str = "ok from stub", status: int = 200) -> None:
        self.reply = reply
        self.status = status
        self.calls: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        body = json.dumps({"response": self.reply}).encode()
        return httpx.Response(self.status, content=body, request=request)


@pytest.fixture
def stub_transport(monkeypatch: pytest.MonkeyPatch) -> _StubTransport:
    """Patch httpx.AsyncClient so every call lands on our stub transport."""
    transport = _StubTransport()
    orig = httpx.AsyncClient

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return orig(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)
    return transport


# ─────────────────────── RemoteSpecialistChatClient ─────────────


@pytest.mark.asyncio
async def test_remote_client_posts_to_a2a_endpoint_and_returns_reply(stub_transport: _StubTransport) -> None:
    stub_transport.reply = "stub says hello"
    client = RemoteSpecialistChatClient(name="order-management", url="http://order:8082/a2a")

    response = await client.get_response([Message(role="user", contents=["hi"])])

    assert isinstance(response, ChatResponse)
    assert response.messages[0].text == "stub says hello"
    assert response.messages[0].author_name == "order-management"

    assert len(stub_transport.calls) == 1
    call = stub_transport.calls[0]
    assert call.url.path.endswith("/message:send")
    body = json.loads(call.content)
    assert body["message"] == "hi"


@pytest.mark.asyncio
async def test_remote_client_flattens_multi_message_prompt(stub_transport: _StubTransport) -> None:
    client = RemoteSpecialistChatClient(name="pricing-promotions", url="http://p:8083/a2a")
    await client.get_response(
        [
            Message(role="user", contents=["first turn"]),
            Message(role="assistant", contents=["intermediate"]),
            Message(role="user", contents=["follow up"]),
        ]
    )
    body = json.loads(stub_transport.calls[0].content)
    assert "first turn" in body["message"]
    assert "follow up" in body["message"]


@pytest.mark.asyncio
async def test_remote_client_streaming_yields_single_chunk(stub_transport: _StubTransport) -> None:
    stub_transport.reply = "streamed reply"
    client = RemoteSpecialistChatClient(name="review-sentiment", url="http://r:8084/a2a")

    pieces = []
    async for update in client.get_response([Message(role="user", contents=["hi"])], stream=True):
        pieces.append(update.text)
    assert "".join(pieces) == "streamed reply"


@pytest.mark.asyncio
async def test_remote_client_surfaces_http_error(stub_transport: _StubTransport) -> None:
    stub_transport.status = 500
    stub_transport.reply = "boom"
    client = RemoteSpecialistChatClient(name="inventory-fulfillment", url="http://i:8085/a2a")

    with pytest.raises(httpx.HTTPStatusError):
        await client.get_response([Message(role="user", contents=["hi"])])


# ─────────────────────── Handoff workflow wiring ───────────────


CANONICAL_REGISTRY: dict[str, str] = {
    "product-discovery": "http://pd:8081/a2a",
    "order-management": "http://om:8082/a2a",
    "pricing-promotions": "http://pp:8083/a2a",
    "review-sentiment": "http://rs:8084/a2a",
    "inventory-fulfillment": "http://if:8085/a2a",
}


@pytest.fixture
def registry_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Pin ``settings.AGENT_REGISTRY`` so ``_load_registry`` produces the canonical mesh."""
    from shared import config as config_mod

    monkeypatch.setattr(config_mod.settings, "AGENT_REGISTRY", json.dumps(CANONICAL_REGISTRY))
    # Also patch the handoff module's imported alias, in case the binding
    # was captured at import time.
    import orchestrator.handoff as handoff_mod

    monkeypatch.setattr(handoff_mod.settings, "AGENT_REGISTRY", json.dumps(CANONICAL_REGISTRY))
    return CANONICAL_REGISTRY


def test_load_registry_parses_agent_registry(registry_env: dict[str, str]) -> None:
    assert _load_registry() == registry_env


def test_build_remote_specialist_agents_covers_registry(registry_env: dict[str, str]) -> None:
    agents = build_remote_specialist_agents(registry=registry_env)
    assert {a.name for a in agents} == set(registry_env.keys())
    for agent in agents:
        assert isinstance(agent, Agent)


def test_handoff_workflow_wires_orchestrator_plus_specialists(registry_env: dict[str, str]) -> None:
    specialists = build_remote_specialist_agents(registry=registry_env)
    workflow = build_orchestrator_handoff_workflow(
        orchestrator=_stub_orchestrator(),
        specialists=specialists,
        autonomous_mode=True,
    )
    ids = {getattr(e, "id", None) for e in workflow.get_executors_list()}
    assert "orchestrator" in ids
    assert set(registry_env.keys()) <= ids


def test_handoff_workflow_respects_autonomous_mode_flag(registry_env: dict[str, str]) -> None:
    """Passing autonomous_mode=False must not raise and must still build."""
    specialists = build_remote_specialist_agents(registry=registry_env)
    workflow = build_orchestrator_handoff_workflow(
        orchestrator=_stub_orchestrator(),
        specialists=specialists,
        autonomous_mode=False,
    )
    assert workflow is not None


def test_handoff_workflow_with_empty_registry() -> None:
    """With no remote specialists configured the orchestrator should still build alone."""
    workflow = build_orchestrator_handoff_workflow(
        orchestrator=_stub_orchestrator(),
        specialists=[],
    )
    ids = {getattr(e, "id", None) for e in workflow.get_executors_list()}
    assert "orchestrator" in ids


# ─────────────────────── make_remote_specialist_agent smoke ────


def test_make_remote_specialist_agent_returns_configured_agent() -> None:
    agent = make_remote_specialist_agent("order-management", "http://order:8082/a2a")
    assert isinstance(agent, Agent)
    assert agent.name == "order-management"
