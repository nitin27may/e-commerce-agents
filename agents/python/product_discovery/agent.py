"""Product Discovery agent definition.

When ``settings.MCP_ENABLED`` is True the agent connects to the Product MCP
server (``ecommerce_mcp_product.server``) via ``MCPStreamableHTTPTool`` instead
of calling asyncpg directly. Both modes expose the same capabilities.
"""

from agent_framework import Agent
from agent_framework._mcp import MCPStreamableHTTPTool

from product_discovery.prompts import get_system_prompt
from product_discovery.tools import (
    compare_products,
    find_similar_products,
    get_product_details,
    get_trending_products,
    search_products,
    semantic_search,
)
from shared.agent_factory import create_chat_client
from shared.config import settings
from shared.context import current_user_role
from shared.context_providers import ECommerceContextProvider
from shared.middleware import build_specialist_middleware
from shared.oauth.service_client import acquire_service_token, build_mcp_http_client, set_mcp_auth_header
from shared.tools.inventory_tools import check_stock, get_warehouse_availability
from shared.tools.memory_tools import recall_memories, store_memory
from shared.tools.pricing_tools import get_price_history
from shared.tools.user_tools import get_purchase_history, get_user_profile

# Set once by create_product_discovery_agent() when MCP_ENABLED+MCP_AUTH_ENABLED;
# refresh_mcp_auth() (called from the async startup hook in main.py) sets its
# Authorization header once a token is acquired — agent construction itself
# is synchronous and can't await the client-credentials grant.
_mcp_product_http_client = None

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
    store_memory,
    recall_memories,
]


def create_product_discovery_agent() -> Agent:
    """Create the Product Discovery ChatAgent.

    Uses the MCP server when ``MCP_ENABLED=true``, direct asyncpg tools otherwise.
    """
    global _mcp_product_http_client
    if settings.MCP_ENABLED:
        # MCP path: core product tools (including price history) come from
        # the MCP server. Semantic search and user-context tools still run
        # locally since they depend on pgvector / ContextVars not exposed by
        # the MCP server — get_price_history is NOT one of these (the MCP
        # server already has its own version; registering both under the
        # same name raises "Duplicate tool name" at agent-construction time).
        if settings.MCP_AUTH_ENABLED:
            _mcp_product_http_client = build_mcp_http_client()
        mcp_product = MCPStreamableHTTPTool(
            name="product-mcp",
            url=settings.MCP_PRODUCT_SERVER_URL,
            description="Product catalog data via MCP",
            http_client=_mcp_product_http_client,
        )
        tools: list = [
            mcp_product,
            semantic_search,
            find_similar_products,
            check_stock,
            get_user_profile,
            get_purchase_history,
        ]
    else:
        tools = AGENT_TOOLS  # type: ignore[assignment]

    return Agent(
        client=create_chat_client(),
        name="product-discovery",
        description="Natural language product search, semantic similarity, recommendations, and price tracking.",
        instructions=get_system_prompt(current_user_role.get() or "customer"),
        tools=tools,
        context_providers=[ECommerceContextProvider()],
        middleware=build_specialist_middleware(),
    )


async def refresh_mcp_auth() -> None:
    """Acquire (or refresh) the ``mcp:product`` service token and set it as
    the default Authorization header on the shared MCP http client. Called
    once from the async startup hook in ``main.py`` — agent construction
    itself is synchronous and can't await the client-credentials grant."""
    if _mcp_product_http_client is None:
        return
    token = await acquire_service_token(settings.MCP_PRODUCT_REQUIRED_SCOPE, settings.MCP_PRODUCT_AUDIENCE)
    set_mcp_auth_header(_mcp_product_http_client, token)
