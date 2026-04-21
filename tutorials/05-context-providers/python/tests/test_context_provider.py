"""
Chapter 05 — Context Providers: tests.

Unit tests drive the provider directly and capture what reaches the LLM.
Integration tests prove real Azure OpenAI answers correctly with injected context.
"""

from __future__ import annotations

import os
import pathlib
import sys
from collections.abc import Awaitable, Mapping, Sequence
from typing import Any

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from main import INSTRUCTIONS, UserProfileProvider, ask, build_agent  # noqa: E402

from agent_framework import BaseChatClient, Content, Message  # noqa: E402
from agent_framework._types import ChatResponse, ChatResponseUpdate, ResponseStream  # noqa: E402


class CannedChatClient(BaseChatClient):
    """Chat client that records options + messages so we can assert what the provider injected."""

    def __init__(self, *canned: str) -> None:
        super().__init__()
        self._responses = list(canned)
        self.calls: list[tuple[list[Message], dict[str, Any]]] = []

    def _inner_get_response(  # type: ignore[override]
        self,
        *,
        messages: Sequence[Message],
        stream: bool,
        options: Mapping[str, Any],
        **kwargs: Any,
    ) -> Awaitable[ChatResponse] | ResponseStream[ChatResponseUpdate, ChatResponse]:
        self.calls.append((list(messages), dict(options)))
        if not self._responses:
            raise AssertionError("no canned responses")
        text = self._responses.pop(0)

        async def _return() -> ChatResponse:
            return ChatResponse(
                messages=[Message(role="assistant", contents=[Content(type="text", text=text)])]
            )

        return _return()


# ─────────── Unit tests (no LLM) ───────────

@pytest.mark.asyncio
async def test_provider_injects_user_into_instructions() -> None:
    client = CannedChatClient("hi Alice")
    provider = UserProfileProvider(email="alice@example.com", name="Alice", loyalty_tier="gold")
    agent = build_agent(provider, client=client)

    await ask(agent, "hello")

    # Exactly one chat call.
    assert len(client.calls) == 1
    _, options = client.calls[0]
    instructions = options.get("instructions") or ""
    assert INSTRUCTIONS in instructions
    assert "Alice" in instructions
    assert "gold" in instructions
    assert "alice@example.com" in instructions


@pytest.mark.asyncio
async def test_provider_populates_state_dict() -> None:
    """The provider's state dict entry should carry structured user data for tools."""
    provider = UserProfileProvider(email="bob@example.com", name="Bob")
    state: dict[str, Any] = {}

    class FakeContext:
        def __init__(self) -> None:
            self.instructions: list[tuple[str, str]] = []

        def extend_instructions(self, source_id: str, text: str | Sequence[str]) -> None:
            if isinstance(text, str):
                self.instructions.append((source_id, text))

    ctx = FakeContext()
    await provider.before_run(agent=None, session=None, context=ctx, state=state)  # type: ignore[arg-type]

    assert state["user"]["email"] == "bob@example.com"
    assert state["user"]["name"] == "Bob"
    assert any("Bob" in text for _, text in ctx.instructions)


@pytest.mark.asyncio
async def test_multiple_users_see_different_context() -> None:
    """A fresh provider + agent per user must never leak context between them."""
    alice_client = CannedChatClient("hi alice")
    alice = build_agent(
        UserProfileProvider(email="alice@example.com", name="Alice", loyalty_tier="gold"),
        client=alice_client,
    )
    await ask(alice, "hello")

    bob_client = CannedChatClient("hi bob")
    bob = build_agent(
        UserProfileProvider(email="bob@example.com", name="Bob", loyalty_tier="silver"),
        client=bob_client,
    )
    await ask(bob, "hello")

    alice_instructions = alice_client.calls[0][1].get("instructions", "")
    bob_instructions = bob_client.calls[0][1].get("instructions", "")

    assert "Alice" in alice_instructions and "Bob" not in alice_instructions
    assert "Bob" in bob_instructions and "Alice" not in bob_instructions


# ─────────── Integration (real LLM) ───────────

def _llm_available() -> bool:
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider == "azure":
        return bool(
            os.environ.get("AZURE_OPENAI_ENDPOINT")
            and (os.environ.get("AZURE_OPENAI_KEY") or os.environ.get("AZURE_OPENAI_API_KEY"))
        )
    key = os.environ.get("OPENAI_API_KEY", "")
    return bool(key) and not key.startswith("sk-your-")


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_uses_injected_name() -> None:
    agent = build_agent(UserProfileProvider(email="alice@example.com", name="Alice", loyalty_tier="gold"))
    answer = await ask(agent, "Greet me by name and tell me my loyalty tier.")
    lowered = answer.lower()
    assert "alice" in lowered, f"expected 'alice' in answer, got: {answer!r}"
    assert "gold" in lowered, f"expected 'gold' tier in answer, got: {answer!r}"
