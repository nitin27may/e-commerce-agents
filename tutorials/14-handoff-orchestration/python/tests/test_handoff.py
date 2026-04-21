"""
Chapter 14 — Handoff Orchestration: tests.

Integration-only — the handoff mesh needs real LLMs to decide routing.
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
from main import ask, build_workflow  # noqa: E402


def _llm_available() -> bool:
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider == "azure":
        return bool(
            os.environ.get("AZURE_OPENAI_ENDPOINT")
            and (os.environ.get("AZURE_OPENAI_KEY") or os.environ.get("AZURE_OPENAI_API_KEY"))
        )
    key = os.environ.get("OPENAI_API_KEY", "")
    return bool(key) and not key.startswith("sk-your-")


def test_workflow_builds() -> None:
    assert build_workflow() is not None


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_routes_math_to_math_agent() -> None:
    participants, answer = await ask("What is 37 * 42?")
    assert "math" in participants, f"math question should reach math agent, got: {participants}"
    assert "1554" in answer.replace(",", "") or "1,554" in answer


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_routes_history_to_history_agent() -> None:
    participants, answer = await ask("When did World War 2 end? Answer with the year only.")
    assert "history" in participants, f"history question should reach history agent, got: {participants}"
    assert "1945" in answer


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_routing_diverges_between_domains() -> None:
    math_routing, _ = await ask("What is 100 / 4?")
    history_routing, _ = await ask("Who was the first president of the United States?")
    # Different domains should route to different specialists.
    assert set(math_routing) != set(history_routing)
