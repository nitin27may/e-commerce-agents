"""
Chapter 08 — MCP Tools: tests.

- Unit test: the weather MCP server returns canned data when exercised directly.
- Integration: end-to-end agent run calls the tool via MCP and includes the
  canned forecast in the final answer.
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
from main import build_mcp_tool, run  # noqa: E402
from weather_mcp_server import get_weather  # noqa: E402


def test_weather_tool_returns_canned_data() -> None:
    # FastMCP wraps the function; access the original via .fn
    fn = getattr(get_weather, "fn", get_weather)
    assert "Sunny" in fn("Paris")
    assert "No weather data" in fn("Atlantis")


def test_weather_tool_is_case_insensitive() -> None:
    fn = getattr(get_weather, "fn", get_weather)
    assert fn("paris") == fn("PARIS")


def test_build_mcp_tool_configures_subprocess() -> None:
    tool = build_mcp_tool()
    assert tool.name == "weather-mcp"


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
async def test_real_llm_calls_mcp_weather_tool() -> None:
    answer = await run("What's the weather in Paris?")
    lowered = answer.lower()
    assert "sunny" in lowered or "18" in lowered, (
        f"expected MCP tool data in answer, got: {answer!r}"
    )


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_skips_mcp_tool_for_unrelated_question() -> None:
    answer = await run("What is the capital of France? Answer with only the city.")
    assert "paris" in answer.lower()
    # Canned weather strings should not appear in non-weather answers.
    assert "sunny, 18" not in answer.lower()
