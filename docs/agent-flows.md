# Agent Collaboration Flows

Detailed sequence diagrams for five multi-agent collaboration scenarios in AgentBazaar. Each flow demonstrates how the Orchestrator classifies intent, routes to multiple specialist agents via A2A protocol, and synthesizes a unified response.

---

## Flow 1: Return and Replace

**User**: "Return my jacket and find me a warmer one"

This flow spans four agents: Order Management checks return eligibility and initiates the return, Product Discovery finds warmer alternatives, Inventory confirms stock, and Pricing applies any return credit.

```mermaid
sequenceDiagram
    actor User
    participant FE as Next.js Frontend
    participant ORCH as Orchestrator<br/>(FastAPI :8080)
    participant LLM as OpenAI / Azure OpenAI
    participant OM as Order Management<br/>(:8082)
    participant PD as Product Discovery<br/>(:8081)
    participant IF as Inventory & Fulfillment<br/>(:8085)
    participant PP as Pricing & Promotions<br/>(:8083)
    participant DB as PostgreSQL

    User->>FE: "Return my jacket and find me a warmer one"
    FE->>ORCH: POST /api/chat<br/>Authorization: Bearer {JWT}

    Note over ORCH: JWT validated, ContextVars set<br/>ECommerceContextProvider loads<br/>user profile + recent orders

    ORCH->>LLM: ChatAgent.run()<br/>Intent: return + product search
    LLM-->>ORCH: Tool call: call_specialist_agent<br/>(order-management, "User wants to return their jacket")

    rect rgb(15, 118, 110)
        Note over ORCH,OM: A2A Call 1: Order Management
        ORCH->>OM: POST /message:send<br/>X-Agent-Secret + X-User-Email
        OM->>LLM: ChatAgent.run()
        LLM-->>OM: Tool: get_user_orders()
        OM->>DB: SELECT orders + order_items<br/>WHERE user_email, category='Clothing'
        DB-->>OM: Recent jacket order found
        LLM-->>OM: Tool: check_return_eligibility(order_id)
        OM->>DB: Check order date vs 30-day window
        DB-->>OM: Eligible for return
        LLM-->>OM: Tool: initiate_return(order_id, reason)
        OM->>DB: INSERT INTO returns<br/>status='requested'
        DB-->>OM: Return created, label generated
        OM-->>ORCH: Return initiated, refund: $89.99 store credit<br/>Return label URL provided
    end

    ORCH->>LLM: Process OM response, continue routing
    LLM-->>ORCH: Tool call: call_specialist_agent<br/>(product-discovery, "Find warmer jackets")

    rect rgb(13, 148, 136)
        Note over ORCH,PD: A2A Call 2: Product Discovery
        ORCH->>PD: POST /message:send
        PD->>LLM: ChatAgent.run()
        LLM-->>PD: Tool: semantic_search("warm winter jacket insulated")
        PD->>DB: Generate embedding -> pgvector cosine similarity
        DB-->>PD: Top 5 matching jackets
        LLM-->>PD: Tool: get_product_details(top_match_id)
        PD->>DB: SELECT product details + specs
        DB-->>PD: Full product info
        PD-->>ORCH: 3 recommended warmer jackets<br/>with prices, ratings, specs
    end

    ORCH->>LLM: Process PD response, continue routing
    LLM-->>ORCH: Tool call: call_specialist_agent<br/>(inventory-fulfillment, "Check stock for recommended jackets")

    rect rgb(100, 116, 139)
        Note over ORCH,IF: A2A Call 3: Inventory & Fulfillment
        ORCH->>IF: POST /message:send
        IF->>LLM: ChatAgent.run()
        LLM-->>IF: Tool: check_stock(product_ids)
        IF->>DB: SELECT warehouse_inventory<br/>for each product
        DB-->>IF: Stock levels per warehouse
        LLM-->>IF: Tool: estimate_shipping(product_id, address)
        IF->>DB: SELECT shipping_rates by region
        DB-->>IF: Delivery estimates
        IF-->>ORCH: Stock confirmed for 2/3 jackets<br/>Delivery: 3-5 business days
    end

    ORCH->>LLM: Process IF response, continue routing
    LLM-->>ORCH: Tool call: call_specialist_agent<br/>(pricing-promotions, "Apply return credit to new purchase")

    rect rgb(217, 119, 6)
        Note over ORCH,PP: A2A Call 4: Pricing & Promotions
        ORCH->>PP: POST /message:send
        PP->>LLM: ChatAgent.run()
        LLM-->>PP: Tool: get_loyalty_tier(user_email)
        PP->>DB: SELECT loyalty_tier FROM users
        DB-->>PP: Silver tier (5% discount)
        LLM-->>PP: Tool: get_active_deals()
        PP->>DB: SELECT active promotions + coupons
        DB-->>PP: Winter sale: 15% off outerwear
        PP-->>ORCH: $89.99 store credit + 5% loyalty + 15% winter sale
    end

    ORCH->>LLM: Synthesize all specialist responses
    Note over LLM: Combines return confirmation,<br/>jacket recommendations,<br/>stock status, and pricing<br/>into one natural response

    ORCH->>DB: Persist conversation + usage logs
    ORCH-->>FE: Combined response
    FE-->>User: "Your jacket return has been initiated ($89.99 credit).<br/>Here are 2 warmer options in stock..."
```

---

## Flow 2: Pre-Purchase Research

**User**: "Should I buy the Sony WH-1000XM5?"

This research flow calls Review & Sentiment for opinion analysis, Product Discovery for alternatives, and Pricing & Promotions for current deals -- giving the user a complete purchase decision brief.

```mermaid
sequenceDiagram
    actor User
    participant FE as Next.js Frontend
    participant ORCH as Orchestrator<br/>(FastAPI :8080)
    participant LLM as OpenAI / Azure OpenAI
    participant RS as Review & Sentiment<br/>(:8084)
    participant PD as Product Discovery<br/>(:8081)
    participant PP as Pricing & Promotions<br/>(:8083)
    participant DB as PostgreSQL

    User->>FE: "Should I buy the Sony WH-1000XM5?"
    FE->>ORCH: POST /api/chat

    Note over ORCH: Intent: review analysis +<br/>product comparison + pricing

    ORCH->>LLM: ChatAgent.run()
    LLM-->>ORCH: Tool call: call_specialist_agent<br/>(review-sentiment, "Analyze reviews for Sony WH-1000XM5")

    rect rgb(15, 118, 110)
        Note over ORCH,RS: A2A Call 1: Review & Sentiment
        ORCH->>RS: POST /message:send
        RS->>LLM: ChatAgent.run()
        LLM-->>RS: Tool: get_product_reviews(product_id)
        RS->>DB: SELECT reviews WHERE product_id<br/>ORDER BY helpful_count DESC
        DB-->>RS: 47 reviews, avg 4.6 stars
        LLM-->>RS: Tool: analyze_sentiment(product_id)
        RS->>DB: Aggregate ratings, extract themes
        DB-->>RS: Sentiment breakdown by aspect
        LLM-->>RS: Tool: get_sentiment_by_topic(product_id)
        RS->>DB: Topic extraction from review body
        DB-->>RS: Topics: noise cancellation (92% pos),<br/>comfort (88% pos), battery (95% pos),<br/>call quality (61% pos)
        LLM-->>RS: Tool: detect_fake_reviews(product_id)
        RS->>DB: Flag suspicious patterns<br/>(low helpful_count, similar text)
        DB-->>RS: 2 reviews flagged, 45 verified
        RS-->>ORCH: 4.6/5 stars (45 verified reviews)<br/>Strengths: ANC, battery, comfort<br/>Weakness: call quality in wind<br/>Authenticity: 96% genuine
    end

    ORCH->>LLM: Process RS response, continue routing
    LLM-->>ORCH: Tool call: call_specialist_agent<br/>(product-discovery, "Find alternatives to Sony WH-1000XM5")

    rect rgb(13, 148, 136)
        Note over ORCH,PD: A2A Call 2: Product Discovery
        ORCH->>PD: POST /message:send
        PD->>LLM: ChatAgent.run()
        LLM-->>PD: Tool: find_similar_products(product_id)
        PD->>DB: pgvector similarity search<br/>on product embedding
        DB-->>PD: Bose QC Ultra, Apple AirPods Max,<br/>Sennheiser Momentum 4
        LLM-->>PD: Tool: compare_products([sony, bose, sennheiser])
        PD->>DB: SELECT specs for all 3 products
        DB-->>PD: Side-by-side comparison data
        PD-->>ORCH: 3 alternatives with comparison:<br/>price, rating, key specs
    end

    ORCH->>LLM: Process PD response, continue routing
    LLM-->>ORCH: Tool call: call_specialist_agent<br/>(pricing-promotions, "Current deals for Sony WH-1000XM5")

    rect rgb(217, 119, 6)
        Note over ORCH,PP: A2A Call 3: Pricing & Promotions
        ORCH->>PP: POST /message:send
        PP->>LLM: ChatAgent.run()
        LLM-->>PP: Tool: get_active_deals()
        PP->>DB: SELECT promotions WHERE<br/>category includes Electronics
        DB-->>PP: Spring tech sale active
        LLM-->>PP: Tool: get_price_history(product_id)
        PP->>DB: SELECT price_history<br/>ORDER BY recorded_at DESC
        DB-->>PP: Current: $349, was $399,<br/>all-time low: $299
        LLM-->>PP: Tool: get_loyalty_tier(user_email)
        PP->>DB: SELECT loyalty_tier FROM users
        DB-->>PP: Gold tier = 10% discount
        PP-->>ORCH: Current: $349 (12% off MSRP)<br/>Gold loyalty: additional 10%<br/>Final price: ~$314<br/>All-time low was $299 (Black Friday)
    end

    ORCH->>LLM: Synthesize purchase decision brief
    Note over LLM: Combines review sentiment,<br/>alternatives comparison,<br/>and pricing into a<br/>clear buy/wait recommendation

    ORCH->>DB: Persist conversation
    ORCH-->>FE: Research summary
    FE-->>User: "The Sony WH-1000XM5 is highly rated (4.6/5)...<br/>Current deal brings it to ~$314 with your Gold discount...<br/>Here's how it compares to alternatives..."
```

---

## Flow 3: Where's My Order

**User**: "Where's my order?"

A focused two-agent flow: Order Management retrieves order details and tracking, then Inventory & Fulfillment checks the real-time carrier status and delivery estimate.

```mermaid
sequenceDiagram
    actor User
    participant FE as Next.js Frontend
    participant ORCH as Orchestrator<br/>(FastAPI :8080)
    participant LLM as OpenAI / Azure OpenAI
    participant OM as Order Management<br/>(:8082)
    participant IF as Inventory & Fulfillment<br/>(:8085)
    participant DB as PostgreSQL

    User->>FE: "Where's my order?"
    FE->>ORCH: POST /api/chat

    Note over ORCH: Intent: order tracking<br/>ECommerceContextProvider injects<br/>recent orders into context

    ORCH->>LLM: ChatAgent.run()
    Note over LLM: Context already contains<br/>recent orders -- selects<br/>most recent active order
    LLM-->>ORCH: Tool call: call_specialist_agent<br/>(order-management, "Track user's most recent order")

    rect rgb(15, 118, 110)
        Note over ORCH,OM: A2A Call 1: Order Management
        ORCH->>OM: POST /message:send
        OM->>LLM: ChatAgent.run()
        LLM-->>OM: Tool: get_user_orders()
        OM->>DB: SELECT orders WHERE user_id<br/>ORDER BY created_at DESC LIMIT 5
        DB-->>OM: Most recent: Order #a1b2c3<br/>Status: shipped, Total: $149.99
        LLM-->>OM: Tool: get_order_details(order_id)
        OM->>DB: SELECT order + items + status_history
        DB-->>OM: 2 items, shipped 2 days ago<br/>Carrier: Express, Tracking: EX123456
        LLM-->>OM: Tool: get_order_tracking(order_id)
        OM->>DB: SELECT order_status_history<br/>ORDER BY timestamp
        DB-->>OM: placed -> confirmed -> shipped<br/>Last location: Memphis, TN
        OM-->>ORCH: Order #a1b2c3: shipped via Express<br/>Tracking: EX123456<br/>Last seen: Memphis, TN (2 days ago)
    end

    ORCH->>LLM: Process OM response, get delivery estimate
    LLM-->>ORCH: Tool call: call_specialist_agent<br/>(inventory-fulfillment, "Carrier status and ETA for tracking EX123456")

    rect rgb(100, 116, 139)
        Note over ORCH,IF: A2A Call 2: Inventory & Fulfillment
        ORCH->>IF: POST /message:send
        IF->>LLM: ChatAgent.run()
        LLM-->>IF: Tool: get_tracking_status(tracking_number)
        IF->>DB: SELECT order_status_history<br/>+ carrier info
        DB-->>IF: In transit, last scan Memphis hub
        LLM-->>IF: Tool: estimate_shipping(product_id, address)
        IF->>DB: SELECT shipping_rates<br/>carrier speed + regions
        DB-->>IF: Express: 2-3 days remaining
        IF-->>ORCH: In transit via Memphis hub<br/>Estimated delivery: 2-3 business days<br/>On track, no delays detected
    end

    ORCH->>LLM: Synthesize tracking response
    ORCH->>DB: Persist conversation
    ORCH-->>FE: Tracking summary
    FE-->>User: "Your order #a1b2c3 ($149.99) is on its way!<br/>It's currently in Memphis, TN via Express shipping.<br/>Expected delivery: April 7-8.<br/>Tracking: EX123456"
```

---

## Flow 4: Stock Alert

**User**: "The Dyson V15 is out of stock, when will it be available?"

Inventory & Fulfillment checks stock levels and restock schedules, then Product Discovery finds in-stock alternatives if the wait is too long.

```mermaid
sequenceDiagram
    actor User
    participant FE as Next.js Frontend
    participant ORCH as Orchestrator<br/>(FastAPI :8080)
    participant LLM as OpenAI / Azure OpenAI
    participant IF as Inventory & Fulfillment<br/>(:8085)
    participant PD as Product Discovery<br/>(:8081)
    participant DB as PostgreSQL

    User->>FE: "The Dyson V15 is out of stock, when will it be available?"
    FE->>ORCH: POST /api/chat

    Note over ORCH: Intent: stock inquiry +<br/>restock schedule + alternatives

    ORCH->>LLM: ChatAgent.run()
    LLM-->>ORCH: Tool call: call_specialist_agent<br/>(inventory-fulfillment, "Check stock and restock for Dyson V15")

    rect rgb(100, 116, 139)
        Note over ORCH,IF: A2A Call 1: Inventory & Fulfillment
        ORCH->>IF: POST /message:send
        IF->>LLM: ChatAgent.run()
        LLM-->>IF: Tool: check_stock(product_id)
        IF->>DB: SELECT SUM(quantity)<br/>FROM warehouse_inventory<br/>WHERE product_id = $1
        DB-->>IF: Total stock: 0 units
        LLM-->>IF: Tool: get_warehouse_availability(product_id)
        IF->>DB: SELECT warehouse_inventory wi<br/>JOIN warehouses w<br/>WHERE product_id = $1
        DB-->>IF: East: 0, Central: 0, West: 0
        LLM-->>IF: Tool: get_restock_schedule(product_id)
        IF->>DB: SELECT restock_schedule<br/>WHERE product_id = $1<br/>AND expected_date > NOW()
        DB-->>IF: Central warehouse: 50 units<br/>Expected: April 12, 2026
        LLM-->>IF: Tool: place_backorder(product_id)
        Note over IF: Offer backorder option
        IF-->>ORCH: Out of stock at all warehouses<br/>Restock: ~50 units at Central warehouse<br/>Expected date: April 12<br/>Backorder available
    end

    ORCH->>LLM: Process IF response, find alternatives
    LLM-->>ORCH: Tool call: call_specialist_agent<br/>(product-discovery, "Find alternatives to Dyson V15 vacuum in stock")

    rect rgb(13, 148, 136)
        Note over ORCH,PD: A2A Call 2: Product Discovery
        ORCH->>PD: POST /message:send
        PD->>LLM: ChatAgent.run()
        LLM-->>PD: Tool: find_similar_products(dyson_v15_id)
        PD->>DB: pgvector similarity search<br/>on Dyson V15 embedding
        DB-->>PD: Similar vacuums ranked by similarity
        LLM-->>PD: Tool: search_products("cordless vacuum", min_rating=4.0)
        PD->>DB: SELECT products WHERE category<br/>AND rating >= 4.0 AND is_active
        DB-->>PD: 5 matching vacuums
        Note over PD: Agent cross-checks stock<br/>using shared inventory tool
        LLM-->>PD: Tool: check_stock(alternative_ids)
        PD->>DB: Check warehouse_inventory<br/>for each alternative
        DB-->>PD: 3 alternatives in stock
        PD-->>ORCH: 3 in-stock alternatives:<br/>Shark Stratos ($299, 4.5 stars)<br/>Samsung Jet 90 ($349, 4.3 stars)<br/>LG CordZero ($279, 4.4 stars)
    end

    ORCH->>LLM: Synthesize availability response
    ORCH->>DB: Persist conversation
    ORCH-->>FE: Stock alert + alternatives
    FE-->>User: "The Dyson V15 is currently out of stock.<br/>Restock expected: April 12 (~8 days).<br/>I can place a backorder for you.<br/><br/>In the meantime, here are similar options in stock:<br/>1. Shark Stratos - $299 (4.5 stars)..."
```

---

## Flow 5: Bulk Purchase Optimization

**User**: "I need headphones and a speaker for my team, 5 sets"

Product Discovery curates options, Pricing calculates bulk discounts and applicable coupons, and Inventory confirms stock availability for the requested quantity across warehouses.

```mermaid
sequenceDiagram
    actor User
    participant FE as Next.js Frontend
    participant ORCH as Orchestrator<br/>(FastAPI :8080)
    participant LLM as OpenAI / Azure OpenAI
    participant PD as Product Discovery<br/>(:8081)
    participant PP as Pricing & Promotions<br/>(:8083)
    participant IF as Inventory & Fulfillment<br/>(:8085)
    participant DB as PostgreSQL

    User->>FE: "I need headphones and a speaker for my team, 5 sets"
    FE->>ORCH: POST /api/chat

    Note over ORCH: Intent: product search +<br/>bulk pricing + stock for quantity

    ORCH->>LLM: ChatAgent.run()
    LLM-->>ORCH: Tool call: call_specialist_agent<br/>(product-discovery, "Curate headphones and speakers suitable for team/office use")

    rect rgb(13, 148, 136)
        Note over ORCH,PD: A2A Call 1: Product Discovery
        ORCH->>PD: POST /message:send
        PD->>LLM: ChatAgent.run()
        LLM-->>PD: Tool: search_products("office headphones", category="Electronics", sort_by="rating")
        PD->>DB: SELECT products WHERE category='Electronics'<br/>AND name ILIKE '%headphone%'<br/>ORDER BY rating DESC
        DB-->>PD: Top-rated headphones
        LLM-->>PD: Tool: search_products("bluetooth speaker office", category="Electronics", sort_by="rating")
        PD->>DB: SELECT products WHERE category='Electronics'<br/>AND name ILIKE '%speaker%'
        DB-->>PD: Top-rated speakers
        LLM-->>PD: Tool: compare_products([headphone_ids])
        PD->>DB: SELECT specs for comparison
        DB-->>PD: Side-by-side specs
        PD-->>ORCH: Recommended bundle:<br/>Headphones: Sony WH-1000XM5 ($349)<br/>or JBL Tune 770NC ($99)<br/>Speakers: JBL Charge 5 ($179)<br/>or Bose SoundLink Flex ($149)
    end

    ORCH->>LLM: Process PD response, check pricing
    LLM-->>ORCH: Tool call: call_specialist_agent<br/>(pricing-promotions, "Bulk pricing for 5 sets of headphones + speaker")

    rect rgb(217, 119, 6)
        Note over ORCH,PP: A2A Call 2: Pricing & Promotions
        ORCH->>PP: POST /message:send
        PP->>LLM: ChatAgent.run()
        LLM-->>PP: Tool: check_bundle_eligibility(product_ids)
        PP->>DB: SELECT promotions WHERE type='bundle'<br/>AND rules match product categories
        DB-->>PP: "Electronics Bundle" promotion active<br/>Buy 3+ items: 10% off
        LLM-->>PP: Tool: get_active_deals()
        PP->>DB: SELECT coupons WHERE is_active=TRUE<br/>AND applicable_categories includes 'Electronics'
        DB-->>PP: TEAM2026 coupon: 8% off orders > $500
        LLM-->>PP: Tool: optimize_cart(items, quantities)
        PP->>DB: Calculate best price combination
        DB-->>PP: Optimal pricing computed
        LLM-->>PP: Tool: get_loyalty_tier(user_email)
        PP->>DB: SELECT loyalty_tier
        DB-->>PP: Silver tier = 5% discount
        PP-->>ORCH: Option A (Premium): 5x Sony + JBL Charge = $2,640<br/>  Bundle: -10%, TEAM2026: -8%, Silver: -5%<br/>  Final: ~$2,030 (save $610)<br/>Option B (Budget): 5x JBL Tune + Bose Flex = $1,240<br/>  Bundle: -10%, TEAM2026: -8%, Silver: -5%<br/>  Final: ~$954 (save $286)
    end

    ORCH->>LLM: Process PP response, verify stock
    LLM-->>ORCH: Tool call: call_specialist_agent<br/>(inventory-fulfillment, "Check if 5 units available for each product")

    rect rgb(100, 116, 139)
        Note over ORCH,IF: A2A Call 3: Inventory & Fulfillment
        ORCH->>IF: POST /message:send
        IF->>LLM: ChatAgent.run()
        LLM-->>IF: Tool: get_warehouse_availability(product_ids)
        IF->>DB: SELECT warehouse_inventory<br/>for each product across all warehouses
        DB-->>IF: Stock levels per warehouse per product
        LLM-->>IF: Tool: calculate_fulfillment_plan(items, quantities)
        IF->>DB: Optimize sourcing across warehouses<br/>to minimize shipping
        DB-->>IF: Fulfillment plan computed
        LLM-->>IF: Tool: estimate_shipping(plan)
        IF->>DB: SELECT shipping_rates for plan
        DB-->>IF: Shipping cost estimate
        IF-->>ORCH: Option A: Sony in stock (12 units total), JBL Charge in stock (8 units)<br/>  Ship from Central warehouse, Standard: $0 (free for Gold/Silver over $500)<br/>Option B: JBL Tune in stock (15 units), Bose Flex: only 3 units<br/>  2 units on backorder, restock April 15<br/>  Recommend: ship 3 now + 2 when restocked
    end

    ORCH->>LLM: Synthesize bulk purchase recommendation
    Note over LLM: Combines product options,<br/>optimized pricing with discounts,<br/>stock status, and fulfillment plan<br/>into an actionable recommendation

    ORCH->>DB: Persist conversation
    ORCH-->>FE: Bulk purchase brief
    FE-->>User: "Here are two bundle options for your team of 5:<br/><br/>Premium (all in stock, ships immediately):<br/>5x Sony WH-1000XM5 + 5x JBL Charge 5<br/>Total: $2,030 (was $2,640 -- you save $610)<br/>Discounts: Bundle 10% + TEAM2026 8% + Silver 5%<br/><br/>Budget (partial backorder):<br/>5x JBL Tune 770NC + 5x Bose SoundLink Flex<br/>Total: $954 (was $1,240 -- you save $286)<br/>Note: 2 Bose speakers on backorder until April 15<br/><br/>Both ship free from Central warehouse. Want to proceed?"
```

---

## Flow Summary

| Flow | Agents Involved | Key Pattern |
|------|----------------|-------------|
| **Return and Replace** | Order Management -> Product Discovery -> Inventory -> Pricing | Sequential chain: action (return) then search then verify then optimize |
| **Pre-Purchase Research** | Review & Sentiment -> Product Discovery -> Pricing | Parallel research: gather opinions, alternatives, and deals |
| **Where's My Order** | Order Management -> Inventory & Fulfillment | Focused: order data then carrier/delivery status |
| **Stock Alert** | Inventory & Fulfillment -> Product Discovery | Fallback pattern: check stock, find alternatives when unavailable |
| **Bulk Purchase** | Product Discovery -> Pricing -> Inventory | Full pipeline: curate, price-optimize, verify fulfillment |

### Common Patterns Across Flows

- **Context injection**: Every agent receives user profile and recent orders via `ECommerceContextProvider` before processing. This enables personalization (loyalty tier, purchase history) without the user repeating themselves.
- **Shared tools**: Agents can call tools outside their primary domain. Product Discovery calls `check_stock()` (shared inventory tool) to avoid recommending out-of-stock items. This reduces unnecessary A2A round-trips.
- **Sequential A2A**: The Orchestrator calls specialists one at a time. Each response informs the next call's message. This is intentional -- later agents need context from earlier results (e.g., Pricing needs to know which products were recommended).
- **LLM synthesis**: The Orchestrator's LLM doesn't just concatenate specialist responses. It synthesizes them into a natural, coherent message with clear structure and actionable next steps.
- **Identity propagation**: User identity flows via `X-User-Email` and `X-User-Role` headers on every A2A call, then into ContextVars. Every tool query filters by user -- there's no data leakage between customers.
