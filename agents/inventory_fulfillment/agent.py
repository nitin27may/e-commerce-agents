"""Inventory & Fulfillment agent definition."""

from agent_framework import Agent

from inventory_fulfillment.prompts import SYSTEM_PROMPT
from inventory_fulfillment.tools import (
    calculate_fulfillment_plan,
    compare_carriers,
    estimate_shipping,
    get_restock_schedule,
    get_tracking_status,
    place_backorder,
)
from shared.agent_factory import create_chat_client
from shared.context_providers import ECommerceContextProvider
from shared.tools.inventory_tools import check_stock, get_warehouse_availability
from shared.tools.user_tools import get_user_profile


def create_inventory_fulfillment_agent() -> Agent:
    """Create the Inventory & Fulfillment ChatAgent with all tools."""
    return Agent(
        client=create_chat_client(),
        name="inventory-fulfillment",
        description="Real-time inventory tracking, shipping estimation, carrier comparison, fulfillment planning, and backorder management.",
        instructions=SYSTEM_PROMPT,
        tools=[
            check_stock,
            get_warehouse_availability,
            get_restock_schedule,
            estimate_shipping,
            compare_carriers,
            get_tracking_status,
            calculate_fulfillment_plan,
            place_backorder,
            get_user_profile,
        ],
        context_providers=[ECommerceContextProvider()],
    )
