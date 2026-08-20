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
from main import RefundApprovalRequest, build_workflow, run_with_response  # noqa: E402


@pytest.mark.asyncio
async def test_workflow_builds() -> None:
    assert build_workflow() is not None


@pytest.mark.asyncio
async def test_approved_refund_reports_approved() -> None:
    result = await run_with_response(order_id="ord-1001", amount=125.0, approved=True)
    assert "approved" in result.lower()
    assert "ord-1001" in result
    assert "125" in result


@pytest.mark.asyncio
async def test_denied_refund_reports_denied() -> None:
    result = await run_with_response(order_id="ord-2002", amount=75.0, approved=False)
    assert "denied" in result.lower()
    assert "ord-2002" in result


@pytest.mark.asyncio
async def test_workflow_pauses_for_human_before_first_response() -> None:
    """The first run should emit a request_info event and pause, not complete."""
    workflow = build_workflow()
    saw_request = False
    saw_output = False
    async for event in workflow.run(RefundApprovalRequest(order_id="ord-3003", amount=50.0), stream=True):
        etype = getattr(event, "type", None)
        if etype == "request_info":
            saw_request = True
        elif etype == "output":
            saw_output = True

    assert saw_request, "workflow must request info from the human"
    assert not saw_output, "workflow must NOT produce an output before receiving a response"
