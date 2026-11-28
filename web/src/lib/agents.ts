/**
 * Static metadata for every specialist agent and the orchestrator.
 * Single source of truth for the agents index, detail pages, and marketplace.
 */

import {
  Search,
  Package,
  Tag,
  Star,
  Warehouse,
  Network,
  type LucideIcon,
} from "lucide-react";

export interface AgentTool {
  name: string;
  description: string;
}

export interface AgentMeta {
  slug: string;
  /** Matches usage_logs.agent_name in the backend. */
  backendName: string;
  name: string;
  role: string;
  tagline: string;
  description: string;
  icon: LucideIcon;
  tools: AgentTool[];
  examplePrompts: string[];
}

export const AGENTS: AgentMeta[] = [
  {
    slug: "product-discovery",
    backendName: "product-discovery",
    name: "Product Discovery",
    role: "Search & Recommendations",
    tagline: "Natural language product search with semantic similarity and personalized recommendations.",
    description:
      "Product Discovery understands what you're looking for — whether you describe it precisely or vaguely. It combines keyword filtering, semantic pgvector embeddings, and price-trend data to surface the right products. It also reads your purchase history to personalize results.",
    icon: Search,
    tools: [
      { name: "search_products", description: "Keyword + filter search across the catalog by category, price range, rating, or any combination." },
      { name: "semantic_search", description: "Embedding-based similarity search (pgvector). Best for vague queries like 'something cozy for winter'." },
      { name: "get_product_details", description: "Full specs, pricing, stock status, and seller info for a specific product." },
      { name: "compare_products", description: "Side-by-side attribute comparison for 2–3 products." },
      { name: "find_similar_products", description: "Find products similar to a given one using cosine similarity on 1536-dim embeddings." },
      { name: "get_trending_products", description: "Top products by recent order volume, optionally filtered by category." },
      { name: "get_price_history", description: "30/60/90-day price trend with average, min, max, and a deal-quality signal." },
      { name: "check_stock", description: "Real-time stock levels across all regional warehouses." },
      { name: "get_user_profile", description: "Current user's loyalty tier and account details for personalization." },
      { name: "get_purchase_history", description: "Recent orders used to tailor recommendations to past interests." },
    ],
    examplePrompts: [
      "Find me wireless headphones under $200 with good reviews",
      "Something cozy for working from home",
      "Compare Sony WH-1000XM5 and Bose QC45",
      "What's the price trend on the DJI Mini 4 Pro?",
      "Show me trending electronics this week",
    ],
  },
  {
    slug: "order-management",
    backendName: "order-management",
    name: "Order Management",
    role: "Orders, Returns & Cart",
    tagline: "Full order lifecycle — tracking, cancellation, returns, refunds, and cart operations.",
    description:
      "Order Management handles everything after you click buy. It can look up any order, track shipments in real time, process cancellations and modifications for orders that haven't shipped, manage the full return and refund workflow, and handle your shopping cart — including applying coupons and setting delivery addresses.",
    icon: Package,
    tools: [
      { name: "get_user_orders", description: "List your recent orders with optional status filter (placed, shipped, delivered, cancelled, etc.)." },
      { name: "get_order_details", description: "Full order info including line items, pricing, status history, and shipping address." },
      { name: "get_order_tracking", description: "Live tracking status and carrier location for a specific order." },
      { name: "cancel_order", description: "Cancel an order — only available for orders in 'placed' or 'confirmed' status." },
      { name: "modify_order", description: "Update the shipping address on an order that hasn't shipped yet." },
      { name: "check_return_eligibility", description: "Check if an order or item qualifies for a return based on policy and purchase date." },
      { name: "initiate_return", description: "Start the return process and generate a return label." },
      { name: "process_refund", description: "Apply a refund once the return is received." },
      { name: "get_return_status", description: "Track the progress of an active return or refund." },
      { name: "add_to_cart", description: "Add a product to your cart by ID or product name." },
      { name: "get_cart", description: "Current cart contents with quantities, subtotal, and any applied discounts." },
      { name: "remove_from_cart", description: "Remove a specific item from your cart." },
      { name: "update_cart_quantity", description: "Change the quantity of an item already in your cart." },
      { name: "apply_coupon_to_cart", description: "Apply a coupon code to the cart and recalculate the total." },
      { name: "set_shipping_address", description: "Set or update the delivery address for your cart." },
    ],
    examplePrompts: [
      "Where is my latest order?",
      "Cancel order #55e8400 — I ordered the wrong size",
      "Can I return the jacket I bought last week?",
      "Add 2 Sony headphones to my cart",
      "What's in my cart right now?",
    ],
  },
  {
    slug: "pricing-promotions",
    backendName: "pricing-promotions",
    name: "Pricing & Promotions",
    role: "Deals, Coupons & Loyalty",
    tagline: "Coupon validation, cart optimization, loyalty discounts, and bundle deal discovery.",
    description:
      "Pricing & Promotions finds you the best deal. It validates coupon codes, identifies active promotions, checks bundle eligibility, and calculates your loyalty tier discount. The cart optimizer goes further — it evaluates every applicable discount and returns the combination that saves you the most.",
    icon: Tag,
    tools: [
      { name: "validate_coupon", description: "Check a coupon code against expiry, minimum spend, usage limits, and category restrictions." },
      { name: "optimize_cart", description: "Evaluate all available coupons, promotions, and loyalty discounts and return the optimal combination." },
      { name: "get_active_deals", description: "List all currently active promotions and non-expired public coupons." },
      { name: "check_bundle_eligibility", description: "Determine if a set of products qualifies for a bundle promotion." },
      { name: "get_loyalty_tier", description: "Your current loyalty tier (bronze/silver/gold) and the benefits it unlocks." },
      { name: "calculate_loyalty_discount", description: "Calculate the loyalty discount amount on a given cart total." },
      { name: "get_loyalty_benefits", description: "Compare all tiers — spend thresholds, discount percentages, free shipping, priority support." },
      { name: "get_price_history", description: "Historical price data to verify whether a deal is genuinely a good price." },
      { name: "get_purchase_history", description: "Past orders used to calculate your loyalty status and eligible rewards." },
    ],
    examplePrompts: [
      "Do I have any coupons I can use today?",
      "What's the best deal on my cart right now?",
      "Check if coupon code SAVE20 is valid",
      "What loyalty tier am I on and what are the benefits?",
      "What bundles qualify with my current cart?",
    ],
  },
  {
    slug: "review-sentiment",
    backendName: "review-sentiment",
    name: "Review & Sentiment",
    role: "Reviews & Sentiment Analysis",
    tagline: "Deep review analysis — sentiment breakdown, topic insights, trend tracking, and fake review detection.",
    description:
      "Review & Sentiment goes beyond star ratings. It breaks reviews down by topic (quality, value, design, shipping), tracks sentiment over time, detects suspicious or fake reviews, and surfaces the genuine signal in customer feedback. Sellers can also use it to draft professional responses to reviews.",
    icon: Star,
    tools: [
      { name: "get_product_reviews", description: "Paginated product reviews with sorting by newest, helpfulness, or rating." },
      { name: "analyze_sentiment", description: "Aggregate sentiment: average rating, rating distribution, pros/cons summary from review text." },
      { name: "get_sentiment_by_topic", description: "Break reviews into topics (quality, value, shipping, design, durability) with mention counts and average rating per topic." },
      { name: "get_sentiment_trend", description: "Monthly average ratings over time to spot whether sentiment is improving or declining." },
      { name: "detect_fake_reviews", description: "Flag suspicious reviews by checking for verified-purchase status, generic language patterns, and rating outliers." },
      { name: "search_reviews", description: "Keyword search inside review titles and bodies for a specific product." },
      { name: "draft_seller_response", description: "Draft a professional seller response to a review (seller/admin role required)." },
      { name: "compare_product_reviews", description: "Cross-product review comparison — useful for deciding between two similar products." },
    ],
    examplePrompts: [
      "What are customers saying about the Sony WH-1000XM5?",
      "Are there any fake reviews on product X?",
      "Show me the sentiment trend for this product over 6 months",
      "What do reviewers say about the build quality?",
      "Compare reviews: Sony headphones vs Bose QC45",
    ],
  },
  {
    slug: "inventory-fulfillment",
    backendName: "inventory-fulfillment",
    name: "Inventory & Fulfillment",
    role: "Stock, Shipping & Logistics",
    tagline: "Real-time stock checks, shipping estimates, carrier comparison, and fulfillment planning.",
    description:
      "Inventory & Fulfillment gives you an accurate picture of stock levels across all three regional warehouses and what shipping options are available. It estimates delivery time and cost for your location, compares carriers, tracks shipments, and can calculate the optimal fulfillment plan for multi-item orders.",
    icon: Warehouse,
    tools: [
      { name: "check_stock", description: "Live stock levels across East, Central, and West warehouses for a product." },
      { name: "get_warehouse_availability", description: "Warehouse inventory plus upcoming restock schedules." },
      { name: "get_restock_schedule", description: "Upcoming restock dates and expected quantities across all warehouses." },
      { name: "estimate_shipping", description: "Shipping cost and delivery time from the nearest stocked warehouse to your region." },
      { name: "compare_carriers", description: "Side-by-side carrier comparison (Standard, Express, Overnight) with pricing and ETAs." },
      { name: "get_tracking_status", description: "Latest shipment tracking and carrier location for an order." },
      { name: "calculate_fulfillment_plan", description: "Optimal warehouse allocation for a multi-item order with total shipping cost estimate." },
      { name: "place_backorder", description: "Place a backorder on an out-of-stock product — only creates the backorder if truly unavailable." },
    ],
    examplePrompts: [
      "Is the DJI Mini 4 Pro in stock?",
      "How long would it take to ship to the East Coast?",
      "Compare Standard vs Express shipping for my order",
      "When will the Dyson V15 be back in stock?",
      "What's the cheapest way to ship 3 items to the West?",
    ],
  },
  {
    slug: "orchestrator",
    backendName: "orchestrator",
    name: "Orchestrator",
    role: "Routing & Coordination",
    tagline: "The front door — routes every request to the right specialist via A2A protocol.",
    description:
      "The Orchestrator is the entry point for every conversation. It classifies your intent, selects the right specialist agent (or combination of specialists), and coordinates the response. It uses the A2A protocol to call each specialist over HTTP, forwarding your identity and session context so specialists can provide personalized, context-aware answers.",
    icon: Network,
    tools: [
      {
        name: "call_specialist_agent",
        description:
          "Route a request to a specialist (product-discovery, order-management, pricing-promotions, review-sentiment, inventory-fulfillment) via HTTP POST to the specialist's A2A endpoint. Forwards user identity, role, and session context in headers.",
      },
    ],
    examplePrompts: [
      "Find wireless headphones under $200 with great reviews and check if they're in stock",
      "What's the best deal on my cart and when will it arrive?",
      "Show me reviews for the top-rated item in my price range",
    ],
  },
];

/** Look up an agent by slug. Returns undefined for unknown slugs. */
export function getAgent(slug: string): AgentMeta | undefined {
  return AGENTS.find((a) => a.slug === slug);
}
