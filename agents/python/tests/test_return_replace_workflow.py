"""
ReturnAndReplaceWorkflow (MAF Sequential + policy gate) tests.

Order under test: check-eligibility -> policy-gate -> initiate-return -> search-replacements ->
apply-discount -> finalize. Nothing is created before the gate: the stubs below record every call, so a test
can assert that a rejected return never reached ``initiate_return``.

Tools are stubbed so tests run without a DB or LLM. With no database the gate's policy lookup cannot decide
and falls back to ``RETURN_HITL_THRESHOLD``; tests that exercise the routing itself inject a ``policy_fn``.
HITL tests exercise the request_info pause + resume cycle end-to-end, including the ``@response_handler``
resume path via ``workflow.run(responses=...)`` with a ``ReturnDecision``.

Resume note: the first ``run()`` stream must be drained to completion before
resuming. Breaking out of it early (e.g. at the first ``request_info`` event)
leaves the workflow with ``_is_running=True`` and the resuming ``run()`` fails
with "Workflow is already running". Any HTTP handler that suspends a workflow
across requests has to drain first, then persist.
"""

from __future__ import annotations

from typing import Any

import pytest

from shared.config import settings
from shared.policy import PolicyDecision
from workflows.return_replace import (
    ReturnAndReplaceWorkflow,
    ReturnApprovalRequest,
    ReturnDecision,
    WorkflowState,
)

# ─────────────────────── Tool stubs ───────────────────────


def _tools(calls: list[tuple[str, dict[str, Any]]] | None = None, **overrides: Any) -> dict[str, Any]:
    """Stub tools that record ``(name, kwargs)`` into ``calls``."""
    log = calls if calls is not None else []

    async def check(order_id: str) -> dict[str, Any]:
        log.append(("check_return_eligibility", {"order_id": order_id}))
        return {"eligible": True}

    async def initiate(order_id: str, reason: str, refund_method: str) -> dict[str, Any]:
        log.append(("initiate_return", {"order_id": order_id, "refund_method": refund_method}))
        return {"return_id": "ret-99", "refund_amount": 120.0}

    async def search(max_price: float, min_rating: float, limit: int) -> list[dict[str, Any]]:
        log.append(("search_products", {"max_price": max_price}))
        return [{"id": "p-1", "name": "Replacement A"}, {"id": "p-2", "name": "Replacement B"}]

    async def tier() -> dict[str, Any]:
        log.append(("get_loyalty_tier", {}))
        return {"tier": "gold", "discount_pct": 10.0}

    async def adjust(return_id: str, amount: float) -> dict[str, Any]:
        log.append(("adjust_return_amount", {"return_id": return_id, "amount": amount}))
        return {"success": True}

    return {
        "check_return_eligibility": check,
        "initiate_return": initiate,
        "search_products": search,
        "get_loyalty_tier": tier,
        "adjust_return_amount": adjust,
        **overrides,
    }


#: Shared with other test modules (tests/test_events.py): the stub tools, wired for the happy path.
TOOLS_HAPPY: dict[str, Any] = _tools()


def _names(calls: list[tuple[str, dict[str, Any]]]) -> list[str]:
    return [name for name, _ in calls]


def _policy(route: str, reason: str = "because") -> Any:
    async def _decide(_state: WorkflowState) -> PolicyDecision:
        return PolicyDecision(route=route, reasons=(reason,), evidence={"rule": "test"}, version="v-test")

    return _decide


def _high() -> float:
    return settings.RETURN_HITL_THRESHOLD + 100.0


# ─────────────────────── Happy path (no HITL) ─────────────


@pytest.mark.asyncio
async def test_low_value_return_completes_without_hitl() -> None:
    state = WorkflowState(user_email="a@b.com", order_id="o1", order_total=50.0, reason="wrong size")
    result = await ReturnAndReplaceWorkflow(_tools()).execute(state)

    assert result.hitl_requested is False
    assert result.hitl_approved is True
    assert result.return_id == "ret-99"
    assert result.refund_amount == 120.0
    assert len(result.replacement_products) == 2
    assert result.applied_discount == {"tier": "gold", "discount_pct": 10.0}
    assert "finalize" in result.completed_steps
    assert result.errors == []


@pytest.mark.asyncio
async def test_steps_run_in_the_new_order() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    state = WorkflowState(user_email="a@b.com", order_id="o1", order_total=50.0)
    result = await ReturnAndReplaceWorkflow(_tools(calls), policy_fn=_policy("auto")).execute(state)

    assert _names(calls) == ["check_return_eligibility", "initiate_return", "search_products", "get_loyalty_tier"]
    assert result.completed_steps == [
        "check_eligibility",
        "policy_gate",
        "initiate_return",
        "search_replacements",
        "apply_discount",
        "finalize",
    ]


@pytest.mark.asyncio
async def test_bronze_tier_means_no_discount_applied() -> None:
    async def _tier_none() -> dict[str, Any]:
        return {"tier": "bronze", "discount_pct": 0.0}

    tools = _tools(get_loyalty_tier=_tier_none)
    state = WorkflowState(user_email="a@b.com", order_id="o2", order_total=50.0)
    result = await ReturnAndReplaceWorkflow(tools).execute(state)

    assert result.applied_discount is None
    assert "apply_discount" in result.completed_steps


# ─────────────────────── Eligibility rejection ───────────


@pytest.mark.asyncio
async def test_ineligible_order_short_circuits() -> None:
    async def _not_eligible(order_id: str) -> dict[str, Any]:
        return {"eligible": False, "reason": "Past 30-day return window"}

    calls: list[tuple[str, dict[str, Any]]] = []
    tools = _tools(calls, check_return_eligibility=_not_eligible)
    state = WorkflowState(user_email="a@b.com", order_id="o3", order_total=50.0)
    result = await ReturnAndReplaceWorkflow(tools).execute(state)

    assert result.return_eligible is False
    assert "Past 30-day return window" in result.errors
    assert result.return_id is None
    assert "initiate_return" not in result.completed_steps
    assert calls == []


# ─────────────────────── The gate pauses BEFORE anything is created ─────────


@pytest.mark.asyncio
async def test_high_value_return_pauses_for_approval_with_nothing_created() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    state = WorkflowState(user_email="a@b.com", order_id="o4", order_total=_high())
    result = await ReturnAndReplaceWorkflow(_tools(calls)).execute(state)

    assert result.hitl_requested is True, "the gate must trigger for high-value orders"
    assert result.hitl_approved is None
    assert "finalize" not in result.completed_steps
    # The point of the reorder: no return row, no label, no order status change before a person decides.
    assert _names(calls) == ["check_return_eligibility"]
    assert result.return_id is None


@pytest.mark.asyncio
async def test_hitl_request_carries_the_full_state_and_the_policy_decision() -> None:
    """Inspect the request_info event directly: the request holds the whole WorkflowState."""
    state = WorkflowState(user_email="a@b.com", order_id="o5", order_total=_high(), reason="broken hinge")
    workflow = ReturnAndReplaceWorkflow(_tools(), policy_fn=_policy("manager", "Amount over 500 needs a manager"))
    workflow = workflow._build_maf_workflow()

    request_payloads: list[ReturnApprovalRequest] = []
    async for event in workflow.run(state, stream=True):
        if getattr(event, "type", None) == "request_info":
            data = getattr(event, "data", None)
            if isinstance(data, ReturnApprovalRequest):
                request_payloads.append(data)

    assert len(request_payloads) == 1
    payload = request_payloads[0]
    assert payload.order_id == "o5"
    assert payload.order_total == _high()
    assert payload.refund_amount == _high(), "no return exists yet, so the amount under review is the order total"
    assert payload.replacement_count == 0
    assert isinstance(payload.state, WorkflowState)
    assert payload.state.reason == "broken hinge"
    assert (payload.route, payload.reasons, payload.policy_version) == (
        "manager",
        ["Amount over 500 needs a manager"],
        "v-test",
    )
    assert payload.evidence == {"rule": "test"}


# ─────────────────────── Policy routing ───────────────────


@pytest.mark.asyncio
async def test_policy_auto_proceeds_without_pausing_even_over_the_threshold() -> None:
    state = WorkflowState(user_email="a@b.com", order_id="o8", order_total=_high())
    result = await ReturnAndReplaceWorkflow(_tools(), policy_fn=_policy("auto")).execute(state)

    assert result.hitl_requested is False
    assert "finalize" in result.completed_steps


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["support", "manager"])
async def test_policy_non_auto_pauses_even_for_a_small_order(route: str) -> None:
    state = WorkflowState(user_email="a@b.com", order_id="o9", order_total=20.0)
    result = await ReturnAndReplaceWorkflow(_tools(), policy_fn=_policy(route)).execute(state)

    assert result.hitl_requested is True
    assert result.hitl_approved is None


@pytest.mark.asyncio
async def test_a_policy_that_cannot_decide_falls_back_to_the_threshold() -> None:
    async def _cannot(_state: WorkflowState) -> None:
        return None

    low = WorkflowState(user_email="a@b.com", order_id="o6", order_total=settings.RETURN_HITL_THRESHOLD)
    high = WorkflowState(user_email="a@b.com", order_id="o7", order_total=_high())
    workflow = ReturnAndReplaceWorkflow(_tools(), policy_fn=_cannot)

    # Threshold is strictly exceeded; exactly equal -> no gate.
    assert (await workflow.execute(low)).hitl_requested is False
    assert (await workflow.execute(high)).hitl_requested is True


# ─────────────────────── Tool failure graceful ──────────────


@pytest.mark.asyncio
async def test_failing_initiate_return_surfaces_error_without_finalize() -> None:
    async def _boom(**_: Any) -> dict[str, Any]:
        raise RuntimeError("downstream unavailable")

    tools = _tools(initiate_return=_boom)
    state = WorkflowState(user_email="a@b.com", order_id="o7", order_total=25.0)
    result = await ReturnAndReplaceWorkflow(tools).execute(state)

    assert any("downstream unavailable" in e for e in result.errors)
    assert "finalize" not in result.completed_steps


# ─────────────────────── Workflow structure ─────────────────


def test_workflow_builder_wires_all_six_executors_in_order() -> None:
    wf = ReturnAndReplaceWorkflow(_tools())._build_maf_workflow()
    ids = {getattr(e, "id", None) for e in wf.get_executors_list()}
    assert ids == {
        "check-eligibility",
        "policy-gate",
        "initiate-return",
        "search-replacements",
        "apply-discount",
        "finalize",
    }
    edges = {
        (e.source_id, e.target_id)
        for group in wf.edge_groups
        for e in group.edges
        if not e.source_id.startswith("internal:")
    }
    assert edges == {
        ("check-eligibility", "policy-gate"),
        ("policy-gate", "initiate-return"),
        ("initiate-return", "search-replacements"),
        ("search-replacements", "apply-discount"),
        ("apply-discount", "finalize"),
    }


# ─────────────────────── HITL resume (response_handler) ─────


async def _pause_and_collect_request_id(workflow, state) -> str:
    """Drain the first run to completion and return the pending request id.

    Draining matters: abandoning the stream early leaves the workflow marked
    as running, and the resuming ``run()`` then raises "Workflow is already
    running". See the module docstring.
    """
    request_id: str | None = None
    async for event in workflow.run(state, stream=True):
        if request_id is None and getattr(event, "type", None) == "request_info":
            request_id = getattr(event, "request_id", None)
    assert request_id, "expected a request_info event to pause the workflow"
    return request_id


async def _resume(workflow, request_id: str, decision: ReturnDecision) -> WorkflowState:
    outputs: list[WorkflowState] = []
    async for event in workflow.run(responses={request_id: decision}, stream=True):
        if getattr(event, "type", None) == "output":
            data = getattr(event, "data", None)
            if isinstance(data, WorkflowState):
                outputs.append(data)
    assert outputs, "resume must produce a terminal workflow state"
    return outputs[-1]


@pytest.mark.asyncio
async def test_approval_resumes_creates_the_return_and_finalizes() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    state = WorkflowState(user_email="a@b.com", order_id="o10", order_total=_high())
    workflow = ReturnAndReplaceWorkflow(_tools(calls))._build_maf_workflow()

    request_id = await _pause_and_collect_request_id(workflow, state)
    assert "initiate_return" not in _names(calls)

    final = await _resume(workflow, request_id, ReturnDecision("approve", decided_by="staff", approval_id="ap-1"))

    assert final.hitl_approved is True
    assert "finalize" in final.completed_steps
    assert not final.errors
    assert _names(calls).count("initiate_return") == 1
    assert _names(calls) == ["check_return_eligibility", "initiate_return", "search_products", "get_loyalty_tier"]
    assert (final.decided_by, final.approval_id) == ("staff", "ap-1")


@pytest.mark.asyncio
async def test_resumed_state_keeps_the_original_fields() -> None:
    """The documented loss on resume (return_id / replacement_products / reason) is fixed: the full state survives."""
    state = WorkflowState(user_email="a@b.com", order_id="o12", order_total=_high(), reason="broken hinge")
    workflow = ReturnAndReplaceWorkflow(_tools())._build_maf_workflow()
    request_id = await _pause_and_collect_request_id(workflow, state)

    final = await _resume(workflow, request_id, ReturnDecision("approve"))

    assert final.user_email == "a@b.com"
    assert final.reason == "broken hinge"
    assert final.return_id == "ret-99"
    assert [p["id"] for p in final.replacement_products] == ["p-1", "p-2"]
    assert final.completed_steps[:2] == ["check_eligibility", "policy_gate"]


@pytest.mark.asyncio
async def test_rejection_creates_nothing_and_never_finalizes() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    state = WorkflowState(user_email="a@b.com", order_id="o11", order_total=_high())
    workflow = ReturnAndReplaceWorkflow(_tools(calls))._build_maf_workflow()
    request_id = await _pause_and_collect_request_id(workflow, state)

    final = await _resume(workflow, request_id, ReturnDecision("reject", decided_by="staff"))

    assert final.hitl_approved is False
    assert "finalize" not in final.completed_steps
    assert any("rejected by reviewer" in e for e in final.errors)
    # No return row, no label, no replacement search, no discount: the order is untouched.
    assert _names(calls) == ["check_return_eligibility"]
    assert final.return_id is None


@pytest.mark.asyncio
async def test_anything_but_approve_is_a_rejection() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    state = WorkflowState(user_email="a@b.com", order_id="o13", order_total=_high())
    workflow = ReturnAndReplaceWorkflow(_tools(calls))._build_maf_workflow()
    request_id = await _pause_and_collect_request_id(workflow, state)

    final = await _resume(workflow, request_id, ReturnDecision("expired"))

    assert final.hitl_approved is False
    assert "initiate_return" not in _names(calls)


@pytest.mark.asyncio
async def test_an_edited_resolution_and_amount_are_honoured() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    state = WorkflowState(user_email="a@b.com", order_id="o14", order_total=_high())
    workflow = ReturnAndReplaceWorkflow(_tools(calls))._build_maf_workflow()
    request_id = await _pause_and_collect_request_id(workflow, state)

    final = await _resume(workflow, request_id, ReturnDecision("approve", resolution="refund", amount=75.5))

    initiate = dict(calls)["initiate_return"]
    assert initiate["refund_method"] == "original_payment", "resolution=refund pays back to the original method"
    assert dict(calls)["adjust_return_amount"] == {"return_id": "ret-99", "amount": 75.5}
    assert final.refund_amount == 75.5
    assert final.resolution == "refund"
    # The replacement search is sized from the edited amount, not the order total.
    assert dict(calls)["search_products"]["max_price"] == pytest.approx(75.5 * 1.2)


@pytest.mark.asyncio
async def test_an_unedited_approval_does_not_touch_the_amount() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    state = WorkflowState(user_email="a@b.com", order_id="o15", order_total=_high())
    workflow = ReturnAndReplaceWorkflow(_tools(calls))._build_maf_workflow()
    request_id = await _pause_and_collect_request_id(workflow, state)

    await _resume(workflow, request_id, ReturnDecision("approve"))

    assert "adjust_return_amount" not in _names(calls)
    assert dict(calls)["initiate_return"]["refund_method"] == "store_credit"


@pytest.mark.asyncio
async def test_an_amount_that_cannot_be_applied_is_an_error_not_a_silent_full_refund() -> None:
    async def _refuse(return_id: str, amount: float) -> dict[str, Any]:
        return {"success": False, "error": "not yours"}

    state = WorkflowState(user_email="a@b.com", order_id="o16", order_total=_high())
    workflow = ReturnAndReplaceWorkflow(_tools(adjust_return_amount=_refuse))._build_maf_workflow()
    request_id = await _pause_and_collect_request_id(workflow, state)

    final = await _resume(workflow, request_id, ReturnDecision("approve", amount=10.0))

    assert any("adjust_return_amount" in e for e in final.errors)
    assert "finalize" not in final.completed_steps
