# CLAUDE.md — E-Commerce Agents

## What This Is

E-Commerce Agents is an e-commerce multi-agent platform built with **Microsoft Agent Framework (MAF)** Python SDK. 6 specialized agents collaborate via **A2A protocol** to handle product discovery, orders, pricing, reviews, inventory, and customer support. Includes a marketplace layer with agent catalog, access requests, and admin approval.

Companion demo repo for the AI article series on nitinksingh.com.

## Tech Stack

- **Language**: Python 3.12
- **Agent Framework**: `agent-framework` v1.0 (Microsoft Agent Framework)
- **Agent Communication**: A2A Protocol via `agent-framework-a2a`
- **LLM Providers**: OpenAI (`agent-framework-openai`) and Azure OpenAI — configurable via `LLM_PROVIDER` env var
- **Web Framework**: FastAPI (orchestrator) + Starlette (agents via `A2AAgentHost`)
- **Database**: PostgreSQL 16 + pgvector (embeddings, all application data)
- **Cache**: Redis 7
- **Frontend**: Next.js 15, React 19, Tailwind CSS, shadcn/ui
- **Infra**: Docker Compose, multi-target Dockerfile
- **Auth**: Self-contained JWT (PyJWT + bcrypt), no external IdP
- **Telemetry**: OpenTelemetry → .NET Aspire Dashboard
- **Package Manager**: `uv` (Python), `pnpm` (Node)

## Project Structure

```
e-commerce-agents/
├── CLAUDE.md
├── PLAN.md                          # Implementation plan and TODO tracker
├── docker-compose.yml
├── .env.example
├── agents/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── shared/                      # Shared library across all agents
│   │   ├── telemetry.py            # OTel setup + auto-instrumentation
│   │   ├── config.py               # Pydantic Settings
│   │   ├── db.py                   # asyncpg pool
│   │   ├── auth.py                 # JWT middleware
│   │   ├── jwt_utils.py            # Token create/verify
│   │   ├── context.py              # ContextVars
│   │   ├── context_providers.py    # MAF ContextProvider
│   │   ├── agent_factory.py        # LLM client factory
│   │   └── usage_db.py             # Usage logging
│   ├── shared/tools/               # Shared tool functions
│   ├── orchestrator/               # Customer Support (FastAPI, port 8080)
│   ├── product_discovery/          # Product Discovery (port 8081)
│   ├── order_management/           # Order Management (port 8082)
│   ├── pricing_promotions/         # Pricing & Promotions (port 8083)
│   ├── review_sentiment/           # Review & Sentiment (port 8084)
│   └── inventory_fulfillment/      # Inventory & Fulfillment (port 8085)
├── docker/
│   └── postgres/
│       └── init.sql                # Schema + pgvector extension
├── scripts/
│   ├── dev.sh                      # One-command dev environment setup
│   ├── seed.py                     # Database seeder
│   └── generate_embeddings.py      # Product embedding generation
└── web/                            # Next.js frontend
    ├── Dockerfile
    ├── package.json
    └── src/
```

## Key Commands

```bash
# Start everything from scratch (one command)
./scripts/dev.sh

# Clean rebuild (nuke volumes, rebuild images)
./scripts/dev.sh --clean

# Start infrastructure only (db, redis, aspire)
./scripts/dev.sh --infra-only

# Re-run seeder against existing DB
./scripts/dev.sh --seed-only

# Start everything via docker compose directly
docker compose up --build

# Run a single agent locally (for dev)
cd agents && uv run uvicorn product_discovery.main:app --port 8081 --reload

# Run frontend locally
cd web && pnpm dev

# Generate embeddings
cd agents && uv run python -m scripts.generate_embeddings

# Open Aspire Dashboard (telemetry visualization)
open http://localhost:18888
```

## LLM Provider Configuration

Controlled via `LLM_PROVIDER` environment variable:

```bash
# OpenAI (default for local dev)
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
LLM_MODEL=gpt-4.1

# Azure OpenAI
LLM_PROVIDER=azure
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_KEY=...
AZURE_OPENAI_DEPLOYMENT=gpt-4.1
AZURE_OPENAI_API_VERSION=2024-12-01-preview

# Embedding model (used for product semantic search)
EMBEDDING_MODEL=text-embedding-3-small          # OpenAI
# or
AZURE_EMBEDDING_DEPLOYMENT=text-embedding-3-small  # Azure OpenAI
```

Both providers use the same `agent-framework` ChatClient interface — swap with one env var.

## Architecture Principles

- **Each agent is an independent microservice** with its own Dockerfile target, port, and A2A endpoint
- **Orchestrator is the front door** — all user requests go through it, it routes to specialist agents via A2A
- **Tools access the database directly** via module-level `get_pool()` — no context dict passing
- **User identity flows via ContextVars** — set by auth middleware, read by tools
- **Inter-agent auth uses shared secret** — orchestrator passes `X-User-Email` header when calling specialists
- **All agents share the same Dockerfile** — multi-target via `ARG AGENT_NAME`

## Coding Conventions

- MAF `@tool` decorator with `Annotated` type hints (not Pydantic input models)
- `async` everywhere — all tools, all DB queries, all HTTP calls
- `asyncpg` for PostgreSQL (connection pool, not ORM)
- Pydantic Settings for configuration (`shared/config.py`)
- ContextVars for request-scoped state (`shared/context.py`)
- Type hints on all functions
- f-strings for string formatting
- Guard clauses for early returns in tools

## Agent Patterns

Each specialist agent follows:
```
agent.py    → create_*_agent() returning ChatAgent
tools.py    → @tool functions for this agent's domain
prompts.py  → SYSTEM_PROMPT constant
main.py     → A2AAgentHost entry point with telemetry in lifespan
```

The orchestrator uses `HandoffOrchestration` from MAF to route between specialists.

## Telemetry

- **OpenTelemetry is integrated from Phase 1** — not deferred. Every agent gets observability automatically.
- **Visualization**: .NET Aspire Dashboard at `http://localhost:18888`
- **Setup pattern**: Every agent's `main.py` calls `setup_telemetry(service_name)` + `instrument_starlette(app)` in its lifespan
- **Auto-instrumented** (zero code): httpx (LLM + A2A calls), asyncpg (DB queries), FastAPI/Starlette (HTTP), Python logging (trace_id correlation)
- **Custom spans**: Only where MAF doesn't auto-instrument (e.g., `agent.a2a_call` in orchestrator)
- **Cross-agent traces**: httpx injects `traceparent` header → Starlette reads it → single trace spans orchestrator + specialist + LLM + DB
- **OTEL_SERVICE_NAME per agent**: e.g., `ecommerce.product-discovery` — filterable in Aspire

## Database

- PostgreSQL 16 with pgvector extension
- Schema in `docker/postgres/init.sql`
- All queries use parameterized `$1, $2` syntax (asyncpg)
- All user-facing queries filter by `user_email` or `user_id`
- Embeddings stored as `vector(1536)` for text-embedding-3-small

## Do Not

- Use Ollama or local models — this demo targets OpenAI / Azure OpenAI
- Create custom tool registries — use MAF's built-in `@tool`
- Write raw OpenAI function-calling loops — use `ChatAgent.run()` / `run_stream()`
- Use `pip` or `poetry` — use `uv` for Python
- Use `npm` or `yarn` — use `pnpm` for Node
- Skip type hints on any function
- Use `requests` — use `httpx` for async HTTP
