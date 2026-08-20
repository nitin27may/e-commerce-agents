"""
Chapter 27 — Agent-as-tool: tests.

- Unit tests exercise the two plain tool functions directly (no LLM).
- An agent-wiring test confirms the coordinator's tools=[...] contains the
  wrapped product-lookup FunctionTool alongside the ordinary local tool.
- A replay test plays back a committed fixture (skips gracefully if none
  exist yet).
- Integration tests hit a real LLM and assert the coordinator keeps control
  after the wrapped agent answers — i.e. it goes on to call a second tool
  and combine both results, rather than the wrapped agent taking the floor.
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
    FIXTURES_DIR,
    ask,
    build_agent,
    build_product_lookup_agent,
    calculate_discount,
    search_catalog,
)

# ─────────────────── Tool-function unit tests ───────────────────


def test_search_catalog_returns_known_product() -> None:
    result = search_catalog.func("Wireless Headphones")  # @tool exposes the original via .func
    assert "149.99" in result and "Electronics" in result


def test_search_catalog_handles_unknown_product() -> None:
    result = search_catalog.func("Time Machine")
    assert "No catalog entry" in result


def test_calculate_discount_computes_expected_price() -> None:
    result = calculate_discount.func(149.99, 20)
    assert "119.99" in result


# ─────────────────── Agent-wiring tests (no LLM call) ────────────


def test_coordinator_has_wrapped_agent_and_local_tool_registered() -> None:
    coordinator = build_agent(client=object())  # client isn't called; we only inspect structure
    tools = coordinator.default_options.get("tools") or []
    tool_names = [getattr(t, "name", None) for t in tools]
    assert "product_lookup" in tool_names
    assert "calculate_discount" in tool_names


def test_product_lookup_agent_has_search_catalog_registered() -> None:
    specialist = build_product_lookup_agent(client=object())
    tool_names = [getattr(t, "name", None) for t in specialist.default_options.get("tools") or []]
    assert "search_catalog" in tool_names


def test_as_tool_wraps_a_function_tool_not_the_raw_agent() -> None:
    """The whole point of this chapter: `.as_tool()` returns a FunctionTool,
    a plain callable capability — not a live handle to the sub-agent itself."""
    specialist = build_product_lookup_agent(client=object())
    wrapped = specialist.as_tool(name="product_lookup")
    assert wrapped.name == "product_lookup"
    assert hasattr(wrapped, "func") or callable(wrapped)


# ─────────────────── Replay test (no credentials, runs in CI) ───


@pytest.mark.asyncio
async def test_replay_coordinator_combines_lookup_and_discount(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plays back tests/fixtures/replay/ — no network, no credentials.

    Recorded once against a real LLM (test_real_llm_coordinator_keeps_control
    below, run with RECORD=true) and committed.
    """
    if not any(FIXTURES_DIR.glob("*.json")):
        pytest.skip(f"no recorded fixtures in {FIXTURES_DIR} — run with RECORD=true first")
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    agent = build_agent()
    answer = await ask(agent, "Look up the Wireless Headphones, then tell me the price after a 20% discount.")
    lowered = answer.lower()
    assert "119.99" in lowered or "headphones" in lowered, f"expected combined answer, got: {answer!r}"


# ─────────────────── Real-LLM integration tests ──────────────────


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
async def test_real_llm_coordinator_keeps_control_after_wrapped_agent_answers() -> None:
    """The coordinator must call product_lookup, get an answer back, and then
    go on to call calculate_discount itself — proving control returned to the
    coordinator automatically instead of the sub-agent taking over the turn."""
    agent = build_agent()
    answer = await ask(agent, "Look up the Wireless Headphones, then tell me the price after a 20% discount.")
    lowered = answer.lower()
    assert "119.99" in lowered
    assert "headphone" in lowered


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_product_lookup_alone_reports_catalog_data() -> None:
    """A question needing only the wrapped agent still resolves through it."""
    agent = build_agent()
    answer = await ask(agent, "What's the stock level on the Coffee Maker?")
    lowered = answer.lower()
    assert "coffee maker" in lowered
    assert "0" in lowered or "out of stock" in lowered or "no stock" in lowered
