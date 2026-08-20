"""
Chapter 23 — A2A Protocol: tests.

- Unit tests exercise the order-lookup function and the A2A transport
  (agent-card, /message:send, /message:stream) directly — no LLM involved,
  since the transport is the concept this chapter teaches.
- Integration tests hit the real LLM and assert it calls the specialist tool.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from main import (  # noqa: E402
    AGENT_CARD,
    FIXTURES_DIR,
    _lookup_order,
    ask,
    build_agent,
    call_order_specialist,
    demo_fetch_agent_card,
    demo_stream_call,
)

# ─────────────────── Order-lookup unit tests (no LLM, no HTTP) ─────


def test_lookup_order_returns_known_status() -> None:
    result = _lookup_order("What's the status of ORD-1001?")
    assert "Shipped" in result


def test_lookup_order_handles_unknown_order() -> None:
    result = _lookup_order("What's the status of ORD-9999?")
    assert "No order found" in result


def test_lookup_order_requires_order_id() -> None:
    result = _lookup_order("How's my order doing?")
    assert "No order id found" in result


def test_lookup_order_is_case_insensitive() -> None:
    assert _lookup_order("ord-1001") == _lookup_order("ORD-1001")


# ─────────────────── A2A transport unit tests (no LLM) ──────────────
# These exercise the real Starlette app through httpx's ASGITransport —
# real routing/JSON/SSE, no LLM, no network socket. See the README for why.


@pytest.mark.asyncio
async def test_agent_card_endpoint_returns_identity() -> None:
    card = await demo_fetch_agent_card()
    assert card == AGENT_CARD
    assert card["name"] == "order-lookup"


@pytest.mark.asyncio
async def test_call_order_specialist_tool_hits_message_send() -> None:
    # @tool exposes the original coroutine function via .func — same
    # unwrap pattern as Chapter 02's get_weather.func(...).
    result = await call_order_specialist.func("What's the status of ORD-1002?")
    assert "Processing" in result


@pytest.mark.asyncio
async def test_message_stream_emits_done_sentinel() -> None:
    chunks = await demo_stream_call("What's the status of ORD-1003?")
    assert len(chunks) == 1
    assert "Delivered" in chunks[0]


@pytest.mark.asyncio
async def test_message_stream_raises_on_error_sentinel() -> None:
    with pytest.raises(RuntimeError, match=r"\[ERROR"):
        await demo_stream_call("")


# ─────────────────── Agent wiring ────────────────────────────────


def test_agent_has_specialist_tool_registered() -> None:
    agent = build_agent(client=object())  # client isn't called; we only inspect structure
    tool_names = [getattr(t, "name", None) for t in agent.default_options.get("tools") or []]
    assert "call_order_specialist" in tool_names


# ─────────────────── Replay test (no credentials, runs in CI) ────


@pytest.mark.asyncio
async def test_replay_calls_order_specialist(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plays back tests/fixtures/replay/ — no network to a real LLM, no credentials.

    (The in-process A2A call to the local Starlette specialist still happens —
    that's not the LLM, it's the same local transport the unit tests above
    exercise directly.)

    Recorded once against a real LLM (test_real_llm_calls_order_specialist
    below, run with RECORD=true) and committed.
    """
    if not any(FIXTURES_DIR.glob("*.json")):
        pytest.skip(f"no recorded fixtures in {FIXTURES_DIR} — run with RECORD=true first")
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    agent = build_agent()
    answer = await ask(agent, "What's the status of order ORD-1001?")
    lowered = answer.lower()
    assert "shipped" in lowered or "2026-08-22" in lowered, f"expected order-status data in the answer, got: {answer!r}"


# ─────────────────── Real-LLM integration tests ────────────────


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
async def test_real_llm_calls_order_specialist() -> None:
    """The LLM should see the tool and use it when asked about an order."""
    agent = build_agent()
    answer = await ask(agent, "What's the status of order ORD-1001?")
    lowered = answer.lower()
    assert "shipped" in lowered or "2026-08-22" in lowered, f"expected order-status data in the answer, got: {answer!r}"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_skips_tool_for_unrelated_question() -> None:
    """For an unrelated question the order-lookup tool must NOT appear in the answer."""
    agent = build_agent()
    answer = await ask(agent, "What is the capital of France? Answer with only the city name.")
    assert "paris" in answer.lower()
    # Canned-order strings must not bleed into a non-order answer.
    assert "shipped" not in answer.lower()
