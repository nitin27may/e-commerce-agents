"""Intent classification types and agent routing map.

The LLM handles actual intent classification via the system prompt and tool selection.
These types exist for structured logging and analytics.
"""

from __future__ import annotations

from enum import StrEnum


class Intent(StrEnum):
    """User intent categories for analytics and routing."""

    PRODUCT_QUESTION = "product_question"
    ORDER_INQUIRY = "order_inquiry"
    RETURN_REQUEST = "return_request"
    PRICING_QUESTION = "pricing_question"
    REVIEW_QUESTION = "review_question"
    SHIPPING_QUESTION = "shipping_question"
    COMPLAINT = "complaint"
    GENERAL_FAQ = "general_faq"


INTENT_TO_AGENT: dict[Intent, str] = {
    Intent.PRODUCT_QUESTION: "product-discovery",
    Intent.ORDER_INQUIRY: "order-management",
    Intent.RETURN_REQUEST: "order-management",
    Intent.PRICING_QUESTION: "pricing-promotions",
    Intent.REVIEW_QUESTION: "review-sentiment",
    Intent.SHIPPING_QUESTION: "inventory-fulfillment",
    Intent.COMPLAINT: "orchestrator",
    Intent.GENERAL_FAQ: "orchestrator",
}
