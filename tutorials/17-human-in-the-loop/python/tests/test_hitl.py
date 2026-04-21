"""
Chapter 17 — Human-in-the-Loop: tests.

No LLM needed — HITL plumbing is deterministic.
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from main import build_workflow, run_with_response  # noqa: E402


@pytest.mark.asyncio
async def test_workflow_builds() -> None:
    assert build_workflow(secret=42) is not None


@pytest.mark.asyncio
async def test_correct_guess_reports_correct() -> None:
    result = await run_with_response(secret=7, guess=7)
    assert "correct" in result.lower()
    assert "7" in result


@pytest.mark.asyncio
async def test_low_guess_reports_too_low() -> None:
    result = await run_with_response(secret=7, guess=3)
    assert "too low" in result.lower()


@pytest.mark.asyncio
async def test_high_guess_reports_too_high() -> None:
    result = await run_with_response(secret=7, guess=10)
    assert "too high" in result.lower()


@pytest.mark.asyncio
async def test_workflow_pauses_for_human_before_first_response() -> None:
    """The first run should emit a request_info event and pause, not complete."""
    workflow = build_workflow(secret=5)
    saw_request = False
    saw_output = False
    async for event in workflow.run("Pick a number:", stream=True):
        etype = getattr(event, "type", None)
        if etype == "request_info":
            saw_request = True
        elif etype == "output":
            saw_output = True

    assert saw_request, "workflow must request info from the human"
    assert not saw_output, "workflow must NOT produce an output before receiving a response"
