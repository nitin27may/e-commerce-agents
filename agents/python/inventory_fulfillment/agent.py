"""Inventory & Fulfillment agent definition.

When ``settings.MCP_ENABLED`` is True the agent connects to the Inventory MCP
server (``ecommerce_mcp_inventory.server``) via ``MCPStreamableHTTPTool`` instead
of calling asyncpg directly. Both modes expose the same capabilities.
"""

from agent_framework import Agent
from agent_framework._mcp import MCPStreamableHTTPTool

from inventory_fulfillment.prompts import get_system_prompt
from inventory_fulfillment.tools import (
    calculate_fulfillment_plan,
    compare_carriers,
    estimate_shipping,
    get_restock_schedule,
    get_tracking_status,
    place_backorder,
)
from shared.agent_factory import create_chat_client
from shared.config import settings
from shared.context import current_user_role
from shared.context_providers import ECommerceContextProvider
from shared.middleware import build_specialist_middleware
from shared.oauth.service_client import acquire_service_token, build_mcp_http_client, set_mcp_auth_header
from shared.tools.inventory_tools import check_stock, get_warehouse_availability
from shared.tools.product_lookup_tools import find_product_by_name
from shared.tools.user_tools import get_user_profile

AGENT_TOOLS = [
    find_product_by_name,
    check_stock,
    get_warehouse_availability,
    get_restock_schedule,
    estimate_shipping,
    compare_carriers,
    get_tracking_status,
    calculate_fulfillment_plan,
    place_backorder,
    get_user_profile,
]

# Set once by create_inventory_fulfillment_agent() when MCP_ENABLED+MCP_AUTH_ENABLED;
# refresh_mcp_auth() (called from the async startup hook in main.py) sets its
# Authorization header once a token is acquired — agent construction itself
# is synchronous and can't await the client-credentials grant.
_mcp_inventory_http_client = None


def create_inventory_fulfillment_agent() -> Agent:
    """Create the Inventory & Fulfillment ChatAgent.

    Uses the MCP server when ``MCP_ENABLED=true``, direct asyncpg tools otherwise.
    """
    global _mcp_inventory_http_client
    if settings.MCP_ENABLED:
        # MCP path: tools are discovered from the running MCP server at startup.
        # Non-MCP tools (tracking, fulfillment plan, backorder, user_profile) still
        # run locally since they are not yet exposed via the MCP server.
        if settings.MCP_AUTH_ENABLED:
            _mcp_inventory_http_client = build_mcp_http_client()
        mcp_inventory = MCPStreamableHTTPTool(
            name="inventory-mcp",
            url=settings.MCP_INVENTORY_SERVER_URL,
            description="Inventory and fulfillment data via MCP",
            http_client=_mcp_inventory_http_client,
        )
        tools: list = [
            mcp_inventory,
            find_product_by_name,
            get_tracking_status,
            calculate_fulfillment_plan,
            place_backorder,
            get_user_profile,
        ]
    else:
        tools = AGENT_TOOLS  # type: ignore[assignment]

    return Agent(
        client=create_chat_client(),
        name="inventory-fulfillment",
        description="Real-time inventory tracking, shipping estimation, carrier comparison, and backorder management.",
        instructions=get_system_prompt(current_user_role.get() or "customer"),
        tools=tools,
        context_providers=[ECommerceContextProvider()],
        middleware=build_specialist_middleware(),
    )


async def refresh_mcp_auth() -> None:
    """Acquire (or refresh) the ``mcp:inventory`` service token and set it as
    the default Authorization header on the shared MCP http client. Called
    once from the async startup hook in ``main.py`` — agent construction
    itself is synchronous and can't await the client-credentials grant."""
    if _mcp_inventory_http_client is None:
        return
    token = await acquire_service_token(settings.MCP_INVENTORY_REQUIRED_SCOPE, settings.MCP_INVENTORY_AUDIENCE)
    set_mcp_auth_header(_mcp_inventory_http_client, token)
