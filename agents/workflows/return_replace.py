"""Return and Replace Workflow — demonstrates graph-based multi-agent orchestration.

Flow: Check return eligibility → Initiate return → Search replacements → Apply discount

This workflow shows how complex multi-step operations can be modeled as a
directed graph instead of relying solely on LLM-driven routing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class WorkflowState:
    """Tracks state through the return-and-replace workflow."""
    user_email: str
    order_id: str
    reason: str = ""

    # Populated by steps
    return_eligible: bool = False
    return_id: str | None = None
    refund_amount: float = 0.0
    replacement_products: list[dict] = field(default_factory=list)
    applied_discount: dict | None = None

    # Execution tracking
    completed_steps: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class ReturnAndReplaceWorkflow:
    """Graph-based workflow: Return → Refund → Search → Discount.

    Instead of relying on the LLM to figure out the sequence,
    this workflow explicitly defines the steps and their order.
    """

    def __init__(self, tools: dict[str, Any]):
        """Initialize with a dict of tool functions keyed by name."""
        self.tools = tools

    async def execute(self, state: WorkflowState) -> WorkflowState:
        """Execute the full workflow, step by step."""
        steps = [
            ("check_eligibility", self._check_eligibility),
            ("initiate_return", self._initiate_return),
            ("search_replacements", self._search_replacements),
            ("apply_discount", self._apply_discount),
        ]

        for step_name, step_fn in steps:
            try:
                logger.info("workflow.step name=%s order=%s", step_name, state.order_id)
                state = await step_fn(state)
                state.completed_steps.append(step_name)

                # Early exit conditions
                if step_name == "check_eligibility" and not state.return_eligible:
                    logger.info("workflow.early_exit reason=not_eligible")
                    break

            except Exception as e:
                logger.exception("workflow.step_error name=%s", step_name)
                state.errors.append(f"{step_name}: {str(e)}")
                break

        return state

    async def _check_eligibility(self, state: WorkflowState) -> WorkflowState:
        """Step 1: Check if the order is eligible for return."""
        check_fn = self.tools.get("check_return_eligibility")
        if not check_fn:
            state.errors.append("check_return_eligibility tool not available")
            return state

        result = await check_fn(order_id=state.order_id)
        state.return_eligible = result.get("eligible", False)
        if not state.return_eligible:
            state.errors.append(result.get("reason", "Not eligible for return"))
        return state

    async def _initiate_return(self, state: WorkflowState) -> WorkflowState:
        """Step 2: Initiate the return and get refund amount."""
        initiate_fn = self.tools.get("initiate_return")
        if not initiate_fn:
            state.errors.append("initiate_return tool not available")
            return state

        result = await initiate_fn(
            order_id=state.order_id,
            reason=state.reason or "Customer requested replacement",
            refund_method="store_credit",
        )
        if "error" in result:
            state.errors.append(result["error"])
            return state

        state.return_id = result.get("return_id")
        state.refund_amount = result.get("refund_amount", 0.0)
        return state

    async def _search_replacements(self, state: WorkflowState) -> WorkflowState:
        """Step 3: Search for replacement products in a similar price range."""
        search_fn = self.tools.get("search_products")
        if not search_fn:
            state.errors.append("search_products tool not available")
            return state

        # Search for similar products within the refund amount
        results = await search_fn(
            max_price=state.refund_amount * 1.2,  # Allow 20% above refund
            min_rating=4.0,
            limit=5,
        )
        state.replacement_products = results if isinstance(results, list) else []
        return state

    async def _apply_discount(self, state: WorkflowState) -> WorkflowState:
        """Step 4: Check if any loyalty discount applies to the replacement."""
        loyalty_fn = self.tools.get("get_loyalty_tier")
        if not loyalty_fn:
            return state  # Optional step, skip if unavailable

        result = await loyalty_fn()
        if result.get("discount_pct", 0) > 0:
            state.applied_discount = {
                "tier": result.get("tier"),
                "discount_pct": result.get("discount_pct"),
            }
        return state
