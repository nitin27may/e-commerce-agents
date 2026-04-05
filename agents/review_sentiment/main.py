"""Review & Sentiment agent — entry point."""

from review_sentiment.agent import AGENT_TOOLS, create_review_sentiment_agent
from shared.agent_host import create_agent_app
from shared.auth import AgentAuthMiddleware
from shared.db import close_db_pool, init_db_pool
from shared.telemetry import instrument_fastapi, setup_telemetry

agent = create_review_sentiment_agent()


async def on_startup(app):
    setup_telemetry("agentbazaar.review-sentiment")
    instrument_fastapi(app)
    await init_db_pool()


app = create_agent_app(
    agent=agent,
    agent_name="review-sentiment",
    port=8084,
    description="Review analysis, sentiment breakdown, fake review detection.",
    tools=AGENT_TOOLS,
    on_startup=on_startup,
    on_shutdown=close_db_pool,
)
app.add_middleware(AgentAuthMiddleware, agent_name="review-sentiment")
