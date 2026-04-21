"""
Chapter 15 — Group Chat Orchestration: tests.

Integration-only — three real LLM calls per run.
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


def test_workflow_builds() -> None:
    assert build_workflow() is not None


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_speakers_in_round_robin_order() -> None:
    turns = await run("slogan for a coffee shop")
    speakers = [s for s, _ in turns]
    assert "writer" in speakers
    assert "critic" in speakers
    assert "editor" in speakers
    # Round-robin selection: writer must speak before critic before editor.
    assert speakers.index("writer") < speakers.index("critic")
    assert speakers.index("critic") < speakers.index("editor")


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_each_speaker_produces_content() -> None:
    turns = await run("slogan for a bookstore")
    assert len(turns) >= 3
    # Each speaker's text is non-empty.
    for speaker, text in turns[:3]:
        assert text.strip(), f"{speaker} produced empty text"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_editor_output_differs_from_writer() -> None:
    turns = await run("slogan for an ice cream parlour")
    by_speaker = {s: t for s, t in turns}
    writer_out = by_speaker.get("writer", "")
    editor_out = by_speaker.get("editor", "")
    assert writer_out, "writer must contribute"
    assert editor_out, "editor must contribute"
    assert writer_out != editor_out, "editor should refine writer's draft, not copy it"
