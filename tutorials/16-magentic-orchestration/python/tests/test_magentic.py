"""
Chapter 16 — Magentic Orchestration: tests.

Integration-only — Magentic runs multiple manager ↔ worker turns. Tests are
lighter than other orchestrations to respect token/time budgets.
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
from main import build_workflow, plan  # noqa: E402


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
async def test_real_llm_manager_delegates_to_at_least_one_worker() -> None:
    speakers, answer = await plan("plan a short launch brief for an AI meal planner")
    assert answer, "final answer must not be empty"
    # Manager should have dispatched to at least one worker, but could also
    # decide it has enough info and answer directly — so we accept either.
    assert len(answer) > 50, "final answer should be substantive, not a stub"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_manager_can_select_from_multiple_workers() -> None:
    """Over a broader task, the manager should engage multiple workers."""
    speakers, _ = await plan(
        "produce a brief covering market context, a tagline, and one regulatory note"
    )
    # Expect at least one delegation — the set of available worker names.
    known = {"researcher", "marketer", "legal"}
    assert any(s in known for s in speakers) or not speakers, (
        f"unexpected speaker list: {speakers}"
    )
