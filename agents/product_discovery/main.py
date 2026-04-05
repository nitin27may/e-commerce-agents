"""Product Discovery agent — entry point."""

from product_discovery.agent import AGENT_TOOLS, create_product_discovery_agent
from shared.agent_host import create_agent_app
from shared.auth import AgentAuthMiddleware
from shared.db import close_db_pool, init_db_pool
from shared.telemetry import instrument_fastapi, setup_telemetry

agent = create_product_discovery_agent()


async def on_startup(app):
    setup_telemetry("agentbazaar.product-discovery")
    instrument_fastapi(app)
    await init_db_pool()


app = create_agent_app(
    agent=agent,
    agent_name="product-discovery",
    port=8081,
    description="Natural language product search with personalized recommendations.",
    tools=AGENT_TOOLS,
    on_startup=on_startup,
    on_shutdown=close_db_pool,
)
app.add_middleware(AgentAuthMiddleware, agent_name="product-discovery")
