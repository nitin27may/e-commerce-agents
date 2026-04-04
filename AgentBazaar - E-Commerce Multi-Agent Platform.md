---
type: idea
area: work
status: active
tags:
  - multi-agent
  - langgraph
  - e-commerce
  - side-project
  - docker
created: 2026-04-03
modified: 2026-04-03
---

# AgentBazaar — E-Commerce Multi-Agent Platform

A self-contained, fully local multi-agent platform built around an e-commerce domain. Users interact with a unified chat interface backed by 6 specialized agents that collaborate to handle product discovery, orders, support, pricing, reviews, and fulfillment. Includes a marketplace layer with agent catalog, access requests, and admin approval.

Companion demo repo for the AI article series on nitinksingh.com. Clone it, `docker compose up`, and have a working multi-agent e-commerce platform.

---

## Goals

1. Demonstrate realistic multi-agent orchestration patterns for blog articles (A1-A6, P3, P4)
2. Show agent-to-agent collaboration — not just isolated agents, but chained workflows
3. Run via Docker Compose — OpenAI/Azure OpenAI for LLMs, PostgreSQL for data, JWT for auth
4. Serve as a reference architecture for enterprise teams evaluating multi-agent systems
5. Be impressive enough to demo in conference talks and LinkedIn posts

---

## Architecture Overview

```
                    +------------------+
                    |   Next.js UI     |
                    |   (port 3000)    |
                    +--------+---------+
                             |
                    +--------+---------+
                    |  API Gateway     |
                    |  FastAPI         |
                    |  (port 8000)     |
                    |  - Auth (JWT)    |
                    |  - Access Control|
                    |  - Usage Tracking|
                    |  - Agent Router  |
                    +--------+---------+
                             |
            +----------------+----------------+
            |                |                |
     +------+------+  +-----+------+  +------+------+
     | Customer    |  | Product    |  | Order       |
     | Support     |  | Discovery  |  | Management  |
     | Agent       |  | Agent      |  | Agent       |
     | (8081)      |  | (8082)     |  | (8083)      |
     +-------------+  +------------+  +-------------+
            |                |                |
     +------+------+  +-----+------+  +------+------+
     | Pricing &   |  | Review &   |  | Inventory & |
     | Promotions  |  | Sentiment  |  | Fulfillment |
     | Agent       |  | Agent      |  | Agent       |
     | (8084)      |  | (8085)     |  | (8086)      |
     +-------------+  +------------+  +-------------+
            |                |                |
     +------+----------------+----------------+------+
     |                  Shared Layer                  |
     |  PostgreSQL (5432) | Redis (6379) | Aspire (18888) |
     +------------------------------------------------+
```

---

## Tech Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Frontend | Next.js 15, App Router, Tailwind, shadcn/ui | Server Components + streaming for agent responses |
| API Gateway | FastAPI (Python 3.12) | Auth, routing, access control, usage logging |
| Agent Runtime | LangGraph | State machine visualization, checkpointing, parallel branches |
| Database | PostgreSQL 16 + pgvector | Users, orders, products, reviews, RAG embeddings, audit logs |
| Cache/Queue | Redis | Session cache, agent task queue, real-time event pub/sub |
| LLM | OpenAI / Azure OpenAI (gpt-4.1 default) | Configurable via LLM_PROVIDER env var (openai or azure) |
| Auth | JWT (self-contained) | Simple signup/login, role-based (user/admin) |
| Infra | Docker Compose | One command, 10 containers, full platform |
| Package Manager | uv | Fast Python dependency management |

---

## Agent Details

### Agent 1: Customer Support Agent (The Orchestrator)

**Role**: Front door for all customer interactions. Classifies intent and either handles directly or delegates to specialist agents.

**LangGraph Pattern**: Router/Orchestrator with conditional edges

```
User Message → Intent Classification
    ├── product_question   → Product Discovery Agent
    ├── order_inquiry      → Order Management Agent
    ├── return_request     → Order Management Agent
    ├── pricing_question   → Pricing & Promotions Agent
    ├── review_question    → Review & Sentiment Agent
    ├── shipping_question  → Inventory & Fulfillment Agent
    ├── complaint          → Handle directly + escalation logic
    ├── general_faq        → Handle directly from knowledge base
    └── multi_intent       → Decompose → Route to multiple agents → Aggregate
```

**Features**:
- Intent classification with confidence scoring
- Multi-intent decomposition — "return my jacket and find something warmer" becomes two tasks
- Conversation memory — remembers context within a session
- Escalation to human — triggers when confidence is low or sentiment is negative
- Context handoff — passes relevant conversation history to specialist agents
- Fallback handling — graceful "I don't understand" with suggested actions

**Endpoints**:
| Method | Path | Description |
|--------|------|-------------|
| POST | `/chat` | Send message, get streamed response |
| POST | `/classify` | Intent classification only (no action) |
| GET | `/history/{session_id}` | Conversation history |
| POST | `/escalate` | Manual escalation to human queue |

**Database Tables**: `conversations`, `messages`, `escalations`

---

### Agent 2: Product Discovery Agent

**Role**: Natural language product search, filtering, recommendations, and comparisons.

**LangGraph Pattern**: RAG + Tool-use (search → filter → rank → present)

```
User Query → Query Understanding
    → Search Strategy (keyword vs semantic vs hybrid)
    → Product Search (pgvector similarity + full-text)
    → Filter & Rank (price, ratings, availability)
    → Personalization (purchase history, browsing context)
    → Response Generation (product cards with reasoning)
```

**Features**:
- **Natural language search**: "waterproof hiking boots under $150 with good reviews"
- **Semantic search**: pgvector embeddings over product descriptions
- **Smart filtering**: Extracts structured filters from natural language (category, price range, brand, rating threshold)
- **Personalization**: Weighs results by user's purchase history and browsing patterns
- **Comparisons**: "Compare the Sony WH-1000XM5 vs AirPods Max" — side-by-side feature table
- **Cross-sell**: "Customers who bought X also bought Y"
- **Upsell**: "For $20 more, you get the Pro version with..."
- **Stock-aware**: Filters out unavailable products, shows "low stock" warnings (calls Inventory Agent)

**Endpoints**:
| Method | Path | Description |
|--------|------|-------------|
| POST | `/search` | Natural language product search |
| GET | `/product/{id}` | Product details with AI-generated summary |
| POST | `/compare` | Side-by-side product comparison |
| POST | `/recommend` | Personalized recommendations for user |
| GET | `/trending` | Trending products by category |

**Database Tables**: `products`, `product_embeddings`, `browsing_history`, `purchase_history`

**Seed Data**: 50 products across 5 categories (Electronics, Clothing, Home, Sports, Books) with descriptions, prices, ratings, images (placeholder URLs), and stock levels.

---

### Agent 3: Order Management Agent

**Role**: Order tracking, modifications, returns, and refund processing.

**LangGraph Pattern**: State machine with approval gates

```
Return Request Flow:
    Validate Order → Check Return Eligibility (window, condition)
        → Eligible: Generate Return Label → Process Refund → Update Inventory
        → Ineligible: Explain Why → Offer Alternatives (store credit, exchange)
        → Edge Case: Escalate to Customer Support Agent

Order Modification Flow:
    Validate Order → Check Status
        → Not Shipped: Apply Modification → Confirm
        → Shipped: Explain Options (intercept, return after delivery)
        → Delivered: Route to Return Flow
```

**Features**:
- **Order tracking**: Real-time status (placed → confirmed → shipped → out for delivery → delivered)
- **Order history**: "What did I buy last month?" with filtering
- **Order modification**: Change address, quantity, cancel (if not shipped)
- **Return processing**: Eligibility check (30-day window), reason classification, return label generation
- **Refund calculation**: Original payment method, partial refunds for damaged items, store credit option
- **Re-order**: "Order the same thing I got last time"
- **Status notifications**: Webhook simulation for status change events

**Endpoints**:
| Method | Path | Description |
|--------|------|-------------|
| GET | `/orders/{user_id}` | Order history with filters |
| GET | `/order/{order_id}/track` | Real-time tracking status |
| POST | `/order/{order_id}/modify` | Modify order (address, quantity) |
| POST | `/order/{order_id}/cancel` | Cancel order |
| POST | `/order/{order_id}/return` | Initiate return |
| GET | `/order/{order_id}/return/status` | Return processing status |
| POST | `/reorder/{order_id}` | Re-order previous order |

**Database Tables**: `orders`, `order_items`, `returns`, `refunds`, `order_status_history`

**Seed Data**: 200 orders across 20 customers with varied statuses (10% pending, 30% shipped, 40% delivered, 10% returned, 10% cancelled).

---

### Agent 4: Pricing & Promotions Agent

**Role**: Coupon validation, deal discovery, dynamic bundle pricing, and price intelligence.

**LangGraph Pattern**: Rule engine + LLM reasoning for complex promo logic

```
Cart Optimization Flow:
    Receive Cart Items → Load Available Promotions
        → Check Coupon Eligibility (user tier, min spend, expiry, usage limit)
        → Calculate Bundle Discounts (buy 2+ from same category)
        → Find Stackable Deals (coupon + bundle + loyalty)
        → Compare: Best Single Discount vs Best Combination
        → Return Optimal Pricing with Explanation
```

**Features**:
- **Coupon validation**: Code verification, eligibility rules, expiry check, usage limits
- **Deal finder**: "What's the best deal for my cart?" — scans all active promotions
- **Bundle pricing**: Automatic detection of bundle opportunities ("add one more for 15% off")
- **Price history**: "Is this a good price?" — shows 30/60/90 day price trend
- **Loyalty tiers**: Bronze/Silver/Gold with tier-specific discounts
- **Cart optimizer**: Suggests reordering cart to maximize savings
- **Price alerts**: Register for price drop notifications on wishlist items

**Endpoints**:
| Method | Path | Description |
|--------|------|-------------|
| POST | `/validate-coupon` | Validate coupon code against cart |
| POST | `/optimize-cart` | Find best pricing for cart items |
| GET | `/deals` | Active deals and promotions |
| GET | `/price-history/{product_id}` | Price trend for a product |
| POST | `/bundle-check` | Check bundle discount eligibility |
| GET | `/loyalty/{user_id}` | Loyalty tier and benefits |

**Database Tables**: `coupons`, `promotions`, `bundle_rules`, `price_history`, `loyalty_tiers`, `user_loyalty`

**Seed Data**: 15 active coupons, 5 bundle rules, 3 loyalty tiers, price history for all 50 products (90 days, with occasional sales).

---

### Agent 5: Review & Sentiment Agent

**Role**: Review analysis, summarization, fake detection, and seller response generation.

**LangGraph Pattern**: Batch analysis + parallel extraction branches

```
Product Review Analysis:
    Load Reviews for Product
        → Branch 1: Sentiment Classification (positive/negative/neutral per review)
        → Branch 2: Topic Extraction (quality, shipping, value, sizing, durability)
        → Branch 3: Fake Review Detection (linguistic patterns, reviewer history)
        → Merge Branches
        → Generate Summary ("Buyers love X, but complain about Y")
        → Trend Analysis (sentiment over time — improving or declining?)
```

**Features**:
- **Review summary**: "What do people say about this product?" — aggregated pros/cons
- **Topic breakdown**: Quality, value, shipping, sizing, durability — scored per topic
- **Sentiment trend**: Is this product's reputation improving or declining over the last 6 months?
- **Fake detection**: Flags suspicious reviews (generic language, burst patterns, unverified purchases)
- **Seller response drafts**: Generate professional responses to negative reviews
- **Comparison reviews**: "How do reviews compare between Product A and Product B?"
- **Review search**: "Show me reviews that mention battery life"

**Endpoints**:
| Method | Path | Description |
|--------|------|-------------|
| GET | `/summary/{product_id}` | AI-generated review summary with pros/cons |
| GET | `/sentiment/{product_id}` | Sentiment breakdown by topic |
| GET | `/trend/{product_id}` | Sentiment trend over time |
| POST | `/detect-fake` | Flag suspicious reviews for a product |
| POST | `/draft-response` | Generate seller response to a review |
| POST | `/search-reviews` | Search reviews by keyword/topic |
| POST | `/compare` | Compare review sentiment between products |

**Database Tables**: `reviews`, `review_sentiments`, `review_topics`, `fake_review_flags`

**Seed Data**: 500 reviews across 50 products (mix of 1-5 stars, varied lengths, 5% planted "fake" reviews with detectable patterns).

---

### Agent 6: Inventory & Fulfillment Agent

**Role**: Stock management, warehouse routing, shipping estimates, and carrier selection.

**LangGraph Pattern**: Multi-step tool-use with optimization logic

```
Fulfillment Decision Flow:
    Receive Order Items → Check Stock per Warehouse
        → Single Warehouse Has All: Route to nearest warehouse
        → Split Across Warehouses: Calculate split-ship cost vs wait-for-restock
        → Out of Stock: Check restock date → Offer backorder or alternatives
    → Select Carrier (cheapest vs fastest based on user preference)
    → Calculate Delivery Estimate
    → Generate Shipping Label (mock)
```

**Features**:
- **Stock check**: Real-time availability across 3 warehouses (East, Central, West)
- **Delivery estimate**: Based on warehouse location, customer address, carrier speed
- **Carrier comparison**: "Standard (5-7 days, $5.99) vs Express (2-day, $14.99) vs Overnight ($29.99)"
- **Split shipment optimization**: When items are in different warehouses, calculate optimal routing
- **Restock alerts**: "This item will be back in stock on April 15"
- **Backorder management**: Accept orders for out-of-stock items with estimated delivery
- **Warehouse dashboard**: Stock levels, reorder alerts, fulfillment metrics

**Endpoints**:
| Method | Path | Description |
|--------|------|-------------|
| GET | `/stock/{product_id}` | Stock levels across all warehouses |
| POST | `/shipping-estimate` | Delivery estimate for cart items + address |
| POST | `/carrier-compare` | Compare carrier options for an order |
| POST | `/fulfill` | Calculate optimal fulfillment plan for order |
| GET | `/restock-schedule` | Upcoming restocks for low-stock items |
| POST | `/backorder` | Place a backorder for out-of-stock item |
| GET | `/warehouse/{id}/dashboard` | Warehouse metrics and alerts |

**Database Tables**: `warehouses`, `warehouse_inventory`, `carriers`, `shipping_rates`, `restock_schedule`, `backorders`

**Seed Data**: 3 warehouses with varied stock levels per product, 3 carriers with rate tables, restock schedule for 10 low-stock items.

---

## Agent Collaboration Flows

These are the multi-agent chaining scenarios that make the demo compelling.

### Flow 1: "Return and Replace"

> Customer: "I want to return my jacket, it's too small. Can you find me the same one in large?"

```
Customer Support Agent
  ├── classifies: return_request + product_question (multi-intent)
  │
  ├──→ Order Management Agent
  │     └── Finds jacket order → checks return eligibility (within 30 days) → initiates return
  │
  ├──→ Product Discovery Agent
  │     └── Searches for same jacket in Large → checks availability
  │
  ├──→ Inventory Agent
  │     └── Confirms Large is in stock at nearest warehouse → delivery estimate
  │
  └──→ Pricing Agent
        └── Applies return credit to new order → checks for any active coupons

Customer Support Agent aggregates:
  "I've started your return for the Medium jacket (return label attached).
   The Large is in stock — with your return credit of $89.99 and a 10% loyalty
   discount, your total for the replacement is $0.00. Want me to place the order?"
```

### Flow 2: "Pre-Purchase Research"

> Customer: "Is the Sony WH-1000XM5 worth it? I'm seeing mixed reviews."

```
Customer Support Agent
  ├── classifies: review_question + product_question
  │
  ├──→ Review & Sentiment Agent
  │     └── Summarizes 45 reviews: "4.3/5 overall. Buyers love noise cancellation
  │         and comfort. Main complaints: call quality and touch controls sensitivity.
  │         Sentiment trending UP over last 3 months (firmware updates helped)."
  │
  ├──→ Product Discovery Agent
  │     └── Pulls product details + suggests alternatives:
  │         "Also consider: Bose QC Ultra ($349) and AirPods Max ($449)"
  │
  └──→ Pricing Agent
        └── "Currently $299 (was $349 last month — 14% off). This is the lowest
            price in 90 days. You also have a $20 loyalty credit available."

Customer Support Agent aggregates into a coherent recommendation.
```

### Flow 3: "Where's My Order?"

> Customer: "My order was supposed to arrive yesterday, where is it?"

```
Customer Support Agent
  ├── classifies: order_inquiry (shipping)
  │
  ├──→ Order Management Agent
  │     └── Finds order #1847 → status: "shipped" → last tracking: "In transit, carrier hub"
  │
  └──→ Inventory & Fulfillment Agent
        └── Carrier API check (mock): "Package delayed at regional hub due to
            weather. Updated ETA: April 5 (2 days late)"

Customer Support Agent:
  "Your order #1847 is currently at the regional carrier hub. There's a weather
   delay — updated delivery is April 5. Would you like me to:
   1. Set up a delivery notification?
   2. Reroute to a pickup location?
   3. Apply a $5 credit for the inconvenience?"
```

### Flow 4: "Stock Alert + Reorder"

> Customer: "I bought this protein powder 2 months ago, need more. Is it in stock?"

```
Customer Support Agent
  ├── classifies: order_inquiry + product_question
  │
  ├──→ Order Management Agent
  │     └── Finds previous order → product: "Optimum Gold Standard Whey 5lb"
  │
  ├──→ Inventory Agent
  │     └── Stock check: "Out of stock at West warehouse. Available at Central
  │         warehouse — delivery: 4-5 days. Restock at West: April 10."
  │
  └──→ Pricing Agent
        └── "Price is same as last purchase ($58.99). Subscribe & Save available:
            $52.99/month (10% off recurring)."

Customer Support Agent:
  "Your Optimum Gold Standard Whey is available from our Central warehouse
   (4-5 day delivery). Same price as last time — $58.99. Want me to:
   1. Reorder now?
   2. Wait for local restock on April 10 (2-3 day delivery)?
   3. Set up Subscribe & Save at $52.99/month?"
```

### Flow 5: "Bulk Purchase Optimization"

> Customer: "I need to buy gifts for my team — 8 people. Budget is $50 each."

```
Customer Support Agent
  ├── classifies: product_question + pricing_question
  │
  ├──→ Product Discovery Agent
  │     └── Curates gift-appropriate products under $50 across categories
  │         Returns 15 suggestions with ratings and descriptions
  │
  ├──→ Pricing Agent
  │     └── "Bulk discount available: 10% off orders of 5+ same item.
  │         Corporate gift code TEAMGIFT applies for free gift wrapping.
  │         Total for 8 gifts: $384 → $345.60 with bulk discount."
  │
  └──→ Inventory Agent
        └── Checks stock for all 15 suggestions → flags 3 as low stock
            "Recommend ordering by Friday to guarantee delivery by April 15"
```

---

## Marketplace Layer

The marketplace sits on top of the agents and controls who can access what.

### User Roles

| Role | Permissions |
|------|------------|
| **Customer** | Chat with Customer Support Agent (always available). Access to other agents requires approval. |
| **Power User** | Direct access to all agents via API. Granted by admin. |
| **Seller** | Access to Review Agent (response drafting), Inventory Agent (stock management), Pricing Agent (promotions). |
| **Admin** | Full access. Approve/deny requests. View usage analytics. Manage agent catalog. |

### Agent Catalog

Each agent has a catalog entry:

```json
{
  "id": "product-discovery",
  "name": "Product Discovery Agent",
  "description": "Natural language product search with personalized recommendations",
  "category": "Shopping",
  "icon": "search",
  "status": "active",
  "version": "1.0",
  "capabilities": ["search", "compare", "recommend", "trending"],
  "input_types": ["text"],
  "output_types": ["product_list", "comparison_table", "recommendation"],
  "avg_response_time": "2.3s",
  "usage_count": 1247,
  "approval_required": true,
  "allowed_roles": ["power_user", "admin"]
}
```

### Access Request Flow

```
1. User browses /marketplace → sees 6 agent cards
2. Customer Support Agent: always available (no approval needed)
3. Other 5 agents: "Request Access" button
4. User submits request with:
   - Which agent
   - Intended use case (dropdown + free text)
   - Requested role (power_user or seller)
5. Admin sees request in /admin/requests
6. Admin approves or denies with notes
7. User gets notification → agent unlocked in their dashboard
8. Every invocation logged: user, agent, input summary, tokens used, duration
```

---

## Database Schema (Conceptual)

### Core Commerce Tables
- `users` — id, email, password_hash, name, role, loyalty_tier, created_at
- `products` — id, name, description, category, price, image_url, rating, review_count
- `product_embeddings` — id, product_id, embedding (pgvector)
- `orders` — id, user_id, status, total, shipping_address, created_at
- `order_items` — id, order_id, product_id, quantity, price
- `order_status_history` — id, order_id, status, timestamp, notes
- `reviews` — id, product_id, user_id, rating, title, body, verified_purchase, created_at

### Inventory & Pricing Tables
- `warehouses` — id, name, location, region
- `warehouse_inventory` — warehouse_id, product_id, quantity, reorder_threshold
- `carriers` — id, name, speed_tier, base_rate
- `shipping_rates` — carrier_id, region_from, region_to, price, estimated_days
- `coupons` — id, code, discount_type, discount_value, min_spend, expiry, usage_limit, uses
- `promotions` — id, name, type, rules (JSONB), start_date, end_date
- `price_history` — id, product_id, price, recorded_at
- `loyalty_tiers` — id, name, min_spend, discount_pct

### Marketplace Tables
- `agent_catalog` — id, name, slug, description, category, status, version, config (JSONB)
- `access_requests` — id, user_id, agent_id, role_requested, use_case, status, admin_notes, created_at, resolved_at
- `agent_permissions` — id, user_id, agent_id, role, granted_at, granted_by
- `usage_logs` — id, user_id, agent_id, session_id, input_summary, tokens_in, tokens_out, duration_ms, created_at

### Conversation Tables
- `conversations` — id, user_id, started_at, last_message_at
- `messages` — id, conversation_id, role, content, agent_id, metadata (JSONB), created_at
- `escalations` — id, conversation_id, reason, priority, status, assigned_to, created_at

---

## Frontend Pages

### Customer-Facing

| Page | Route | Description |
|------|-------|-------------|
| Home | `/` | Product catalog with search bar, category filters |
| Product Detail | `/product/{id}` | Product info, AI-generated review summary, "Ask Agent" button |
| Chat | `/chat` | Full-screen chat with Customer Support Agent. Shows which specialist agents are working (agent trace sidebar) |
| My Orders | `/orders` | Order history, tracking, return initiation |
| Marketplace | `/marketplace` | Agent catalog cards — browse, request access |
| My Agents | `/agents` | Approved agents with direct API access, usage stats |

### Admin Dashboard

| Page | Route | Description |
|------|-------|-------------|
| Overview | `/admin` | Key metrics: active users, agent invocations today, pending requests |
| Access Requests | `/admin/requests` | Pending/approved/denied requests with filters |
| Usage Analytics | `/admin/usage` | Charts: invocations per agent, tokens consumed, avg response time, top users |
| Audit Log | `/admin/audit` | Searchable log of every agent invocation |
| Agent Management | `/admin/agents` | Enable/disable agents, update descriptions, view health status |

### Agent Trace View (the wow factor)

When a multi-agent flow runs, the UI shows a **real-time trace panel**:

```
[Customer Support Agent] Classifying intent...
  ├── Intent: return_request + product_question
  ├── [Order Management Agent] Processing return for Order #1847...
  │     └── Return approved. Label generated.
  ├── [Product Discovery Agent] Searching for "jacket, size L"...
  │     └── Found 3 matches. Top: North Face Thermoball ($129.99)
  └── [Pricing Agent] Applying return credit...
        └── Credit: $89.99. New total: $40.00

Response generated in 4.2s | 3 agents used | 1,847 tokens
```

---

## Seed Data Plan

All generated via a Python seeder script (`scripts/seed.py`):

| Entity | Count | Notes |
|--------|-------|-------|
| Users | 20 | 15 customers, 2 sellers, 2 power users, 1 admin |
| Products | 50 | 10 per category (Electronics, Clothing, Home, Sports, Books) |
| Orders | 200 | Across 15 customers, varied statuses |
| Reviews | 500 | 1-5 stars, varied lengths, 5% fake patterns |
| Coupons | 15 | Mix of percentage/fixed, some expired, some user-specific |
| Warehouses | 3 | East (Virginia), Central (Texas), West (Oregon) |
| Price History | 4,500 | 90 days x 50 products |

---

## Docker Compose Services

```yaml
services:
  # Infrastructure
  db:          PostgreSQL 16 + pgvector   (port 5432)
  redis:       Redis 7                    (port 6379)
  aspire:      .NET Aspire Dashboard      (port 18888)

  # Platform
  gateway:     FastAPI API Gateway        (port 8000)
  frontend:    Next.js 15                 (port 3000)
  seeder:      One-shot: seeds database   (runs once, exits)

  # Agents
  agent-support:    Customer Support      (port 8081)
  agent-discovery:  Product Discovery     (port 8082)
  agent-orders:     Order Management      (port 8083)
  agent-pricing:    Pricing & Promotions  (port 8084)
  agent-reviews:    Review & Sentiment    (port 8085)
  agent-inventory:  Inventory & Fulfillment (port 8086)
```

Total: 12 containers. All agents share the same base Dockerfile with a build arg for agent name (multi-target pattern from WorkGraph.ai).

---

## How This Maps to Blog Articles

| Article | What AgentBazaar Demonstrates |
|---------|-------------------------------|
| A1 — What Is an AI Agent | Customer Support Agent as the canonical example |
| A2 — LangGraph Fundamentals | Product Discovery Agent — search → filter → rank graph |
| A3 — Stateful Agent | Order Management Agent — return flow with checkpoints |
| A5 — Multi-Agent Orchestration | All 5 collaboration flows above |
| A6 — Framework Comparison | Compare this LangGraph version vs WorkGraph.ai's MAF version |
| P3 — RAG in Production | Product Discovery Agent's semantic search pipeline |
| P4 — LLM Observability | Gateway-level tracing with the agent trace view |
| P7 — Cost Control | Token tracking per user per agent in usage_logs |
| T5 — MCP Explained | Each agent exposes tools that could be MCP servers |
| **NEW series** | "Building an E-Commerce Agent Platform" (3-4 parts) |

---

## Build Order (Phases)

### Phase 1: Foundation
- PostgreSQL schema + seed script
- FastAPI gateway with JWT auth (signup, login, token refresh)
- Docker Compose with db + redis + aspire
- Health check endpoints for all services

### Phase 2: Marketplace
- Agent catalog API + seed data
- Access request flow (submit, list, approve/deny)
- Permission middleware on gateway (check user has access before proxying)
- Admin endpoints for request management

### Phase 3: First Agent (Customer Support)
- LangGraph intent classifier
- Basic chat with conversation memory
- FAQ handling from knowledge base
- Streaming responses via SSE

### Phase 4: Specialist Agents
- Product Discovery (RAG + search)
- Order Management (state machine + return flow)
- Pricing & Promotions (rule engine)
- Review & Sentiment (batch analysis + parallel branches)
- Inventory & Fulfillment (multi-step optimization)

### Phase 5: Agent Collaboration
- Inter-agent communication via gateway
- Multi-intent decomposition in Customer Support Agent
- Implement the 5 collaboration flows
- Agent trace events (SSE to frontend)

### Phase 6: Frontend
- Chat interface with agent trace sidebar
- Product catalog + detail pages
- Order dashboard
- Marketplace catalog + access request UI
- Admin dashboard with analytics

### Phase 7: Polish
- Comprehensive seed data
- README with screenshots and quick start
- Blog article drafts referencing specific code

---

## Open Decisions

- [ ] Project name: AgentBazaar vs AgentMart vs ShopAgent vs other?
- [ ] Should agents communicate directly (agent-to-agent) or always through the gateway?
- [ ] Add WebSocket support for real-time chat, or SSE is sufficient?
- [ ] Include a simple mobile-responsive view, or desktop-only for demo?
- [ ] Add a Swagger/OpenAPI playground page for each agent?

---

## Related

- [[Content Hub]] — Blog publishing pipeline
- [[Local Voice Dictation App]] — Previous side project (completed)
- AI Series Plan — `~/workspace/nitin27may.github.io/ai-series-plan.md`
- WorkGraph.ai — `/Users/nks/workspace/workgraph.ai/` (reference architecture)
