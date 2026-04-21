"""
Chapter 13 — Concurrent Orchestration: tests.

Integration-only — Concurrent fires three real LLM calls. We also assert
wall-clock behavior to confirm they actually ran in parallel, not serially.
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
from main import analyze, build_workflow  # noqa: E402


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
async def test_real_llm_all_three_agents_respond() -> None:
    per_agent, _ = await analyze("ultrasonic pet collar")
    assert "researcher" in per_agent
    assert "marketer" in per_agent
    assert "legal" in per_agent
    assert all(per_agent[name] for name in ("researcher", "marketer", "legal"))


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_runs_in_parallel_not_serial() -> None:
    """Three LLM calls in parallel must finish faster than a serial baseline."""
    _, elapsed = await analyze("subscription box for rare herbal teas")
    # Each call is ~1–3s. If they ran serially, we'd expect > 3s easily.
    # Parallel should finish well under 6s on normal networks.
    assert elapsed < 6.0, f"expected parallel execution (<6s), got {elapsed:.2f}s"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_perspectives_differ_between_agents() -> None:
    per_agent, _ = await analyze("AI-powered meal planner")
    # Three distinct perspectives should produce three different strings.
    r, m, l = per_agent["researcher"], per_agent["marketer"], per_agent["legal"]
    assert r != m
    assert m != l
    assert r != l
