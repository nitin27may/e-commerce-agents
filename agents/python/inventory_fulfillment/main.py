"""Inventory & Fulfillment agent — entry point."""

from inventory_fulfillment.agent import create_inventory_fulfillment_agent, refresh_mcp_auth
from shared.agent_host import create_agent_app
from shared.auth import AgentAuthMiddleware
from shared.config import settings
from shared.db import close_db_pool, init_db_pool
from shared.telemetry import instrument_fastapi, setup_telemetry

agent = create_inventory_fulfillment_agent()


async def on_startup(app):
    setup_telemetry("ecommerce.inventory-fulfillment")
    instrument_fastapi(app)
    await init_db_pool()
    if settings.MCP_ENABLED and settings.MCP_AUTH_ENABLED:
        await refresh_mcp_auth()


app = create_agent_app(
    agent=agent,
    agent_name="inventory-fulfillment",
    port=8085,
    description="Stock checking, shipping estimation, carrier comparison, fulfillment planning.",
    on_startup=on_startup,
    on_shutdown=close_db_pool,
)
app.add_middleware(AgentAuthMiddleware, agent_name="inventory-fulfillment")
