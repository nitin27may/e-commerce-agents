"""Shared scaffolding for the A2A tests: a real MAF agent behind a real specialist app, with a scripted model.

Only the model is scripted (no key is available or allowed): a chat client that asks for one tool call
and then answers in two chunks and reports usage. Everything else is the production path: ``Agent``'s
tool loop, the step recorder, ``create_agent_app`` and ``AgentAuthMiddleware``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any

import pytest
from agent_framework import (
    Agent,
    BaseChatClient,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    FunctionInvocationLayer,
    tool,
)

from shared.agent_host import create_agent_app
from shared.agent_observability import STEP_MIDDLEWARE
from shared.auth import AgentAuthMiddleware
from shared.config import settings
from shared.context import current_user_email
from shared.replay_client import ReplayFixtureMissingError

SECRET = "conformance-secret"
USER = "alice@example.com"
SESSION = "6f1c0a5e-2d3b-4c7e-9a10-1b2c3d4e5f60"
ANSWER = "Hello world"
# The same wire shape the legacy `usage` field/frame carries (shared.run_usage.to_wire_usage).
WIRE_USAGE = {"input_token_count": 5, "output_token_count": 2, "cached_input_token_count": 0, "model": "gpt-4.1"}


# ─────────────────────── a scripted model and a real agent ───────────────────────


class ScriptedModel(FunctionInvocationLayer, BaseChatClient):
    """Asks for the ``lookup`` tool once, then answers ``ANSWER`` in two chunks with usage.

    ``mode`` switches the behaviour: ``boom`` raises an error whose text must never reach a caller,
    ``miss`` raises a replay-fixture miss.
    """

    OTEL_PROVIDER_NAME = "scripted"

    def __init__(self, mode: str = "ok") -> None:
        super().__init__()
        self.mode = mode
        self.seen: list[list[tuple[str, str]]] = []

    def _inner_get_response(self, *, messages: Any, stream: bool, options: Any = None, **_: Any) -> Any:
        self.seen.append([(str(m.role), m.text or "") for m in messages])
        has_result = any(c.type == "function_result" for m in messages for c in m.contents)

        async def _updates() -> AsyncIterator[ChatResponseUpdate]:
            if self.mode == "boom":
                raise RuntimeError('relation "secret_table" does not exist')
            if self.mode == "miss":
                raise ReplayFixtureMissingError("no fixture for hash abc123")
            if not has_result:
                call = Content.from_function_call(call_id="c1", name="lookup", arguments='{"sku": "XM5"}')
                yield ChatResponseUpdate(role="assistant", contents=[call])
                return
            yield ChatResponseUpdate(role="assistant", contents=[Content.from_text(text="Hello ")])
            yield ChatResponseUpdate(role="assistant", contents=[Content.from_text(text="world")])
            yield ChatResponseUpdate(
                role="assistant", contents=[Content.from_usage({"input_token_count": 5, "output_token_count": 2})]
            )

        if stream:
            return self._build_response_stream(_updates())

        async def _one() -> ChatResponse:
            updates = [u async for u in _updates()]
            return ChatResponse.from_updates(updates)

        return _one()


@tool(name="lookup", description="Look up the current price of a product by SKU.")
async def lookup(sku: Annotated[str, "the product SKU"]) -> str:
    # Reads the identity the middleware stamped: proves it reached the executor's task.
    return f"{sku}=79.99 for {current_user_email.get()}"


@tool(name="stock", description="Check stock for a SKU.")
async def stock(sku: Annotated[str, "the product SKU"]) -> str:
    return "in stock"


def build_app(mode: str = "ok") -> tuple[Any, ScriptedModel]:
    model = ScriptedModel(mode)
    agent = Agent(
        client=model,
        name="product-discovery",
        instructions="You are a test specialist.",
        tools=[lookup, stock],
        middleware=STEP_MIDDLEWARE,
    )
    app = create_agent_app(
        agent=agent,
        agent_name="product-discovery",
        port=8081,
        description="Conformance test specialist.",
    )
    app.add_middleware(AgentAuthMiddleware, agent_name="product-discovery")
    return app, model


def apply_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "AGENT_SHARED_SECRET", SECRET, raising=False)
    monkeypatch.setattr(settings, "AUTH_MODE", "local", raising=False)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai", raising=False)


def headers(**extra: str) -> dict[str, str]:
    return {"x-agent-secret": SECRET, "x-user-email": USER, "x-user-role": "customer", **extra}
