"""Pricing & Promotions agent — entry point."""

from pricing_promotions.agent import AGENT_TOOLS, create_pricing_promotions_agent
from shared.agent_host import create_agent_app
from shared.auth import AgentAuthMiddleware
from shared.db import close_db_pool, init_db_pool
from shared.telemetry import instrument_fastapi, setup_telemetry

agent = create_pricing_promotions_agent()


async def on_startup(app):
    setup_telemetry("ecommerce.pricing-promotions")
    instrument_fastapi(app)
    await init_db_pool()


app = create_agent_app(
    agent=agent,
    agent_name="pricing-promotions",
    port=8083,
    description="Coupon validation, cart optimization, loyalty discounts, and deal discovery.",
    tools=AGENT_TOOLS,
    on_startup=on_startup,
    on_shutdown=close_db_pool,
)
app.add_middleware(AgentAuthMiddleware, agent_name="pricing-promotions")
