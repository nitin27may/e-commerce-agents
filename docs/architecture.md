# Architecture

E-Commerce Agents is a multi-agent e-commerce platform built on Microsoft Agent Framework (MAF). Six specialized agents collaborate via A2A protocol, orchestrated by a central Customer Support agent that classifies user intent and routes requests to the right specialist.

---

## 1. System Overview

The platform comprises three tiers: a Next.js frontend, a FastAPI orchestrator gateway backed by five specialist agents, and shared infrastructure (PostgreSQL + pgvector, Redis, .NET Aspire Dashboard).

```mermaid
graph TB
    subgraph Client["Client Tier"]
        FE["Next.js 15 Frontend<br/>(React 19 + Tailwind)"]
    end

    subgraph Gateway["Gateway Tier"]
        ORCH["Orchestrator<br/>FastAPI :8080<br/>JWT Auth + Intent Routing"]
    end

    subgraph Agents["Specialist Agent Tier · A2A Protocol"]
        PD["Product Discovery<br/>:8081"]
        OM["Order Management<br/>:8082"]
        PP["Pricing &amp; Promotions<br/>:8083"]
        RS["Review &amp; Sentiment<br/>:8084"]
        IF["Inventory &amp; Fulfillment<br/>:8085"]
    end

    subgraph Infra["Infrastructure"]
        PG[("PostgreSQL 16<br/>+ pgvector")]
        RD[("Redis 7")]
        ASPIRE[".NET Aspire Dashboard<br/>:18888"]
    end

    subgraph External["External Services"]
        LLM["OpenAI / Azure OpenAI<br/>GPT-4.1 + Embeddings"]
    end

    FE -->|"REST + SSE"| ORCH
    ORCH -->|"A2A /message:send"| PD
    ORCH -->|"A2A /message:send"| OM
    ORCH -->|"A2A /message:send"| PP
    ORCH -->|"A2A /message:send"| RS
    ORCH -->|"A2A /message:send"| IF

    PD --> PG
    OM --> PG
    PP --> PG
    RS --> PG
    IF --> PG

    ORCH --> PG
    ORCH --> RD

    PD -->|"Embeddings API"| LLM
    ORCH -->|"ChatClient"| LLM
    PD -->|"ChatClient"| LLM
    OM -->|"ChatClient"| LLM
    PP -->|"ChatClient"| LLM
    RS -->|"ChatClient"| LLM
    IF -->|"ChatClient"| LLM

    ORCH -.->|"OTLP"| ASPIRE
    PD -.->|"OTLP"| ASPIRE
    OM -.->|"OTLP"| ASPIRE
    PP -.->|"OTLP"| ASPIRE
    RS -.->|"OTLP"| ASPIRE
    IF -.->|"OTLP"| ASPIRE

    style Client fill:#6366f1,stroke:#4f46e5,stroke-width:2px,color:#fff
    style FE fill:#818cf8,stroke:#6366f1,color:#fff

    style Gateway fill:#0891b2,stroke:#0e7490,stroke-width:2px,color:#fff
    style ORCH fill:#22d3ee,stroke:#06b6d4,color:#0c4a6e

    style Agents fill:#0d9488,stroke:#0f766e,stroke-width:2px,color:#fff
    style PD fill:#2dd4bf,stroke:#14b8a6,color:#134e4a
    style OM fill:#2dd4bf,stroke:#14b8a6,color:#134e4a
    style PP fill:#2dd4bf,stroke:#14b8a6,color:#134e4a
    style RS fill:#2dd4bf,stroke:#14b8a6,color:#134e4a
    style IF fill:#2dd4bf,stroke:#14b8a6,color:#134e4a

    style Infra fill:#475569,stroke:#334155,stroke-width:2px,color:#fff
    style PG fill:#94a3b8,stroke:#64748b,color:#1e293b
    style RD fill:#94a3b8,stroke:#64748b,color:#1e293b
    style ASPIRE fill:#94a3b8,stroke:#64748b,color:#1e293b

    style External fill:#d97706,stroke:#b45309,stroke-width:2px,color:#fff
    style LLM fill:#fbbf24,stroke:#f59e0b,color:#78350f
```

---

## 2. Agent Communication Pattern

All user requests enter through the Orchestrator. The Orchestrator classifies intent, calls one or more specialist agents via A2A, and synthesizes a unified response.

```mermaid
sequenceDiagram
    actor User
    participant FE as Next.js Frontend
    participant ORCH as Orchestrator<br/>(FastAPI :8080)
    participant LLM as OpenAI / Azure OpenAI
    participant AGENT as Specialist Agent<br/>(A2AAgentHost)
    participant DB as PostgreSQL + pgvector

    User->>FE: Sends chat message
    FE->>ORCH: POST /api/chat<br/>Authorization: Bearer {JWT}

    Note over ORCH: JWT validation<br/>Set ContextVars (email, role)

    ORCH->>LLM: ChatAgent.run() with system prompt<br/>+ ECommerceContextProvider
    Note over LLM: Intent classification<br/>Tool selection

    LLM-->>ORCH: Tool call: call_specialist_agent<br/>(agent_name, message)

    ORCH->>AGENT: POST /message:send<br/>X-Agent-Secret + X-User-Email
    Note over AGENT: Auth middleware validates<br/>shared secret, sets ContextVars

    AGENT->>LLM: ChatAgent.run() with<br/>specialist system prompt
    Note over LLM: Selects domain tools<br/>(search, check_stock, etc.)
    LLM-->>AGENT: Tool calls

    AGENT->>DB: Execute tool queries<br/>(asyncpg parameterized SQL)
    DB-->>AGENT: Query results

    AGENT-->>ORCH: A2A response (JSON)

    Note over ORCH: Orchestrator LLM synthesizes<br/>specialist response into<br/>natural language

    ORCH->>DB: Persist conversation + usage log
    ORCH-->>FE: ChatResponse (JSON)
    FE-->>User: Rendered response
```

---

## 3. Agent Architecture

Every specialist agent follows a consistent four-file structure. The Orchestrator is the only agent that uses FastAPI directly -- all specialists use `A2AAgentHost` from the MAF A2A library.

```mermaid
graph TB
    subgraph AgentHost["Specialist Agent (e.g., product_discovery/)"]
        MAIN["main.py<br/>A2AAgentHost entry point<br/>Lifespan: telemetry + DB pool"]
        AGENTPY["agent.py<br/>create_*_agent() -> ChatAgent<br/>Registers tools + context providers"]
        TOOLS["tools.py<br/>@tool decorated functions<br/>Domain-specific DB queries"]
        PROMPTS["prompts.py<br/>SYSTEM_PROMPT constant<br/>Agent persona + instructions"]
    end

    subgraph SharedLib["shared/ (Cross-Agent Library)"]
        CONFIG["config.py<br/>Pydantic Settings"]
        DBMOD["db.py<br/>asyncpg pool management"]
        AUTH["auth.py<br/>AgentAuthMiddleware"]
        CTX["context.py<br/>ContextVars (email, role)"]
        CTXPROV["context_providers.py<br/>ECommerceContextProvider"]
        FACTORY["agent_factory.py<br/>ChatClient factory (OpenAI/Azure)"]
        TELEM["telemetry.py<br/>OTel setup + instrumentation"]
        SHARED_TOOLS["tools/<br/>Shared tools (inventory,<br/>pricing, user, return, loyalty)"]
    end

    MAIN --> AGENTPY
    MAIN --> DBMOD
    MAIN --> AUTH
    MAIN --> TELEM
    AGENTPY --> TOOLS
    AGENTPY --> PROMPTS
    AGENTPY --> FACTORY
    AGENTPY --> CTXPROV
    AGENTPY --> SHARED_TOOLS
    TOOLS --> DBMOD
    TOOLS --> CTX
    CTXPROV --> CTX
    CTXPROV --> DBMOD
    AUTH --> CTX
    AUTH --> CONFIG
    FACTORY --> CONFIG

    style MAIN fill:#0ea5e9,stroke:#0284c7,color:#fff
    style AGENTPY fill:#0ea5e9,stroke:#0284c7,color:#fff
    style TOOLS fill:#0ea5e9,stroke:#0284c7,color:#fff
    style PROMPTS fill:#0ea5e9,stroke:#0284c7,color:#fff
    style CONFIG fill:#64748b,stroke:#475569,color:#fff
    style DBMOD fill:#0d9488,stroke:#115e59,color:#fff
    style AUTH fill:#ef4444,stroke:#dc2626,color:#fff
    style CTX fill:#64748b,stroke:#475569,color:#fff
    style CTXPROV fill:#64748b,stroke:#475569,color:#fff
    style FACTORY fill:#f59e0b,stroke:#d97706,color:#fff
    style TELEM fill:#64748b,stroke:#475569,color:#fff
    style SHARED_TOOLS fill:#0d9488,stroke:#0f766e,color:#fff
```

### Agent Inventory

| Agent | Port | Module | Key Tools |
|-------|------|--------|-----------|
| **Orchestrator** | 8080 | `orchestrator/` | `call_specialist_agent` (A2A router) |
| **Product Discovery** | 8081 | `product_discovery/` | `search_products`, `semantic_search`, `compare_products`, `find_similar_products`, `get_trending_products` |
| **Order Management** | 8082 | `order_management/` | `get_user_orders`, `get_order_details`, `get_order_tracking`, `cancel_order`, `modify_order`, `check_return_eligibility`, `initiate_return`, `process_refund` |
| **Pricing & Promotions** | 8083 | `pricing_promotions/` | `validate_coupon`, `optimize_cart`, `get_active_deals`, `check_bundle_eligibility`, `get_loyalty_tier`, `calculate_loyalty_discount` |
| **Review & Sentiment** | 8084 | `review_sentiment/` | `get_product_reviews`, `analyze_sentiment`, `get_sentiment_by_topic`, `get_sentiment_trend`, `detect_fake_reviews`, `compare_product_reviews` |
| **Inventory & Fulfillment** | 8085 | `inventory_fulfillment/` | `check_stock`, `get_warehouse_availability`, `get_restock_schedule`, `estimate_shipping`, `compare_carriers`, `calculate_fulfillment_plan`, `place_backorder` |

---

## 4. Orchestrator Pattern

The Orchestrator is the single entry point for all user traffic. It handles authentication, intent classification via LLM, agent routing via A2A, and conversation persistence.

```mermaid
flowchart TD
    REQ["Incoming Request<br/>POST /api/chat"]
    JWT{"JWT Valid?"}
    REJECT["401 Unauthorized"]
    SETCTX["Set ContextVars<br/>(email, role, session_id)"]
    LOAD["Load Conversation History<br/>+ ECommerceContextProvider"]
    CLASSIFY["LLM Intent Classification<br/>via ChatAgent.run()"]

    SINGLE{"Single or<br/>Multi-Intent?"}

    ROUTE_ONE["call_specialist_agent<br/>(agent_name, message)"]
    ROUTE_MULTI["Sequential A2A Calls<br/>to Multiple Specialists"]

    A2A_CALL["POST /message:send<br/>X-Agent-Secret + X-User-Email<br/>to Specialist Agent"]

    TIMEOUT{"Response<br/>OK?"}
    ERROR["Error Handling<br/>Retry or Fallback Message"]
    RESPONSE["Specialist Response"]

    SYNTH["LLM Synthesizes<br/>Specialist Responses<br/>into Natural Language"]

    PERSIST["Persist to DB<br/>conversations + messages +<br/>usage_logs + execution_steps"]
    RETURN["Return ChatResponse<br/>(response, conversation_id,<br/>agents_involved)"]

    REQ --> JWT
    JWT -->|No| REJECT
    JWT -->|Yes| SETCTX
    SETCTX --> LOAD
    LOAD --> CLASSIFY
    CLASSIFY --> SINGLE

    SINGLE -->|Single| ROUTE_ONE
    SINGLE -->|Multi| ROUTE_MULTI
    ROUTE_ONE --> A2A_CALL
    ROUTE_MULTI --> A2A_CALL

    A2A_CALL --> TIMEOUT
    TIMEOUT -->|Error / Timeout| ERROR
    TIMEOUT -->|OK| RESPONSE

    ERROR --> SYNTH
    RESPONSE --> SYNTH
    SYNTH --> PERSIST
    PERSIST --> RETURN

    style REQ fill:#0ea5e9,stroke:#0284c7,color:#fff
    style JWT fill:#ef4444,stroke:#dc2626,color:#fff
    style REJECT fill:#ef4444,stroke:#dc2626,color:#fff
    style SETCTX fill:#64748b,stroke:#475569,color:#fff
    style LOAD fill:#0ea5e9,stroke:#0284c7,color:#fff
    style CLASSIFY fill:#f59e0b,stroke:#d97706,color:#fff
    style SINGLE fill:#f59e0b,stroke:#d97706,color:#fff
    style ROUTE_ONE fill:#0ea5e9,stroke:#0284c7,color:#fff
    style ROUTE_MULTI fill:#0ea5e9,stroke:#0284c7,color:#fff
    style A2A_CALL fill:#0ea5e9,stroke:#0284c7,color:#fff
    style TIMEOUT fill:#ef4444,stroke:#dc2626,color:#fff
    style ERROR fill:#ef4444,stroke:#dc2626,color:#fff
    style RESPONSE fill:#10b981,stroke:#059669,color:#fff
    style SYNTH fill:#f59e0b,stroke:#d97706,color:#fff
    style PERSIST fill:#0d9488,stroke:#115e59,color:#fff
    style RETURN fill:#10b981,stroke:#059669,color:#fff
```

---

## 5. Auth Flow

E-Commerce Agents uses self-contained JWT authentication (PyJWT + bcrypt). There is no external identity provider. Inter-agent calls use a shared secret instead of JWT.

### User Authentication

```mermaid
sequenceDiagram
    actor User
    participant FE as Next.js Frontend
    participant ORCH as Orchestrator API
    participant DB as PostgreSQL

    Note over User,DB: Signup Flow
    User->>FE: Enter email, password, name
    FE->>ORCH: POST /api/auth/signup
    ORCH->>ORCH: Hash password (bcrypt)
    ORCH->>DB: INSERT INTO users (email, password_hash, name, role='customer')
    DB-->>ORCH: User created
    ORCH->>ORCH: Create access token (JWT HS256, 60 min)<br/>Create refresh token (JWT HS256, 7 days)
    ORCH-->>FE: { access_token, refresh_token, user }
    FE->>FE: Store tokens in localStorage

    Note over User,DB: Login Flow
    User->>FE: Enter email, password
    FE->>ORCH: POST /api/auth/login
    ORCH->>DB: SELECT password_hash FROM users WHERE email = $1
    DB-->>ORCH: password_hash
    ORCH->>ORCH: bcrypt.checkpw(password, hash)
    alt Password Invalid
        ORCH-->>FE: 401 Invalid credentials
    else Password Valid
        ORCH->>ORCH: Create access + refresh tokens
        ORCH-->>FE: { access_token, refresh_token, user }
    end

    Note over User,DB: Authenticated Request
    FE->>ORCH: POST /api/chat<br/>Authorization: Bearer {access_token}
    ORCH->>ORCH: jwt.decode(token, JWT_SECRET, HS256)
    ORCH->>ORCH: Validate type='access', not expired
    ORCH->>ORCH: Set ContextVars:<br/>current_user_email = sub<br/>current_user_role = role
    ORCH-->>FE: Proceed to handler

    Note over User,DB: Token Refresh
    FE->>ORCH: POST /api/auth/refresh<br/>{ refresh_token }
    ORCH->>ORCH: jwt.decode(refresh_token)<br/>Validate type='refresh'
    ORCH->>DB: SELECT * FROM users WHERE email = sub
    ORCH->>ORCH: Create new access + refresh tokens
    ORCH-->>FE: { access_token, refresh_token, user }
```

### Inter-Agent Authentication

```mermaid
sequenceDiagram
    participant ORCH as Orchestrator
    participant AGENT as Specialist Agent
    participant MW as AgentAuthMiddleware

    ORCH->>AGENT: POST /message:send<br/>X-Agent-Secret: {AGENT_SHARED_SECRET}<br/>X-User-Email: alice.johnson@gmail.com<br/>X-User-Role: customer

    AGENT->>MW: Request intercepted

    alt Secret matches AGENT_SHARED_SECRET
        MW->>MW: Set ContextVars:<br/>email = X-User-Email<br/>role = X-User-Role
        MW-->>AGENT: Proceed to handler
        Note over AGENT: Tools read user identity<br/>from ContextVars
    else Secret invalid
        MW-->>ORCH: 401 Invalid agent secret
    end
```

### RBAC Roles

| Role | Access Level | Description |
|------|-------------|-------------|
| `customer` | Default | Standard shopping, orders, reviews |
| `power_user` | Extended | Access to advanced agent features via marketplace |
| `seller` | Seller tools | Draft review responses, view sentiment reports |
| `admin` | Full | Approve access requests, manage agent catalog, all operations |

---

## 6. Data Flow

End-to-end data flow showing how a user request traverses the system, from initial HTTP request through agent processing to database persistence.

```mermaid
flowchart LR
    subgraph Input["Request"]
        USER_MSG["User Message<br/>'Find me wireless headphones<br/>under $200 with good reviews'"]
    end

    subgraph Auth["Authentication"]
        JWT_CHECK["JWT Decode<br/>HS256 Validation"]
        CTX_SET["ContextVars Set<br/>email + role"]
    end

    subgraph Orchestration["Orchestration Layer"]
        CTX_LOAD["Context Loaded<br/>User profile + recent orders<br/>(ECommerceContextProvider)"]
        LLM_ROUTE["LLM Classifies Intent<br/>-> product-discovery<br/>-> review-sentiment"]
    end

    subgraph A2A_1["Product Discovery Agent"]
        PD_LLM["ChatAgent selects tools"]
        PD_SEARCH["semantic_search()<br/>Embed query -> pgvector"]
        PD_FILTER["search_products()<br/>category + price filter"]
        PD_STOCK["check_stock()<br/>Cross-check inventory"]
    end

    subgraph A2A_2["Review & Sentiment Agent"]
        RS_LLM["ChatAgent selects tools"]
        RS_REVIEWS["get_product_reviews()"]
        RS_SENT["analyze_sentiment()<br/>Rating breakdown + themes"]
    end

    subgraph Synthesis["Response Synthesis"]
        COMBINE["Orchestrator LLM combines:<br/>- Product results<br/>- Stock status<br/>- Review summaries<br/>into natural language"]
    end

    subgraph Persist["Persistence"]
        CONV["conversations table"]
        MSG["messages table<br/>(user + assistant)"]
        USAGE["usage_logs table<br/>+ execution_steps"]
    end

    subgraph Output["Response"]
        RESP["ChatResponse<br/>Products + reviews + stock<br/>agents_involved: [product-discovery,<br/>review-sentiment]"]
    end

    USER_MSG --> JWT_CHECK
    JWT_CHECK --> CTX_SET
    CTX_SET --> CTX_LOAD
    CTX_LOAD --> LLM_ROUTE

    LLM_ROUTE -->|"A2A call 1"| PD_LLM
    PD_LLM --> PD_SEARCH
    PD_LLM --> PD_FILTER
    PD_LLM --> PD_STOCK

    LLM_ROUTE -->|"A2A call 2"| RS_LLM
    RS_LLM --> RS_REVIEWS
    RS_LLM --> RS_SENT

    PD_SEARCH --> COMBINE
    PD_FILTER --> COMBINE
    PD_STOCK --> COMBINE
    RS_REVIEWS --> COMBINE
    RS_SENT --> COMBINE

    COMBINE --> CONV
    COMBINE --> MSG
    COMBINE --> USAGE
    COMBINE --> RESP

    style USER_MSG fill:#0ea5e9,stroke:#0284c7,color:#fff
    style JWT_CHECK fill:#ef4444,stroke:#dc2626,color:#fff
    style CTX_SET fill:#64748b,stroke:#475569,color:#fff
    style CTX_LOAD fill:#0ea5e9,stroke:#0284c7,color:#fff
    style LLM_ROUTE fill:#f59e0b,stroke:#d97706,color:#fff
    style PD_LLM fill:#f59e0b,stroke:#d97706,color:#fff
    style PD_SEARCH fill:#0ea5e9,stroke:#0284c7,color:#fff
    style PD_FILTER fill:#0ea5e9,stroke:#0284c7,color:#fff
    style PD_STOCK fill:#0ea5e9,stroke:#0284c7,color:#fff
    style RS_LLM fill:#f59e0b,stroke:#d97706,color:#fff
    style RS_REVIEWS fill:#0ea5e9,stroke:#0284c7,color:#fff
    style RS_SENT fill:#0ea5e9,stroke:#0284c7,color:#fff
    style COMBINE fill:#f59e0b,stroke:#d97706,color:#fff
    style CONV fill:#0d9488,stroke:#115e59,color:#fff
    style MSG fill:#0d9488,stroke:#115e59,color:#fff
    style USAGE fill:#0d9488,stroke:#115e59,color:#fff
    style RESP fill:#10b981,stroke:#059669,color:#fff
```

---

## 7. Technology Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Agent Framework** | Microsoft Agent Framework (MAF) Python SDK | First-class `ChatAgent` abstraction with `@tool` decorators, `ContextProvider`, and built-in A2A support. Avoids hand-rolling function-calling loops. |
| **Inter-Agent Protocol** | A2A via `agent-framework-a2a` | Standard protocol for agent-to-agent communication. Each specialist exposes `/message:send`. Decoupled from transport -- could swap HTTP for gRPC later. |
| **LLM Provider** | OpenAI / Azure OpenAI (configurable) | Single `ChatClient` interface via MAF. Swap with `LLM_PROVIDER` env var. Azure for production (managed identity, RBAC); OpenAI for local dev. |
| **Database** | PostgreSQL 16 + pgvector | Single database for relational data and vector embeddings. `text-embedding-3-small` (1536 dims) for semantic product search. IVFFlat index for fast cosine similarity. |
| **Web Framework** | FastAPI (orchestrator) + Starlette (specialists) | FastAPI for the orchestrator because it needs REST endpoints (auth, chat, marketplace, admin). Specialists use the lighter `A2AAgentHost` which wraps Starlette. |
| **Auth** | Self-contained JWT (HS256) + bcrypt | No external IdP dependency for the demo. Access tokens (60 min) + refresh tokens (7 days). Inter-agent auth via shared secret header. |
| **User Context** | Python ContextVars | Request-scoped state (email, role) set by auth middleware, read by any `@tool` function. No need to pass user info through function parameters. |
| **DB Access** | asyncpg (raw SQL) | Maximum control over queries. No ORM overhead. Parameterized `$1, $2` syntax prevents SQL injection. Connection pool (5-20) per agent. |
| **Telemetry** | OpenTelemetry -> .NET Aspire Dashboard | Auto-instrumented: httpx (LLM + A2A calls), asyncpg (DB queries), FastAPI/Starlette (HTTP). Custom spans for A2A calls and tool execution. All correlate via trace_id. |
| **Cache** | Redis 7 | Session data and conversation state caching. Alpine image for minimal footprint. |
| **Frontend** | Next.js 15 + React 19 + Tailwind + shadcn/ui | Server Components by default, `pnpm` for package management. Minimal client-side JS. |
| **Containerization** | Docker Compose + multi-target Dockerfile | All 6 agents share one Dockerfile with `ARG AGENT_NAME`. Each agent is a separate service with its own port. Single `docker compose up --build` to start everything. |
| **Package Management** | `uv` (Python) + `pnpm` (Node) | `uv` for fast dependency resolution and virtual environment management. `pnpm` for disk-efficient node_modules. |
