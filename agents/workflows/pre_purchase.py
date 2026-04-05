"""Pre-Purchase Research Workflow — parallel agent execution.

Runs review analysis, inventory check, and price history in parallel,
then combines results into a purchase recommendation.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ResearchState:
    """Tracks state through the pre-purchase research workflow."""
    product_id: str
    user_region: str = "east"

    # Populated by parallel steps
    reviews: dict = field(default_factory=dict)
    stock: dict = field(default_factory=dict)
    price_history: dict = field(default_factory=dict)
    shipping: dict = field(default_factory=dict)

    # Final recommendation
    recommendation: str = ""
    completed_steps: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class PrePurchaseWorkflow:
    """Parallel research workflow: Reviews + Stock + Price run concurrently."""

    def __init__(self, tools: dict[str, Any]):
        self.tools = tools

    async def execute(self, state: ResearchState) -> ResearchState:
        """Execute research steps in parallel, then synthesize."""

        # Phase 1: Parallel data gathering
        results = await asyncio.gather(
            self._get_reviews(state),
            self._check_stock(state),
            self._get_price_history(state),
            return_exceptions=True,
        )

        for result in results:
            if isinstance(result, Exception):
                state.errors.append(str(result))

        # Phase 2: Sequential shipping estimate (depends on stock)
        if state.stock.get("in_stock"):
            try:
                state = await self._estimate_shipping(state)
            except Exception as e:
                state.errors.append(f"shipping: {str(e)}")

        # Phase 3: Synthesize recommendation
        state = self._synthesize(state)

        return state

    async def _get_reviews(self, state: ResearchState) -> None:
        analyze_fn = self.tools.get("analyze_sentiment")
        if analyze_fn:
            state.reviews = await analyze_fn(product_id=state.product_id)
            state.completed_steps.append("reviews")

    async def _check_stock(self, state: ResearchState) -> None:
        stock_fn = self.tools.get("check_stock")
        if stock_fn:
            state.stock = await stock_fn(product_id=state.product_id)
            state.completed_steps.append("stock")

    async def _get_price_history(self, state: ResearchState) -> None:
        price_fn = self.tools.get("get_price_history")
        if price_fn:
            state.price_history = await price_fn(product_id=state.product_id, days=90)
            state.completed_steps.append("price_history")

    async def _estimate_shipping(self, state: ResearchState) -> ResearchState:
        ship_fn = self.tools.get("estimate_shipping")
        if ship_fn:
            state.shipping = await ship_fn(
                product_id=state.product_id,
                destination_region=state.user_region,
            )
            state.completed_steps.append("shipping")
        return state

    def _synthesize(self, state: ResearchState) -> ResearchState:
        """Build a recommendation from all gathered data."""
        parts = []

        if state.reviews.get("sentiment"):
            parts.append(f"Reviews: {state.reviews['sentiment']} ({state.reviews.get('total_reviews', 0)} reviews)")

        if state.stock.get("in_stock"):
            parts.append(f"Stock: {state.stock.get('total_quantity', 0)} units available")
        else:
            parts.append("Stock: Currently out of stock")

        if state.price_history.get("is_good_deal"):
            parts.append(f"Price: Good deal (below {state.price_history.get('average_price', 0):.0f} avg)")
        elif state.price_history.get("trend"):
            parts.append(f"Price trend: {state.price_history['trend']}")

        if state.shipping.get("options"):
            cheapest = state.shipping["options"][0]
            parts.append(f"Shipping: from ${cheapest.get('price', 0):.2f}, {cheapest.get('days', 'N/A')} days")

        state.recommendation = " | ".join(parts) if parts else "Insufficient data for recommendation"
        return state
