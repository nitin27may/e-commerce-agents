---
type: resource
area: work
status: active
tags:
  - multi-agent
  - microsoft-agent-framework
  - e-commerce
  - technical-design
  - a2a-protocol
created: 2026-04-03
modified: 2026-04-03
---

# AgentBazaar — Technical Design Document

> Detailed technical design for the E-Commerce Multi-Agent Platform.
> See [[AgentBazaar - E-Commerce Multi-Agent Platform]] for the product spec, agent features, and collaboration flows.

---

## 0. Microsoft Agent Framework — Official References

### Repositories

| Resource | URL |
|----------|-----|
| Framework Repo | [github.com/microsoft/agent-framework](https://github.com/microsoft/agent-framework) |
| Samples Repo | [github.com/microsoft/Agent-Framework-Samples](https://github.com/microsoft/Agent-Framework-Samples) |
| A2A Protocol SDK | [github.com/a2aproject/a2a-python](https://github.com/a2aproject/a2a-python) |
| A2A Protocol Spec | [a2a-protocol.org/latest](https://a2a-protocol.org/latest/) |

### Documentation

| Topic | URL |
|-------|-----|
| Docs Hub | [learn.microsoft.com/agent-framework](https://learn.microsoft.com/en-us/agent-framework/) |
| Your First Agent | [learn.microsoft.com/agent-framework/get-started/your-first-agent](https://learn.microsoft.com/en-us/agent-framework/get-started/your-first-agent) |
| Function Tools | [learn.microsoft.com/agent-framework/.../function-tools](https://learn.microsoft.com/en-us/agent-framework/agents/tools/function-tools) |
| Memory & Sessions | [learn.microsoft.com/agent-framework/get-started/memory](https://learn.microsoft.com/en-us/agent-framework/get-started/memory) |
| Handoff Orchestration | [learn.microsoft.com/agent-framework/.../handoff](https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/handoff) |
| A2A Agent Type | [learn.microsoft.com/agent-framework/.../a2a-agent](https://learn.microsoft.com/en-us/agent-framework/user-guide/agents/agent-types/a2a-agent) |
| Context Providers | [learn.microsoft.com/agent-framework/.../context-providers](https://learn.microsoft.com/en-us/agent-framework/agents/conversations/context-providers) |
| Python API Reference | [learn.microsoft.com/python/api/agent-framework-core](https://learn.microsoft.com/en-us/python/api/agent-framework-core/agent_framework?view=agent-framework-python-latest) |

### Python Packages (v1.0.0, GA April 2026)

```bash
pip install agent-framework             # Full framework
pip install agent-framework-core        # Core only
pip install agent-framework-a2a         # A2A protocol integration
pip install agent-framework-openai      # OpenAI / Azure OpenAI provider  ← used by AgentBazaar
pip install agent-framework-openai      # OpenAI provider
pip install agent-framework-foundry     # Azure AI Foundry provider
pip install agent-framework-devui       # Development UI
```

Notable optional packages: `agent-framework-anthropic`, `agent-framework-redis`, `agent-framework-azure-cosmos`, `agent-framework-azure-ai-search`, `agent-framework-mem0`, `agent-framework-declarative`.

### Samples Repository Structure (Progressive Learning)

```
Agent-Framework-Samples/
├── 00.ForBeginners/       — Travel agents, basic patterns, RAG, multi-agent
├── 01.AgentFoundation/    — Core concepts
├── 02.CreateYourFirstAgent/— Travel planning with GitHub Models
├── 03.ExploreAgentFramework/— Providers (Azure OpenAI, GitHub, Foundry)
├── 04.Tools/              — Vision, code interpreter, custom tools
├── 05.Providers/          — MCP + Agent-to-Agent communication
├── 06.RAGs/               — Knowledge-enhanced agents, file search
├── 07.Workflow/           — Orchestration (sequential, concurrent, conditional)
├── 08.EvaluationAndTracing/— Eval, debugging, DevUI
└── 09.Cases/              — Real-world production patterns
```

### Key Difference: MAF SDK vs WorkGraph.ai Approach

WorkGraph.ai uses the **raw OpenAI function-calling loop** with a custom `executor_base.py` and direct `a2a-sdk` usage. For AgentBazaar, we use the **proper MAF SDK** which provides:

- `ChatAgent` — built-in agent abstraction with tool registration and streaming
- `@tool` decorator — official MAF tool definition (not custom)
- `AgentSession` — automatic conversation memory and state management
- `ContextProvider` — pluggable context injection (history, user memory, RAG)
- Handoff orchestration — built-in multi-agent routing with context broadcast
- `A2AAgent` — first-class A2A protocol wrapper for inter-agent communication
- OpenTelemetry — built-in observability without manual setup
- Provider flexibility — swap OpenAI/Azure OpenAI with one env var

This means we can delete the custom executor, function-calling loop, and tool registry from WorkGraph.ai and let MAF handle it natively.

---

## 1. System Architecture

### 1.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Browser / Client                             │
│                 Next.js 15 (React 19, Tailwind CSS)                 │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ HTTP/JSON + SSE
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  Orchestrator Agent (port 8080)                      │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  FastAPI + Microsoft Agent Framework                          │  │
│  │  • JWT Auth Middleware (self-contained, no Azure AD)          │  │
│  │  • RBAC + Agent Access Control                                │  │
│  │  • A2A Client (discovers + calls specialist agents)           │  │
│  │  • Conversation Management (PostgreSQL)                       │  │
│  │  • Usage Tracking + Audit Logging                             │  │
│  │  • SSE Streaming to frontend                                  │  │
│  │  • AG-UI Protocol endpoint (CopilotKit compatible)            │  │
│  └──────────────┬──────────────────────────────────┬─────────────┘  │
└─────────────────┼──────────────────────────────────┼────────────────┘
                  │  A2A Protocol (HTTP + SSE)        │
    ┌─────────────┼──────────────┬───────────────────┼─────────┐
    ▼             ▼              ▼                    ▼         ▼
┌────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│Product │ │  Order   │ │ Pricing  │ │ Review   │ │Inventory │
│Discovery│ │Management│ │& Promos  │ │& Sentim. │ │& Fulfill.│
│ :8081  │ │  :8082   │ │  :8083   │ │  :8084   │ │  :8085   │
└────┬───┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘
     │          │             │            │             │
     └──────────┴─────────────┴────────────┴─────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        ┌──────────┐   ┌──────────┐   ┌──────────┐
        │PostgreSQL│   │  Redis   │   │  Aspire  │
        │+ pgvector│   │  Cache   │   │Dashboard │
        │  :5432   │   │  :6379   │   │ :18888   │
        └──────────┘   └──────────┘   └──────────┘
```

### 1.2 Key Architecture Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Agent Framework | Microsoft Agent Framework v1.0 (Python SDK) | GA release, native A2A, Handoff orchestration, OpenAI/Azure providers |
| Orchestration Pattern | Handoff orchestration via Orchestrator Agent | Customer Support Agent acts as the front door, hands off to specialists via A2A |
| Agent Communication | A2A Protocol (HTTP + SSE) | Standard protocol, agents are independently deployable, matches WorkGraph.ai |
| LLM Provider | OpenAI / Azure OpenAI | Configurable via LLM_PROVIDER env var. gpt-4.1 default model |
| Auth | Self-contained JWT (no Azure AD) | Demo simplicity. Same middleware pattern as WorkGraph.ai but with local JWT |
| Database | PostgreSQL 16 + pgvector + asyncpg | Proven stack from WorkGraph.ai. pgvector for product embeddings |
| Frontend Protocol | AG-UI (CopilotKit) + A2A streaming | Reuse WorkGraph.ai's frontend streaming pattern |
| Observability | OpenTelemetry → .NET Aspire Dashboard | Same telemetry pipeline as WorkGraph.ai |

### 1.3 What We Reuse vs Replace from WorkGraph.ai

**Reuse (infrastructure patterns):**

| Component | WorkGraph.ai Path | Adaptation |
|-----------|-------------------|------------|
| `AgentAuthMiddleware` | `agents/shared/auth.py` | Swap Azure AD JWT for local JWT (PyJWT + bcrypt) |
| `ContextVar` pattern | `agents/shared/context.py` | Same, remove `access_token`, keep `user_email` |
| DB pool (asyncpg) | `agents/shared/db.py` | Same pool pattern |
| Telemetry (OTel) | `agents/shared/telemetry.py` | Same OTLP exporter to Aspire Dashboard |
| Dockerfile multi-target | `agents/Dockerfile` | Same `ARG AGENT_NAME` pattern |
| Docker Compose profiles | `docker-compose.yml` | Same profile pattern |
| Agent registry env var | `AGENT_REGISTRY` JSON | Same discovery pattern |

**Replace with MAF SDK (agent layer):**

| WorkGraph.ai Custom | Replaced By MAF | Why |
|---------------------|-----------------|-----|
| `A2AAgentDefinition` dataclass | `ChatAgent` class | MAF handles agent config natively |
| `WorkGraphAgentExecutor` + function-calling loop | `ChatAgent.run()` / `run_stream()` | MAF manages the LLM loop internally |
| Custom `@tool` + `ToolRegistry` | MAF `@tool` decorator | Official decorator, auto-generates schemas |
| `build_agent_app()` Starlette builder | `A2AAgentHost` from `agent-framework-a2a` | Official A2A hosting, less boilerplate |
| Manual A2A client calls | `A2AAgent.connect()` + `HandoffOrchestration` | Built-in agent-to-agent communication |
| Manual conversation history | `AgentSession` + `InMemoryHistoryProvider` | Automatic session management |
| Manual OTel span creation | Built-in telemetry | MAF instruments agent runs automatically |

---

## 2. Tech Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| Language | Python | 3.12 |
| Agent Framework | agent-framework | 1.0.0 (GA) |
| Agent A2A | agent-framework-a2a | 1.0.0 |
| OpenAI Provider | agent-framework-openai | 1.0.0 |
| A2A Protocol SDK | a2a-python | >= 0.2.4 |
| Web Framework | FastAPI (orchestrator) + Starlette (agents via A2AAgentHost) | >= 0.115 |
| ASGI Server | uvicorn | >= 0.34 |
| LLM Client | agent-framework-openai (OpenAI + Azure OpenAI) | 1.0.0 |
| LLM Runtime | OpenAI API / Azure OpenAI API | - |
| Default Model | gpt-4.1 | - |
| Database | PostgreSQL + pgvector | 16 + 0.7 |
| DB Driver | asyncpg | >= 0.30 |
| Cache | Redis | 7 |
| Auth | PyJWT + bcrypt | - |
| Validation | Pydantic + pydantic-settings | >= 2.10 |
| Streaming | sse-starlette | >= 2.2 |
| HTTP Client | httpx | >= 0.28 |
| Telemetry | opentelemetry-sdk + exporters | >= 1.28 |
| Token Counting | tiktoken | >= 0.8 |
| Package Manager | uv | latest |
| Frontend | Next.js 15 (App Router, React 19) | 15.x |
| UI Framework | Tailwind CSS + shadcn/ui | - |
| Frontend Chat | CopilotKit (AG-UI protocol) | latest |
| Containerization | Docker + Docker Compose | - |

---

## 3. Project Structure

```
agentbazaar/
├── docker-compose.yml
├── .env.example
├── README.md
├── scripts/
│   ├── seed.py                      # Database seeder (products, orders, reviews, etc.)
│   └── generate_embeddings.py       # Generate product embeddings for pgvector
│
├── docker/
│   └── postgres/
│       └── init.sql                 # Schema creation + pgvector extension
│
├── agents/
│   ├── Dockerfile                   # Multi-target: ARG AGENT_NAME, ARG AGENT_PORT
│   ├── pyproject.toml               # Shared dependencies for all agents
│   │
│   ├── shared/                      # Shared library (copied into every agent container)
│   │   ├── __init__.py
│   │   ├── auth.py                 # AgentAuthMiddleware (JWT + RBAC)
│   │   ├── jwt_utils.py            # JWT creation/validation (PyJWT + bcrypt)
│   │   ├── config.py               # Pydantic Settings singleton
│   │   ├── context.py              # ContextVars (user_email, session_id)
│   │   ├── db.py                   # asyncpg pool init/get/close
│   │   ├── context_providers.py    # MAF ContextProvider: ECommerceContextProvider
│   │   ├── usage_db.py             # Usage logging + audit trail
│   │   └── agent_factory.py        # Helper: create OpenAI/Azure ChatClient + A2AAgentHost
│   │
│   ├── shared/tools/               # E-commerce tool modules
│   │   ├── __init__.py             # Auto-imports all tool modules
│   │   ├── product_search.py       # search_products, get_product, compare_products
│   │   ├── product_embeddings.py   # semantic_search, find_similar_products
│   │   ├── order_tools.py          # get_orders, get_order_details, cancel_order
│   │   ├── return_tools.py         # check_return_eligibility, initiate_return, process_refund
│   │   ├── pricing_tools.py        # validate_coupon, optimize_cart, get_price_history
│   │   ├── loyalty_tools.py        # get_loyalty_tier, calculate_loyalty_discount
│   │   ├── review_tools.py         # get_reviews, analyze_sentiment, detect_fake_reviews
│   │   ├── inventory_tools.py      # check_stock, get_warehouse_availability
│   │   ├── shipping_tools.py       # estimate_shipping, compare_carriers, get_tracking
│   │   ├── user_tools.py           # get_user_profile, get_purchase_history
│   │   └── catalog_tools.py        # get_agent_catalog, check_agent_access
│   │
│   ├── orchestrator/                # Customer Support / Orchestrator Agent
│   │   ├── __init__.py
│   │   ├── main.py                 # FastAPI app (custom routes + HandoffOrchestration)
│   │   ├── agent.py                # create_orchestrator() using HandoffOrchestration
│   │   ├── intent.py               # Intent classifier (structured output)
│   │   ├── routes.py               # Auth, marketplace, admin API routes
│   │   └── prompts.py              # System prompts for orchestration
│   │
│   ├── product_discovery/           # Product Discovery Agent
│   │   ├── __init__.py
│   │   ├── main.py                 # A2AAgentHost entry point
│   │   ├── agent.py                # create_product_discovery_agent() → ChatAgent
│   │   ├── tools.py                # @tool functions (search, compare, semantic)
│   │   └── prompts.py
│   │
│   ├── order_management/            # Order Management Agent
│   │   ├── __init__.py
│   │   ├── main.py
│   │   ├── agent.py                # create_order_management_agent() → ChatAgent
│   │   ├── tools.py                # @tool functions (track, return, refund)
│   │   └── prompts.py
│   │
│   ├── pricing_promotions/          # Pricing & Promotions Agent
│   │   ├── __init__.py
│   │   ├── main.py
│   │   ├── agent.py                # create_pricing_agent() → ChatAgent
│   │   ├── tools.py                # @tool functions (coupon, cart, deals)
│   │   └── prompts.py
│   │
│   ├── review_sentiment/            # Review & Sentiment Agent
│   │   ├── __init__.py
│   │   ├── main.py
│   │   ├── agent.py                # create_review_agent() → ChatAgent
│   │   ├── tools.py                # @tool functions (sentiment, fake detect)
│   │   └── prompts.py
│   │
│   └── inventory_fulfillment/       # Inventory & Fulfillment Agent
│       ├── __init__.py
│       ├── main.py
│       ├── agent.py                # create_inventory_agent() → ChatAgent
│       ├── tools.py                # @tool functions (stock, shipping, carriers)
│       └── prompts.py
│
├── web/                             # Next.js 15 Frontend
│   ├── package.json
│   ├── next.config.ts
│   ├── tailwind.config.ts
│   ├── src/
│   │   ├── app/
│   │   │   ├── layout.tsx
│   │   │   ├── page.tsx             # Product catalog home
│   │   │   ├── chat/page.tsx        # Chat interface + agent trace
│   │   │   ├── product/[id]/page.tsx
│   │   │   ├── orders/page.tsx
│   │   │   ├── marketplace/page.tsx # Agent catalog
│   │   │   ├── agents/page.tsx      # My approved agents
│   │   │   ├── admin/
│   │   │   │   ├── page.tsx         # Admin overview
│   │   │   │   ├── requests/page.tsx
│   │   │   │   ├── usage/page.tsx
│   │   │   │   └── audit/page.tsx
│   │   │   └── api/
│   │   │       ├── auth/
│   │   │       │   ├── signup/route.ts
│   │   │       │   ├── login/route.ts
│   │   │       │   └── refresh/route.ts
│   │   │       ├── marketplace/route.ts
│   │   │       ├── access-requests/route.ts
│   │   │       └── admin/route.ts
│   │   ├── components/
│   │   │   ├── chat/
│   │   │   │   ├── ChatPanel.tsx
│   │   │   │   ├── AgentTrace.tsx   # Real-time agent trace sidebar
│   │   │   │   └── MessageBubble.tsx
│   │   │   ├── marketplace/
│   │   │   │   ├── AgentCard.tsx
│   │   │   │   └── AccessRequestDialog.tsx
│   │   │   ├── products/
│   │   │   │   ├── ProductGrid.tsx
│   │   │   │   └── ProductDetail.tsx
│   │   │   └── admin/
│   │   │       ├── RequestsTable.tsx
│   │   │       └── UsageCharts.tsx
│   │   ├── lib/
│   │   │   ├── auth.ts              # JWT token management
│   │   │   ├── api.ts               # Typed API client
│   │   │   └── validations.ts       # Zod schemas
│   │   └── hooks/
│   │       ├── useChat.ts           # Chat + streaming hook
│   │       └── useAgentTrace.ts     # SSE agent trace events
│   └── Dockerfile
│
└── docs/
    └── architecture.md              # This document (for repo)
```

---

## 4. Core Framework — Using MAF SDK Natively

Unlike WorkGraph.ai which uses a custom `executor_base.py` with a raw OpenAI function-calling loop, AgentBazaar uses the **official MAF SDK classes**. This eliminates the need for custom tool registries, function-calling loops, and A2A wrappers.

### 4.1 Agent Definition (MAF ChatAgent)

Each specialist agent is defined using MAF's `ChatAgent` with the OpenAI/Azure OpenAI provider. No custom `AgentDefinition` dataclass needed.

```python
# agents/product_discovery/agent.py

from agent_framework import ChatAgent, tool
from shared.agent_factory import create_chat_client
from typing import Annotated
from pydantic import Field

# --- Tool definitions using MAF's @tool decorator ---

@tool(name="search_products", description="Search the product catalog using natural language. Supports filtering by category, price range, and rating.")
async def search_products(
    query: Annotated[str, Field(description="Natural language search query")],
    category: Annotated[str | None, Field(description="Filter by category")] = None,
    min_price: Annotated[float | None, Field(description="Minimum price")] = None,
    max_price: Annotated[float | None, Field(description="Maximum price")] = None,
    min_rating: Annotated[float | None, Field(description="Minimum rating 1-5")] = None,
    limit: Annotated[int, Field(description="Max results")] = 10,
) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        # Build query with filters...
        rows = await conn.fetch(query_sql, *args)
        return [dict(r) for r in rows]

@tool(name="semantic_search", description="Search products using semantic similarity via pgvector embeddings.")
async def semantic_search(
    query: Annotated[str, Field(description="Descriptive search query")],
    limit: Annotated[int, Field(description="Max results")] = 5,
) -> list[dict]:
    # Generate embedding via OpenAI, search pgvector
    ...

# --- Agent creation ---

def create_product_discovery_agent() -> ChatAgent:
    client = create_chat_client()
    return ChatAgent(
        chat_client=client,
        name="product-discovery",
        description="Natural language product search with personalized recommendations",
        instructions=SYSTEM_PROMPT,
        tools=[
            search_products,
            semantic_search,
            get_product_details,
            compare_products,
            find_similar_products,
            check_stock,
            get_purchase_history,
            get_price_history,
        ],
    )
```

### 4.2 Sessions & Memory (MAF AgentSession)

MAF handles conversation memory automatically via `AgentSession` and `ContextProvider`.

```python
from agent_framework import ChatAgent, AgentSession, ContextProvider, SessionContext

class ECommerceContextProvider(ContextProvider):
    """Injects user profile and recent order context into every agent call."""

    DEFAULT_SOURCE_ID = "ecommerce_context"

    def __init__(self):
        super().__init__(self.DEFAULT_SOURCE_ID)

    async def before_run(self, *, agent, session, context: SessionContext, state: dict) -> None:
        user_email = state.get("user_email")
        if not user_email:
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            # Inject user profile
            user = await conn.fetchrow("SELECT name, loyalty_tier, total_spend FROM users WHERE email = $1", user_email)
            if user:
                context.extend_instructions(
                    self.source_id,
                    f"Customer: {user['name']}, Loyalty: {user['loyalty_tier']}, Total Spend: ${user['total_spend']}"
                )

            # Inject recent orders summary
            orders = await conn.fetch(
                "SELECT id, status, total, created_at FROM orders WHERE user_id = (SELECT id FROM users WHERE email = $1) ORDER BY created_at DESC LIMIT 5",
                user_email,
            )
            if orders:
                summary = "\n".join([f"  Order {o['id'][:8]}: {o['status']} (${o['total']})" for o in orders])
                context.extend_instructions(self.source_id, f"Recent orders:\n{summary}")

# Usage in agent creation:
agent = ChatAgent(
    chat_client=client,
    name="order-management",
    instructions=SYSTEM_PROMPT,
    tools=[...],
    context_providers=[
        ECommerceContextProvider(),
        InMemoryHistoryProvider(load_messages=True),  # Conversation history
    ],
)

# Session-based execution:
session = agent.create_session()
session.state["user_email"] = "customer@example.com"

response = await agent.run("Where's my last order?", session=session)
# Session automatically maintains history for follow-up questions
```

### 4.3 Handoff Orchestration (Customer Support → Specialists)

The orchestrator uses MAF's built-in **Handoff orchestration** pattern. This is a key MAF feature — agents hand off to each other based on their instructions, with context broadcast.

```python
# agents/orchestrator/agent.py

from agent_framework import ChatAgent
from agent_framework.workflows import HandoffOrchestration
from agent_framework.a2a import A2AAgent

async def create_orchestrator():
    """Build the orchestrator with handoff to specialist agents via A2A."""

    # Connect to specialist agents via A2A protocol
    product_agent = await A2AAgent.connect(
        name="product-discovery",
        url="http://product-discovery:8081",
    )
    order_agent = await A2AAgent.connect(
        name="order-management",
        url="http://order-management:8082",
    )
    pricing_agent = await A2AAgent.connect(
        name="pricing-promotions",
        url="http://pricing-promotions:8083",
    )
    review_agent = await A2AAgent.connect(
        name="review-sentiment",
        url="http://review-sentiment:8084",
    )
    inventory_agent = await A2AAgent.connect(
        name="inventory-fulfillment",
        url="http://inventory-fulfillment:8085",
    )

    # Create the orchestrator with handoff capability
    orchestrator = ChatAgent(
        chat_client=create_chat_client(),
        name="customer-support",
        instructions=ORCHESTRATOR_SYSTEM_PROMPT,
        description="Customer support orchestrator that routes to specialist agents",
    )

    # Build handoff orchestration
    workflow = HandoffOrchestration(
        agents=[
            orchestrator,
            product_agent,
            order_agent,
            pricing_agent,
            review_agent,
            inventory_agent,
        ],
        default_agent=orchestrator,  # Entry point
    )

    return workflow
```

### 4.4 A2A Agent Hosting (Exposing Agents via A2A Protocol)

Each specialist agent exposes itself as an A2A endpoint using `agent-framework-a2a`.

```python
# agents/product_discovery/main.py

from agent_framework.a2a.hosting import A2AAgentHost
from product_discovery.agent import create_product_discovery_agent
from shared.auth import AgentAuthMiddleware
from shared.db import init_db_pool, close_db_pool
from contextlib import asynccontextmanager
import uvicorn

@asynccontextmanager
async def lifespan(app):
    await init_db_pool()
    yield
    await close_db_pool()

agent = create_product_discovery_agent()

# A2AAgentHost wraps the ChatAgent in a Starlette app with:
#   GET  /.well-known/agent-card.json
#   POST /message:send
#   POST /message:stream
#   GET  /health
host = A2AAgentHost(
    agent=agent,
    port=8081,
    lifespan=lifespan,
    middleware=[AgentAuthMiddleware],
)

app = host.app  # Starlette ASGI app for uvicorn

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8081)
```

### 4.5 Tool Pattern Comparison (MAF vs WorkGraph.ai)

**WorkGraph.ai (custom pattern):**
```python
# Custom @tool decorator + Pydantic input model
class SearchInput(BaseModel):
    query: str = Field(description="Search query")

@tool(description="Search products")
async def search_products(params: SearchInput, context: dict) -> list[dict]:
    token = context["access_token"]
    # ...
```

**AgentBazaar with MAF (official pattern):**
```python
# MAF @tool decorator + Annotated params (no separate input model needed)
from agent_framework import tool
from typing import Annotated
from pydantic import Field

@tool(name="search_products", description="Search products")
async def search_products(
    query: Annotated[str, Field(description="Search query")],
    category: Annotated[str | None, Field(description="Category filter")] = None,
) -> list[dict]:
    pool = get_pool()  # Access DB via module-level pool
    # ...
```

Key differences:
- MAF uses `Annotated` type hints instead of a separate Pydantic input model
- No `context: dict` parameter — use module-level singletons or ContextVars
- MAF automatically generates the function schema from type annotations
- No custom `ToolRegistry` needed — pass tools directly to `ChatAgent(tools=[...])`

---

## 5. Authentication & Authorization

### 5.1 JWT Auth (Self-Contained)

No Azure AD. Local JWT with bcrypt password hashing.

```python
# agents/shared/jwt_utils.py

import jwt
import bcrypt
from datetime import datetime, timedelta

JWT_SECRET = settings.JWT_SECRET          # Random 256-bit key from .env
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRY = timedelta(hours=24)
REFRESH_TOKEN_EXPIRY = timedelta(days=30)

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

def create_access_token(user_email: str, role: str) -> str:
    payload = {
        "sub": user_email,
        "role": role,
        "exp": datetime.utcnow() + ACCESS_TOKEN_EXPIRY,
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def decode_token(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
```

### 5.2 Auth Middleware

Adapted from WorkGraph.ai's `AgentAuthMiddleware`. Swaps Azure AD decode for local JWT.

```python
# agents/shared/auth.py

class AgentAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, agent_name: str):
        super().__init__(app)
        self.agent_name = agent_name

    async def dispatch(self, request: Request, call_next):
        # Skip public paths
        if request.url.path in ("/health", "/.well-known/agent-card.json"):
            return await call_next(request)

        token = request.headers.get("Authorization", "").removeprefix("Bearer ")

        # Inter-agent calls use shared secret
        if token == settings.AGENT_SHARED_SECRET:
            # Extract user_email from X-User-Email header (set by orchestrator)
            user_email = request.headers.get("X-User-Email", "system")
            current_user_email.set(user_email)
            return await call_next(request)

        # User calls: decode JWT
        try:
            payload = decode_token(token)
        except jwt.ExpiredSignatureError:
            return JSONResponse({"error": "Token expired"}, status_code=401)
        except jwt.InvalidTokenError:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        user_email = payload["sub"]
        user_role = payload["role"]

        # Check agent access (skip for orchestrator — always accessible)
        if self.agent_name != "orchestrator":
            has_access = await _check_agent_access(user_email, user_role, self.agent_name)
            if not has_access:
                return JSONResponse({"error": "Agent access not granted"}, status_code=403)

        current_user_email.set(user_email)
        current_user_role.set(user_role)
        return await call_next(request)
```

### 5.3 RBAC Model

```
Roles:
  customer    → orchestrator only (default on signup)
  power_user  → all agents via API (granted by admin)
  seller      → review + inventory + pricing agents
  admin       → everything + admin endpoints

Access flow:
  1. User signs up → role = "customer"
  2. User requests access to an agent → access_requests table
  3. Admin approves → agent_permissions table
  4. Middleware checks agent_permissions before proxying
```

---

## 6. LLM Client Abstraction (MAF Providers)

MAF provides provider-specific `ChatClient` classes. We use a factory helper to create the right client based on config.

```python
# agents/shared/agent_factory.py

from agent_framework.openai import OpenAIChatClient
from agent_framework.azure import AzureOpenAIChatClient
from shared.config import settings

def create_chat_client():
    """Create the appropriate MAF ChatClient based on LLM_PROVIDER env var."""
    provider = settings.LLM_PROVIDER

    if provider == "openai":
        return OpenAIChatClient(
            model_id=settings.LLM_MODEL,           # "gpt-4.1"
            api_key=settings.OPENAI_API_KEY,
        )
    elif provider == "azure":
        return AzureOpenAIChatClient(
            model_id=settings.AZURE_OPENAI_DEPLOYMENT,
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
        )
    else:
        raise ValueError(f"Unknown LLM provider: {provider}. Use 'openai' or 'azure'.")


def create_embedding_client():
    """Create an OpenAI/Azure client for embedding generation."""
    import openai
    if settings.LLM_PROVIDER == "azure":
        return openai.AsyncAzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
        )
    return openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
```

Usage in any agent:
```python
from shared.agent_factory import create_chat_client

agent = ChatAgent(
    chat_client=create_chat_client(),  # Swap provider with one env var
    name="product-discovery",
    instructions=SYSTEM_PROMPT,
    tools=[...],
)
```

Available MAF provider packages: `agent-framework-openai`, `agent-framework-foundry`, `agent-framework-anthropic`, `agent-framework-bedrock`.

---

## 7. Orchestrator Agent (Customer Support)

The orchestrator is the only agent that uses FastAPI (not Starlette) because it needs custom routes for conversations, auth endpoints, marketplace, and admin.

### 7.1 Architecture

```
User Message
    │
    ▼
┌──────────────────────────────┐
│  Intent Classification       │
│  (LLM with structured output)│
└──────────┬───────────────────┘
           │
           ▼
┌──────────────────────────────┐
│  Intent Router               │
│  Maps intents → agents       │
│  Handles multi-intent split  │
└──────────┬───────────────────┘
           │
    ┌──────┼──────┬──────┐
    ▼      ▼      ▼      ▼
  Agent  Agent  Agent  Direct
   A      B      C    Response
    │      │      │      │
    └──────┼──────┘      │
           ▼             │
┌──────────────────┐     │
│  Response        │     │
│  Aggregator      │◄────┘
│  (merges multi-  │
│   agent results) │
└──────────────────┘
           │
           ▼
    Streamed to User
```

### 7.2 Intent Classification

```python
# agents/orchestrator/intent.py

class Intent(str, Enum):
    PRODUCT_QUESTION = "product_question"
    ORDER_INQUIRY = "order_inquiry"
    RETURN_REQUEST = "return_request"
    PRICING_QUESTION = "pricing_question"
    REVIEW_QUESTION = "review_question"
    SHIPPING_QUESTION = "shipping_question"
    COMPLAINT = "complaint"
    GENERAL_FAQ = "general_faq"

class ClassifiedIntent(BaseModel):
    """Structured output from intent classification."""
    intents: list[Intent]                    # Can be multiple (multi-intent)
    confidence: float                        # 0.0 - 1.0
    requires_order_context: bool             # Should we look up user's recent orders?
    requires_product_context: bool           # Should we look up mentioned products?
    extracted_entities: dict[str, str]       # product_name, order_id, etc.
    escalation_needed: bool                  # Low confidence or negative sentiment

INTENT_TO_AGENT = {
    Intent.PRODUCT_QUESTION: "product-discovery",
    Intent.ORDER_INQUIRY: "order-management",
    Intent.RETURN_REQUEST: "order-management",
    Intent.PRICING_QUESTION: "pricing-promotions",
    Intent.REVIEW_QUESTION: "review-sentiment",
    Intent.SHIPPING_QUESTION: "inventory-fulfillment",
}
```

### 7.3 A2A Client (Agent-to-Agent Calls)

The orchestrator calls specialist agents via A2A protocol.

```python
# agents/orchestrator/router.py

class AgentRouter:
    """Routes classified intents to specialist agents via A2A protocol."""

    def __init__(self):
        self.registry: dict[str, str] = json.loads(settings.AGENT_REGISTRY)
        # {"product-discovery": "http://product-discovery:8081", ...}

    async def route(
        self, intents: list[Intent], user_message: str, context: dict
    ) -> AsyncGenerator[str, None]:
        """Route to one or more agents. For multi-intent, run in parallel."""

        agents_to_call = []
        for intent in intents:
            agent_name = INTENT_TO_AGENT.get(intent)
            if agent_name and agent_name not in [a[0] for a in agents_to_call]:
                agents_to_call.append((agent_name, self.registry[agent_name]))

        if len(agents_to_call) == 1:
            # Single agent: stream directly
            async for chunk in self._call_agent_a2a(
                agents_to_call[0][0], agents_to_call[0][1], user_message, context
            ):
                yield chunk
        else:
            # Multi-agent: run concurrently, aggregate
            results = await asyncio.gather(*[
                self._call_agent_buffered(name, url, user_message, context)
                for name, url in agents_to_call
            ])
            # Aggregate results via LLM
            async for chunk in self._aggregate_responses(results, user_message, context):
                yield chunk

    async def _call_agent_a2a(
        self, agent_name: str, agent_url: str, message: str, context: dict
    ) -> AsyncGenerator[str, None]:
        """Call a specialist agent via A2A /message:stream endpoint."""
        headers = {
            "Authorization": f"Bearer {settings.AGENT_SHARED_SECRET}",
            "X-User-Email": context["user_email"],
        }
        payload = {
            "jsonrpc": "2.0",
            "method": "message/stream",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": message}],
                },
            },
        }

        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream(
                "POST", f"{agent_url}/message:stream", json=payload, headers=headers
            ) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        event = json.loads(line[6:])
                        if text := _extract_text_from_a2a_event(event):
                            yield text
```

### 7.4 Orchestrator Custom Routes

```python
# agents/orchestrator/main.py

app = FastAPI(lifespan=lifespan)

# Chat endpoints
@app.post("/")                          # AG-UI Protocol (CopilotKit)
@app.post("/message:send")              # A2A buffered
@app.post("/message:stream")            # A2A streaming

# Auth endpoints (no middleware)
@app.post("/api/auth/signup")           # Create account → JWT
@app.post("/api/auth/login")            # Authenticate → JWT
@app.post("/api/auth/refresh")          # Refresh token

# Conversation endpoints
@app.get("/api/conversations")          # List user's conversations
@app.get("/api/conversations/{id}")     # Get conversation with messages
@app.delete("/api/conversations/{id}")  # Delete conversation

# Marketplace endpoints
@app.get("/api/marketplace/agents")     # List agent catalog
@app.get("/api/marketplace/agents/{name}")  # Agent details
@app.post("/api/marketplace/request")   # Request agent access
@app.get("/api/marketplace/my-agents")  # User's approved agents

# Admin endpoints (admin role required)
@app.get("/api/admin/requests")         # Pending access requests
@app.post("/api/admin/requests/{id}/approve")
@app.post("/api/admin/requests/{id}/deny")
@app.get("/api/admin/usage")            # Usage analytics
@app.get("/api/admin/audit")            # Audit log
@app.get("/api/admin/agents")           # Agent health + management

# Middleware
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"])
app.add_middleware(AgentAuthMiddleware, agent_name="orchestrator")
```

---

## 8. Specialist Agent Definitions (MAF ChatAgent)

All agents follow the same pattern: `agent.py` creates a `ChatAgent` with tools and prompt, `main.py` hosts it via `A2AAgentHost`.

### 8.1 Product Discovery Agent

```python
# agents/product_discovery/agent.py

from agent_framework import ChatAgent
from shared.agent_factory import create_chat_client
from shared.context_providers import ECommerceContextProvider
from product_discovery.tools import (
    search_products, get_product_details, compare_products,
    semantic_search, find_similar_products,
)
from shared.tools.inventory_tools import check_stock
from shared.tools.user_tools import get_purchase_history
from shared.tools.pricing_tools import get_price_history
from product_discovery.prompts import SYSTEM_PROMPT

def create_product_discovery_agent() -> ChatAgent:
    return ChatAgent(
        chat_client=create_chat_client(),
        name="product-discovery",
        description="Natural language product search with personalized recommendations",
        instructions=SYSTEM_PROMPT,
        tools=[
            search_products,
            get_product_details,
            compare_products,
            semantic_search,
            find_similar_products,
            check_stock,
            get_purchase_history,
            get_price_history,
        ],
        context_providers=[ECommerceContextProvider()],
    )
```

```python
# agents/product_discovery/prompts.py

SYSTEM_PROMPT = """You are the Product Discovery Agent for AgentBazaar, an e-commerce platform.
Your job is to help customers find products through natural language search, comparisons,
and personalized recommendations.

You have access to tools that search the product catalog (50 products across Electronics,
Clothing, Home, Sports, Books), check stock levels, and retrieve user purchase history
for personalization.

Guidelines:
- Always check stock availability before recommending products
- When comparing products, present a clear side-by-side table
- For price-sensitive queries, mention price history trends
- Suggest alternatives when a product is out of stock
- Include ratings and review counts in product recommendations
"""
```

### 8.2 Order Management Agent

```python
# agents/order_management/agent.py

def create_order_management_agent() -> ChatAgent:
    return ChatAgent(
        chat_client=create_chat_client(),
        name="order-management",
        description="Order tracking, modifications, returns, and refund processing",
        instructions=SYSTEM_PROMPT,  # Includes return policy, modification rules
        tools=[
            get_user_orders, get_order_details, get_order_tracking,
            cancel_order, modify_order,
            check_return_eligibility, initiate_return, process_refund, get_return_status,
            get_user_profile,
        ],
        context_providers=[ECommerceContextProvider()],
    )
```

Key system prompt rules:
- 30-day return window from delivery date
- Items must be unused
- Can only modify orders in "placed" or "confirmed" status
- Always verify order belongs to requesting user

### 8.3 Pricing & Promotions Agent

```python
# agents/pricing_promotions/agent.py

def create_pricing_agent() -> ChatAgent:
    return ChatAgent(
        chat_client=create_chat_client(),
        name="pricing-promotions",
        description="Coupon validation, deal discovery, bundle pricing, and price intelligence",
        instructions=SYSTEM_PROMPT,
        tools=[
            validate_coupon, optimize_cart, get_active_deals,
            get_price_history, check_bundle_eligibility,
            get_loyalty_tier, calculate_loyalty_discount, get_loyalty_benefits,
        ],
    )
```

### 8.4 Review & Sentiment Agent

```python
# agents/review_sentiment/agent.py

def create_review_agent() -> ChatAgent:
    return ChatAgent(
        chat_client=create_chat_client(),
        name="review-sentiment",
        description="Review analysis, summarization, sentiment trends, and fake detection",
        instructions=SYSTEM_PROMPT,
        tools=[
            get_product_reviews, analyze_sentiment, get_sentiment_by_topic,
            get_sentiment_trend, detect_fake_reviews, search_reviews,
            draft_seller_response, compare_product_reviews,
        ],
    )
```

### 8.5 Inventory & Fulfillment Agent

```python
# agents/inventory_fulfillment/agent.py

def create_inventory_agent() -> ChatAgent:
    return ChatAgent(
        chat_client=create_chat_client(),
        name="inventory-fulfillment",
        description="Stock management, warehouse routing, shipping estimates, carrier selection",
        instructions=SYSTEM_PROMPT,
        tools=[
            check_stock, get_warehouse_availability, get_restock_schedule,
            estimate_shipping, compare_carriers, get_tracking_status,
            calculate_fulfillment_plan, place_backorder,
        ],
    )
```

---

## 9. Tool Definitions (MAF @tool Pattern)

All tools use MAF's `@tool` decorator with `Annotated` type hints. No separate Pydantic input models needed. Database access via module-level `get_pool()`.

### 9.1 Product Search Tool

```python
# agents/product_discovery/tools.py

from agent_framework import tool
from typing import Annotated
from pydantic import Field
from shared.db import get_pool

@tool(name="search_products", description="Search the product catalog using natural language. Supports filtering by category, price range, and rating.")
async def search_products(
    query: Annotated[str, Field(description="Natural language search query")],
    category: Annotated[str | None, Field(description="Filter by category")] = None,
    min_price: Annotated[float | None, Field(description="Minimum price filter")] = None,
    max_price: Annotated[float | None, Field(description="Maximum price filter")] = None,
    min_rating: Annotated[float | None, Field(description="Minimum rating (1-5)")] = None,
    sort_by: Annotated[str, Field(description="Sort: relevance, price_asc, price_desc, rating")] = "relevance",
    limit: Annotated[int, Field(description="Max results to return")] = 10,
) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        sql = "SELECT id, name, description, category, price, rating, review_count, image_url FROM products WHERE 1=1"
        args = []
        idx = 1

        if category:
            sql += f" AND category = ${idx}"; args.append(category); idx += 1
        if min_price is not None:
            sql += f" AND price >= ${idx}"; args.append(min_price); idx += 1
        if max_price is not None:
            sql += f" AND price <= ${idx}"; args.append(max_price); idx += 1
        if min_rating is not None:
            sql += f" AND rating >= ${idx}"; args.append(min_rating); idx += 1
        if query:
            sql += f" AND (name ILIKE ${idx} OR description ILIKE ${idx})"
            args.append(f"%{query}%"); idx += 1

        sort_map = {"price_asc": "price ASC", "price_desc": "price DESC", "rating": "rating DESC", "relevance": "review_count DESC"}
        sql += f" ORDER BY {sort_map.get(sort_by, 'review_count DESC')} LIMIT {limit}"

        rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]
```

### 9.2 Return Eligibility Tool

```python
# agents/order_management/tools.py

from agent_framework import tool
from shared.db import get_pool
from shared.context import current_user_email

@tool(name="check_return_eligibility", description="Check if an order is eligible for return. Returns eligibility status, remaining days in window, and reason if ineligible.")
async def check_return_eligibility(
    order_id: Annotated[str, Field(description="The order ID to check")],
) -> dict:
    pool = get_pool()
    user_email = current_user_email.get()

    async with pool.acquire() as conn:
        order = await conn.fetchrow(
            """SELECT o.id, o.status, u.email,
                      osh.timestamp as delivered_at
               FROM orders o
               JOIN users u ON o.user_id = u.id
               LEFT JOIN order_status_history osh
                   ON o.id = osh.order_id AND osh.status = 'delivered'
               WHERE o.id = $1""",
            order_id,
        )

        if not order:
            return {"eligible": False, "reason": "Order not found"}
        if order["email"] != user_email:
            return {"eligible": False, "reason": "Order does not belong to this user"}
        if order["status"] != "delivered":
            return {"eligible": False, "reason": f"Order status is '{order['status']}', must be 'delivered'"}

        days_since = (datetime.utcnow() - order["delivered_at"]).days
        if days_since > 30:
            return {"eligible": False, "reason": f"Return window expired ({days_since} days, 30-day policy)"}

        return {
            "eligible": True,
            "days_remaining": 30 - days_since,
            "refund_options": ["original_payment", "store_credit"],
        }
```

### 9.3 Semantic Product Search Tool

```python
# agents/product_discovery/tools.py

import json
from shared.config import settings
from shared.agent_factory import create_embedding_client
from shared.db import get_pool

@tool(name="semantic_search", description="Search products using semantic similarity via pgvector. Best for vague queries like 'something cozy for winter'.")
async def semantic_search(
    query: Annotated[str, Field(description="Descriptive search query")],
    limit: Annotated[int, Field(description="Max results")] = 5,
) -> list[dict]:
    pool = get_pool()

    # Generate embedding via OpenAI / Azure OpenAI (text-embedding-3-small, 1536 dims)
    client = create_embedding_client()
    response = await client.embeddings.create(
        model=settings.EMBEDDING_MODEL,
        input=[query],
    )
    embedding = response.data[0].embedding

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT p.id, p.name, p.description, p.category, p.price, p.rating,
                      1 - (pe.embedding <=> $1::vector) as similarity
               FROM product_embeddings pe
               JOIN products p ON pe.product_id = p.id
               ORDER BY pe.embedding <=> $1::vector
               LIMIT $2""",
            json.dumps(embedding), limit,
        )
        return [dict(r) for r in rows]
```

---

## 10. Database Schema

```sql
-- docker/postgres/init.sql

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ============================================================
-- AUTH & USERS
-- ============================================================

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL DEFAULT 'customer',  -- customer, power_user, seller, admin
    loyalty_tier VARCHAR(50) DEFAULT 'bronze',      -- bronze, silver, gold
    total_spend DECIMAL(10, 2) DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    is_active BOOLEAN DEFAULT TRUE
);

-- ============================================================
-- PRODUCT CATALOG
-- ============================================================

CREATE TABLE products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    category VARCHAR(100) NOT NULL,   -- Electronics, Clothing, Home, Sports, Books
    brand VARCHAR(100),
    price DECIMAL(10, 2) NOT NULL,
    original_price DECIMAL(10, 2),    -- For showing discounts
    image_url VARCHAR(500),
    rating DECIMAL(3, 2) DEFAULT 0,
    review_count INTEGER DEFAULT 0,
    specs JSONB DEFAULT '{}',          -- Product-specific attributes
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE product_embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID REFERENCES products(id) ON DELETE CASCADE,
    embedding vector(1536),            -- text-embedding-3-small dimension
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_product_embedding ON product_embeddings
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

CREATE TABLE price_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID REFERENCES products(id) ON DELETE CASCADE,
    price DECIMAL(10, 2) NOT NULL,
    recorded_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- ORDERS & RETURNS
-- ============================================================

CREATE TABLE orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    status VARCHAR(50) NOT NULL DEFAULT 'placed',
        -- placed, confirmed, shipped, out_for_delivery, delivered, cancelled, returned
    total DECIMAL(10, 2) NOT NULL,
    shipping_address JSONB NOT NULL,    -- {street, city, state, zip, country}
    shipping_carrier VARCHAR(100),
    tracking_number VARCHAR(255),
    coupon_code VARCHAR(50),
    discount_amount DECIMAL(10, 2) DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE order_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID REFERENCES orders(id) ON DELETE CASCADE,
    product_id UUID REFERENCES products(id),
    quantity INTEGER NOT NULL,
    unit_price DECIMAL(10, 2) NOT NULL,
    subtotal DECIMAL(10, 2) NOT NULL
);

CREATE TABLE order_status_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID REFERENCES orders(id) ON DELETE CASCADE,
    status VARCHAR(50) NOT NULL,
    notes TEXT,
    location VARCHAR(255),            -- Tracking location
    timestamp TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE returns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID REFERENCES orders(id),
    user_id UUID REFERENCES users(id),
    reason VARCHAR(255) NOT NULL,
    status VARCHAR(50) DEFAULT 'requested',  -- requested, approved, shipped_back, received, refunded, denied
    return_label_url VARCHAR(500),
    refund_method VARCHAR(50),          -- original_payment, store_credit
    refund_amount DECIMAL(10, 2),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

-- ============================================================
-- REVIEWS
-- ============================================================

CREATE TABLE reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID REFERENCES products(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id),
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    title VARCHAR(255),
    body TEXT NOT NULL,
    verified_purchase BOOLEAN DEFAULT FALSE,
    helpful_count INTEGER DEFAULT 0,
    is_flagged BOOLEAN DEFAULT FALSE,   -- Flagged as potentially fake
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- INVENTORY & SHIPPING
-- ============================================================

CREATE TABLE warehouses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,         -- "East", "Central", "West"
    location VARCHAR(255) NOT NULL,     -- "Richmond, VA"
    region VARCHAR(50) NOT NULL         -- "east", "central", "west"
);

CREATE TABLE warehouse_inventory (
    warehouse_id UUID REFERENCES warehouses(id),
    product_id UUID REFERENCES products(id),
    quantity INTEGER NOT NULL DEFAULT 0,
    reorder_threshold INTEGER DEFAULT 10,
    PRIMARY KEY (warehouse_id, product_id)
);

CREATE TABLE carriers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,         -- "Standard Shipping", "Express", "Overnight"
    speed_tier VARCHAR(50) NOT NULL,    -- "standard", "express", "overnight"
    base_rate DECIMAL(10, 2) NOT NULL
);

CREATE TABLE shipping_rates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    carrier_id UUID REFERENCES carriers(id),
    region_from VARCHAR(50) NOT NULL,
    region_to VARCHAR(50) NOT NULL,
    price DECIMAL(10, 2) NOT NULL,
    estimated_days_min INTEGER NOT NULL,
    estimated_days_max INTEGER NOT NULL
);

CREATE TABLE restock_schedule (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID REFERENCES products(id),
    warehouse_id UUID REFERENCES warehouses(id),
    expected_quantity INTEGER NOT NULL,
    expected_date DATE NOT NULL
);

-- ============================================================
-- PRICING & PROMOTIONS
-- ============================================================

CREATE TABLE coupons (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(50) UNIQUE NOT NULL,
    description TEXT,
    discount_type VARCHAR(20) NOT NULL,  -- "percentage", "fixed"
    discount_value DECIMAL(10, 2) NOT NULL,
    min_spend DECIMAL(10, 2) DEFAULT 0,
    max_discount DECIMAL(10, 2),         -- Cap for percentage discounts
    usage_limit INTEGER,
    times_used INTEGER DEFAULT 0,
    valid_from TIMESTAMPTZ DEFAULT NOW(),
    valid_until TIMESTAMPTZ,
    applicable_categories TEXT[],         -- NULL = all categories
    user_specific_email VARCHAR(255),     -- NULL = all users
    is_active BOOLEAN DEFAULT TRUE
);

CREATE TABLE promotions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    type VARCHAR(50) NOT NULL,           -- "bundle", "buy_x_get_y", "flash_sale"
    rules JSONB NOT NULL,                -- Flexible rule definitions
    start_date TIMESTAMPTZ NOT NULL,
    end_date TIMESTAMPTZ NOT NULL,
    is_active BOOLEAN DEFAULT TRUE
);

CREATE TABLE loyalty_tiers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(50) UNIQUE NOT NULL,    -- "bronze", "silver", "gold"
    min_spend DECIMAL(10, 2) NOT NULL,   -- Spend threshold to reach tier
    discount_pct DECIMAL(5, 2) NOT NULL, -- Tier-wide discount percentage
    free_shipping_threshold DECIMAL(10, 2),
    priority_support BOOLEAN DEFAULT FALSE
);

-- ============================================================
-- MARKETPLACE (Agent Catalog & Access Control)
-- ============================================================

CREATE TABLE agent_catalog (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) UNIQUE NOT NULL,   -- "product-discovery"
    display_name VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    category VARCHAR(100),
    icon VARCHAR(50),
    status VARCHAR(50) DEFAULT 'active',
    version VARCHAR(20) DEFAULT '1.0',
    capabilities TEXT[] DEFAULT '{}',
    input_types TEXT[] DEFAULT '{text}',
    output_types TEXT[] DEFAULT '{text}',
    requires_approval BOOLEAN DEFAULT TRUE,
    allowed_roles TEXT[] DEFAULT '{power_user,admin}',
    config JSONB DEFAULT '{}'
);

CREATE TABLE access_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    agent_name VARCHAR(100) REFERENCES agent_catalog(name),
    role_requested VARCHAR(50) NOT NULL,
    use_case TEXT NOT NULL,
    status VARCHAR(50) DEFAULT 'pending',  -- pending, approved, denied
    admin_notes TEXT,
    reviewed_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

CREATE TABLE agent_permissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    agent_name VARCHAR(100) REFERENCES agent_catalog(name),
    role VARCHAR(50) NOT NULL,
    granted_at TIMESTAMPTZ DEFAULT NOW(),
    granted_by UUID REFERENCES users(id),
    UNIQUE(user_id, agent_name)
);

-- ============================================================
-- CONVERSATIONS & USAGE
-- ============================================================

CREATE TABLE conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    title VARCHAR(255),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_message_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
    role VARCHAR(50) NOT NULL,           -- "user", "assistant", "system"
    content TEXT NOT NULL,
    agent_name VARCHAR(100),             -- Which agent generated this response
    agents_involved TEXT[],              -- For multi-agent responses
    metadata JSONB DEFAULT '{}',         -- Tool calls, trace data, etc.
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE usage_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    agent_name VARCHAR(100) NOT NULL,
    session_id UUID,
    input_summary TEXT,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    tool_calls_count INTEGER DEFAULT 0,
    duration_ms INTEGER,
    status VARCHAR(50) DEFAULT 'success',
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE agent_execution_steps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    usage_log_id UUID REFERENCES usage_logs(id) ON DELETE CASCADE,
    step_index INTEGER NOT NULL,
    tool_name VARCHAR(255),
    tool_input JSONB,
    tool_output JSONB,
    status VARCHAR(50) DEFAULT 'success',
    duration_ms INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- INDEXES
-- ============================================================

CREATE INDEX idx_products_category ON products(category);
CREATE INDEX idx_products_price ON products(price);
CREATE INDEX idx_products_rating ON products(rating DESC);
CREATE INDEX idx_orders_user ON orders(user_id, created_at DESC);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_reviews_product ON reviews(product_id, created_at DESC);
CREATE INDEX idx_reviews_rating ON reviews(product_id, rating);
CREATE INDEX idx_warehouse_inv ON warehouse_inventory(product_id);
CREATE INDEX idx_price_history ON price_history(product_id, recorded_at DESC);
CREATE INDEX idx_access_requests_status ON access_requests(status, created_at DESC);
CREATE INDEX idx_usage_logs_user ON usage_logs(user_id, created_at DESC);
CREATE INDEX idx_usage_logs_agent ON usage_logs(agent_name, created_at DESC);
CREATE INDEX idx_messages_conversation ON messages(conversation_id, created_at);
```

---

## 11. Docker Compose

```yaml
# docker-compose.yml

services:
  # ── Infrastructure ─────────────────────────────────────────
  db:
    image: pgvector/pgvector:pg16
    ports: ["5432:5432"]
    environment:
      POSTGRES_DB: agentbazaar
      POSTGRES_USER: agentbazaar
      POSTGRES_PASSWORD: agentbazaar
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./docker/postgres/init.sql:/docker-entrypoint-initdb.d/init.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U agentbazaar"]
      interval: 5s
      timeout: 3s
      retries: 5

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s

  # Seed database (runs once)
  seeder:
    build:
      context: ./agents
      args:
        AGENT_NAME: seeder
    command: python -m scripts.seed
    depends_on:
      db:
        condition: service_healthy
    environment:
      DATABASE_URL: postgresql://agentbazaar:agentbazaar@db:5432/agentbazaar
    restart: "no"

  # ── Aspire Dashboard (OpenTelemetry) ────────────────────────
  aspire:
    image: mcr.microsoft.com/dotnet/aspire-dashboard:latest
    ports:
      - "18888:18888"   # Dashboard UI
      - "18890:18889"   # OTLP receiver
    environment:
      DASHBOARD__FRONTEND__AUTHMODE: Unsecured

  # ── Orchestrator (Customer Support Agent) ───────────────────
  orchestrator:
    build:
      context: ./agents
      args:
        AGENT_NAME: orchestrator
        AGENT_PORT: 8080
    ports: ["8080:8080"]
    depends_on:
      db: { condition: service_healthy }
      redis: { condition: service_healthy }
      aspire: { condition: service_started }
    environment: &agent-env
      DATABASE_URL: postgresql://agentbazaar:agentbazaar@db:5432/agentbazaar
      REDIS_URL: redis://redis:6379
      LLM_PROVIDER: ${LLM_PROVIDER:-openai}
      OPENAI_API_KEY: ${OPENAI_API_KEY:-}
      LLM_MODEL: ${LLM_MODEL:-gpt-4.1}
      EMBEDDING_MODEL: ${EMBEDDING_MODEL:-text-embedding-3-small}
      AZURE_OPENAI_ENDPOINT: ${AZURE_OPENAI_ENDPOINT:-}
      AZURE_OPENAI_KEY: ${AZURE_OPENAI_KEY:-}
      AZURE_OPENAI_DEPLOYMENT: ${AZURE_OPENAI_DEPLOYMENT:-}
      AZURE_OPENAI_API_VERSION: ${AZURE_OPENAI_API_VERSION:-2024-12-01-preview}
      JWT_SECRET: ${JWT_SECRET:-change-me-in-production-use-random-256-bit-key}
      AGENT_SHARED_SECRET: ${AGENT_SHARED_SECRET:-agent-internal-secret}
      OTEL_ENABLED: "true"
      OTEL_EXPORTER_OTLP_ENDPOINT: http://aspire:18889
      AGENT_REGISTRY: >
        {
          "product-discovery": "http://product-discovery:8081",
          "order-management": "http://order-management:8082",
          "pricing-promotions": "http://pricing-promotions:8083",
          "review-sentiment": "http://review-sentiment:8084",
          "inventory-fulfillment": "http://inventory-fulfillment:8085"
        }

  # ── Specialist Agents ──────────────────────────────────────
  product-discovery:
    build:
      context: ./agents
      args: { AGENT_NAME: product-discovery, AGENT_PORT: 8081 }
    ports: ["8081:8081"]
    depends_on:
      db: { condition: service_healthy }
      aspire: { condition: service_started }
    environment:
      <<: *agent-env
      OTEL_SERVICE_NAME: agentbazaar.product-discovery

  order-management:
    build:
      context: ./agents
      args: { AGENT_NAME: order-management, AGENT_PORT: 8082 }
    ports: ["8082:8082"]
    depends_on:
      db: { condition: service_healthy }
      aspire: { condition: service_started }
    environment:
      <<: *agent-env
      OTEL_SERVICE_NAME: agentbazaar.order-management

  pricing-promotions:
    build:
      context: ./agents
      args: { AGENT_NAME: pricing-promotions, AGENT_PORT: 8083 }
    ports: ["8083:8083"]
    depends_on:
      db: { condition: service_healthy }
      aspire: { condition: service_started }
    environment:
      <<: *agent-env
      OTEL_SERVICE_NAME: agentbazaar.pricing-promotions

  review-sentiment:
    build:
      context: ./agents
      args: { AGENT_NAME: review-sentiment, AGENT_PORT: 8084 }
    ports: ["8084:8084"]
    depends_on:
      db: { condition: service_healthy }
      aspire: { condition: service_started }
    environment:
      <<: *agent-env
      OTEL_SERVICE_NAME: agentbazaar.review-sentiment

  inventory-fulfillment:
    build:
      context: ./agents
      args: { AGENT_NAME: inventory-fulfillment, AGENT_PORT: 8085 }
    ports: ["8085:8085"]
    depends_on:
      db: { condition: service_healthy }
      aspire: { condition: service_started }
    environment:
      <<: *agent-env
      OTEL_SERVICE_NAME: agentbazaar.inventory-fulfillment

  # ── Frontend ────────────────────────────────────────────────
  frontend:
    build:
      context: ./web
    ports: ["3000:3000"]
    depends_on:
      - orchestrator
    environment:
      NEXT_PUBLIC_API_URL: http://localhost:8080
      NEXT_PUBLIC_WS_URL: ws://localhost:8080

volumes:
  pgdata:
```

---

## 12. Observability

**OpenTelemetry is integrated from Phase 0/1** — not deferred. Every agent gets full observability the moment it is built.

### 12.1 Telemetry Pipeline

Every agent sends traces, metrics, and logs to the .NET Aspire Dashboard via OTLP HTTP.

**Implementation file:** `agents/shared/telemetry.py`

```python
setup_telemetry(service_name: str) → None     # Call in agent lifespan
instrument_fastapi(app) → None                 # Orchestrator only
instrument_starlette(app) → None               # Specialist agents
get_tracer(name: str) → Tracer                 # For custom spans
get_meter(name: str) → Meter                   # For custom metrics
```

### 12.2 Auto-Instrumentation (Zero Code in Agents)

| Layer | Instrumentor Package | What It Captures |
|-------|---------------------|-----------------|
| FastAPI (orchestrator) | `opentelemetry-instrumentation-fastapi` | HTTP request/response spans for all API routes |
| Starlette (specialist agents) | `opentelemetry-instrumentation-starlette` | A2A endpoint spans (/message:send, /message:stream) |
| httpx | `opentelemetry-instrumentation-httpx` | LLM API calls (OpenAI/Azure) + inter-agent A2A calls |
| asyncpg | `opentelemetry-instrumentation-asyncpg` | All DB queries with SQL text and duration |
| Python logging | `opentelemetry-instrumentation-logging` | All log statements with trace_id/span_id correlation |

### 12.3 Span Hierarchy

```
Specialist Agent (single request):
[HTTP POST /message:stream]                    ← Starlette auto
  └── [agent.run / agent.run_stream]           ← MAF built-in (if available)
        ├── [HTTP POST api.openai.com/...]     ← httpx auto (LLM call)
        ├── [db.query SELECT ... products]     ← asyncpg auto
        ├── [db.query SELECT ... inventory]    ← asyncpg auto
        └── [HTTP POST api.openai.com/...]     ← httpx auto (final LLM)

Orchestrator → Specialist (distributed trace):
[HTTP POST /api/chat]                          ← FastAPI auto (orchestrator)
  └── [agent.a2a_call target=product-discovery]← custom span
        └── [HTTP POST product-discovery:8081] ← httpx auto (propagates traceparent)
              └── [HTTP POST /message:stream]  ← Starlette auto (specialist)
                    ├── [HTTP POST api.openai] ← httpx auto (LLM)
                    └── [db.query ...]         ← asyncpg auto
```

### 12.4 Cross-Agent Trace Propagation

httpx auto-instrumentation injects `traceparent` header on outbound calls. Starlette auto-instrumentation reads it on the specialist side. Result: a single trace spans orchestrator + specialist + LLM + DB — visible as one unified trace in Aspire.

### 12.5 Custom Spans (Only Where MAF Gaps Exist)

| Span Name | Location | Attributes |
|-----------|----------|-----------|
| `agent.a2a_call` | Orchestrator routing to specialists | `target.agent`, `target.url`, `user.email` |
| `agent.tool_call` | `traced_tool` decorator (only if MAF doesn't emit) | `tool.name`, `tool.success` |

### 12.6 Usage Metrics

Every agent invocation logs to `usage_logs`:
- Token count (in/out) via tiktoken
- Duration (ms)
- Tool calls count
- Per-step breakdown in `agent_execution_steps`
- `trace_id` from active span context (for correlation with Aspire traces)

Admin dashboard queries these tables for analytics.

---

## 13. Configuration

```python
# agents/shared/config.py

class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql://agentbazaar:agentbazaar@localhost:5432/agentbazaar"

    # Redis
    REDIS_URL: str = "redis://localhost:6379"

    # LLM
    LLM_PROVIDER: str = "openai"                  # openai | azure
    LLM_MODEL: str = "gpt-4.1"
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    OPENAI_API_KEY: str = ""
    AZURE_OPENAI_ENDPOINT: str = ""
    AZURE_OPENAI_KEY: str = ""
    AZURE_OPENAI_DEPLOYMENT: str = ""
    AZURE_OPENAI_API_VERSION: str = "2024-12-01-preview"
    AZURE_EMBEDDING_DEPLOYMENT: str = ""

    # Auth
    JWT_SECRET: str = "change-me-in-production"
    AGENT_SHARED_SECRET: str = "agent-internal-secret"

    # Agent Registry
    AGENT_REGISTRY: str = "{}"

    # Telemetry
    OTEL_ENABLED: bool = False
    OTEL_EXPORTER_OTLP_ENDPOINT: str = "http://localhost:18889"

    # General
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )

settings = Settings()
```

---

## 14. Streaming & Frontend Protocol

### 14.1 AG-UI Protocol (CopilotKit)

The orchestrator exposes `POST /` for the CopilotKit `HttpAgent`. Events:

```
RUN_STARTED         → {runId, threadId}
TEXT_MESSAGE_START   → {messageId}
TEXT_MESSAGE_CONTENT → {text: "chunk..."}
TOOL_CALL_START      → {toolCallId, toolName}     # Agent trace event
TOOL_CALL_END        → {toolCallId, result}
TEXT_MESSAGE_END     → {}
RUN_FINISHED         → {}
```

### 14.2 Agent Trace Events

Custom SSE events for the agent trace sidebar:

```json
{"event": "agent_started", "agent": "order-management", "task": "Processing return for Order #1847"}
{"event": "tool_called", "agent": "order-management", "tool": "check_return_eligibility", "input": {"order_id": "1847"}}
{"event": "tool_result", "agent": "order-management", "tool": "check_return_eligibility", "result": {"eligible": true}}
{"event": "agent_completed", "agent": "order-management", "duration_ms": 2340}
{"event": "agent_started", "agent": "product-discovery", "task": "Searching for jacket, size L"}
```

Frontend renders these as a real-time tree view alongside the chat response.

---

## 15. Error Handling & Resilience

| Scenario | Handling |
|----------|----------|
| Agent unreachable | Orchestrator retries once, then responds: "I couldn't reach the [agent] service. Let me try to help directly." |
| LLM timeout | 30s timeout with retry. OpenAI API can occasionally have high latency. |
| Tool execution failure | Catch exception, return error message to LLM, let it explain to user gracefully |
| Max steps exceeded | Break loop, return partial response with "I need more information to complete this" |
| Invalid JWT | Return 401 with clear error message |
| Agent access denied | Return 403 with "You don't have access to this agent. Request access in the marketplace." |
| Database connection failure | Health check fails, Docker restarts container |

---

## 16. Seed Data Highlights

The seeder creates a realistic e-commerce dataset:

### Products (50 items)

| Category | Example Products |
|----------|-----------------|
| Electronics | Sony WH-1000XM5 ($299), AirPods Max ($449), Logitech MX Master ($99), Samsung T7 SSD ($89) |
| Clothing | North Face Thermoball Jacket ($179), Patagonia Better Sweater ($139), Nike Air Max ($129) |
| Home | Dyson V15 Detect ($649), Nespresso Vertuo ($199), Philips Hue Starter Kit ($129) |
| Sports | Garmin Forerunner 265 ($349), Hydro Flask 32oz ($44), Manduka PRO Mat ($120) |
| Books | "Designing Data-Intensive Applications" ($45), "Staff Engineer" ($35), "System Design Interview" ($39) |

### Demo Scenarios Pre-Seeded

| Scenario | Seed Data |
|----------|-----------|
| Return eligible | Order #demo-1: Delivered 10 days ago, within 30-day window |
| Return expired | Order #demo-2: Delivered 45 days ago, window closed |
| Low stock alert | Sony WH-1000XM5: 2 units at West warehouse |
| Out of stock | Dyson V15: 0 at all warehouses, restock scheduled April 15 |
| Price drop | Logitech MX Master: was $129 last month, now $99 |
| Active coupons | WELCOME10 (10% off first order), TECHSAVE (15% off Electronics), TEAMGIFT (free wrapping) |
| Fake reviews | 25 reviews with detectable patterns (generic language, 5-star burst, unverified) |

---

## 17. Build Order

### Phase 1: Foundation (Week 1)
- [ ] Project scaffold (agents/, web/, docker/)
- [ ] PostgreSQL schema (`init.sql`)
- [ ] Shared library: config, db, auth (JWT), context vars, **telemetry.py**
- [ ] Docker Compose: db + redis + aspire
- [ ] `scripts/dev.sh` — one-command setup
- [ ] Health check endpoints

### Phase 2: Core Framework (Week 2)
- [ ] Tool registry (`@tool` decorator)
- [ ] Agent executor (MAF `ChatAgent.run()`)
- [ ] Agent app builder (Starlette + A2A routes)
- [ ] LLM client abstraction (OpenAI/Azure OpenAI)

### Phase 3: Orchestrator (Week 3)
- [ ] FastAPI orchestrator with auth endpoints (signup/login)
- [ ] Intent classifier
- [ ] Agent router + A2A client
- [ ] Conversation management (CRUD)
- [ ] Marketplace endpoints (catalog, access requests)

### Phase 4: Specialist Agents (Weeks 4-5)
- [ ] Product Discovery + semantic search tools
- [ ] Order Management + return flow tools
- [ ] Pricing & Promotions + coupon tools
- [ ] Review & Sentiment + analysis tools
- [ ] Inventory & Fulfillment + shipping tools

### Phase 5: Collaboration Flows (Week 6)
- [ ] Multi-intent decomposition
- [ ] Parallel agent calls + response aggregation
- [ ] Implement 5 demo flows
- [ ] Agent trace event streaming

### Phase 6: Seed Data + Frontend (Weeks 7-8)
- [ ] Comprehensive seed script (products, orders, reviews, etc.)
- [ ] Embedding generation script
- [ ] Next.js: Chat + agent trace
- [ ] Next.js: Product catalog + marketplace
- [ ] Next.js: Admin dashboard

### Phase 7: Polish (Week 9)
- [ ] README with screenshots and quick start guide
- [ ] Demo script (pre-configured scenarios)
- [ ] Blog article draft: "Building an E-Commerce Agent Platform with MAF"

---

## Related

- [[AgentBazaar - E-Commerce Multi-Agent Platform]] — Product spec and feature details
- [[Content Hub]] — Blog publishing pipeline
- AI Series Plan — `~/workspace/nitin27may.github.io/ai-series-plan.md`
- WorkGraph.ai — `/Users/nks/workspace/workgraph.ai/` (reference architecture)
