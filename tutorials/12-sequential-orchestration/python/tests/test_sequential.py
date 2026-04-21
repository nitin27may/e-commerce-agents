"""
Chapter 12 — Sequential Orchestration: tests.

Integration-only — the Sequential pipeline invokes three real LLM calls per run.
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
from main import build_workflow, run  # noqa: E402


def _llm_available() -> bool:
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider == "azure":
        return bool(
            os.environ.get("AZURE_OPENAI_ENDPOINT")
            and (os.environ.get("AZURE_OPENAI_KEY") or os.environ.get("AZURE_OPENAI_API_KEY"))
        )
    key = os.environ.get("OPENAI_API_KEY", "")
    return bool(key) and not key.startswith("sk-your-")


def test_workflow_builds_with_three_participants() -> None:
    workflow = build_workflow()
    assert workflow is not None


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_runs_all_three_agents() -> None:
    writer_out, reviewer_out, final = await run("Why sleep matters")
    assert writer_out, "writer must produce an output"
    assert reviewer_out, "reviewer must produce an output"
    assert final, "finalizer must produce an output"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_writer_drafts_and_reviewer_critiques() -> None:
    writer_out, reviewer_out, _ = await run("Benefits of learning Python")
    # Writer should produce multiple sentences (at least one period).
    assert "." in writer_out
    # Reviewer should reference the critique concepts (strength/weakness framing).
    lowered = reviewer_out.lower()
    assert any(k in lowered for k in ("strength", "weakness", "could", "however", "but", "improve"))


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_outputs_differ_between_agents() -> None:
    w, r, f = await run("The importance of exercise")
    # Three distinct outputs — no accidental loopback.
    assert w != r
    assert r != f
    assert w != f
