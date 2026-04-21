"""
Tests for Chapter 01 — Your First Agent.

Two modes:
- Unit tests use a canned BaseChatClient subclass — no LLM calls, run anywhere.
- Integration test hits the real LLM using keys from the repo-root .env.
  It is skipped when OPENAI_API_KEY (or Azure equivalent) is missing.
"""

from __future__ import annotations

import os
import pathlib
import sys
from collections.abc import Awaitable, Mapping, Sequence
from typing import Any

import pytest

# Bootstrap MAF + load repo-root .env before touching agent_framework.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from main import INSTRUCTIONS, ask, build_agent  # noqa: E402

from agent_framework import Agent, BaseChatClient, Message  # noqa: E402
from agent_framework._types import ChatResponse, ChatResponseUpdate, ResponseStream  # noqa: E402


class CannedChatClient(BaseChatClient):
    """Test-only chat client that returns canned responses and records inputs."""

    def __init__(self, *canned: str) -> None:
        super().__init__()
        self._responses = list(canned)
        self.calls: list[tuple[Sequence[Message], Mapping[str, Any]]] = []

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
            raise AssertionError("CannedChatClient ran out of responses")
        text = self._responses.pop(0)
        assistant = Message(role="assistant", contents=[text])

        async def _return() -> ChatResponse:
            return ChatResponse(messages=[assistant])

        return _return()


# ───────────────────── Unit tests (no LLM) ──────────────────────

def test_build_agent_uses_instructions() -> None:
    client = CannedChatClient("Paris.")
    agent = build_agent(client=client)
    assert isinstance(agent, Agent)
    # MAF stores system instructions inside default_options, not as a top-level attribute.
    assert agent.default_options["instructions"] == INSTRUCTIONS
    assert agent.name == "first-agent"


@pytest.mark.asyncio
async def test_ask_returns_canned_answer() -> None:
    client = CannedChatClient("Paris.")
    agent = build_agent(client=client)
    answer = await ask(agent, "What is the capital of France?")
    assert answer == "Paris."
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_system_instructions_reach_chat_client() -> None:
    client = CannedChatClient("Canberra.")
    agent = build_agent(client=client)
    await ask(agent, "What is the capital of Australia?")

    assert client.calls, "expected at least one call to the chat client"
    sent_messages, sent_options = client.calls[0]
    # MAF can carry instructions either in ChatOptions or as a system message in the list.
    options_instructions = sent_options.get("instructions", "") if isinstance(sent_options, Mapping) else ""
    system_texts = [m.text for m in sent_messages if str(m.role).lower() == "system"]
    combined = options_instructions + " " + " ".join(system_texts)
    assert INSTRUCTIONS in combined, (
        f"system instructions missing — options={options_instructions!r} messages={system_texts!r}"
    )


@pytest.mark.asyncio
async def test_user_question_reaches_chat_client() -> None:
    client = CannedChatClient("Ottawa.")
    agent = build_agent(client=client)
    await ask(agent, "What is the capital of Canada?")

    messages, _ = client.calls[0]
    sent = [m.text for m in messages if str(m.role).lower() == "user"]
    assert sent == ["What is the capital of Canada?"]


@pytest.mark.asyncio
async def test_run_out_of_canned_responses_raises() -> None:
    client = CannedChatClient()  # no canned responses
    agent = build_agent(client=client)
    with pytest.raises(AssertionError, match="ran out of responses"):
        await ask(agent, "nothing to say")


# ─────────────────── Integration test (hits LLM) ────────────────

def _llm_available() -> bool:
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider == "azure":
        key = os.environ.get("AZURE_OPENAI_KEY") or os.environ.get("AZURE_OPENAI_API_KEY")
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
        return bool(key and endpoint)
    key = os.environ.get("OPENAI_API_KEY", "")
    return bool(key) and not key.startswith("sk-your-")


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_answers_capital_of_france() -> None:
    """Hit the real LLM to prove the whole stack works end-to-end."""
    agent = build_agent()
    answer = await ask(agent, "What is the capital of France? Answer with the city name only.")
    assert "paris" in answer.lower(), f"expected Paris in answer, got: {answer!r}"
