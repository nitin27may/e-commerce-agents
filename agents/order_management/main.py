"""Order Management agent — A2A host entry point."""

from contextlib import asynccontextmanager

from agent_framework_a2a import A2AAgentHost

from order_management.agent import create_order_management_agent
from shared.auth import AgentAuthMiddleware
from shared.db import close_db_pool, init_db_pool
from shared.telemetry import instrument_starlette, setup_telemetry

agent = create_order_management_agent()


@asynccontextmanager
async def lifespan(app):
    setup_telemetry("agentbazaar.order-management")
    instrument_starlette(app)
    await init_db_pool()
    yield
    await close_db_pool()


host = A2AAgentHost(
    agent=agent,
    port=8082,
    lifespan=lifespan,
)
app = host.app
app.add_middleware(AgentAuthMiddleware, agent_name="order-management")
