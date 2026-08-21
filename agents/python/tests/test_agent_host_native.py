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
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from shared.context import current_user_email  # noqa: E402
from shared.agent_host import (  # noqa: E402
    _history_as_maf_messages,
    _rehydrate_history_from_session,
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


# ─────────────────────── Session rehydration ─────────────


class _FakePool:
    def __init__(self, rows: list[dict] | None = None, raise_on_fetch: Exception | None = None) -> None:
        self._rows = rows or []
        self._raise = raise_on_fetch
        self.last_query: str | None = None
        self.last_args: tuple | None = None

    async def fetch(self, query: str, *args):
        self.last_query = query
        self.last_args = args
        if self._raise is not None:
            raise self._raise
        return self._rows


@pytest.mark.asyncio
async def test_rehydrate_returns_none_when_session_id_missing() -> None:
    assert await _rehydrate_history_from_session("") is None


@pytest.mark.asyncio
async def test_rehydrate_returns_none_without_a_caller_identity(monkeypatch, caplog) -> None:
    """No identity means no scoped read — and it must be logged, not silent.

    Rehydration is scoped to the caller's own conversation (#9), so an absent
    ``x-user-email`` can no longer produce a full history. The original bug was
    a silent short-circuit in this same function; this asserts the replacement
    one announces itself.
    """
    fake_pool = _FakePool(rows=[{"role": "user", "content": "hello"}])
    monkeypatch.setattr("shared.db.get_pool", lambda: fake_pool)
    current_user_email.set("")

    with caplog.at_level("INFO"):
        assert await _rehydrate_history_from_session("11111111-1111-1111-1111-111111111111") is None

    assert fake_pool.last_query is None, "it must not even reach the database"
    assert "rehydrate_skipped" in caplog.text


@pytest.mark.asyncio
async def test_rehydrate_scopes_the_query_to_the_caller(monkeypatch) -> None:
    """The session id arrives in a header, so the query must not trust it alone."""
    fake_pool = _FakePool(rows=[])
    monkeypatch.setattr("shared.db.get_pool", lambda: fake_pool)
    current_user_email.set("owner@example.com")

    await _rehydrate_history_from_session("11111111-1111-1111-1111-111111111111")

    assert "EXISTS" in (fake_pool.last_query or ""), "no ownership predicate in the query"
    assert fake_pool.last_args[-1] == "owner@example.com"


@pytest.mark.asyncio
async def test_rehydrate_reads_messages_by_conversation_id(monkeypatch) -> None:
    current_user_email.set("owner@example.com")
    rows = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi back"},
        {"role": "tool", "content": "ignored"},  # non-user/assistant filtered
        {"role": "user", "content": ""},  # empty content filtered
        {"role": "user", "content": "still here"},
    ]
    fake_pool = _FakePool(rows=rows)
    monkeypatch.setattr("shared.db.get_pool", lambda: fake_pool)

    history = await _rehydrate_history_from_session("11111111-1111-1111-1111-111111111111")

    assert history == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi back"},
        {"role": "user", "content": "still here"},
    ]
    # uses the $2 LIMIT parameter — no string interpolation.
    assert "LIMIT $2" in (fake_pool.last_query or "")
    assert fake_pool.last_args == ("11111111-1111-1111-1111-111111111111", 50, "owner@example.com")


@pytest.mark.asyncio
async def test_rehydrate_swallows_db_errors(monkeypatch) -> None:
    fake_pool = _FakePool(raise_on_fetch=RuntimeError("db down"))
    monkeypatch.setattr("shared.db.get_pool", lambda: fake_pool)
    # Identity set on purpose: without it the new #9 guard would return None
    # before the query ran, and this test would pass without ever reaching the
    # error path it exists to cover.
    current_user_email.set("owner@example.com")
    assert await _rehydrate_history_from_session("any-id") is None


@pytest.mark.asyncio
async def test_rehydrate_swallows_missing_pool(monkeypatch) -> None:
    def _boom():
        raise RuntimeError("pool not initialised")

    monkeypatch.setattr("shared.db.get_pool", _boom)
    current_user_email.set("owner@example.com")
    assert await _rehydrate_history_from_session("any-id") is None


# ─────────────────────── Native path (stubbed agent) ──────


class _FakeResponse:
    def __init__(self, text: str, additional_properties: dict | None = None) -> None:
        self.text = text
        self.additional_properties = additional_properties or {}


class _FakeStreamingUpdate:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeAgent:
    """Tiny stand-in exposing just the ``run`` signatures the helpers use."""

    def __init__(self, text: str = "stubbed-answer") -> None:
        self._text = text
        self.last_call_messages: list | None = None
        self.last_call_stream: bool | None = None
        self.last_options: dict | None = None

    def run(self, messages=None, *, stream: bool = False, options=None, **_kwargs):
        self.last_call_messages = list(messages or [])
        self.last_call_stream = stream
        self.last_options = options

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
async def test_run_agent_native_pins_temperature() -> None:
    """Every run must carry the configured temperature so identical queries
    produce consistent answers (provider default ~1.0 makes them diverge)."""
    from shared.config import settings

    agent = _FakeAgent("ok")
    await _run_agent_native(agent, "hi")
    assert agent.last_options is not None
    assert agent.last_options.get("temperature") == settings.LLM_TEMPERATURE


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
async def test_run_agent_native_fills_metadata_box_from_additional_properties() -> None:
    class _AgentWithMetadata:
        def run(self, messages=None, *, stream: bool = False, options=None, **_kwargs):
            async def _return():
                return _FakeResponse("ok", additional_properties={"grounding": {"verified": 1}})

            return _return()

    box: dict = {}
    await _run_agent_native(_AgentWithMetadata(), "hi", metadata_box=box)
    assert box == {"grounding": {"verified": 1}}


@pytest.mark.asyncio
async def test_run_agent_native_metadata_box_untouched_when_none_passed() -> None:
    # Must not raise when the caller doesn't care about metadata.
    agent = _FakeAgent("ok")
    text = await _run_agent_native(agent, "hi")
    assert text == "ok"


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
        def run(self, messages=None, *, stream: bool = False, options=None, **_kwargs):
            async def _gen():
                yield _FakeStreamingUpdate("")
                yield _FakeStreamingUpdate("real")
                yield _FakeStreamingUpdate(None)  # type: ignore[arg-type]

            return _gen()

    chunks = [c async for c in _run_agent_native_stream(_AgentWithEmptyDeltas(), "hi")]
    assert chunks == ["real"]


@pytest.mark.asyncio
async def test_run_agent_native_stream_fills_metadata_box_after_exhaustion() -> None:
    class _FakeResponseStream:
        """Minimal stand-in for MAF's ResponseStream: async-iterable plus a
        get_final_response() the helper calls once iteration completes."""

        def __init__(self, chunks: list[str], final: _FakeResponse) -> None:
            self._chunks = chunks
            self._final = final

        def __aiter__(self):
            return self._gen()

        async def _gen(self):
            for c in self._chunks:
                yield _FakeStreamingUpdate(c)

        async def get_final_response(self) -> _FakeResponse:
            return self._final

    class _AgentWithStreamingMetadata:
        def run(self, messages=None, *, stream: bool = False, options=None, **_kwargs):
            return _FakeResponseStream(
                ["hi"],
                _FakeResponse("hi", additional_properties={"grounding": {"verified": 2}}),
            )

    box: dict = {}
    chunks = [c async for c in _run_agent_native_stream(_AgentWithStreamingMetadata(), "hi", metadata_box=box)]
    assert chunks == ["hi"]
    assert box == {"grounding": {"verified": 2}}


@pytest.mark.asyncio
async def test_run_agent_native_stream_metadata_box_skipped_when_stream_has_no_finalizer() -> None:
    # A plain async generator (no get_final_response) must not raise —
    # covers every existing _FakeAgent-based test above, which return bare
    # generators rather than a real MAF ResponseStream.
    agent = _FakeAgent("ok")
    box: dict = {}
    chunks = [c async for c in _run_agent_native_stream(agent, "hi", metadata_box=box)]
    assert "".join(chunks) == "ok"
    assert box == {}


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
