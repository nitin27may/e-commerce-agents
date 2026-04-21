"""
Phase 7 Refactor 03 — MAF-native execution path tests.

Verifies:

- ``_history_as_maf_messages`` builds the right MAF ``Message`` list from
  the A2A history payload + current user message.
- ``_run_agent_native`` returns ``response.text`` from ``agent.run``.
- ``_run_agent_native_stream`` yields the text chunks from streaming
  updates.
- Real-LLM integration: running a live ``ChatClientAgent`` through the
  native helpers against Azure OpenAI produces a sensible answer.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
from types import SimpleNamespace

import pytest

# Load the repo-root .env into os.environ so the integration tests see the
# live Azure / OpenAI credentials the rest of the suite uses.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from shared.agent_host import (  # noqa: E402
    _history_as_maf_messages,
    _run_agent_native,
    _run_agent_native_stream,
)


# ─────────────────────── Pure helpers ───────────────────────


def test_history_builder_wraps_current_message_last() -> None:
    msgs = _history_as_maf_messages(
        history=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        user_message="latest",
    )
    assert [str(m.role).lower() for m in msgs] == ["user", "assistant", "user"]
    assert msgs[-1].text == "latest"


def test_history_builder_accepts_none_history() -> None:
    msgs = _history_as_maf_messages(history=None, user_message="only")
    assert len(msgs) == 1
    assert msgs[0].text == "only"


def test_history_builder_skips_other_roles_and_empty_content() -> None:
    """System/tool messages and empty payloads get filtered out."""
    msgs = _history_as_maf_messages(
        history=[
            {"role": "system", "content": "ignored"},
            {"role": "user", "content": ""},
            {"role": "user", "content": "kept"},
            {"role": "tool", "content": "ignored"},
        ],
        user_message="final",
    )
    assert [m.text for m in msgs] == ["kept", "final"]


# ─────────────────────── Native path (stubbed agent) ──────


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeStreamingUpdate:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeAgent:
    """Tiny stand-in exposing just the ``run`` signatures the helpers use."""

    def __init__(self, text: str = "stubbed-answer") -> None:
        self._text = text
        self.last_call_messages: list | None = None
        self.last_call_stream: bool | None = None

    def run(self, messages=None, *, stream: bool = False):
        self.last_call_messages = list(messages or [])
        self.last_call_stream = stream

        if stream:
            async def _gen():
                # Two chunks so tests can see incremental yielding.
                for piece in [self._text[: len(self._text) // 2], self._text[len(self._text) // 2 :]]:
                    yield _FakeStreamingUpdate(piece)
            return _gen()

        async def _return():
            return _FakeResponse(self._text)
        return _return()


@pytest.mark.asyncio
async def test_run_agent_native_returns_response_text() -> None:
    agent = _FakeAgent("Paris is the capital of France.")
    text = await _run_agent_native(agent, "What's the capital of France?")
    assert text == "Paris is the capital of France."


@pytest.mark.asyncio
async def test_run_agent_native_threads_history_into_messages() -> None:
    agent = _FakeAgent("ok")
    await _run_agent_native(
        agent,
        "latest",
        history=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
    )
    assert agent.last_call_stream is False
    assert agent.last_call_messages is not None
    assert [m.text for m in agent.last_call_messages] == ["hi", "hello", "latest"]


@pytest.mark.asyncio
async def test_run_agent_native_stream_yields_all_chunks() -> None:
    agent = _FakeAgent("Paris is the capital of France.")
    pieces = [chunk async for chunk in _run_agent_native_stream(agent, "hi")]
    assert "".join(pieces) == "Paris is the capital of France."
    assert agent.last_call_stream is True


@pytest.mark.asyncio
async def test_run_agent_native_stream_skips_empty_updates() -> None:
    """Some providers emit empty delta events; the helper must filter them."""

    class _AgentWithEmptyDeltas:
        def run(self, messages=None, *, stream: bool = False):
            async def _gen():
                yield _FakeStreamingUpdate("")
                yield _FakeStreamingUpdate("real")
                yield _FakeStreamingUpdate(None)  # type: ignore[arg-type]
            return _gen()

    chunks = [c async for c in _run_agent_native_stream(_AgentWithEmptyDeltas(), "hi")]
    assert chunks == ["real"]


# ─────────────────────── Live LLM parity ───────────────────


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
async def test_native_path_against_real_llm() -> None:
    """Proves the native path produces a sensible answer against Azure/OpenAI."""
    from agent_framework import Agent
    from shared.factory import get_chat_client

    agent = Agent(
        get_chat_client(),
        instructions="You are a concise geography assistant. Keep answers to one short sentence.",
        name="native-test-agent",
    )
    answer = await _run_agent_native(agent, "What is the capital of France?")
    assert "paris" in answer.lower(), f"expected Paris in answer, got {answer!r}"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_native_path_streams_real_llm_output() -> None:
    from agent_framework import Agent
    from shared.factory import get_chat_client

    agent = Agent(
        get_chat_client(),
        instructions="You are a concise assistant. Keep answers to one short sentence.",
        name="native-stream-agent",
    )
    pieces = [chunk async for chunk in _run_agent_native_stream(agent, "Say 'hi'.")]
    assert pieces, "expected at least one streaming update"
    combined = "".join(pieces).lower()
    assert "hi" in combined
