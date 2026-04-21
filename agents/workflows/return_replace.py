"""Return and Replace Workflow — MAF Sequential orchestration with HITL gate.

Step chain: check-eligibility → initiate-return → search-replacements →
hitl-gate → apply-discount → finalize.

The HITL gate pauses the workflow above ``settings.RETURN_HITL_THRESHOLD``
and emits a ``ReturnApprovalRequest`` via ``ctx.request_info`` so an
external system (UI, Slack, on-call human) can approve or reject before
the discount is applied and the return is finalized.

Refactored from a custom sequential state machine to a MAF
``WorkflowBuilder`` per ``plans/refactor/09-return-replace-sequential-hitl.md``.
Public API — class, dataclass, ``execute(state) -> state`` signature — is
preserved so callers don't have to change.

Note: do NOT add ``from __future__ import annotations`` here. MAF's
``@response_handler`` resolves parameter types via ``inspect.signature``
at import time; stringified annotations break that resolution.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from agent_framework._workflows._executor import Executor, handler
from agent_framework._workflows._request_info_mixin import response_handler
from agent_framework._workflows._workflow_builder import WorkflowBuilder
from agent_framework._workflows._workflow_context import WorkflowContext

from shared.config import settings

logger = logging.getLogger(__name__)


@dataclass
class WorkflowState:
    """Carries the in-flight workflow state from executor to executor."""

    user_email: str
    order_id: str
    order_total: float = 0.0
    reason: str = ""

    # Populated along the chain
    return_eligible: bool = False
    return_id: "str | None" = None
    refund_amount: float = 0.0
    replacement_products: list = field(default_factory=list)
    applied_discount: "dict | None" = None

    # HITL state
    hitl_requested: bool = False
    hitl_approved: "bool | None" = None

    # Execution tracking
    completed_steps: list = field(default_factory=list)
    errors: list = field(default_factory=list)


@dataclass
class ReturnApprovalRequest:
    """Payload emitted by the HITL gate for high-value returns."""

    order_id: str
    order_total: float
    refund_amount: float
    replacement_count: int


# ─────────────────────── Executors ───────────────────────


class _CheckEligibilityExecutor(Executor):
    def __init__(self, tools: dict) -> None:
        super().__init__(id="check-eligibility")
        self._tools = tools

    @handler
    async def run(self, state: WorkflowState, ctx: WorkflowContext[WorkflowState, WorkflowState]) -> None:
        fn = self._tools.get("check_return_eligibility")
        if not fn:
            state.errors.append("check_return_eligibility tool not available")
            await ctx.yield_output(state)
            return
        try:
            result = await fn(order_id=state.order_id)
        except Exception as exc:
            state.errors.append(f"check_eligibility: {exc}")
            await ctx.yield_output(state)
            return

        state.return_eligible = bool(result.get("eligible"))
        state.completed_steps.append("check_eligibility")
        if not state.return_eligible:
            state.errors.append(result.get("reason", "Not eligible for return"))
            await ctx.yield_output(state)
            return
        await ctx.send_message(state)


class _InitiateReturnExecutor(Executor):
    def __init__(self, tools: dict) -> None:
        super().__init__(id="initiate-return")
        self._tools = tools

    @handler
    async def run(self, state: WorkflowState, ctx: WorkflowContext[WorkflowState, WorkflowState]) -> None:
        fn = self._tools.get("initiate_return")
        if not fn:
            state.errors.append("initiate_return tool not available")
            await ctx.yield_output(state)
            return
        try:
            result = await fn(
                order_id=state.order_id,
                reason=state.reason or "Customer requested replacement",
                refund_method="store_credit",
            )
        except Exception as exc:
            state.errors.append(f"initiate_return: {exc}")
            await ctx.yield_output(state)
            return

        if "error" in result:
            state.errors.append(f"initiate_return: {result['error']}")
            await ctx.yield_output(state)
            return

        state.return_id = result.get("return_id")
        state.refund_amount = float(result.get("refund_amount", 0.0))
        state.completed_steps.append("initiate_return")
        await ctx.send_message(state)


class _SearchReplacementsExecutor(Executor):
    def __init__(self, tools: dict) -> None:
        super().__init__(id="search-replacements")
        self._tools = tools

    @handler
    async def run(self, state: WorkflowState, ctx: WorkflowContext[WorkflowState, WorkflowState]) -> None:
        fn = self._tools.get("search_products")
        if fn:
            try:
                results = await fn(
                    max_price=state.refund_amount * 1.2,
                    min_rating=4.0,
                    limit=5,
                )
                state.replacement_products = list(results) if isinstance(results, list) else []
                state.completed_steps.append("search_replacements")
            except Exception as exc:
                state.errors.append(f"search_replacements: {exc}")
        await ctx.send_message(state)


class _HitlGateExecutor(Executor):
    """Pauses the workflow via ``ctx.request_info`` for high-value orders."""

    def __init__(self, threshold: float) -> None:
        super().__init__(id="hitl-gate")
        self._threshold = threshold

    @handler
    async def run(self, state: WorkflowState, ctx: WorkflowContext[WorkflowState, WorkflowState]) -> None:
        state.completed_steps.append("hitl_gate")
        if state.order_total > self._threshold:
            state.hitl_requested = True
            # Emit a snapshot so callers observing the stream can see the
            # pause state before the request_info event pauses execution.
            await ctx.yield_output(state)
            await ctx.request_info(
                ReturnApprovalRequest(
                    order_id=state.order_id,
                    order_total=state.order_total,
                    refund_amount=state.refund_amount,
                    replacement_count=len(state.replacement_products),
                ),
                response_type=bool,
            )
            return
        state.hitl_approved = True
        await ctx.send_message(state)

    @response_handler(request=ReturnApprovalRequest, response=bool)
    async def on_approval(self, original_request, response, ctx) -> None:
        approved = bool(response)
        # Rehydrate a minimal state from the original_request; the rest of
        # the chain only needs the refund context that was captured there.
        resumed = WorkflowState(
            user_email="",
            order_id=original_request.order_id,
            order_total=original_request.order_total,
            refund_amount=original_request.refund_amount,
            hitl_requested=True,
            hitl_approved=approved,
            completed_steps=["check_eligibility", "initiate_return", "search_replacements", "hitl_gate"],
        )
        if not approved:
            resumed.errors.append("hitl_gate: return rejected by reviewer")
            await ctx.yield_output(resumed)
            return
        await ctx.send_message(resumed)


class _ApplyDiscountExecutor(Executor):
    def __init__(self, tools: dict) -> None:
        super().__init__(id="apply-discount")
        self._tools = tools

    @handler
    async def run(self, state: WorkflowState, ctx: WorkflowContext[WorkflowState, WorkflowState]) -> None:
        fn = self._tools.get("get_loyalty_tier")
        if fn:
            try:
                result = await fn()
                if float(result.get("discount_pct", 0)) > 0:
                    state.applied_discount = {
                        "tier": result.get("tier"),
                        "discount_pct": result.get("discount_pct"),
                    }
                state.completed_steps.append("apply_discount")
            except Exception as exc:
                state.errors.append(f"apply_discount: {exc}")
        else:
            state.completed_steps.append("apply_discount")
        await ctx.send_message(state)


class _FinalizeExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="finalize")

    @handler
    async def run(self, state: WorkflowState, ctx: WorkflowContext[None, WorkflowState]) -> None:
        state.completed_steps.append("finalize")
        await ctx.yield_output(state)


# ─────────────────────── Public API ───────────────────────


class ReturnAndReplaceWorkflow:
    """MAF-backed sequential return workflow with HITL approval gate.

    Construct once with the tools dict, then call ``execute(state)`` as
    many times as you like; each call builds a fresh MAF workflow under
    the covers.
    """

    def __init__(self, tools: dict) -> None:
        self._tools = tools

    def _build_maf_workflow(self):
        check = _CheckEligibilityExecutor(self._tools)
        initiate = _InitiateReturnExecutor(self._tools)
        search = _SearchReplacementsExecutor(self._tools)
        gate = _HitlGateExecutor(float(settings.RETURN_HITL_THRESHOLD))
        discount = _ApplyDiscountExecutor(self._tools)
        finalize = _FinalizeExecutor()

        return (
            WorkflowBuilder(start_executor=check, name="return-and-replace")
            .add_edge(check, initiate)
            .add_edge(initiate, search)
            .add_edge(search, gate)
            .add_edge(gate, discount)
            .add_edge(discount, finalize)
            .build()
        )

    async def execute(self, state: WorkflowState) -> WorkflowState:
        """Run the workflow and return the final state snapshot.

        When the HITL gate fires, execution pauses waiting for a response
        and the most recent state snapshot is returned (with
        ``hitl_requested=True`` and ``hitl_approved=None``).
        """
        workflow = self._build_maf_workflow()

        final_state = state
        async for event in workflow.run(state, stream=True):
            if getattr(event, "type", None) == "output":
                data = getattr(event, "data", None)
                if isinstance(data, WorkflowState):
                    final_state = data

        # Mirror the happy-path invariant: if no HITL was requested,
        # treat the run as implicitly approved.
        if not final_state.hitl_requested and final_state.hitl_approved is None:
            final_state.hitl_approved = True

        return final_state
