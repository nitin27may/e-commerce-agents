"""
Chapter 02 — Adding Tools: tests.

- Unit tests exercise the tool function directly (no LLM).
- Integration tests hit the real LLM and assert tool-call behavior.
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
from main import ask, build_agent, get_weather  # noqa: E402


# ─────────────────── Tool-function unit tests ──────────────────

def test_weather_tool_returns_canned_data() -> None:
    result = get_weather.func("Paris")  # @tool exposes the original via __wrapped__
    assert "Sunny" in result and "18" in result


def test_weather_tool_handles_unknown_city() -> None:
    result = get_weather.func("Atlantis")
    assert "No weather data" in result


def test_weather_tool_is_case_insensitive() -> None:
    assert get_weather.func("paris") == get_weather.func("PARIS")


def test_agent_has_weather_tool_registered() -> None:
    agent = build_agent(client=object())  # client isn't called; we only inspect structure
    tool_names = [getattr(t, "name", None) for t in agent.default_options.get("tools") or []]
    assert "get_weather" in tool_names


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
async def test_real_llm_invokes_weather_tool() -> None:
    """The LLM should see the tool and use it when asked about weather."""
    agent = build_agent()
    answer = await ask(agent, "What's the weather in Paris?")
    # The canned response seeds "Sunny" / "18" — if the LLM called the tool, one will appear.
    lowered = answer.lower()
    assert "sunny" in lowered or "18" in lowered, (
        f"expected weather-tool data in the answer, got: {answer!r}"
    )


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_skips_tool_for_unrelated_question() -> None:
    """For geography questions the weather tool must NOT appear in the answer."""
    agent = build_agent()
    answer = await ask(agent, "What is the capital of France? Answer with only the city name.")
    assert "paris" in answer.lower()
    # Canned-weather strings must not bleed into a non-weather answer.
    assert "sunny" not in answer.lower()
