"""Product Discovery agent — A2A host entry point."""

from contextlib import asynccontextmanager

from agent_framework_a2a import A2AAgentHost

from product_discovery.agent import create_product_discovery_agent
from shared.auth import AgentAuthMiddleware
from shared.db import close_db_pool, init_db_pool
from shared.telemetry import instrument_starlette, setup_telemetry

agent = create_product_discovery_agent()


@asynccontextmanager
async def lifespan(app):
    setup_telemetry("agentbazaar.product-discovery")
    instrument_starlette(app)
    await init_db_pool()
    yield
    await close_db_pool()


host = A2AAgentHost(
    agent=agent,
    port=8081,
    lifespan=lifespan,
)
app = host.app
app.add_middleware(AgentAuthMiddleware, agent_name="product-discovery")
