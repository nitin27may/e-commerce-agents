"""Review & Sentiment agent — A2A host entry point."""

from contextlib import asynccontextmanager

from agent_framework_a2a import A2AAgentHost

from review_sentiment.agent import create_review_sentiment_agent
from shared.auth import AgentAuthMiddleware
from shared.db import close_db_pool, init_db_pool
from shared.telemetry import instrument_starlette, setup_telemetry

agent = create_review_sentiment_agent()


@asynccontextmanager
async def lifespan(app):
    setup_telemetry("agentbazaar.review-sentiment")
    instrument_starlette(app)
    await init_db_pool()
    yield
    await close_db_pool()


host = A2AAgentHost(
    agent=agent,
    port=8084,
    lifespan=lifespan,
)
app = host.app
app.add_middleware(AgentAuthMiddleware, agent_name="review-sentiment")
