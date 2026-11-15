"""Static guardrail policy: which tool outputs carry untrusted content.

Only tools whose results include user-generated / stored text are sanitized;
everything else passes through untouched so structured/numeric results are
never mangled. Tool names match the ``@tool(name=...)`` registrations.

Mapping value is the set of free-text field names to neutralize (at any nesting
depth). Use ``None`` to neutralize *every* string in that tool's result.
"""

from __future__ import annotations

# tool name -> field-name allowlist (None = sanitize all strings)
SANITIZE_TOOLS: dict[str, set[str] | None] = {
    # review-sentiment: review bodies/titles are the top stored-injection vector
    "get_product_reviews": {"title", "body", "review", "comment", "reviewer", "reviewer_name"},
    "analyze_sentiment": {"title", "body", "summary", "quote", "quotes", "theme", "themes", "comment"},
    "search_reviews": {"title", "body", "reviewer", "reviewer_name"},
    "get_sentiment_by_topic": {"title", "body", "summary", "quote", "quotes", "comment"},
    "get_sentiment_trend": {"title", "body", "summary"},
    "compare_product_reviews": {"title", "body", "summary", "comment", "product_name"},
    "draft_seller_response": {"review_title", "review_body", "reviewer", "response_template"},
    # body_preview is the first 100 chars of the review body, i.e. review text.
    "detect_fake_reviews": {"title", "body", "body_preview", "comment", "reviewer", "reviewer_name"},
    # product-discovery: descriptions/specs are seller-editable free text
    "search_products": {"name", "description", "specs", "features"},
    "get_product_details": {"name", "description", "specs", "features"},
    "find_similar_products": {"name", "description"},
    "semantic_search": {"name", "description"},
    "get_trending_products": {"name", "description"},
    "compare_products": {"name", "description", "specs", "features"},
    # order-management: free-text order notes / status history
    "get_order_details": {"note", "notes", "reason", "comment", "description"},
    "get_user_orders": {"note", "notes", "reason"},
    "get_order_tracking": {"notes", "location"},
}

# Every registered tool that is deliberately NOT sanitized: its result is
# structured / numeric / system-generated, or it is a write whose result echoes
# nothing user-authored. ``tests/test_guardrails_tool_coverage.py`` asserts that
# each registered tool appears in exactly one of SANITIZE_TOOLS / NO_SANITIZE,
# so adding a tool forces a decision here instead of silently skipping it.
NO_SANITIZE: set[str] = {
    # shared lookups / user context
    "find_product_by_name",
    "check_stock",
    "get_warehouse_availability",
    "get_price_history",
    "get_user_profile",
    "get_purchase_history",
    "store_memory",
    "recall_memories",
    # order-management: mutations, returns, cart
    "cancel_order",
    "modify_order",
    "check_return_eligibility",
    "initiate_return",
    "process_refund",
    "get_return_status",
    "add_to_cart",
    "get_cart",
    "remove_from_cart",
    "update_cart_quantity",
    "set_shipping_address",
    "set_billing_address",
    "set_billing_same_as_shipping",
    "apply_coupon_to_cart",
    # pricing-promotions
    "validate_coupon",
    "optimize_cart",
    "get_active_deals",
    "check_bundle_eligibility",
    "get_loyalty_tier",
    "calculate_loyalty_discount",
    "get_loyalty_benefits",
    # inventory-fulfillment
    "get_restock_schedule",
    "estimate_shipping",
    "compare_carriers",
    "get_tracking_status",
    "calculate_fulfillment_plan",
    "place_backorder",
    # orchestrator
    "call_specialist_agent",
}
