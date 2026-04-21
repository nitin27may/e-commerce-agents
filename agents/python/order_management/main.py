"""Order Management agent — entry point."""

from order_management.agent import AGENT_TOOLS, create_order_management_agent
from shared.agent_host import create_agent_app
from shared.auth import AgentAuthMiddleware
from shared.db import close_db_pool, init_db_pool
from shared.telemetry import instrument_fastapi, setup_telemetry

agent = create_order_management_agent()


async def on_startup(app):
    setup_telemetry("ecommerce.order-management")
    instrument_fastapi(app)
    await init_db_pool()


app = create_agent_app(
    agent=agent,
    agent_name="order-management",
    port=8082,
    description="Order tracking, cancellation, modification, returns, and refund processing.",
    tools=AGENT_TOOLS,
    on_startup=on_startup,
    on_shutdown=close_db_pool,
)
app.add_middleware(AgentAuthMiddleware, agent_name="order-management")
