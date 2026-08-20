"""Review & Sentiment agent definition."""

from agent_framework import Agent

from review_sentiment.prompts import get_system_prompt
from review_sentiment.tools import (
    analyze_sentiment,
    compare_product_reviews,
    detect_fake_reviews,
    draft_seller_response,
    get_product_reviews,
    get_sentiment_by_topic,
    get_sentiment_trend,
    search_reviews,
)
from shared.agent_factory import create_chat_client
from shared.context import current_user_role
from shared.context_providers import ECommerceContextProvider
from shared.middleware import build_specialist_middleware
from shared.tools.memory_tools import recall_memories, store_memory
from shared.tools.product_lookup_tools import find_product_by_name
from shared.tools.user_tools import get_purchase_history, get_user_profile

AGENT_TOOLS = [
    find_product_by_name,
    get_product_reviews,
    analyze_sentiment,
    get_sentiment_by_topic,
    get_sentiment_trend,
    detect_fake_reviews,
    search_reviews,
    draft_seller_response,
    compare_product_reviews,
    get_user_profile,
    get_purchase_history,
    store_memory,
    recall_memories,
]


def create_review_sentiment_agent() -> Agent:
    """Create the Review & Sentiment ChatAgent with all tools."""
    return Agent(
        client=create_chat_client(),
        name="review-sentiment",
        description=(
            "Product review analysis with sentiment breakdown, topic insights, trend tracking, "
            "fake review detection, and cross-product comparisons."
        ),
        instructions=get_system_prompt(current_user_role.get() or "customer"),
        tools=AGENT_TOOLS,
        context_providers=[ECommerceContextProvider()],
        middleware=build_specialist_middleware(),
    )
