# AgentBazaar — Implementation Plan

> Detailed phase-by-phase TODO tracker for building the e-commerce multi-agent platform.
> Refer to `AgentBazaar - Technical Design.md` for architecture details.
> Refer to `AgentBazaar - E-Commerce Multi-Agent Platform.md` for product spec.

---

## Phase 0: Project Scaffold & Infrastructure

**Goal**: Docker Compose running with PostgreSQL (pgvector), Redis, and Aspire Dashboard. Telemetry infra ready. dev.sh operational.

### Tasks

- [ ] **0.1** Initialize git repo, add `.gitignore` (Python + Node + Docker)
- [ ] **0.2** Create `agents/pyproject.toml` with all dependencies:
  - `agent-framework`, `agent-framework-a2a`, `agent-framework-openai`
  - `fastapi`, `uvicorn`, `starlette`, `sse-starlette`
  - `asyncpg`, `pgvector`, `pydantic`, `pydantic-settings`
  - `PyJWT`, `bcrypt`, `httpx`, `tiktoken`
  - `openai` (for embeddings via OpenAI/Azure OpenAI SDK directly)
  - **Telemetry (from day 1):**
    - `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`
    - `opentelemetry-instrumentation-fastapi`, `opentelemetry-instrumentation-starlette`
    - `opentelemetry-instrumentation-httpx`, `opentelemetry-instrumentation-asyncpg`
    - `opentelemetry-instrumentation-logging`
  - Dev: `pytest`, `pytest-asyncio`, `ruff`
- [ ] **0.3** Create `agents/Dockerfile` (multi-target with `ARG AGENT_NAME`)
  - Python 3.12-slim base
  - Install uv, system deps (gcc, libpq-dev)
  - Copy pyproject.toml, install deps
  - Copy shared/ then agent-specific module
  - Non-root user, EXPOSE, health check CMD
- [ ] **0.4** Create `docker/postgres/init.sql` — full schema (see Technical Design Section 10)
  - Enable pgvector extension
  - All tables: users, products, product_embeddings (`vector(1536)`), orders, order_items, order_status_history, returns, reviews, warehouses, warehouse_inventory, carriers, shipping_rates, coupons, promotions, price_history, loyalty_tiers, agent_catalog, access_requests, agent_permissions, conversations, messages, usage_logs, agent_execution_steps
  - All indexes
- [ ] **0.5** Create `docker-compose.yml` with infrastructure services:
  - `db` (pgvector/pgvector:pg16, port 5432, health check, init.sql mount)
  - `redis` (redis:7-alpine, port 6379, health check)
  - `aspire` (.NET Aspire Dashboard, ports 18888/18890, unsecured auth)
  - `seeder` service (runs once, depends on db healthy)
  - Agent services with `OTEL_SERVICE_NAME` per agent, `OTEL_EXPORTER_OTLP_ENDPOINT` pointing to aspire
- [ ] **0.6** Create `.env.example` with all environment variables
- [ ] **0.7** Create `scripts/dev.sh` — one-command dev environment setup
  - `./scripts/dev.sh` — full rebuild and start everything
  - `./scripts/dev.sh --clean` — nuke volumes, rebuild from scratch
  - `./scripts/dev.sh --seed-only` — re-run seeder against existing DB
  - `./scripts/dev.sh --infra-only` — start db + redis + aspire only
  - Color-coded output, health check polling, summary table with all URLs
- [ ] **0.8** Verify: `docker compose up db redis aspire` — all healthy, Aspire at localhost:18888

### Files Created
```
.gitignore
agents/pyproject.toml
agents/Dockerfile
docker/postgres/init.sql
docker-compose.yml
.env.example
scripts/dev.sh
```

---

## Phase 1: Shared Library

**Goal**: All shared utilities including telemetry that every agent depends on. Every agent built in subsequent phases automatically gets full observability.

### Tasks

- [ ] **1.1** `agents/shared/__init__.py`
- [ ] **1.2** `agents/shared/telemetry.py` — OpenTelemetry setup (the telemetry centerpiece)
  - `setup_telemetry(service_name)` — configure TracerProvider, MeterProvider, LoggerProvider with OTLP HTTP exporters to Aspire
  - `instrument_fastapi(app)` — auto-instrument FastAPI (orchestrator)
  - `instrument_starlette(app)` — auto-instrument Starlette (specialist agents)
  - Auto-instrument on setup: httpx (catches LLM API calls + A2A calls), asyncpg (all DB queries), Python logging (bridges to OTel logs with trace_id correlation)
  - `get_tracer(name)` / `get_meter(name)` — for custom spans where MAF doesn't auto-instrument
  - `traced_tool` decorator — wraps MAF `@tool` functions with OTel spans (only if MAF doesn't emit tool spans natively)
  - Graceful degradation: no crash when Aspire is unreachable
- [ ] **1.3** `agents/shared/config.py` — Pydantic Settings
  - DATABASE_URL, REDIS_URL
  - LLM_PROVIDER (openai | azure), OPENAI_API_KEY, LLM_MODEL
  - AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, AZURE_OPENAI_DEPLOYMENT, AZURE_OPENAI_API_VERSION
  - EMBEDDING_MODEL, AZURE_EMBEDDING_DEPLOYMENT
  - JWT_SECRET, AGENT_SHARED_SECRET
  - AGENT_REGISTRY (JSON string)
  - OTEL_ENABLED, OTEL_EXPORTER_OTLP_ENDPOINT, OTEL_SERVICE_NAME
- [ ] **1.4** `agents/shared/db.py` — asyncpg pool (init_db_pool, get_pool, close_db_pool)
- [ ] **1.5** `agents/shared/context.py` — ContextVars (current_user_email, current_user_role, current_session_id)
- [ ] **1.6** `agents/shared/jwt_utils.py` — JWT creation/validation
  - `hash_password()`, `verify_password()` (bcrypt)
  - `create_access_token()`, `create_refresh_token()`, `decode_token()` (PyJWT)
- [ ] **1.7** `agents/shared/auth.py` — AgentAuthMiddleware (Starlette BaseHTTPMiddleware)
  - Skip /health, /.well-known/agent-card.json
  - Inter-agent: check AGENT_SHARED_SECRET, read X-User-Email header
  - User: decode JWT, check role, check agent_permissions table
  - Set ContextVars
  - Structured logging for auth events (captured by OTel logging bridge)
- [ ] **1.8** `agents/shared/agent_factory.py` — LLM client factory
  - `create_chat_client()` → returns MAF ChatClient (OpenAI or Azure OpenAI)
  - `create_embedding_client()` → returns OpenAI AsyncClient for embeddings
- [ ] **1.9** `agents/shared/context_providers.py` — MAF ContextProvider
  - `ECommerceContextProvider` — injects user profile + recent orders into agent context
- [ ] **1.10** `agents/shared/usage_db.py` — Usage logging
  - `log_agent_usage()` — insert into usage_logs table with trace_id from active span
  - `log_execution_step()` — insert into agent_execution_steps table
- [ ] **1.11** Verify: Write unit tests for jwt_utils, config loading, telemetry graceful degradation

### Files Created
```
agents/shared/__init__.py
agents/shared/telemetry.py
agents/shared/config.py
agents/shared/db.py
agents/shared/context.py
agents/shared/jwt_utils.py
agents/shared/auth.py
agents/shared/agent_factory.py
agents/shared/context_providers.py
agents/shared/usage_db.py
```

---

## Phase 2: Seed Data

**Goal**: Realistic e-commerce dataset that makes the demo compelling.

### Tasks

- [ ] **2.1** `scripts/seed.py` — Main seeder script
  - 20 users (15 customers, 2 sellers, 2 power_users, 1 admin) with hashed passwords
  - 50 products across 5 categories (Electronics, Clothing, Home, Sports, Books)
    - Real product names (Sony WH-1000XM5, Patagonia Better Sweater, etc.)
    - Descriptions, prices, ratings, image placeholder URLs, specs JSONB
  - 200 orders across 15 customers with realistic status distribution
    - 10% placed, 15% confirmed, 15% shipped, 40% delivered, 10% returned, 10% cancelled
    - Each order has 1-4 items
    - Order status history with timestamps
  - 500 reviews (mix of 1-5 stars, varied lengths)
    - 5% planted fake reviews with detectable patterns
    - Verified purchase flags
  - 3 warehouses (East/Virginia, Central/Texas, West/Oregon)
    - Varied stock levels per product per warehouse
    - Some products deliberately low stock or out of stock
  - 3 carriers (Standard 5-7 days, Express 2-day, Overnight)
    - Shipping rate tables per region pair
  - 15 coupons (WELCOME10, TECHSAVE, TEAMGIFT, etc.)
    - Mix of percentage/fixed, some expired, some user-specific
  - 5 bundle promotion rules
  - 3 loyalty tiers (Bronze, Silver, Gold)
  - Price history: 90 days for all 50 products (with occasional sales)
  - 10 restock schedule entries for low-stock items
  - 6 agent catalog entries
  - Pre-approved agent permissions for power_users and admin
- [ ] **2.2** `scripts/generate_embeddings.py` — Generate product embeddings
  - Read all products from DB
  - Generate embeddings via OpenAI/Azure OpenAI (text-embedding-3-small, 1536 dimensions)
  - Store in product_embeddings table
- [ ] **2.3** Add seeder service to docker-compose.yml (runs once, exits)
- [ ] **2.4** Verify: Run seeder, check row counts, run embedding generation

### Files Created
```
scripts/__init__.py
scripts/seed.py
scripts/generate_embeddings.py
```

---

## Phase 3: First Agent — Product Discovery

**Goal**: One fully working agent end-to-end: tools, ChatAgent, A2AAgentHost, Docker container, health check, **telemetry visible in Aspire Dashboard**.

### Tasks

- [ ] **3.1** `agents/shared/tools/__init__.py` — Auto-import all tool modules
- [ ] **3.2** `agents/product_discovery/tools.py` — Product Discovery tools
  - `@tool search_products` — full-text search with filters (category, price, rating, sort)
  - `@tool get_product_details` — single product by ID with full specs
  - `@tool compare_products` — side-by-side comparison of 2-3 products
  - `@tool semantic_search` — pgvector similarity search via OpenAI embeddings
  - `@tool find_similar_products` — given a product ID, find similar ones
  - `@tool get_trending_products` — top products by recent order count
- [ ] **3.3** `agents/shared/tools/inventory_tools.py` — Shared inventory tools
  - `@tool check_stock` — stock levels across warehouses for a product
  - `@tool get_warehouse_availability` — all warehouse stock for a product
- [ ] **3.4** `agents/shared/tools/user_tools.py` — Shared user tools
  - `@tool get_user_profile` — user details + loyalty tier
  - `@tool get_purchase_history` — user's past orders (for personalization)
- [ ] **3.5** `agents/shared/tools/pricing_tools.py` — Shared pricing tools (subset)
  - `@tool get_price_history` — price trend for a product (30/60/90 days)
- [ ] **3.6** `agents/product_discovery/prompts.py` — System prompt
- [ ] **3.7** `agents/product_discovery/agent.py` — `create_product_discovery_agent()` → ChatAgent
- [ ] **3.8** `agents/product_discovery/main.py` — A2AAgentHost entry point
  - Call `setup_telemetry("agentbazaar.product-discovery")` in lifespan
  - Call `instrument_starlette(app)` for HTTP span auto-instrumentation
  - Init/close DB pool in lifespan
- [ ] **3.9** Add `product-discovery` service to docker-compose.yml
- [ ] **3.10** Verify end-to-end + telemetry:
  - Health check: `curl http://localhost:8081/health`
  - Agent card: `curl http://localhost:8081/.well-known/agent-card.json`
  - A2A call: `curl -X POST http://localhost:8081/message:send` with test message
  - Test: "Find me wireless headphones under $300"
  - Test: "Compare Sony WH-1000XM5 vs AirPods Max"
  - Test: semantic search "something for running in the rain"
  - **Open Aspire Dashboard at localhost:18888** — verify:
    - HTTP spans for /message:send
    - DB query spans (asyncpg auto)
    - LLM call spans (httpx auto → api.openai.com)
    - Correlated log entries with trace_ids

### Files Created
```
agents/shared/tools/__init__.py
agents/shared/tools/inventory_tools.py
agents/shared/tools/user_tools.py
agents/shared/tools/pricing_tools.py
agents/product_discovery/__init__.py
agents/product_discovery/tools.py
agents/product_discovery/prompts.py
agents/product_discovery/agent.py
agents/product_discovery/main.py
```

---

## Phase 4: Remaining Specialist Agents

**Goal**: All 5 specialist agents running as Docker containers with A2A endpoints. Each inherits full telemetry from shared/telemetry.py — zero additional observability work.

### 4A: Order Management Agent

- [ ] **4A.1** `agents/order_management/tools.py`
  - `@tool get_user_orders` — order list with filters (status, date range)
  - `@tool get_order_details` — full order with items and status history
  - `@tool get_order_tracking` — latest tracking status + location
  - `@tool cancel_order` — cancel if status is placed/confirmed
  - `@tool modify_order` — change address/quantity if not shipped
- [ ] **4A.2** `agents/shared/tools/return_tools.py`
  - `@tool check_return_eligibility` — 30-day window check
  - `@tool initiate_return` — create return record, generate mock label
  - `@tool process_refund` — calculate refund, update order status
  - `@tool get_return_status` — return processing status
- [ ] **4A.3** `agents/order_management/prompts.py`, `agent.py`, `main.py`
  - main.py: `setup_telemetry("agentbazaar.order-management")` + `instrument_starlette(app)`
- [ ] **4A.4** Add to docker-compose.yml, verify A2A endpoint

### 4B: Pricing & Promotions Agent

- [ ] **4B.1** `agents/pricing_promotions/tools.py`
  - `@tool validate_coupon` — code verification, eligibility, expiry, usage limits
  - `@tool optimize_cart` — find best pricing combination for cart items
  - `@tool get_active_deals` — list current promotions
  - `@tool check_bundle_eligibility` — bundle discount check
- [ ] **4B.2** `agents/shared/tools/loyalty_tools.py`
  - `@tool get_loyalty_tier` — user's current tier and benefits
  - `@tool calculate_loyalty_discount` — tier-specific discount for cart
  - `@tool get_loyalty_benefits` — full tier comparison
- [ ] **4B.3** `agents/pricing_promotions/prompts.py`, `agent.py`, `main.py`
  - main.py: `setup_telemetry("agentbazaar.pricing-promotions")` + `instrument_starlette(app)`
- [ ] **4B.4** Add to docker-compose.yml, verify

### 4C: Review & Sentiment Agent

- [ ] **4C.1** `agents/review_sentiment/tools.py`
  - `@tool get_product_reviews` — paginated reviews for a product
  - `@tool analyze_sentiment` — LLM-powered sentiment summary (pros/cons)
  - `@tool get_sentiment_by_topic` — breakdown by quality, value, shipping, etc.
  - `@tool get_sentiment_trend` — sentiment over time (improving/declining)
  - `@tool detect_fake_reviews` — flag suspicious patterns
  - `@tool search_reviews` — keyword search within reviews
  - `@tool draft_seller_response` — generate professional response to negative review
  - `@tool compare_product_reviews` — sentiment comparison between products
- [ ] **4C.2** `agents/review_sentiment/prompts.py`, `agent.py`, `main.py`
  - main.py: `setup_telemetry("agentbazaar.review-sentiment")` + `instrument_starlette(app)`
- [ ] **4C.3** Add to docker-compose.yml, verify

### 4D: Inventory & Fulfillment Agent

- [ ] **4D.1** `agents/inventory_fulfillment/tools.py`
  - `@tool check_stock` (shared, already done in 3.3)
  - `@tool get_warehouse_availability` (shared, already done)
  - `@tool get_restock_schedule` — upcoming restocks for a product
  - `@tool estimate_shipping` — delivery estimate for address + items
  - `@tool compare_carriers` — Standard vs Express vs Overnight with prices
  - `@tool get_tracking_status` — mock carrier tracking
  - `@tool calculate_fulfillment_plan` — optimal warehouse routing for multi-item order
  - `@tool place_backorder` — accept order for out-of-stock item
- [ ] **4D.2** `agents/inventory_fulfillment/prompts.py`, `agent.py`, `main.py`
  - main.py: `setup_telemetry("agentbazaar.inventory-fulfillment")` + `instrument_starlette(app)`
- [ ] **4D.3** Add to docker-compose.yml, verify

### Verification
- [ ] **4E.1** All 5 agents running: `docker compose up db redis aspire product-discovery order-management pricing-promotions review-sentiment inventory-fulfillment`
- [ ] **4E.2** Health checks pass for all agents
- [ ] **4E.3** Agent cards accessible at each `/.well-known/agent-card.json`
- [ ] **4E.4** Each agent responds to a basic A2A `/message:send` test
- [ ] **4E.5** All 5 agents visible in Aspire Dashboard with distinct service names

---

## Phase 5: Orchestrator Agent

**Goal**: Customer Support orchestrator with auth endpoints, intent classification, A2A routing to specialists, and conversation management. Distributed traces visible in Aspire.

### Tasks

- [ ] **5.1** `agents/orchestrator/prompts.py` — Orchestrator system prompt
  - Intent classification instructions
  - Agent routing rules
  - Multi-intent decomposition guidance
  - Escalation criteria
- [ ] **5.2** `agents/orchestrator/intent.py` — Intent classifier
  - `Intent` enum (product_question, order_inquiry, return_request, pricing_question, review_question, shipping_question, complaint, general_faq)
  - `ClassifiedIntent` Pydantic model (intents list, confidence, extracted entities)
  - `INTENT_TO_AGENT` mapping
- [ ] **5.3** `agents/orchestrator/agent.py` — Orchestrator setup
  - Connect to specialist agents via `A2AAgent.connect()`
  - Create `HandoffOrchestration` with all agents
  - Custom `agent.a2a_call` spans around outbound A2A calls to specialists
- [ ] **5.4** `agents/orchestrator/routes.py` — FastAPI API routes
  - **Auth**: POST /api/auth/signup, /api/auth/login, /api/auth/refresh
  - **Chat**: POST / (AG-UI), POST /message:send, POST /message:stream (A2A)
  - **Conversations**: GET /api/conversations, GET /api/conversations/{id}, DELETE /api/conversations/{id}
  - **Marketplace**: GET /api/marketplace/agents, POST /api/marketplace/request, GET /api/marketplace/my-agents
  - **Admin**: GET /api/admin/requests, POST /api/admin/requests/{id}/approve, POST /api/admin/requests/{id}/deny, GET /api/admin/usage, GET /api/admin/audit
- [ ] **5.5** `agents/orchestrator/main.py` — FastAPI app with CORS, auth middleware, lifespan
  - Call `setup_telemetry("agentbazaar.orchestrator")` + `instrument_fastapi(app)` in lifespan
- [ ] **5.6** Add orchestrator to docker-compose.yml with AGENT_REGISTRY env var
- [ ] **5.7** Verify: Signup → login → chat with orchestrator
  - Test single-intent: "Where's my order?"
  - Test multi-intent: "Return my jacket and find me a warmer one"
  - Test FAQ: "What's your return policy?"
  - Test routing: confirm correct agent is called for each intent
  - **Verify distributed traces in Aspire**: orchestrator → specialist → LLM → DB in one trace

---

## Phase 6: Agent Collaboration Flows

**Goal**: Multi-agent chaining works end-to-end. Orchestrator decomposes, routes, and aggregates.

### Tasks

- [ ] **6.1** Implement multi-intent decomposition in orchestrator
  - Parse LLM response for multiple intents
  - Route to multiple agents concurrently (asyncio.gather)
  - Aggregate results into coherent response
- [ ] **6.2** Implement agent trace events (SSE)
  - Emit events: agent_started, tool_called, tool_result, agent_completed
  - Include agent name, tool name, duration
  - Stream alongside text response
- [ ] **6.3** Test Flow 1: "Return and Replace"
  - Orchestrator → Order Management (return) + Product Discovery (search) + Inventory (stock) + Pricing (credit)
- [ ] **6.4** Test Flow 2: "Pre-Purchase Research"
  - Orchestrator → Review Sentiment (summary) + Product Discovery (alternatives) + Pricing (deals)
- [ ] **6.5** Test Flow 3: "Where's My Order?"
  - Orchestrator → Order Management (tracking) + Inventory (carrier status)
- [ ] **6.6** Test Flow 4: "Stock Alert + Reorder"
  - Orchestrator → Order Management (history) + Inventory (stock) + Pricing (subscribe)
- [ ] **6.7** Test Flow 5: "Bulk Purchase Optimization"
  - Orchestrator → Product Discovery (curate) + Pricing (bulk discount) + Inventory (stock check)

---

## Phase 7: Frontend

**Goal**: Next.js app with chat interface, product catalog, marketplace, and admin dashboard.

### Tasks

- [ ] **7.1** Initialize Next.js 15 project with pnpm, Tailwind, shadcn/ui
- [ ] **7.2** `web/Dockerfile` — Multi-stage build (deps → build → runtime)
- [ ] **7.3** Auth pages — signup, login (store JWT in httpOnly cookie or localStorage)
- [ ] **7.4** Layout — sidebar nav, header with user info, responsive
- [ ] **7.5** Product catalog page `/` — grid of products, search bar, category filters
- [ ] **7.6** Product detail page `/product/[id]` — specs, reviews summary, "Ask Agent" button
- [ ] **7.7** Chat page `/chat` — full-screen chat interface
  - CopilotKit `HttpAgent` connecting to orchestrator AG-UI endpoint
  - Message bubbles with markdown rendering
  - Agent trace sidebar (real-time tree view of which agents are working)
  - Typing indicators during streaming
- [ ] **7.8** My Orders page `/orders` — order list, tracking, return initiation
- [ ] **7.9** Marketplace page `/marketplace` — agent catalog cards
  - Agent description, capabilities, status
  - "Request Access" dialog with use case input
  - "Already Approved" indicator
- [ ] **7.10** My Agents page `/agents` — approved agents, usage stats, direct API access
- [ ] **7.11** Admin dashboard `/admin` — overview metrics
- [ ] **7.12** Admin requests `/admin/requests` — approve/deny table
- [ ] **7.13** Admin usage `/admin/usage` — charts (invocations, tokens, response times)
- [ ] **7.14** Admin audit `/admin/audit` — searchable log
- [ ] **7.15** Add frontend to docker-compose.yml
- [ ] **7.16** Verify: Full flow from signup → browse → chat → marketplace → admin

---

## Phase 8: Hardening & Polish

**Goal**: Usage tracking, error handling, rate limiting, and production-readiness. (Observability already done in Phase 1.)

### Tasks

- [ ] **8.1** Usage tracking middleware — log every agent invocation to usage_logs with trace_id correlation
- [ ] **8.2** Error handling
  - Agent unreachable: retry once, then fallback response
  - LLM timeout: 30s timeout with retry
  - Tool failure: return error to LLM, let it explain gracefully
  - Max steps exceeded: return partial response
- [ ] **8.3** Health check dashboard — `/admin/agents` shows health status of all agents
- [ ] **8.4** Token counting — tiktoken for usage_logs token tracking
- [ ] **8.5** Rate limiting — per-user, per-agent rate limits via Redis

---

## Phase 9: Documentation & Demo

**Goal**: README, demo script, screenshots, and blog article preparation.

### Tasks

- [ ] **9.1** README.md with:
  - Project description and architecture diagram
  - Quick start (`./scripts/dev.sh`)
  - Environment configuration (OpenAI vs Azure OpenAI)
  - Agent catalog with descriptions
  - Demo scenarios to try
  - Screenshots (including Aspire Dashboard traces)
  - Tech stack and references
- [ ] **9.2** Demo script — pre-configured curl commands for all 5 collaboration flows
- [ ] **9.3** Screenshots for blog articles
- [ ] **9.4** Draft blog article: "Building an E-Commerce Agent Platform with Microsoft Agent Framework"
- [ ] **9.5** Link repo in AI series articles (A1, A2, A3, A5)

---

## Quick Reference: Port Map

| Service | Port | Container Name |
|---------|------|---------------|
| PostgreSQL | 5432 | db |
| Redis | 6379 | redis |
| Aspire Dashboard | 18888 | aspire |
| OTLP Receiver | 18890 | aspire (18889 internal) |
| Orchestrator | 8080 | orchestrator |
| Product Discovery | 8081 | product-discovery |
| Order Management | 8082 | order-management |
| Pricing & Promotions | 8083 | pricing-promotions |
| Review & Sentiment | 8084 | review-sentiment |
| Inventory & Fulfillment | 8085 | inventory-fulfillment |
| Frontend | 3000 | frontend |

## Quick Reference: Test Users (from seed)

| Email | Password | Role |
|-------|----------|------|
| admin@agentbazaar.com | admin123 | admin |
| power@agentbazaar.com | power123 | power_user |
| seller@agentbazaar.com | seller123 | seller |
| alice@example.com | customer123 | customer |
| bob@example.com | customer123 | customer |

## Quick Reference: Demo Coupons

| Code | Discount | Notes |
|------|----------|-------|
| WELCOME10 | 10% off | First order only |
| TECHSAVE | 15% off Electronics | Min spend $100 |
| TEAMGIFT | Free gift wrapping | Bulk orders 5+ |
| LOYALTY20 | 20% off | Gold tier only |
| SPRING25 | $25 off | Min spend $150, expires April 30 |

## Quick Reference: OTel Service Names

| Agent | OTEL_SERVICE_NAME |
|-------|------------------|
| Orchestrator | agentbazaar.orchestrator |
| Product Discovery | agentbazaar.product-discovery |
| Order Management | agentbazaar.order-management |
| Pricing & Promotions | agentbazaar.pricing-promotions |
| Review & Sentiment | agentbazaar.review-sentiment |
| Inventory & Fulfillment | agentbazaar.inventory-fulfillment |
