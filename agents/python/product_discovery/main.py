"""Product Discovery agent — entry point."""

from product_discovery.agent import create_product_discovery_agent, refresh_mcp_auth
from shared.agent_host import create_agent_app
from shared.auth import AgentAuthMiddleware
from shared.config import settings
from shared.db import close_db_pool, init_db_pool
from shared.telemetry import instrument_fastapi, setup_telemetry

agent = create_product_discovery_agent()


async def on_startup(app):
    setup_telemetry("ecommerce.product-discovery")
    instrument_fastapi(app)
    await init_db_pool()
    if settings.MCP_ENABLED and settings.MCP_AUTH_ENABLED:
        await refresh_mcp_auth()


app = create_agent_app(
    agent=agent,
    agent_name="product-discovery",
    port=8081,
    description="Natural language product search with personalized recommendations.",
    on_startup=on_startup,
    on_shutdown=close_db_pool,
)
app.add_middleware(AgentAuthMiddleware, agent_name="product-discovery")
