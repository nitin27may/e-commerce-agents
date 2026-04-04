"""Pricing & Promotions agent definition."""

from agent_framework import Agent

from pricing_promotions.prompts import SYSTEM_PROMPT
from pricing_promotions.tools import (
    check_bundle_eligibility,
    get_active_deals,
    optimize_cart,
    validate_coupon,
)
from shared.agent_factory import create_chat_client
from shared.context_providers import ECommerceContextProvider
from shared.tools.loyalty_tools import (
    calculate_loyalty_discount,
    get_loyalty_benefits,
    get_loyalty_tier,
)
from shared.tools.pricing_tools import get_price_history
from shared.tools.user_tools import get_purchase_history, get_user_profile


def create_pricing_promotions_agent() -> Agent:
    """Create the Pricing & Promotions ChatAgent with all tools."""
    return Agent(
        client=create_chat_client(),
        name="pricing-promotions",
        description="Coupon validation, cart optimization, loyalty discounts, bundle deals, and active promotions discovery.",
        instructions=SYSTEM_PROMPT,
        tools=[
            validate_coupon,
            optimize_cart,
            get_active_deals,
            check_bundle_eligibility,
            get_loyalty_tier,
            calculate_loyalty_discount,
            get_loyalty_benefits,
            get_price_history,
            get_user_profile,
            get_purchase_history,
        ],
        context_providers=[ECommerceContextProvider()],
    )
