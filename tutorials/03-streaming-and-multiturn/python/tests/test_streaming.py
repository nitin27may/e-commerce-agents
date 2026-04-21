"""
Chapter 03 — Streaming and Multi-turn: tests.

Unit tests use a streaming-capable CannedChatClient that yields text chunks
one at a time; integration tests hit the real LLM.
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
from main import build_agent, chat, stream_answer  # noqa: E402

from agent_framework import BaseChatClient, Content, Message  # noqa: E402
from agent_framework._types import ChatResponse, ChatResponseUpdate, ResponseStream  # noqa: E402


class StreamingCannedClient(BaseChatClient):
    """Yields each canned response as 3 text chunks so tests can assert on streaming."""

    def __init__(self, *canned: str) -> None:
        super().__init__()
        self._responses = list(canned)
        self.call_count = 0
        self.conversation_lengths: list[int] = []

    def _inner_get_response(  # type: ignore[override]
        self,
        *,
        messages: Sequence[Message],
        stream: bool,
        options: Mapping[str, Any],
        **kwargs: Any,
    ) -> Awaitable[ChatResponse] | ResponseStream[ChatResponseUpdate, ChatResponse]:
        self.call_count += 1
        self.conversation_lengths.append(len(list(messages)))
        if not self._responses:
            raise AssertionError("StreamingCannedClient ran out of responses")
        text = self._responses.pop(0)

        if stream:
            parts = _split_in_three(text)

            async def _gen():
                for p in parts:
                    yield ChatResponseUpdate(contents=[Content(type="text", text=p)])

            return ResponseStream(
                _gen(),
                finalizer=lambda updates: ChatResponse(
                    messages=[
                        Message(
                            role="assistant",
                            contents=[Content(type="text", text="".join(u.text for u in updates if u.text))],
                        )
                    ]
                ),
            )

        async def _return() -> ChatResponse:
            return ChatResponse(
                messages=[Message(role="assistant", contents=[Content(type="text", text=text)])]
            )

        return _return()


def _split_in_three(s: str) -> list[str]:
    if len(s) < 3:
        return [s]
    a = len(s) // 3
    b = 2 * len(s) // 3
    return [s[:a], s[a:b], s[b:]]


# ─────────── Unit tests (stubbed streaming) ───────────

@pytest.mark.asyncio
async def test_stream_yields_multiple_chunks() -> None:
    client = StreamingCannedClient("Hello world, this is a longer response.")
    agent = build_agent(client=client)
    session = agent.create_session()
    chunks = await stream_answer(agent, "hi", session)
    # Three non-empty chunks expected from our 3-way split.
    assert len([c for c in chunks if c]) >= 2


@pytest.mark.asyncio
async def test_multiturn_reuses_session() -> None:
    client = StreamingCannedClient("First answer", "Second answer")
    agent = build_agent(client=client)
    await chat(agent, ["First question?", "Follow-up?"])
    # Two turns: second turn's conversation should be longer than the first.
    assert client.call_count == 2
    assert client.conversation_lengths[1] > client.conversation_lengths[0]


@pytest.mark.asyncio
async def test_streamed_chunks_combine_to_full_text() -> None:
    client = StreamingCannedClient("abcdefghij")
    agent = build_agent(client=client)
    session = agent.create_session()
    chunks = await stream_answer(agent, "hi", session)
    assert "".join(chunks) == "abcdefghij"


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
async def test_real_llm_multiturn_preserves_context() -> None:
    """Second turn must be able to resolve 'it' to Python from turn 1."""
    agent = build_agent()
    per_turn = await chat(agent, [
        "What is Python in one line?",
        "What year was it first released? Answer with the year only.",
    ])
    second_answer = "".join(per_turn[1])
    assert "1991" in second_answer, f"expected 1991 in follow-up, got: {second_answer!r}"
