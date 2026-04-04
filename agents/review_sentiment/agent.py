"""Review & Sentiment agent definition."""

from agent_framework import Agent

from review_sentiment.prompts import SYSTEM_PROMPT
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
from shared.context_providers import ECommerceContextProvider
from shared.tools.user_tools import get_purchase_history, get_user_profile


def create_review_sentiment_agent() -> Agent:
    """Create the Review & Sentiment ChatAgent with all tools."""
    return Agent(
        client=create_chat_client(),
        name="review-sentiment",
        description="Product review analysis with sentiment breakdown, topic insights, trend tracking, fake review detection, and cross-product comparisons.",
        instructions=SYSTEM_PROMPT,
        tools=[
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
        ],
        context_providers=[ECommerceContextProvider()],
    )
