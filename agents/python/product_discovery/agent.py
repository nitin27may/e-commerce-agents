"""Product Discovery agent definition."""

from agent_framework import Agent

from product_discovery.prompts import SYSTEM_PROMPT
from product_discovery.tools import (
    compare_products,
    find_similar_products,
    get_product_details,
    get_trending_products,
    search_products,
    semantic_search,
)
from shared.agent_factory import create_chat_client
from shared.context_providers import ECommerceContextProvider
from shared.tools.inventory_tools import check_stock, get_warehouse_availability
from shared.tools.pricing_tools import get_price_history
from shared.tools.user_tools import get_purchase_history, get_user_profile

AGENT_TOOLS = [
    search_products,
    get_product_details,
    compare_products,
    semantic_search,
    find_similar_products,
    get_trending_products,
    check_stock,
    get_warehouse_availability,
    get_price_history,
    get_user_profile,
    get_purchase_history,
]


def create_product_discovery_agent() -> Agent:
    """Create the Product Discovery ChatAgent with all tools."""
    return Agent(
        client=create_chat_client(),
        name="product-discovery",
        description="Natural language product search with personalized recommendations, semantic search, and price tracking.",
        instructions=SYSTEM_PROMPT,
        tools=AGENT_TOOLS,
        context_providers=[ECommerceContextProvider()],
    )
