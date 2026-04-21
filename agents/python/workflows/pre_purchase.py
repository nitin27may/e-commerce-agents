"""Pre-Purchase Research Workflow — MAF Concurrent orchestration.

Runs three parallel data-gathering tool calls (reviews, stock, price
history), fans the results into a sequential shipping estimate that
depends on stock, then synthesizes a final recommendation.

Refactored from a custom ``asyncio.gather`` state machine to a MAF
``WorkflowBuilder`` with ``add_fan_out_edges`` + ``add_fan_in_edges``
per ``plans/refactor/08-pre-purchase-concurrent.md``. The public API —
class, dataclass, and ``execute(state) -> state`` signature — is
preserved so callers don't need to change.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from agent_framework._workflows._executor import Executor, handler
from agent_framework._workflows._workflow_builder import WorkflowBuilder
from agent_framework._workflows._workflow_context import WorkflowContext

logger = logging.getLogger(__name__)


@dataclass
class ResearchState:
    """Carries the in-flight workflow state from executor to executor."""

    product_id: str
    user_region: str = "east"

    # Populated by fan-out executors
    reviews: dict = field(default_factory=dict)
    stock: dict = field(default_factory=dict)
    price_history: dict = field(default_factory=dict)
    shipping: dict = field(default_factory=dict)

    # Final output
    recommendation: str = ""
    completed_steps: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ─────────────────────── Executors ───────────────────────


class _FanOutExecutor(Executor):
    """Start node — broadcasts the initial state to the three gatherers."""

    def __init__(self) -> None:
        super().__init__(id="fan-out")

    @handler
    async def run(self, state: ResearchState, ctx: WorkflowContext[ResearchState]) -> None:
        await ctx.send_message(state)


class _ReviewsExecutor(Executor):
    """Calls analyze_sentiment and attaches reviews to state."""

    def __init__(self, tools: dict[str, Any]) -> None:
        super().__init__(id="reviews")
        self._tools = tools

    @handler
    async def run(self, state: ResearchState, ctx: WorkflowContext[ResearchState]) -> None:
        fn = self._tools.get("analyze_sentiment")
        if fn:
            try:
                state.reviews = await fn(product_id=state.product_id)
                state.completed_steps.append("reviews")
            except Exception as exc:
                state.errors.append(f"reviews: {exc}")
        await ctx.send_message(state)


class _StockExecutor(Executor):
    """Calls check_stock."""

    def __init__(self, tools: dict[str, Any]) -> None:
        super().__init__(id="stock")
        self._tools = tools

    @handler
    async def run(self, state: ResearchState, ctx: WorkflowContext[ResearchState]) -> None:
        fn = self._tools.get("check_stock")
        if fn:
            try:
                state.stock = await fn(product_id=state.product_id)
                state.completed_steps.append("stock")
            except Exception as exc:
                state.errors.append(f"stock: {exc}")
        await ctx.send_message(state)


class _PriceHistoryExecutor(Executor):
    """Calls get_price_history over the last 90 days."""

    def __init__(self, tools: dict[str, Any]) -> None:
        super().__init__(id="price-history")
        self._tools = tools

    @handler
    async def run(self, state: ResearchState, ctx: WorkflowContext[ResearchState]) -> None:
        fn = self._tools.get("get_price_history")
        if fn:
            try:
                state.price_history = await fn(product_id=state.product_id, days=90)
                state.completed_steps.append("price_history")
            except Exception as exc:
                state.errors.append(f"price_history: {exc}")
        await ctx.send_message(state)


class _MergeAndShipExecutor(Executor):
    """Fan-in barrier: merges the three parallel state snapshots and,
    if stock is confirmed, runs the shipping estimate sequentially."""

    def __init__(self, tools: dict[str, Any]) -> None:
        super().__init__(id="merge-and-ship")
        self._tools = tools

    @handler
    async def run(
        self,
        inputs: list[ResearchState],
        ctx: WorkflowContext[ResearchState],
    ) -> None:
        merged = _merge_states(inputs)

        if merged.stock.get("in_stock"):
            fn = self._tools.get("estimate_shipping")
            if fn:
                try:
                    merged.shipping = await fn(
                        product_id=merged.product_id,
                        destination_region=merged.user_region,
                    )
                    merged.completed_steps.append("shipping")
                except Exception as exc:
                    merged.errors.append(f"shipping: {exc}")

        await ctx.send_message(merged)


class _SynthesisExecutor(Executor):
    """Terminal node — builds the recommendation string and yields it."""

    def __init__(self) -> None:
        super().__init__(id="synthesis")

    @handler
    async def run(
        self,
        state: ResearchState,
        ctx: WorkflowContext[None, ResearchState],
    ) -> None:
        state.recommendation = _build_recommendation(state)
        await ctx.yield_output(state)


# ─────────────────────── Helpers ───────────────────────


def _merge_states(inputs: list[ResearchState]) -> ResearchState:
    """Combine three partial ResearchStates into one."""
    merged = ResearchState(product_id=inputs[0].product_id, user_region=inputs[0].user_region)
    for partial in inputs:
        if partial.reviews:
            merged.reviews = partial.reviews
        if partial.stock:
            merged.stock = partial.stock
        if partial.price_history:
            merged.price_history = partial.price_history
        for step in partial.completed_steps:
            if step not in merged.completed_steps:
                merged.completed_steps.append(step)
        merged.errors.extend(e for e in partial.errors if e not in merged.errors)
    return merged


def _build_recommendation(state: ResearchState) -> str:
    parts: list[str] = []

    if state.reviews.get("sentiment"):
        parts.append(
            f"Reviews: {state.reviews['sentiment']} "
            f"({state.reviews.get('total_reviews', 0)} reviews)"
        )

    if state.stock.get("in_stock"):
        parts.append(f"Stock: {state.stock.get('total_quantity', 0)} units available")
    else:
        parts.append("Stock: Currently out of stock")

    if state.price_history.get("is_good_deal"):
        parts.append(
            f"Price: Good deal (below {state.price_history.get('average_price', 0):.0f} avg)"
        )
    elif state.price_history.get("trend"):
        parts.append(f"Price trend: {state.price_history['trend']}")

    if state.shipping.get("options"):
        cheapest = state.shipping["options"][0]
        parts.append(
            f"Shipping: from ${cheapest.get('price', 0):.2f}, "
            f"{cheapest.get('days', 'N/A')} days"
        )

    return " | ".join(parts) if parts else "Insufficient data for recommendation"


# ─────────────────────── Public API ───────────────────────


class PrePurchaseWorkflow:
    """MAF-backed parallel research workflow.

    Construct once with the tools dict, then call ``execute(state)`` as
    many times as you like; each call builds a fresh MAF workflow under
    the covers.
    """

    def __init__(self, tools: dict[str, Any]) -> None:
        self._tools = tools

    def _build_maf_workflow(self):
        fan_out = _FanOutExecutor()
        reviews = _ReviewsExecutor(self._tools)
        stock = _StockExecutor(self._tools)
        price = _PriceHistoryExecutor(self._tools)
        merge = _MergeAndShipExecutor(self._tools)
        synthesis = _SynthesisExecutor()

        return (
            WorkflowBuilder(start_executor=fan_out, name="pre-purchase")
            .add_fan_out_edges(fan_out, [reviews, stock, price])
            .add_fan_in_edges([reviews, stock, price], merge)
            .add_edge(merge, synthesis)
            .build()
        )

    async def execute(self, state: ResearchState) -> ResearchState:
        """Run the workflow and return the final populated state."""
        workflow = self._build_maf_workflow()

        final_state = state
        async for event in workflow.run(state, stream=True):
            if getattr(event, "type", None) == "output":
                data = getattr(event, "data", None)
                if isinstance(data, ResearchState):
                    final_state = data
        return final_state
