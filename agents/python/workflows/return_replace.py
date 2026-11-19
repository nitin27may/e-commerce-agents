"""Return and Replace Workflow — MAF Sequential orchestration with a policy gate.

Step chain: check-eligibility → policy-gate → initiate-return → search-replacements →
apply-discount → finalize.

With ``RETURN_LIFECYCLE_ENABLED`` the chain gains a wait between the discount and ``finalize``:
``… → apply-discount → await-carrier-scan → finalize``. The label already exists (``initiate-return`` issues it),
so the return waits for the carrier's first scan, which can be days away, and resumes from its checkpoint when the
scan arrives (``ScanEvent``). The flag is read when the graph is built; a checkpoint belongs to the graph that
wrote it, so change the flag only while no return is paused.

Nothing is created before the gate: the ``returns`` row, the label and the order's ``returned`` status all
come from ``initiate-return``, which runs only once the policy has said ``auto`` or a reviewer has approved.
A rejected (or expired) request therefore leaves no return row and the order untouched.

The gate asks the approval policy engine (``shared/policy``, kind ``return_approval``) where the request goes.
``auto`` proceeds without pausing; anything else pauses via ``ctx.request_info`` with a ``ReturnApprovalRequest``
that carries the FULL ``WorkflowState`` — so the resumed run restores it exactly, instead of rebuilding a
minimal copy — and waits for a ``ReturnDecision`` (``decision``, ``resolution``, ``amount``, ``note``,
``decided_by``, ``approval_id``). ``settings.RETURN_HITL_THRESHOLD`` is only the fallback for when the engine
cannot decide (no database, no identity, unknown order).

An approving reviewer may edit the resolution (``refund`` / ``store_credit`` / ``replacement``) and the amount;
both are honoured by ``initiate-return``. A rejection, or anything that is not an approval, ends the run with
an error and no side effects.

Refactored from a custom sequential state machine to a MAF ``WorkflowBuilder`` per
``plans/refactor/09-return-replace-sequential-hitl.md``. Public API — class, dataclass,
``execute(state) -> state`` signature — is preserved so callers don't have to change.

Note: do NOT add ``from __future__ import annotations`` here. MAF's
``@response_handler`` resolves parameter types via ``inspect.signature``
at import time; stringified annotations break that resolution.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler, response_handler

from shared.config import settings
from shared.policy import PolicyDecision

logger = logging.getLogger(__name__)

#: What a reviewer may resolve a return as. ``replacement`` is paid out as store credit toward the replacement.
RESOLUTIONS = ("refund", "store_credit", "replacement")
_REFUND_METHOD = {"refund": "original_payment", "store_credit": "store_credit", "replacement": "store_credit"}


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

    # Policy gate / reviewer decision
    hitl_requested: bool = False
    hitl_approved: "bool | None" = None
    resolution: str = "store_credit"
    amount_override: "float | None" = None
    approval_id: "str | None" = None
    decided_by: "str | None" = None

    # Lifecycle (RETURN_LIFECYCLE_ENABLED): what the return is waiting for, and what it has learned so far
    label_url: "str | None" = None
    awaiting: "str | None" = None  # None | "carrier_scan"
    scan: "dict | None" = None

    # Execution tracking
    completed_steps: list = field(default_factory=list)
    errors: list = field(default_factory=list)


@dataclass
class ReturnApprovalRequest:
    """Payload emitted by the policy gate when a person has to decide.

    ``state`` is the whole in-flight ``WorkflowState`` (verified to round-trip through MAF's checkpoint
    encoder): the response handler restores it instead of rebuilding a partial copy. ``refund_amount`` is the
    amount under review — the order total, since no return exists yet. The policy fields are what the inbox
    shows the reviewer.
    """

    order_id: str
    order_total: float
    refund_amount: float
    replacement_count: int
    state: WorkflowState
    route: str = "support"
    reasons: list = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    policy_version: str = ""


@dataclass
class ReturnDecision:
    """A reviewer's answer. ``decision`` is ``approve`` or ``reject``; anything else counts as a rejection."""

    decision: str = "reject"
    resolution: str = "store_credit"  # refund | store_credit | replacement
    amount: "float | None" = None  # an edited refund amount; None keeps the order total
    note: str = ""
    decided_by: str = ""
    approval_id: str = ""


@dataclass
class AwaitCarrierScan:
    """Emitted when an approved return has its label and is waiting for the carrier's first scan."""

    return_id: str
    order_id: str
    label_url: "str | None"
    state: WorkflowState
    timeout_days: int = 14


@dataclass
class ScanEvent:
    """The answer to :class:`AwaitCarrierScan`. ``received=False`` means the wait ran out with no scan."""

    received: bool = True
    carrier: str = ""
    tracking: str = ""
    scanned_at: str = ""
    note: str = ""


PolicyFn = Callable[[WorkflowState], Awaitable[PolicyDecision | None]]


def _policy_unavailable_decision() -> PolicyDecision:
    """What a return gets when the approval policy cannot be loaded: a human at manager level, never ``auto``."""
    return PolicyDecision(
        route="manager",
        reasons=("Approval policy unavailable: routed to a manager review",),
        evidence={"fallback": "policy_unavailable"},
        version="",
    )


async def default_return_policy(state: WorkflowState) -> PolicyDecision | None:
    """Ask the policy engine; ``None`` means it could not gather facts (the gate then uses the threshold fallback).

    Facts are gathered for whoever the request is running as (the identity ContextVars), never for a
    function argument, and only from that person's own rows.

    A policy file that cannot be loaded or does not define ``return_approval`` is different: that is a
    broken control, not missing evidence, so it fails closed -- the return goes to a manager and is never
    auto-approved by a threshold -- and is logged at error level.
    """
    from shared.policy import PolicyError, evaluate, get_policy
    from shared.policy.facts import gather_facts

    try:
        get_policy()
    except Exception:
        logger.exception("return policy file could not be loaded; routing the return to a manager")
        return _policy_unavailable_decision()

    try:
        facts = await gather_facts(state.order_id, amount=state.order_total)
        return evaluate("return_approval", facts)
    except PolicyError:
        logger.exception("return policy has no usable return_approval kind; routing the return to a manager")
        return _policy_unavailable_decision()
    except Exception as exc:  # missing evidence must never wedge a customer's return
        logger.warning("return policy could not decide (%s); using the threshold fallback", type(exc).__name__)
        return None


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


class _PolicyGateExecutor(Executor):
    """Routes the request through the approval policy; pauses via ``ctx.request_info`` unless it is ``auto``."""

    def __init__(self, threshold: float, policy_fn: PolicyFn = default_return_policy) -> None:
        super().__init__(id="policy-gate")
        self._threshold = threshold
        self._policy_fn = policy_fn

    def _fallback(self, state: WorkflowState) -> PolicyDecision:
        over = state.order_total > self._threshold
        return PolicyDecision(
            route="support" if over else "auto",
            reasons=(
                (f"Policy engine unavailable; order total is over the {self._threshold:.2f} manual-review threshold",)
                if over
                else ("Policy engine unavailable; order total is within the auto-approval threshold",)
            ),
            evidence={"fallback": "threshold", "threshold": self._threshold},
            version="",
        )

    @handler
    async def run(self, state: WorkflowState, ctx: WorkflowContext[WorkflowState, WorkflowState]) -> None:
        state.completed_steps.append("policy_gate")
        decision = await self._policy_fn(state) or self._fallback(state)
        if decision.route == "auto":
            state.hitl_approved = True
            await ctx.send_message(state)
            return

        state.hitl_requested = True
        state.refund_amount = state.order_total  # the amount under review; no return exists yet
        # Emit a snapshot so callers observing the stream can see the
        # pause state before the request_info event pauses execution.
        await ctx.yield_output(state)
        await ctx.request_info(
            ReturnApprovalRequest(
                order_id=state.order_id,
                order_total=state.order_total,
                refund_amount=state.order_total,
                replacement_count=0,
                state=state,
                route=decision.route,
                reasons=list(decision.reasons),
                evidence=dict(decision.evidence),
                policy_version=decision.version,
            ),
            response_type=ReturnDecision,
        )

    @response_handler(request=ReturnApprovalRequest, response=ReturnDecision)
    async def on_decision(self, original_request, response, ctx) -> None:
        state = _restore_state(original_request.state)
        state.hitl_requested = True
        state.approval_id = response.approval_id or None
        state.decided_by = response.decided_by or None
        if str(response.decision).lower() != "approve":
            state.hitl_approved = False
            state.errors.append("policy_gate: return rejected by reviewer")
            await ctx.yield_output(state)
            return
        state.hitl_approved = True
        if response.resolution in RESOLUTIONS:
            state.resolution = response.resolution
        if response.amount is not None:
            state.amount_override = float(response.amount)
        await ctx.send_message(state)


def _restore_state(raw) -> WorkflowState:
    """The checkpointed state, as a ``WorkflowState`` (tolerates a plain mapping from another encoder)."""
    if isinstance(raw, WorkflowState):
        return raw
    return WorkflowState(**dict(raw))


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
                refund_method=_REFUND_METHOD.get(state.resolution, "store_credit"),
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
        state.label_url = result.get("return_label_url")
        state.refund_amount = float(result.get("refund_amount", 0.0))
        state.completed_steps.append("initiate_return")

        if state.amount_override is not None and state.amount_override != state.refund_amount:
            if not await self._apply_edited_amount(state):
                await ctx.yield_output(state)
                return
        await ctx.send_message(state)

    async def _apply_edited_amount(self, state: WorkflowState) -> bool:
        """Honour a reviewer's edited amount on the return that was just created."""
        adjust = self._tools.get("adjust_return_amount")
        if adjust is None:
            state.errors.append("initiate_return: cannot apply the reviewed amount (adjust_return_amount unavailable)")
            return False
        try:
            outcome = await adjust(return_id=state.return_id, amount=state.amount_override)
        except Exception as exc:
            state.errors.append(f"adjust_return_amount: {exc}")
            return False
        if not outcome.get("success"):
            state.errors.append(f"adjust_return_amount: {outcome.get('error', 'not applied')}")
            return False
        state.refund_amount = float(state.amount_override)
        return True


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


class _AwaitCarrierScanExecutor(Executor):
    """Pauses until the carrier's first scan; the return, its label and the order are already in place."""

    def __init__(self, timeout_days: int) -> None:
        super().__init__(id="await-carrier-scan")
        self._timeout_days = timeout_days

    @handler
    async def run(self, state: WorkflowState, ctx: WorkflowContext[WorkflowState, WorkflowState]) -> None:
        if not state.return_id:  # nothing was created, so there is nothing to wait for
            await ctx.send_message(state)
            return
        state.awaiting = "carrier_scan"
        # A snapshot for whoever watches the stream, before request_info pauses it (as the policy gate does).
        await ctx.yield_output(state)
        await ctx.request_info(
            AwaitCarrierScan(
                return_id=state.return_id,
                order_id=state.order_id,
                label_url=state.label_url,
                state=state,
                timeout_days=self._timeout_days,
            ),
            response_type=ScanEvent,
        )

    @response_handler(request=AwaitCarrierScan, response=ScanEvent)
    async def on_scan(self, original_request, response, ctx) -> None:
        state = _restore_state(original_request.state)
        state.awaiting = None
        if not response.received:
            state.errors.append(f"carrier_scan: {response.note or 'no scan arrived before the return expired'}")
            await ctx.yield_output(state)
            return
        state.scan = {"carrier": response.carrier, "tracking": response.tracking, "scanned_at": response.scanned_at}
        state.completed_steps.append("carrier_scan")
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
    """MAF-backed sequential return workflow with a policy-driven approval gate.

    Construct once with the tools dict, then call ``execute(state)`` as
    many times as you like; each call builds a fresh MAF workflow under
    the covers. ``policy_fn`` overrides how the gate asks the policy engine (tests).
    """

    def __init__(self, tools: dict, policy_fn: PolicyFn = default_return_policy) -> None:
        self._tools = tools
        self._policy_fn = policy_fn

    def _build_maf_workflow(self):
        check = _CheckEligibilityExecutor(self._tools)
        gate = _PolicyGateExecutor(float(settings.RETURN_HITL_THRESHOLD), self._policy_fn)
        initiate = _InitiateReturnExecutor(self._tools)
        search = _SearchReplacementsExecutor(self._tools)
        discount = _ApplyDiscountExecutor(self._tools)
        finalize = _FinalizeExecutor()

        builder = (
            WorkflowBuilder(start_executor=check, name="return-and-replace")
            .add_edge(check, gate)
            .add_edge(gate, initiate)
            .add_edge(initiate, search)
            .add_edge(search, discount)
        )
        if settings.RETURN_LIFECYCLE_ENABLED:
            wait_for_scan = _AwaitCarrierScanExecutor(int(settings.RETURN_SCAN_TIMEOUT_DAYS))
            builder = builder.add_edge(discount, wait_for_scan).add_edge(wait_for_scan, finalize)
        else:
            builder = builder.add_edge(discount, finalize)
        return builder.build()

    async def execute(self, state: WorkflowState) -> WorkflowState:
        """Run the workflow and return the final state snapshot.

        When the gate pauses for a reviewer, execution stops waiting for a response
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
