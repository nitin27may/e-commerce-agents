"""
Phase 7 Refactor 09 — ReturnAndReplaceWorkflow (MAF Sequential + HITL) tests.

Tools are stubbed so tests run without a DB or LLM. HITL tests exercise
the request_info pause + resume cycle end-to-end.
"""

from __future__ import annotations

from typing import Any

import pytest

from shared.config import settings
from workflows.return_replace import (
    ReturnApprovalRequest,
    ReturnAndReplaceWorkflow,
    WorkflowState,
)


# ─────────────────────── Tool stubs ───────────────────────


async def _eligible(order_id: str) -> dict[str, Any]:
    return {"eligible": True}


async def _not_eligible(order_id: str) -> dict[str, Any]:
    return {"eligible": False, "reason": "Past 30-day return window"}


async def _initiate_ok(order_id: str, reason: str, refund_method: str) -> dict[str, Any]:
    return {"return_id": "ret-99", "refund_amount": 120.0}


async def _search_ok(max_price: float, min_rating: float, limit: int) -> list[dict[str, Any]]:
    return [{"id": "p-1", "name": "Replacement A"}, {"id": "p-2", "name": "Replacement B"}]


async def _tier_gold() -> dict[str, Any]:
    return {"tier": "gold", "discount_pct": 10.0}


async def _tier_none() -> dict[str, Any]:
    return {"tier": "bronze", "discount_pct": 0.0}


TOOLS_HAPPY: dict[str, Any] = {
    "check_return_eligibility": _eligible,
    "initiate_return": _initiate_ok,
    "search_products": _search_ok,
    "get_loyalty_tier": _tier_gold,
}


# ─────────────────────── Happy path (no HITL) ─────────────


@pytest.mark.asyncio
async def test_low_value_return_completes_without_hitl() -> None:
    state = WorkflowState(user_email="a@b.com", order_id="o1", order_total=50.0, reason="wrong size")
    result = await ReturnAndReplaceWorkflow(TOOLS_HAPPY).execute(state)

    assert result.hitl_requested is False
    assert result.hitl_approved is True
    assert result.return_id == "ret-99"
    assert result.refund_amount == 120.0
    assert len(result.replacement_products) == 2
    assert result.applied_discount == {"tier": "gold", "discount_pct": 10.0}
    assert "finalize" in result.completed_steps
    assert result.errors == []


@pytest.mark.asyncio
async def test_bronze_tier_means_no_discount_applied() -> None:
    tools = {**TOOLS_HAPPY, "get_loyalty_tier": _tier_none}
    state = WorkflowState(user_email="a@b.com", order_id="o2", order_total=50.0)
    result = await ReturnAndReplaceWorkflow(tools).execute(state)

    assert result.applied_discount is None
    assert "apply_discount" in result.completed_steps


# ─────────────────────── Eligibility rejection ───────────


@pytest.mark.asyncio
async def test_ineligible_order_short_circuits() -> None:
    tools = {**TOOLS_HAPPY, "check_return_eligibility": _not_eligible}
    state = WorkflowState(user_email="a@b.com", order_id="o3", order_total=50.0)
    result = await ReturnAndReplaceWorkflow(tools).execute(state)

    assert result.return_eligible is False
    assert "Past 30-day return window" in result.errors
    assert result.return_id is None
    assert "initiate_return" not in result.completed_steps


# ─────────────────────── HITL — high-value orders ─────────


@pytest.mark.asyncio
async def test_high_value_return_pauses_for_approval() -> None:
    high = settings.RETURN_HITL_THRESHOLD + 100.0
    state = WorkflowState(user_email="a@b.com", order_id="o4", order_total=high)
    result = await ReturnAndReplaceWorkflow(TOOLS_HAPPY).execute(state)

    assert result.hitl_requested is True, "HITL gate must trigger for high-value orders"
    # Workflow paused — finalize must not have run.
    assert "finalize" not in result.completed_steps
    assert result.hitl_approved is None


@pytest.mark.asyncio
async def test_hitl_request_emits_expected_payload() -> None:
    """Inspect the request_info event directly to assert on the ReturnApprovalRequest payload."""
    high = settings.RETURN_HITL_THRESHOLD + 100.0
    state = WorkflowState(user_email="a@b.com", order_id="o5", order_total=high)
    workflow = ReturnAndReplaceWorkflow(TOOLS_HAPPY)._build_maf_workflow()

    request_payloads: list[ReturnApprovalRequest] = []
    async for event in workflow.run(state, stream=True):
        if getattr(event, "type", None) == "request_info":
            data = getattr(event, "data", None)
            if isinstance(data, ReturnApprovalRequest):
                request_payloads.append(data)

    assert len(request_payloads) == 1
    payload = request_payloads[0]
    assert payload.order_id == "o5"
    assert payload.order_total == high
    assert payload.refund_amount == 120.0
    assert payload.replacement_count == 2


# ─────────────────────── Threshold boundary ───────────────


@pytest.mark.asyncio
async def test_order_at_threshold_does_not_trigger_hitl() -> None:
    """Threshold is strictly exceeded; exactly equal → no gate."""
    state = WorkflowState(
        user_email="a@b.com",
        order_id="o6",
        order_total=settings.RETURN_HITL_THRESHOLD,
    )
    result = await ReturnAndReplaceWorkflow(TOOLS_HAPPY).execute(state)
    assert result.hitl_requested is False
    assert "finalize" in result.completed_steps


# ─────────────────────── Tool failure graceful ──────────────


@pytest.mark.asyncio
async def test_failing_initiate_return_surfaces_error_without_finalize() -> None:
    async def _boom(**_: Any) -> dict[str, Any]:
        raise RuntimeError("downstream unavailable")

    tools = {**TOOLS_HAPPY, "initiate_return": _boom}
    state = WorkflowState(user_email="a@b.com", order_id="o7", order_total=25.0)
    result = await ReturnAndReplaceWorkflow(tools).execute(state)

    assert any("downstream unavailable" in e for e in result.errors)
    assert "finalize" not in result.completed_steps


# ─────────────────────── Workflow structure ─────────────────


def test_workflow_builder_wires_all_six_executors() -> None:
    wf = ReturnAndReplaceWorkflow(TOOLS_HAPPY)._build_maf_workflow()
    ids = {getattr(e, "id", None) for e in wf.get_executors_list()}
    assert {
        "check-eligibility",
        "initiate-return",
        "search-replacements",
        "hitl-gate",
        "apply-discount",
        "finalize",
    } <= ids
