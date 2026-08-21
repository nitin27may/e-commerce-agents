# E-Commerce Agents

[![Tests](https://github.com/nitin27may/e-commerce-agents/actions/workflows/tests.yml/badge.svg)](https://github.com/nitin27may/e-commerce-agents/actions/workflows/tests.yml)
[![Build Images](https://github.com/nitin27may/e-commerce-agents/actions/workflows/build-images.yml/badge.svg)](https://github.com/nitin27may/e-commerce-agents/actions/workflows/build-images.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-3776AB.svg)](https://python.org)
[![.NET 10](https://img.shields.io/badge/.NET-10-512BD4.svg)](https://dotnet.microsoft.com)
[![Next.js 16](https://img.shields.io/badge/Next.js-16-000000.svg)](https://nextjs.org)
[![MAF v1](https://img.shields.io/badge/Microsoft%20Agent%20Framework-v1-5E5DF0.svg)](https://github.com/microsoft/agent-framework)
[![PostgreSQL + pgvector](https://img.shields.io/badge/PostgreSQL-pgvector-336791.svg)](https://github.com/pgvector/pgvector)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED.svg)](https://docs.docker.com/compose/)

A **multi-agent e-commerce platform** built with [Microsoft Agent Framework](https://github.com/microsoft/agent-framework) (MAF). Six specialized AI agents collaborate over the **A2A protocol** to handle product discovery, orders, pricing, reviews, inventory and support — with **two complete, working backends, Python and .NET / C#**, behind one Next.js frontend.

### Run it

```bash
git clone https://github.com/nitin27may/e-commerce-agents.git
cd e-commerce-agents
cp .env.example .env          # add your OPENAI_API_KEY (or Azure OpenAI credentials)
./scripts/dev.sh              # builds, seeds, and starts everything
```

Then open **http://localhost:3000** and sign in as `alice.johnson@gmail.com` / `customer123`.
Docker is the only requirement — no Python, .NET or Node needed.

**On Windows**, `scripts/dev.sh` is a bash script and won't run in PowerShell. Use Compose directly:

```powershell
docker compose up -d db redis aspire
docker compose --profile seed run --rm seeder
docker compose --profile agents --profile frontend up -d --build
```

Full detail — the .NET stack, WSL2 notes, running with no API key, and what to do when something
breaks: **[Quick Start](#quick-start)** below, or the
**[Quick Start page](https://nitinksingh.com/e-commerce-agents/getting-started/quick-start.html)**
on the docs site.

**Generative UI, not raw JSON.** The chat surface never dumps a tool result as text or a code block. Every agent response is inspected by shape and rendered as the right interactive component: a single detailed result becomes a card, a list becomes a table, a distribution or trend becomes a chart, a status becomes a tone-coded badge — see it live in the [Screens](#screens) gallery below (review sentiment: rating distribution + 6-month trend, rendered from the same data an LLM would otherwise only describe in prose).

**Pick your stack:** [`agents/python/`](./agents/python/) or [`agents/dotnet/`](./agents/dotnet/) — same schema, same prompts, same frontend for either (toggle with `NEXT_PUBLIC_BACKEND_STACK`). Parity is enforced by a dual-backend test gate, and [`docs/parity-matrix.md`](./docs/parity-matrix.md) lists the remaining differences row by row.

**Full documentation:** **[nitinksingh.com/e-commerce-agents](https://nitinksingh.com/e-commerce-agents/)** — the concepts library, all 34 tutorial chapters, and the architecture reference, with every diagram rendered.

Companion demo repo for the AI article series on [nitinksingh.com](https://nitinksingh.com):
**[MAF v1: Putting It All Together](https://nitinksingh.com/posts/maf-v1-21-putting-it-all-together/)**
(the current Python + .NET series) and
**[Building a Multi-Agent E-Commerce Platform — The Complete Guide](https://nitinksingh.com/posts/building-a-multi-agent-e-commerce-platform-the-complete-guide/)**
(the original Python-only walkthrough). The articles are optional background — this repo and its
[documentation site](https://nitinksingh.com/e-commerce-agents/) are the canonical, always-current source.

![AI shopping assistant with product cards](docs/images/shop-ai-assistant.png)

---

## Where to start

| I want to... | Go here |
|---|---|
| **Just run it** | [Quick Start](#quick-start) — Docker, one command |
| Run the .NET backend instead | [Quick Start → .NET](#run-the-net-backend) |
| Run it on **Windows** | [Quick Start → Windows](#run-the-python-backend) — Compose directly, or WSL2 |
| Run it without an API key | [Quick Start → free and local options](#run-without-a-paid-api-key) |
| Read the documentation | **[nitinksingh.com/e-commerce-agents](https://nitinksingh.com/e-commerce-agents/)** — rendered and searchable |
| Learn what an agent even is — new to AI/agents | [Concepts](https://nitinksingh.com/e-commerce-agents/concepts/) — start at page 01 |
| Understand how the agents work / add a new one | [Architecture](docs/architecture.md) · [Adding an Agent](docs/adding-an-agent.md) |
| Use the MCP server | [MCP Integration](docs/mcp-integration.md) |
| Follow the step-by-step tutorial series | [tutorials/README.md](./tutorials/README.md) |
| See the generative UI in action (cards, tables, charts — never raw JSON) | [Screens](#screens) below |

---

## Quick Start

Both backends share the same setup, the same Postgres schema, and the same Next.js frontend —
only the compose file (and port-8080 orchestrator) differs. Pick one.

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose
- An [OpenAI API key](https://platform.openai.com/api-keys) (or Azure OpenAI credentials)

### Setup (shared by both stacks)

```bash
# 1. Clone the repo
git clone https://github.com/nitin27may/e-commerce-agents.git
cd e-commerce-agents

# 2. Configure environment
cp .env.example .env
# Edit .env — add your OPENAI_API_KEY (or Azure OpenAI credentials)
```

### Run the Python backend

**macOS / Linux:**

```bash
# Option A — helper script (builds, seeds, and starts everything):
./scripts/dev.sh

# Option B — plain Docker Compose (equivalent, no script):
docker compose --profile seed --profile agents --profile frontend up --build
```

**Windows:** `scripts/dev.sh` is a bash script and will not run in PowerShell or `cmd`. Either use
Docker Compose directly — the same three steps the script performs — or run the script under
[WSL2](https://learn.microsoft.com/windows/wsl/install) or Git Bash:

```powershell
docker compose up -d db redis aspire
docker compose --profile seed run --rm seeder
docker compose --profile agents --profile frontend up -d --build
```

No waiting between those: the seeder declares `depends_on: db: {condition: service_healthy}`, so
Compose blocks it until Postgres is ready. Under WSL2, keep the clone inside the Linux filesystem
(`~/`, not `/mnt/c/`) — bind-mounting across the Windows boundary is the usual cause of a very slow
container start.

The single-command form works on every platform too:

```bash
docker compose --profile seed --profile agents --profile frontend up --build
```

### Run without a paid API key

Nothing above requires an OpenAI subscription. Any OpenAI-compatible endpoint works through the
same code path — set `LLM_BASE_URL` in `.env` and leave `LLM_PROVIDER=openai`:

```dotenv
# GitHub Models — free with a GitHub PAT
LLM_PROVIDER=openai
LLM_BASE_URL=https://models.inference.ai.azure.com
OPENAI_API_KEY=<a GitHub PAT with the models:read scope>
LLM_MODEL=gpt-4o

# Ollama — fully local, no account, no key
LLM_PROVIDER=openai
LLM_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama          # any non-empty string — Ollama doesn't check it
LLM_MODEL=llama3.1:8b          # must be a tool-calling-capable model
```

**Gotcha worth knowing before you pick a local model:** every specialist here depends on
tool-calling. Many small or heavily-quantized models advertise an OpenAI-compatible chat API but
have unreliable function-calling, and the failure is quiet — the agent simply stops calling tools
and starts inventing answers instead of erroring. Llama 3.1+, Qwen2.5 and tool-tagged Mistral
builds are known to work. See [Setup](./tutorials/00-setup/) for the full guidance.

### Run the .NET backend

```bash
# Option A — helper script:
./scripts/dev.sh --dotnet

# Option B — plain Docker Compose (equivalent, no script):
docker compose -f docker-compose.dotnet.yml \
  --profile seed --profile agents --profile mcp --profile frontend up --build
```

> Both compose files gate agents/frontend/seeder behind profiles — infra (`db`, `redis`, `aspire`)
> starts unconditionally, but a bare `docker compose up` (no `--profile` flags) only brings up
> infra. Use the commands above to get the full app.

Open in your browser (either stack):
- **Frontend**: http://localhost:3000
- **Aspire Dashboard** (telemetry): http://localhost:18888

### Other Commands

```bash
./scripts/dev.sh --clean               # Nuke volumes, rebuild from scratch
./scripts/dev.sh --seed-only           # Re-run database seeder only
./scripts/dev.sh --infra-only          # Start db + redis + aspire only
./scripts/dev.sh --clean --dotnet      # Same flags work with --dotnet too
```

---

## Table of Contents

The [documentation site](https://nitinksingh.com/e-commerce-agents/) has a full searchable nav
across the concepts library, the tutorial series and the architecture reference. What follows
here is the repository tour.

- [Project Status](#project-status) · [Learning Path](#learning-path--maf-v1-python-and-net) · [Architecture](#architecture) · [Screens](#screens)
- [Test Users](#test-users) · [Agent Catalog](#agent-catalog) · [Demo Scenarios](#demo-scenarios) · [Tech Stack](#tech-stack)
- [Project Structure](#project-structure) · [Configuration](#configuration) · [Documentation](#documentation) · [Port Map](#port-map)
- [Roadmap](#roadmap) · [Contributing](#contributing) · [License](#license)

---

## Project Status

**This is v1, and both backends are live.** Each runs end-to-end: an orchestrator plus five specialist agents, auth, telemetry, and a full Next.js frontend that either backend can serve.

The frontend is a **public, agentic e-commerce storefront**: anyone can browse the catalog, search, and use the AI shopping assistant (`/shop`) without an account — product discovery is served anonymously — while account flows (cart checkout, orders, tracking, returns) require sign-in. A built-in **agent-activity timeline** surfaces the multi-agent routing (orchestrator → specialist → tool) live in chat, backed by OpenTelemetry → .NET Aspire. Light/dark theming throughout.

The **.NET / C# backend** at [`agents/dotnet/`](./agents/dotnet/) is a real implementation, not a demonstration slice: it serves the same frontend, the same database and the same prompts as [`agents/python/`](./agents/python/). Parity is enforced rather than asserted — `web/e2e/orchestration-parity.spec.ts` drives one frontend against both backends and asserts *presence* of each capability, because the earlier suite went green against a .NET stack that was missing four whole features. Remaining differences are listed in [`docs/parity-matrix.md`](./docs/parity-matrix.md).

---

## Learning Path — *MAF v1: Python and .NET*

A new step-by-step tutorial series walks through **every Microsoft Agent Framework concept** — agents, tools, memory, middleware, workflow primitives, all five orchestration patterns, HITL, checkpoints, declarative workflows, visualization — with runnable examples in **both Python and .NET**. This repository is the capstone.

**Start here:** [`tutorials/README.md`](./tutorials/README.md)

**34 chapters**, browsable on the [documentation site](https://nitinksingh.com/e-commerce-agents/tutorials/).

| Tier | Chapters | Topics |
|------|----------|--------|
| 1 · Core Agent | [01–04](./tutorials/) | First agent · tools · streaming · sessions |
| 2 · Agent Internals | 05–08 | Context providers · middleware · OpenTelemetry · MCP |
| 3 · Workflow Foundations | 09–11 | Executors · edges · events · builder · agents in workflows |
| 4 · Orchestrations | 12–16 | Sequential · Concurrent · Handoff · Group Chat · Magentic |
| 5 · Advanced | 17–20, 20b | HITL · checkpoints · declarative YAML · visualization · DevUI |
| Capstone | 21 | Guided tour of this repo |
| Bonus pattern | 22 | Round-table group-chat debate |
| 6 · Missing Concepts | 23–27 | A2A protocol · RAG/grounding · guardrails · evals · agent-as-tool |
| 7 · Patterns Without Production Wiring | 28–31 | Reflection · planner-executor · subworkflows · saga/compensation |
| — | 32 | Cost control and budgets |

Every chapter ships a runnable `python/` example with its own tests under `python/tests/`. Chapters **00–21 and 20b also ship `dotnet/`**; chapters 22–32 are Python-only so far, tracked as [#20](https://github.com/nitin27may/e-commerce-agents/issues/20). Each chapter's `README.md` is the canonical teaching artifact — concept, diagram, runnable example, a `file:line` pointer into the capstone, and gotchas — enforced by `scripts/check_tutorial_readmes.py` in CI. (`tutorials/_template/PLAN.md` is a template for authoring new chapters; individual chapters don't ship their own `PLAN.md`.) Companion posts cross-post to [nitinksingh.com](https://nitinksingh.com) when published; a chapter with no post is still complete.

---

## Architecture

![System Architecture](docs/architecture.png)

<details>
<summary>View as Mermaid diagram</summary>

```mermaid
graph TB
    subgraph Client["Browser / Client"]
        FE["Next.js 16<br/>React 19 + Tailwind CSS"]
    end

    subgraph Orchestrator["Orchestrator Agent :8080"]
        FA["FastAPI + MAF"]
        AUTH["JWT Auth + RBAC"]
        ROUTER["Intent Router"]
        CONV["Conversation Mgmt"]
        MKT["Marketplace API"]
    end

    subgraph Specialists["Specialist Agents · A2A Protocol"]
        PD["Product Discovery<br/>:8081"]
        OM["Order Management<br/>:8082"]
        PP["Pricing &amp; Promotions<br/>:8083"]
        RS["Review &amp; Sentiment<br/>:8084"]
        IF["Inventory &amp; Fulfillment<br/>:8085"]
    end

    subgraph Infrastructure["Shared Infrastructure"]
        PG[("PostgreSQL 16<br/>+ pgvector")]
        RD[("Redis 7")]
        ASP["Aspire Dashboard<br/>:18888"]
    end

    subgraph LLM["LLM Provider"]
        OAI["OpenAI API<br/>gpt-4.1"]
        AZ["Azure OpenAI<br/>(configurable)"]
    end

    FE -->|"HTTP/JSON"| FA
    FA --> AUTH
    AUTH --> ROUTER
    ROUTER -->|"A2A"| PD
    ROUTER -->|"A2A"| OM
    ROUTER -->|"A2A"| PP
    ROUTER -->|"A2A"| RS
    ROUTER -->|"A2A"| IF

    PD --> PG
    OM --> PG
    PP --> PG
    RS --> PG
    IF --> PG

    PD -->|"Embeddings"| OAI
    PD -.->|"or"| AZ
    Orchestrator --> RD
    Specialists -->|"OTLP"| ASP

    Orchestrator -->|"OTLP"| ASP
    Specialists --> OAI
    Specialists -.-> AZ

    style Client fill:#6366f1,stroke:#4f46e5,stroke-width:2px,color:#fff
    style FE fill:#818cf8,stroke:#6366f1,color:#fff

    style Orchestrator fill:#0891b2,stroke:#0e7490,stroke-width:2px,color:#fff
    style FA fill:#22d3ee,stroke:#06b6d4,color:#0c4a6e
    style AUTH fill:#fca5a5,stroke:#f87171,color:#7f1d1d
    style ROUTER fill:#67e8f9,stroke:#22d3ee,color:#0c4a6e
    style CONV fill:#67e8f9,stroke:#22d3ee,color:#0c4a6e
    style MKT fill:#67e8f9,stroke:#22d3ee,color:#0c4a6e

    style Specialists fill:#0d9488,stroke:#0f766e,stroke-width:2px,color:#fff
    style PD fill:#2dd4bf,stroke:#14b8a6,color:#134e4a
    style OM fill:#2dd4bf,stroke:#14b8a6,color:#134e4a
    style PP fill:#2dd4bf,stroke:#14b8a6,color:#134e4a
    style RS fill:#2dd4bf,stroke:#14b8a6,color:#134e4a
    style IF fill:#2dd4bf,stroke:#14b8a6,color:#134e4a

    style Infrastructure fill:#475569,stroke:#334155,stroke-width:2px,color:#fff
    style PG fill:#94a3b8,stroke:#64748b,color:#1e293b
    style RD fill:#94a3b8,stroke:#64748b,color:#1e293b
    style ASP fill:#94a3b8,stroke:#64748b,color:#1e293b

    style LLM fill:#d97706,stroke:#b45309,stroke-width:2px,color:#fff
    style OAI fill:#fbbf24,stroke:#f59e0b,color:#78350f
    style AZ fill:#fbbf24,stroke:#f59e0b,color:#78350f
```
</details>

---

## Screens

<details open>
<summary>Screenshots — guest browsing, the AI shopping flow, and the platform (click to collapse)</summary>

### Guest experience (no login required)

Anyone can browse the catalog, use the AI shopping assistant, and explore product details without creating an account.

<table>
<tr><td><img src="docs/images/flow-guest-storefront.png" alt="Public storefront — browse without signing in" width="820"/></td></tr>
<tr><td align="center"><em>Product detail — full info, pricing, reviews, and stock status</em></td></tr>
<tr><td><img src="docs/images/flow-guest-assistant.png" alt="Public AI shopping assistant" width="820"/></td></tr>
<tr><td align="center"><em>AI shopping assistant — product questions answered via multi-agent routing, no login needed</em></td></tr>
</table>

### AI shopping flow (signed in)

Sign in as any seeded user to access cart, checkout, order tracking, and returns — all driven by natural language in the chat interface. Every response renders as generative UI, not raw text: the component (card, table, chart, badge) is chosen by the shape of the data an agent returns.

<table>
<tr><td><img src="docs/images/flow-product-search.png" alt="AI chat — product search with cards" width="820"/></td></tr>
<tr><td align="center"><em>Find a product — orchestrator routes to Product Discovery; results render as interactive cards</em></td></tr>
<tr><td><img src="docs/images/flow-add-to-cart.png" alt="AI chat — add to cart" width="820"/></td></tr>
<tr><td align="center"><em>Add to cart — ask the assistant; it calls the cart API and confirms with a card</em></td></tr>
<tr><td><img src="docs/images/flow-view-cart.png" alt="AI chat — cart summary" width="820"/></td></tr>
<tr><td align="center"><em>View cart — the agent renders a cart summary with totals and a checkout link</em></td></tr>
<tr><td><img src="docs/images/flow-order-tracking.png" alt="AI chat — order tracking" width="820"/></td></tr>
<tr><td align="center"><em>Track an order — Order Management agent returns live status and shipment detail</em></td></tr>
<tr><td><img src="docs/images/flow-refund.png" alt="AI chat — return / refund request" width="820"/></td></tr>
<tr><td align="center"><em>Return / refund — agent initiates the return flow and issues a return label</em></td></tr>
<tr><td><img src="docs/images/flow-review-sentiment.png" alt="AI chat — review sentiment analysis with generative UI charts" width="820"/></td></tr>
<tr><td align="center"><em>Generative UI — the Review & Sentiment agent's data renders as an interactive card: a rating-distribution bar chart, a 6-month trend line chart, and tone-coded pros/cons, all picked from the shape of the data itself, not a fixed template</em></td></tr>
</table>

### Platform

<table>
<tr><td><img src="docs/images/agent-timeline.png" alt="Live agent activity timeline" width="820"/></td></tr>
<tr><td align="center"><em>Agent timeline — orchestrator → specialist → tool routing surfaced live in chat</em></td></tr>
<tr><td><img src="docs/images/storefront.png" alt="Product storefront" width="820"/></td></tr>
<tr><td align="center"><em>Product storefront — authenticated view with cart and account access</em></td></tr>
<tr><td><img src="docs/images/marketplace.png" alt="Agent marketplace" width="820"/></td></tr>
<tr><td align="center"><em>Agent marketplace — browse, request, and manage specialist agent access</em></td></tr>
<tr><td><img src="docs/images/admin-dashboard.png" alt="Admin dashboard" width="820"/></td></tr>
<tr><td align="center"><em>Admin dashboard — usage analytics, approval queues, and audit log</em></td></tr>
<tr><td><img src="docs/images/seller-dashboard.png" alt="Seller dashboard" width="820"/></td></tr>
<tr><td align="center"><em>Seller dashboard — product catalog and order management</em></td></tr>
</table>

</details>

---

## Test Users

Pre-seeded accounts for testing different roles:

| Email | Password | Role | Loyalty Tier |
|-------|----------|------|-------------|
| `admin.demo@gmail.com` | admin123 | Admin | Gold |
| `seller.demo@gmail.com` | seller123 | Seller | Bronze |
| `seller2.demo@gmail.com` | seller123 | Seller | Bronze |
| `alice.johnson@gmail.com` | customer123 | Customer | Gold |
| `bob.smith@gmail.com` | customer123 | Customer | Silver |

---

## Agent Catalog

| Agent | Port | Description | Key Tools |
|-------|------|-------------|-----------|
| **Customer Support** (Orchestrator) | 8080 | Routes requests to specialists via A2A | `call_specialist_agent` |
| **Product Discovery** | 8081 | Search, semantic search, comparisons, trending | `search_products`, `semantic_search`, `compare_products` |
| **Order Management** | 8082 | Order tracking, cancellation, returns, refunds | `get_user_orders`, `cancel_order`, `initiate_return` |
| **Pricing & Promotions** | 8083 | Coupon validation, cart optimization, loyalty | `validate_coupon`, `optimize_cart`, `get_active_deals` |
| **Review & Sentiment** | 8084 | Sentiment analysis, fake review detection | `analyze_sentiment`, `detect_fake_reviews` |
| **Inventory & Fulfillment** | 8085 | Stock, shipping estimates, fulfillment planning | `check_stock`, `estimate_shipping` |

---

## Demo Scenarios

Try these in the chat after logging in:

1. **Product Search**: "Find me wireless headphones under $300 with good noise cancellation"
2. **Comparison**: "Compare the Sony WH-1000XM5 with AirPods Max"
3. **Order Tracking**: "Where's my latest order?"
4. **Return Flow**: "I want to return my last order"
5. **Price Check**: "Is the Logitech MX Master 3S a good deal right now?"
6. **Review Analysis**: "What do people think about the Dyson V15?"
7. **Stock Check**: "Is the Dyson V15 Detect in stock?"
8. **Multi-Intent**: "Return my jacket and find me a warmer one under $200"

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Agent Framework | [Microsoft Agent Framework](https://github.com/microsoft/agent-framework) v1 — Python SDK (`agent-framework-core` 1.14.0) **and** .NET SDK (`Microsoft.Agents.AI` 1.18.0), both backends fully implemented |
| Agent Communication | A2A Protocol (HTTP) |
| LLM | OpenAI / Azure OpenAI (gpt-4.1) — plus any OpenAI-compatible endpoint (Ollama, LM Studio, vLLM, OpenRouter, GitHub Models) via `LLM_PROVIDER=openai` + `LLM_BASE_URL`, see [Setup](./tutorials/00-setup/) |
| Orchestrator | FastAPI (Python 3.12) · ASP.NET Core minimal APIs (.NET 10, C#) |
| Database | PostgreSQL 16 + pgvector (1536-dim embeddings) |
| Cache | Redis 7 |
| Frontend | Next.js 16, React 19, Tailwind CSS, shadcn/ui |
| Auth | Self-contained JWT by default (PyJWT + bcrypt on Python; BCrypt.Net + `System.IdentityModel.Tokens.Jwt` on .NET), or the bundled OAuth2 server with RS256 + JWKS via `AUTH_MODE=oauth` |
| Telemetry | OpenTelemetry → .NET Aspire Dashboard |
| Package Managers | uv (Python), pnpm (Node) |
| Containerization | Docker Compose |

---

## Project Structure

```
e-commerce-agents/
├── docker-compose.yml               # 14 services with profiles
├── .env.example                     # Environment template
├── agents/                          # Both backends live here
│   ├── python/                      # Python backend
│   │   ├── Dockerfile               # Multi-target agent image (ARG AGENT_NAME)
│   │   ├── Dockerfile.mcp           # Lean MCP server image (ARG MCP_PACKAGE)
│   │   ├── pyproject.toml           # Workspace root + dependencies (MAF, OTel, FastAPI)
│   │   ├── packages/                # Standalone publishable MCP server packages
│   │   │   ├── mcp-product/         # ecommerce-mcp-product (:9000)
│   │   │   └── mcp-inventory/       # ecommerce-mcp-inventory (:9001)
│   │   ├── shared/                  # Shared library (config, auth, DB, prompts, telemetry,
│   │   │                            #   guardrails, grounding, idempotency, rate limiting)
│   │   ├── config/prompts/          # YAML prompt configs (shared with .NET)
│   │   ├── auth_server/             # Self-hosted OAuth2 authorization server (:8090)
│   │   ├── evals/                   # Eval harness, scorers, datasets, replay fixtures
│   │   ├── workflows/               # MAF WorkflowBuilder graphs (pre-purchase, return-replace)
│   │   ├── tests/                   # Test suite (~700 tests)
│   │   ├── orchestrator/            # Customer Support (:8080)
│   │   ├── product_discovery/       # Product Discovery (:8081)
│   │   ├── order_management/        # Order Management (:8082)
│   │   ├── pricing_promotions/      # Pricing & Promotions (:8083)
│   │   ├── review_sentiment/        # Review & Sentiment (:8084)
│   │   └── inventory_fulfillment/   # Inventory & Fulfillment (:8085)
│   └── dotnet/                      # .NET backend — parity tracked in docs/parity-matrix.md
│       ├── ECommerceAgents.sln
│       ├── Directory.Packages.props # Central package versions
│       └── src/
│           ├── ECommerceAgents.Shared/
│           ├── ECommerceAgents.Orchestrator/   # :8080
│           ├── ECommerceAgents.ProductDiscovery/
│           ├── ECommerceAgents.OrderManagement/
│           ├── ECommerceAgents.PricingPromotions/
│           ├── ECommerceAgents.ReviewSentiment/
│           ├── ECommerceAgents.InventoryFulfillment/
│           └── ECommerceAgents.Mcp/            # MCP host (:9001, both domains)
├── docker/postgres/
│   └── init.sql                    # 34-table schema + pgvector
├── scripts/
│   ├── dev.sh                      # One-command dev setup
│   ├── seed.py                     # Database seeder
│   └── generate_embeddings.py      # Product embedding generation
├── web/                            # Next.js 16 frontend
│   └── src/
│       ├── app/                    # 25 routes (App Router)
│       ├── components/             # UI components (shadcn/ui)
│       └── lib/                    # API client, auth context
├── tutorials/                      # 34-chapter MAF v1 tutorial series (Python + .NET)
└── docs/                           # Published at nitinksingh.com/e-commerce-agents/
    ├── README.md                   # Docs index and reading order
    ├── architecture.md             # System design, agent patterns, A2A protocol
    ├── adding-an-agent.md          # Step-by-step guide to adding a specialist
    ├── api-reference.md            # All REST endpoints with examples
    ├── agent-flows.md              # Multi-agent collaboration sequence diagrams
    ├── database-schema.md          # 34 tables with ER diagram
    ├── deployment.md               # Docker Compose, dev.sh, environment config
    ├── frontend.md                 # Routes, theming, SSE/timeline, auth model
    ├── telemetry.md                # OpenTelemetry setup and Aspire Dashboard
    ├── mcp-integration.md          # MCP servers, setup, agent wiring
    ├── maf-best-practices.md       # MAF idioms: @tool, middleware, prompt YAML
    ├── security-guide.md           # Threat model, guardrails, hardening checklist
    ├── agent-quality.md            # Eval methodology, datasets, CI gate
    ├── agent-audit-matrix.md       # Per-agent security posture matrix
    └── troubleshooting.md          # Common local-stack issues and fixes
```

---

## Configuration

Copy `.env.example` to `.env` and configure your LLM provider:

```bash
# OpenAI (default)
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
LLM_MODEL=gpt-4.1

# Azure OpenAI (alternative)
LLM_PROVIDER=azure
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_KEY=...
AZURE_OPENAI_DEPLOYMENT=gpt-4.1

# Local / self-hosted / free (Ollama, LM Studio, OpenRouter, vLLM, GitHub Models)
LLM_PROVIDER=openai
LLM_BASE_URL=http://localhost:11434/v1   # any OpenAI-compatible endpoint
OPENAI_API_KEY=ollama                    # any non-empty string for local servers
LLM_MODEL=qwen2.5:14b                    # a tool-calling-capable model
```

See [Setup — Chapter 00](tutorials/00-setup/README.md) for the full walkthrough (including a
tool-calling gotcha worth reading before picking a local model) and
[Deployment Guide](docs/deployment.md) for all configuration options.

---

## Documentation

Everything below is published, searchable and cross-linked at
**[nitinksingh.com/e-commerce-agents](https://nitinksingh.com/e-commerce-agents/)** — with all
71 Mermaid diagrams rendered as diagrams. The site is generated from this repository by
`scripts/build_docs_site.py`, so the files here are always the source of truth; nothing lives
only on the site.

| Section | What's in it |
|---------|--------------|
| **[Concepts](https://nitinksingh.com/e-commerce-agents/concepts/)** | 14 pages for readers new to agents — what an agent is, the agentic loop, tools, harnesses, why multi-agent, orchestration patterns, graphs, state and memory, grounding, guardrails, HITL, evaluation, observability and cost, production concerns |
| **[Tutorials](https://nitinksingh.com/e-commerce-agents/tutorials/)** | 34 chapters, Python and .NET, each runnable without an API key |
| **[Architecture](https://nitinksingh.com/e-commerce-agents/architecture/)** | [System design](docs/architecture.md) · [agent flows](docs/agent-flows.md) · [database schema](docs/database-schema.md) · [API reference](docs/api-reference.md) · [frontend](docs/frontend.md) · [workflows](docs/workflows/README.md) |
| **[Guides](https://nitinksingh.com/e-commerce-agents/guides/)** | [Adding an agent](docs/adding-an-agent.md) · [MCP integration](docs/mcp-integration.md) · [telemetry](docs/telemetry.md) · [security](docs/security-guide.md) · [agent quality and evals](docs/agent-quality.md) · [MAF best practices](docs/maf-best-practices.md) |
| **[Getting Started](https://nitinksingh.com/e-commerce-agents/getting-started/)** | [Deployment](docs/deployment.md) · [troubleshooting](docs/troubleshooting.md) |
| **[Reference](https://nitinksingh.com/e-commerce-agents/reference/)** | [Python vs .NET parity matrix](docs/parity-matrix.md) · [agent audit matrix](docs/agent-audit-matrix.md) · [glossary](tutorials/_shared/jargon-glossary.md) · [Mermaid style guide](tutorials/_shared/mermaid-style-guide.md) |

Contributor-facing docs stay in the repo rather than on the site:
[CONTRIBUTING.md](CONTRIBUTING.md) (setup, conventions, testing policy, PR checklist) and
[CLAUDE.md](CLAUDE.md).

---

## Port Map

| Service | Port | URL |
|---------|------|-----|
| Frontend | 3000 | http://localhost:3000 |
| Orchestrator | 8080 | http://localhost:8080 |
| Product Discovery | 8081 | |
| Order Management | 8082 | |
| Pricing & Promotions | 8083 | |
| Review & Sentiment | 8084 | |
| Inventory & Fulfillment | 8085 | |
| Aspire Dashboard | 18888 | http://localhost:18888 |
| PostgreSQL | 5432 | |
| Redis | 6379 | |
| Auth Server | 8090 | http://localhost:8090 (when `AUTH_MODE=oauth`) |
| MCP Product | 9000 | http://localhost:9000/mcp — **Python stack only** (when `--profile mcp`) |
| MCP Inventory | 9001 | http://localhost:9001/mcp (when `--profile mcp`) |

Ports are identical across both stacks, with one exception: the .NET stack serves
product *and* inventory tools from a single MCP host on **:9001**, so there is no
:9000 service in `docker-compose.dotnet.yml`. Because the two compose files bind the
same ports, only one stack can run at a time.

---

## Roadmap

This is v1. Both backends are live and stable. Remaining work is consolidated in
[`.claude/plans/remaining-work.md`](.claude/plans/remaining-work.md) — including the gaps
this section does not claim to cover.

Legend: `- [x]` shipped · `- [ ]` planned or in progress.

### Shipped in v1

- [x] **Agent evaluators** — scripted eval sets (precision@k, recall@k, answer faithfulness, tool-call correctness) across all six specialists, run against the seeded catalog. `.github/workflows/evals.yml` runs two jobs. **`smoke` gates every pull request**: deterministic scorers only, driven by committed replay fixtures under `LLM_PROVIDER=replay`, so it needs no API key, costs nothing, and fails the PR when a suite regresses more than 5% against its committed baseline. **`full`** runs weekly on a schedule (and on demand) with a real key and the LLM judge. The harness drives the *production* path — `evals/harness.py` runs the same orchestration modes a real request does, so guardrails, sanitization and HITL gates are exercised rather than bypassed.
- [x] **Prompt injection prevention** — `shared/guardrails/` wired into the middleware stack for all agents. Enabled by default (`GUARDRAILS_ENABLED=true`); runs in observe-first mode (`GUARDRAILS_FAIL_OPEN=true`) — flags and logs injections. Set `GUARDRAILS_BLOCK_ON_INJECTION=true` to enable hard blocking once false-positive rates are measured in your environment.
- [x] **Session memory & context persistence** — `store_memory` / `recall_memories` tools in `shared/tools/memory_tools.py`, surfaced to the orchestrator via `shared/context_providers.py`. Per-user preferences, recent intents, and history make follow-ups feel continuous.
- [x] **Full .NET / C# backend** — the same orchestrator and five specialists plus an MCP host, the same A2A protocol and PostgreSQL schema, idiomatic .NET throughout. Eight test projects, 450 test methods (~500 cases counting `[Theory]` data). Reached parity on the shipped surface through a nine-PR effort covering the shared tool library, orchestration modes, normalized SSE events, server-side grounding, rate limiting, cost estimation and a HITL claim-before-execute fix — gated by a dual-backend Playwright suite rather than a checklist. See [`agents/dotnet/`](./agents/dotnet/) and [`docs/parity-matrix.md`](./docs/parity-matrix.md).
- [x] **Distributed tracing across every agent** — OpenTelemetry throughout (`shared/telemetry.py`), GenAI semantic conventions, a Langfuse sink, and `trace_id` correlated into `usage_logs` so a row in the admin usage table links back to its trace. Spans nest correctly across A2A hops, so one chat turn reads as a single tree in the [Aspire Dashboard](http://localhost:18888). The dashboard itself runs stock — this repo ships no pre-built views.
- [x] **MCP data-access layer (2 servers)** — `mcp-product` (:9000) and `mcp-inventory` (:9001) are standalone, independently publishable Python packages (`packages/mcp-product`, `packages/mcp-inventory`) in a uv workspace. They expose product and inventory data over the MCP streamable HTTP transport (FastMCP). Flag-gated via `MCP_ENABLED`; `product-discovery` and `inventory-fulfillment` swap their direct-asyncpg `@tool` set for `MCPStreamableHTTPTool` with no behavior change. Any MCP-compatible client — Claude Desktop, Cursor, LangGraph — can use them without this codebase. See [MCP Integration](docs/mcp-integration.md).
- [x] **Self-hosted OAuth2 Authorization Server** — opt-in `AUTH_MODE=oauth` path with the token issuer living *inside* this repo (`agents/python/auth_server/`, built on `authlib`), so login and every service call are genuinely OAuth2-compliant with no external identity provider or cloud dependency. RS256 signing with an AS-generated keypair and a JWKS endpoint; user login via the resource-owner-password grant brokered by the orchestrator (the browser keeps its email/password form); client-credentials service tokens replacing the static A2A shared secret; and both MCP servers hardened into OAuth 2.1 resource servers (audience/scope validation, `.well-known/oauth-protected-resource`, `WWW-Authenticate` challenge) — Python and .NET parity throughout. Fully additive — `AUTH_MODE=local` (self-issued JWT + shared secret) stays the zero-config default, so the OpenAI-key-only quick-start is unaffected. Verified end to end against a live stack: real browser login and chat session on AS-issued tokens, role-gated routes, inter-agent and MCP calls authenticated purely on OAuth scopes (no shared secrets), and cross-scope/cross-resource token rejection — both stacks, including the .NET MCP host validated against the real running auth-server. See [`.claude/plans/enhancements/10-oauth-authorization.md`](.claude/plans/enhancements/10-oauth-authorization.md).

- [x] **Server-side grounding** — the model's claims are checked against Postgres before the answer leaves. Product and order ids in card blocks are verified to exist and to carry the quoted price; a fact-check badge reports how many claims were verified. `GROUNDING_MODE` is `annotate` by default (`shared/grounding/`, `Shared/Grounding/`).
- [x] **Orchestration modes, live** — the same question can be answered by a tool router, a handoff mesh, two workflow graphs or a group-chat round table, selected per request from the composer. The graph animates node-by-node from real SSE events, and "compare modes" runs one prompt through several and reports latency side by side.
- [x] **Idempotency on money-moving actions** — an `idempotency_keys` table plus an `@idempotent` decorator on returns, refunds and checkout, so a resubmitted approval cannot double-execute. Approval writes fail *closed*.
- [x] **Resilience and rate limiting** — bounded retries with jittered backoff and a per-endpoint circuit breaker on every A2A call (`shared/http_resilience.py`, mirroring the .NET Polly pipeline that led here), and a Redis sliding-window limiter on both chat routes, keyed by user and by IP for anonymous traffic.
- [x] **Generative UI** — every agent response is rendered by the shape of its data: cards, tables, charts, badges. An unrecognized or malformed payload renders nothing rather than falling back to raw JSON.

### In Progress

- [ ] **In-chat approval card** — the full pause-and-resume loop already works: a workflow suspends on its in-workflow HITL gate, the run shows a pending badge on `/runs`, and Approve/Reject resumes it from a real Postgres checkpoint (`POST /api/orchestration/{run_id}/resume`, both stacks). Destructive tools are separately gated by approval middleware with an atomic claim-before-execute so a double click cannot double-refund. The remaining piece is rendering that same control *inside the chat thread* rather than only on `/runs`.
- [ ] **Cost metrics as first-class counters** — token counts are persisted (`shared/usage_db.py`), surfaced on the admin usage page, and already exported as OTel GenAI metrics by the OpenAI instrumentor. Dollar estimation (`shared/cost.py`, `Shared/Cost/CostEstimator.cs`) and a per-run budget ceiling (`COST_BUDGET_MODE`, default `observe`) both ship. The remaining piece is a dedicated cost counter instrument owned by this repo, so an OTLP sink can alert on spend anomalies directly.
- [ ] **Streaming tool calls end-to-end** — text-delta streaming is live and product/order cards render progressively as the LLM generates the response. The remaining piece is propagating raw tool-result payloads as separate SSE frames so cards can appear before the text completes.

---

### Planned — Search & Retrieval

`semantic_search` and `find_similar_products` are real pgvector cosine queries and the prompt already routes vague descriptive queries to them. But `search_products` — the workhorse — still uses `ILIKE` matching, with no lexical index behind it. Planned:

- [ ] **Postgres full-text search** — `tsvector` column + GIN index, `plainto_tsquery` + `ts_rank` to replace the `ILIKE` loop.
- [ ] **Hybrid retrieval (FTS + vector)** — combine lexical and semantic scores via Reciprocal Rank Fusion in a single CTE.
- [ ] **Typed filter DSL** — replace the flat parameter list on `search_products` with a structured `ProductFilters` Pydantic model (category, price, brand, sort). Keeps SQL parameterized and safe.

Text-to-SQL was considered and rejected: `user_email`/`user_role` scoping via ContextVars means dynamic SQL would bypass that contract. The typed filter DSL gives the model flexibility at the boundary while keeping SQL generation server-side and auditable.

---

### MCP as the Agent Data-Access Layer

| Server | Port | Domain |
|--------|------|--------|
| `mcp-product` | 9000 | Product search, details, comparison, trending, price history |
| `mcp-inventory` | 9001 | Stock levels, warehouses, shipping, carriers |

Those two are the **Python** stack. The .NET stack serves both domains from a single host (`ECommerceAgents.Mcp`) on **:9001** — there is no :9000 in `docker-compose.dotnet.yml`.

Both are standalone publishable packages in a uv workspace (`packages/mcp-product`, `packages/mcp-inventory`). Start them with:

```bash
docker compose --profile mcp --profile agents up

# then set in .env
MCP_ENABLED=true
MCP_PRODUCT_SERVER_URL=http://localhost:9000/mcp
MCP_INVENTORY_SERVER_URL=http://localhost:9001/mcp
```

See [MCP Integration](docs/mcp-integration.md) for the full setup guide, tool coverage table, external client examples (Claude Desktop, LangGraph), and publishing instructions.

Planned:

- [ ] **External integration surface** — publish `ecommerce-mcp-product` and `ecommerce-mcp-inventory` to PyPI so any MCP-compatible client can `pip install` and run them against any PostgreSQL database without this codebase.
- [ ] **Eval gate** — run each eval dataset twice (native tools vs MCP path) and fail CI if the MCP run scores below the native baseline.

---

### Planned — Platform & Observability

- [ ] **Prompt caching** — cache system prompts and tool schemas per agent to reduce per-request token cost on repeated specialist invocations.

---

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Make your changes and ensure tests pass
4. Submit a pull request

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed setup, conventions, testing policy, and PR checklist.

---

## License

This project is licensed under the [MIT License](LICENSE).

---

Built with [Microsoft Agent Framework](https://github.com/microsoft/agent-framework) and [A2A Protocol](https://google.github.io/A2A/).
