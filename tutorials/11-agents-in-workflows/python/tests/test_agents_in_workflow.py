"""
Chapter 11 — Agents in Workflows: tests.

Integration-only — the translator chain invokes real Azure OpenAI twice
per run (English→French, French→Spanish). Workflow wiring is validated
in the tests so we know the graph is correctly assembled before hitting
the LLM.
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
from main import build_workflow, translate  # noqa: E402


def test_workflow_has_four_executors_including_two_agents() -> None:
    workflow = build_workflow()
    ids = {getattr(e, "id", None) for e in workflow.get_executors_list()}
    assert {"input-adapter", "en-to-fr", "fr-to-es", "output-adapter"} <= ids


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
async def test_real_llm_translates_hello_to_spanish() -> None:
    result = (await translate("Hello, how are you?")).lower()
    # Spanish equivalent is "hola, ¿cómo estás?" — accept either noun.
    assert "hola" in result, f"expected Spanish in final output, got: {result!r}"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_chain_fires_both_agent_executors() -> None:
    """Track which agent executors emit output events during the run."""
    from main import build_workflow

    workflow = build_workflow()
    invoked: list[str] = []
    async for event in workflow.run("Good morning", stream=True):
        if getattr(event, "type", None) == "executor_completed":
            invoked.append(getattr(event, "executor_id", ""))
    # Both the French and Spanish translators must have completed.
    assert "en-to-fr" in invoked
    assert "fr-to-es" in invoked
    assert invoked.index("en-to-fr") < invoked.index("fr-to-es")


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_output_contains_spanish_markers() -> None:
    result = (await translate("The sun is shining")).lower()
    # Any of these common Spanish words likely appear.
    assert any(word in result for word in ("el ", "la ", "está", "sol"))
