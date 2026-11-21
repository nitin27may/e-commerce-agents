# Deployment & Development

## 1. Prerequisites

| Tool | Minimum Version | Notes |
|------|----------------|-------|
| Docker | 24+ | Desktop or Engine |
| Docker Compose | v2 | Bundled with Docker Desktop; verify with `docker compose version` |
| OpenAI API key **or** Azure OpenAI credentials | -- | At least one LLM provider must be configured |
| Git | 2.x | For cloning the repo |

Optional for local (non-Docker) development:

| Tool | Version | Notes |
|------|---------|-------|
| Python | 3.12 | Required only for running agents outside Docker |
| uv | latest | Python package manager (`pip install uv` or `brew install uv`) |
| Node.js | 22+ | Required only for running the frontend outside Docker |
| pnpm | 9+ | Enable via `corepack enable && corepack prepare pnpm@latest --activate` |

## 2. Docker Compose Architecture

The platform runs 15 services organized into 5 profile groups (`docker-compose.yml`). Services without a profile start by default.

```mermaid
graph TB
    subgraph defaultGroup["Default (always start)"]
        style defaultGroup fill:#f8fafc,stroke:#e2e8f0,stroke-width:2px
        db["PostgreSQL 16 + pgvector<br/>:5432"]
        redis["Redis 8<br/>:6379"]
        aspire["Aspire Dashboard<br/>:18888"]
    end

    subgraph seed["Profile: seed"]
        style seed fill:#f8fafc,stroke:#e2e8f0,stroke-width:2px
        seeder["Seeder<br/>(run once, exits)"]
    end

    subgraph agents["Profile: agents"]
        style agents fill:#f8fafc,stroke:#e2e8f0,stroke-width:2px
        orchestrator["Orchestrator<br/>:8080"]
        product["Product Discovery<br/>:8081"]
        order["Order Management<br/>:8082"]
        pricing["Pricing & Promotions<br/>:8083"]
        review["Review & Sentiment<br/>:8084"]
        inventory["Inventory & Fulfillment<br/>:8085"]
        authsrv["Auth Server<br/>:8090"]
        worker["Outbox Worker<br/>(no port)"]
    end

    subgraph mcp_profile["Profile: mcp"]
        style mcp_profile fill:#f8fafc,stroke:#e2e8f0,stroke-width:2px
        mcpproduct["MCP Product<br/>:9000"]
        mcpinventory["MCP Inventory<br/>:9001"]
    end

    subgraph frontend_profile["Profile: frontend"]
        style frontend_profile fill:#f8fafc,stroke:#e2e8f0,stroke-width:2px
        frontend["Next.js Frontend<br/>:3000"]
    end

    seeder -->|depends_on| db
    orchestrator -->|depends_on| db
    orchestrator -->|depends_on| redis
    orchestrator -->|depends_on| aspire
    product -->|depends_on| db
    product -->|depends_on| aspire
    order -->|depends_on| db
    order -->|depends_on| aspire
    pricing -->|depends_on| db
    pricing -->|depends_on| aspire
    review -->|depends_on| db
    review -->|depends_on| aspire
    inventory -->|depends_on| db
    inventory -->|depends_on| aspire
    frontend -->|depends_on| orchestrator
    authsrv -->|depends_on| db
    worker -->|depends_on| db
    mcpproduct -->|depends_on| db
    mcpinventory -->|depends_on| db

    orchestrator -- "A2A Protocol" --> product
    orchestrator -- "A2A Protocol" --> order
    orchestrator -- "A2A Protocol" --> pricing
    orchestrator -- "A2A Protocol" --> review
    orchestrator -- "A2A Protocol" --> inventory
```

## 3. Service Profiles

Docker Compose profiles control which services start. Services without a profile always start.

| Profile | Services | When to Use |
|---------|----------|-------------|
| *(none)* | `db`, `redis`, `aspire` | Always started -- infrastructure baseline |
| `seed` | `seeder` | Populates the database with sample data. Runs once and exits. |
| `agents` | `orchestrator`, `product-discovery`, `order-management`, `pricing-promotions`, `review-sentiment`, `inventory-fulfillment` | The 6 AI agent microservices |
| `agents` (cont.) | `auth-server` | The self-hosted OAuth 2.1 authorization server (:8090, see [Security Guide](security-guide.md)) |
| `agents` (cont.) | `outbox-worker` | Background worker: approval reminders, SLA escalation and timeouts (no port; see [Approvals](approvals.md#the-outbox-worker-sla-and-notifications)). Built from the Python agent image with `AGENT_NAME=worker`. Disable with `OUTBOX_WORKER_ENABLED=false` |
| `mcp` | `mcp-product`, `mcp-inventory` | The two MCP servers (:9000 and :9001). Used when `MCP_ENABLED` is set; `dev.sh --dotnet` always adds this profile (the .NET MCP inventory host). See [MCP Integration](mcp-integration.md) |
| `frontend` | `frontend` | Next.js web application |

**Usage examples:**

```bash
# Infrastructure only
docker compose up -d

# Infrastructure + agents
docker compose --profile agents up -d

# Everything
docker compose --profile agents --profile frontend up -d

# Everything, with the MCP servers
docker compose --profile agents --profile mcp --profile frontend up -d

# Run the seeder
docker compose --profile seed run --rm seeder
```

### The seeder outside compose (`db-init`)

Compose bind-mounts `scripts/` and `docker/postgres/` into the seeder. A managed environment has no such
mount, so the `db-init` image (`agents/python/Dockerfile.dbinit`, build context is the **repo root**) bakes
them in:

```bash
docker build -f agents/python/Dockerfile.dbinit -t db-init .
docker run --rm -e DATABASE_URL=postgresql://... -e SEED_STAFF_PASSWORD=... db-init
```

It migrates, seeds only if `users` is empty, loads embeddings only if the table is empty and a real provider
is configured, then runs `ANALYZE`. It is idempotent and never truncates. `SEED_STAFF_PASSWORD` replaces the
published admin/support/seller demo passwords; set it anywhere but a laptop. See
[Configuration](configuration.md#database-bootstrap-seed_-db-init).

## 4. dev.sh Script

The `scripts/dev.sh` script is the recommended way to start the development environment. It handles build ordering, health checks, seeding, and prints a summary when ready.

**On Windows, use `scripts/dev.ps1`** — a PowerShell script with identical behaviour: same profiles,
same ordering, same health gates, and the same flags in PowerShell form (`--clean` → `-Clean`,
`--seed-only` → `-SeedOnly`, `--infra-only` → `-InfraOnly`, `--dotnet` → `-Dotnet`, `--demo` → `-Demo`, `--switch` → `-SwitchStack`). It also runs on
macOS and Linux under PowerShell 7, though `dev.sh` is the more idiomatic choice there. Everything
documented in this section applies to both.

### Flags

| Flag | Description |
|------|-------------|
| *(no flags)* | Full rebuild: stops existing containers, builds all images, starts infrastructure, seeds the database, starts all agents, starts the frontend |
| `--clean` | Nuclear option: removes all containers, volumes (including DB data), and orphans, then does a full rebuild |
| `--seed-only` | Ensures infrastructure is running, then re-runs the seeder against the existing database. Useful after schema changes or to reset sample data. |
| `--infra-only` | Starts only `db`, `redis`, and `aspire`. Does not start agents or frontend. Use this when running agents locally via `uvicorn`. |
| `--dotnet` | Targets the .NET backend: composes with `docker-compose.dotnet.yml` and adds the `mcp` profile. Combines with the other flags, e.g. `--clean --dotnet`. |
| `--demo` | Skips the local build and pulls the prebuilt images from GHCR (`docker-compose.demo.yml`, tag from `IMAGE_TAG`, default `latest`). Cannot be combined with `--dotnet`: no prebuilt .NET images are published. |
| `--switch` | Tears the other stack down first, volumes included, so changing between Python and .NET is one command (`./scripts/dev.sh --dotnet --switch`). Without it, the script stops and tells you the other stack is running. |
| `--help`, `-h` | Prints usage information |

### Script Flow

```mermaid
flowchart TD
    start([dev.sh]) --> check_prereqs{Docker +<br/>Compose installed?}
    check_prereqs -- No --> fail_exit([Exit with error])
    check_prereqs -- Yes --> check_env{.env exists?}
    check_env -- No --> copy_env[Copy .env.example to .env]
    check_env -- Yes --> parse_flags
    copy_env --> parse_flags

    parse_flags --> is_clean{--clean?}
    is_clean -- Yes --> clean[docker compose down -v<br/>Remove volumes + orphans]
    is_clean -- No --> is_seed_only

    clean --> is_seed_only{--seed-only?}
    is_seed_only -- Yes --> start_infra_seed[Start db + redis + aspire]
    start_infra_seed --> health_infra_seed[Wait for health checks]
    health_infra_seed --> run_seeder_only[Run seeder]
    run_seeder_only --> exit_seed([Exit])

    is_seed_only -- No --> stop_existing[Stop existing containers]
    stop_existing --> build[Build agent images]
    build --> start_infra[Start db + redis + aspire]
    start_infra --> health_infra[Wait for health checks]
    health_infra --> run_seeder[Run database seeder]

    run_seeder --> is_infra_only{--infra-only?}
    is_infra_only -- Yes --> summary_infra[Print infrastructure summary]
    summary_infra --> exit_infra([Exit])

    is_infra_only -- No --> start_agents[Start all 6 agents]
    start_agents --> health_agents[Wait for agent /health endpoints]
    health_agents --> start_frontend[Start frontend]
    start_frontend --> health_frontend[Wait for frontend :3000]
    health_frontend --> summary_full[Print full summary]
    summary_full --> done([Done])
```

## 5. Environment Configuration

Copy `.env.minimal` to `.env` and set the model key. `.env.example` is the full reference of every variable:

```bash
cp .env.minimal .env
```

### LLM Provider

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LLM_PROVIDER` | Yes | `openai` | LLM provider: `openai`, `azure`, or `replay` (plays back recorded fixtures, no credentials — see `shared/replay_client.py`) |

### OpenAI Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | Yes (if `openai`) | -- | Your OpenAI API key. Any non-empty string works against a local server that doesn't check it (Ollama, LM Studio) |
| `LLM_MODEL` | No | `gpt-4.1` | Chat completion model name |
| `LLM_BASE_URL` | No | unset (uses `api.openai.com`) | Only takes effect when `LLM_PROVIDER=openai`. Points `OpenAIChatClient` at any OpenAI-compatible endpoint instead — GitHub Models, OpenRouter, vLLM, LM Studio, or a local Ollama server (`http://localhost:11434/v1`). See `tutorials/00-setup/README.md` for worked examples and a tool-calling-support gotcha before picking a local model. |

### Azure OpenAI Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `AZURE_OPENAI_ENDPOINT` | Yes (if `azure`) | -- | Azure OpenAI resource endpoint URL |
| `AZURE_OPENAI_KEY` | Yes (if `azure`) | -- | Azure OpenAI API key |
| `AZURE_OPENAI_DEPLOYMENT` | Yes (if `azure`) | -- | Deployment name for chat completions |
| `AZURE_OPENAI_API_VERSION` | No | `2024-12-01-preview` | Azure OpenAI API version |

### Embeddings

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `EMBEDDING_MODEL` | No | `text-embedding-3-small` | OpenAI embedding model for product semantic search (pgvector) |
| `AZURE_EMBEDDING_DEPLOYMENT` | No (if `azure`) | -- | Azure OpenAI deployment for embeddings |

### Database

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `POSTGRES_DB` | No | `ecommerce_agents` | PostgreSQL database name |
| `POSTGRES_USER` | No | `ecommerce` | PostgreSQL user |
| `POSTGRES_PASSWORD` | No | `ecommerce_secret` | PostgreSQL password |
| `DATABASE_URL` | No | `postgresql://ecommerce:ecommerce_secret@db:5432/ecommerce_agents` | Full connection string. In Docker, `db` resolves to the Compose service. For local dev, use `localhost`. |

### Redis

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `REDIS_URL` | No | `redis://redis:6379` | Redis connection string. In Docker, `redis` resolves to the Compose service. |

### Auth

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `JWT_SECRET` | Yes | `change-me-...` | Secret key for signing JWTs. Generate with: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `AGENT_SHARED_SECRET` | No | `agent-internal-shared-secret` | Shared secret for inter-agent authentication (orchestrator to specialists) |

### Telemetry

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OTEL_ENABLED` | No | `true` | Enable/disable OpenTelemetry export |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | No | `http://aspire:18889` | OTLP receiver endpoint (Aspire Dashboard) |
| `OTEL_SERVICE_NAME` | No | `ecommerce.orchestrator` | Service name reported to OTLP. Each agent overrides this in `docker-compose.yml`. |

### General

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ENVIRONMENT` | No | `development` | Runtime environment identifier |
| `AGENT_REGISTRY` | No | *(JSON map)* | JSON object mapping agent names to their internal Docker network URLs. Used by the orchestrator to discover specialist agents. |
| `ORCHESTRATOR_URL` | No | `http://localhost:8080` | Where the frontend's server-side `/api/*` proxy forwards to. Read at runtime, so one image works in every environment. The browser never sees it. |

## 6. Dockerfile Architecture

### Agent Dockerfile (Multi-Target)

All 6 agents share a single `agents/Dockerfile`. The build target is controlled via two `ARG` values:

| ARG | Default | Purpose |
|-----|---------|---------|
| `AGENT_NAME` | `orchestrator` | Python package directory to copy (e.g., `product_discovery`, `order_management`) |
| `AGENT_PORT` | `8080` | Port the agent listens on |

**Build flow:**

1. **Base image**: `python:3.12-slim` with system dependencies (`gcc`, `libpq-dev`, `curl`)
2. **Install uv**: Copied from the official `ghcr.io/astral-sh/uv` image
3. **Create non-root user**: `agent` user and group
4. **Install Python deps**: `uv sync --no-dev --no-install-project` (cached layer -- only re-runs when `pyproject.toml` changes)
5. **Copy shared library**: `shared/` directory used by all agents
6. **Copy agent module**: Only the `${AGENT_NAME}/` directory for this specific agent
7. **Switch to non-root user**
8. **Health check**: `curl -f http://localhost:${AGENT_PORT}/health`
9. **Entrypoint**: `uv run uvicorn ${AGENT_NAME}.main:app --host 0.0.0.0 --port ${AGENT_PORT}`

The seeder service reuses the orchestrator image but overrides the `command` to run `uv run python -m scripts.seed` with the `scripts/` directory mounted as a read-only volume.

### Frontend Dockerfile (Multi-Stage)

The `web/Dockerfile` uses a 3-stage build for minimal production images:

| Stage | Base | Purpose |
|-------|------|---------|
| `deps` | `node:22-alpine` | Install dependencies with `pnpm install --frozen-lockfile` |
| `builder` | `node:22-alpine` | Build Next.js (`pnpm build`). No backend address is compiled in — see `ORCHESTRATOR_URL` above |
| `runner` | `node:22-alpine` | Production runtime with standalone output only. Non-root `nextjs` user. |

The final image contains only the standalone server, static assets, and public directory -- no `node_modules` or source code.

## 7. Local Development

For faster iteration, run infrastructure in Docker and agents/frontend locally.

**Start infrastructure:**

```bash
./scripts/dev.sh --infra-only
```

**Run a single agent:**

```bash
cd agents
export DATABASE_URL=postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents
export REDIS_URL=redis://localhost:6379
export OPENAI_API_KEY=sk-your-key
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:18890

uv run uvicorn product_discovery.main:app --port 8081 --reload
```

**Run the orchestrator** (needs `AGENT_REGISTRY` pointing to local ports):

```bash
cd agents
export AGENT_REGISTRY='{"product-discovery":"http://localhost:8081","order-management":"http://localhost:8082","pricing-promotions":"http://localhost:8083","review-sentiment":"http://localhost:8084","inventory-fulfillment":"http://localhost:8085"}'

uv run uvicorn orchestrator.main:app --port 8080 --reload
```

**Run the frontend:**

```bash
cd web
pnpm install
pnpm dev
```

The frontend starts on `http://localhost:3000` and forwards its `/api/*` calls to `http://localhost:8080` (configurable via `ORCHESTRATOR_URL`).

**Seed the database locally:**

```bash
cd agents
DATABASE_URL=postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents \
  uv run python -m scripts.seed
```

**Generate embeddings locally:**

```bash
DATABASE_URL=postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents \
OPENAI_API_KEY=sk-your-key \
  uv run --project agents/python python -m scripts.generate_embeddings
```

## Running under Aspire (alternative to compose)

`agents/dotnet/src/ECommerceAgents.AppHost` runs the same stack under [Aspire](https://aspire.dev) instead of
`docker-compose.yml`. Compose stays the reference path (CI, `dev.sh`, the published images); the AppHost
is for the Aspire dashboard's resource graph, one-command local debugging of the .NET projects, and
mixing stacks per service. It reads the same variable names with the same defaults as compose
(`EnvironmentContract.cs` copies them), and the two never share a data volume.

**Prerequisites:** the .NET 10 SDK, Docker (Postgres, Redis, the seeder and the auth-server are containers),
`uv` (a single `uv sync` of `agents/python` feeds the Python processes), Node and `pnpm` (the frontend runs as
`pnpm dev`). No Aspire workload or CLI is needed: the AppHost is a normal project.

```bash
# .NET orchestrator and specialists (default)
dotnet run --project agents/dotnet/src/ECommerceAgents.AppHost

# Python orchestrator and specialists, run as uvicorn apps
AgentStack=python dotnet run --project agents/dotnet/src/ECommerceAgents.AppHost

# One service on the other stack (a third way to run a mixed stack, beside docker-compose.mixed.yml)
AgentStackOverrides__order-management=python dotnet run --project agents/dotnet/src/ECommerceAgents.AppHost
```

Settings are plain configuration, so an environment variable, `--Name=value` after `--`, or user secrets all
work: `AgentStack` (`dotnet` | `python`), `AgentStackOverrides:<specialist>`, `Seed` (default `true`; `false` skips
the seeder) and `Mcp` (default `false`; adds the MCP servers, like compose profile `mcp`). Keys such as
`OPENAI_API_KEY` and `LLM_PROVIDER` are read from the AppHost's own environment and passed on, the way
`${VAR:-default}` does in compose. The dashboard is at http://localhost:15888 (`Properties/launchSettings.json`).

| Resource | What it is |
|----------|-----------|
| `postgres`, `ecommerce-agents` | `pgvector/pgvector:pg16`, database `ecommerce_agents`, volume `e-commerce-agents-aspire-pgdata` |
| `redis` | Redis |
| `migrate`, `seeder` | One-shot containers built from `agents/python/Dockerfile`. `scripts/migrate.py` is still the only migration runner (no `init.sql` mount); the seeder waits for it |
| `auth-server` | The shared Python auth-server container on :8090 |
| `orchestrator` (:8080), five specialists (:8081 to :8085) | .NET projects, or uvicorn apps under `AgentStack=python`; `AGENT_REGISTRY` is built from their endpoints |
| `outbox-worker` | The Python worker, on both stacks |
| `frontend` | `pnpm dev` on :3000 with `ORCHESTRATOR_URL` set |
| `python-deps` | One `uv sync --frozen` in `agents/python`, awaited by every Python process |

Tests: `dotnet test agents/dotnet/tests/ECommerceAgents.AppHost.Tests` builds the application model with
`DistributedApplicationTestingBuilder` and inspects it. Nothing is started, so no Docker is needed.

### Gotchas

- **It binds the compose ports** (8080 to 8085, 8090, 3000), so stop compose first.
- **Aspire is pinned to 13.6.5 on purpose.** 17.0.0 is newer on nuget.org but marks `Aspire.AppHost.Sdk` and
  the `ProjectReference` resource model obsolete (ASPIRE012) and moves to the Aspire CLI bundle. Moving to it
  means `AddDotnetProject` and a new test harness; it is a separate piece of work.
- The AppHost project sets `CentralPackageTransitivePinningEnabled=false`, because Aspire needs newer
  `Microsoft.Extensions.*` than the repo-wide pins; the services themselves keep the pins.
- Host-side processes reach the auth-server at `http://localhost:8090`, which is also its issuer; containers
  reach it by resource name.
- **A real boot is not covered by CI.** The model tests prove names, wiring and the env contract. Port binding
  of the uvicorn apps, `WithPnpm()` and the first-run image builds have not been exercised by a test.
- Secrets are skipped when publishing a manifest. The AppHost is a local-development tool, not a deployment path.

## 8. Port Map

| Port | Service | Protocol | Notes |
|------|---------|----------|-------|
| 3000 | Next.js Frontend | HTTP | Browser-facing UI |
| 5432 | PostgreSQL | TCP | pgvector enabled |
| 6379 | Redis | TCP | Rate-limit buckets and cache. The limiter (`shared/rate_limit.py`, `RATE_LIMIT_*`) fails open when Redis is down |
| 8080 | Orchestrator (Customer Support Agent) | HTTP | API gateway -- all user requests enter here |
| 8081 | Product Discovery Agent | HTTP | A2A endpoint, called by orchestrator |
| 8082 | Order Management Agent | HTTP | A2A endpoint, called by orchestrator |
| 8083 | Pricing & Promotions Agent | HTTP | A2A endpoint, called by orchestrator |
| 8084 | Review & Sentiment Agent | HTTP | A2A endpoint, called by orchestrator |
| 8085 | Inventory & Fulfillment Agent | HTTP | A2A endpoint, called by orchestrator |
| 8090 | Auth Server | HTTP | OAuth 2.1 authorization server (`agents` profile) |
| 9000 | MCP Product server | HTTP | `mcp` profile |
| 9001 | MCP Inventory server | HTTP | `mcp` profile |
| 18888 | Aspire Dashboard | HTTP | OpenTelemetry traces and logs UI |
| 18890 | Aspire OTLP Receiver | gRPC | Mapped from container port 18889 |

## 9. Health Checks

### Docker Health Checks

All services have built-in health checks defined in `docker-compose.yml` or the Dockerfile:

| Service | Check | Interval | Timeout | Retries |
|---------|-------|----------|---------|---------|
| PostgreSQL | `pg_isready -U ecommerce` | 5s | 3s | 5 |
| Redis | `redis-cli ping` | 5s | 3s | 5 |
| All agents | `curl -f http://localhost:{PORT}/health` | 15s | 5s | 3 (30s start period) |

### Manual Verification

```bash
# Check all container statuses
docker compose --profile agents --profile frontend ps

# Check individual agent health
curl http://localhost:8080/health   # Orchestrator
curl http://localhost:8081/health   # Product Discovery
curl http://localhost:8082/health   # Order Management
curl http://localhost:8083/health   # Pricing & Promotions
curl http://localhost:8084/health   # Review & Sentiment
curl http://localhost:8085/health   # Inventory & Fulfillment

# Check PostgreSQL connectivity
docker compose exec db pg_isready -U ecommerce

# Check Redis
docker compose exec redis redis-cli ping

# View OpenTelemetry traces
open http://localhost:18888
```

## 10. Troubleshooting

For common issues — port conflicts, missing API keys, empty data, Aspire traces, build failures, and frontend errors — see **[troubleshooting.md](./troubleshooting.md)**.

Deployment-specific issues are below.

### Health versus readiness

`/health` is static liveness ("the process is up"). `/ready` (orchestrator and every specialist, both stacks) answers "can this service work right now": a database round trip within 2 s, a `schema_migrations` row, and Redis reported but non-fatal. It returns `{"status", "service", "checks"}` with HTTP 200 or 503. Compose healthchecks probe `/ready`, and start-up is ordered agents, then orchestrator, then frontend (`depends_on: service_healthy`). A stack started without running the seeder (which runs the migrations) therefore stays unhealthy instead of looking fine.

### Database volume out of date after schema changes

**Cause**: `init.sql` only runs on the first volume creation, and it is a frozen baseline. Schema changes since then are numbered files in `docker/postgres/migrations/`, applied by `scripts/migrate.py` (the seeder runs it before seeding; `./scripts/dev.sh` runs it too). A volume that predates a migration is upgraded in place by running that script; `schema_migrations` records what has been applied, and an applied file that was edited afterwards fails the run.

**Fix**: Run `./scripts/dev.sh --seed-only` (applies pending migrations, keeps your data). If the volume predates the *baseline* itself, destroy it and re-initialize:

```bash
./scripts/dev.sh --clean
```

This removes the `pgdata` volume, re-creates the database from `init.sql`, and re-seeds.

### Seeder fails with "relation does not exist"

**Cause**: Same root cause — the database volume was created before the latest `init.sql`.

**Fix**: `./scripts/dev.sh --clean`.

### Docker build slow — dependency layer invalidated

**Cause**: Changing `pyproject.toml` triggers a full `uv sync` reinstall.

**Fix**: The Dockerfile is structured so the dependency layer is independent of source code. If you only changed `.py` files, `uv sync` is reused from cache. Avoid touching `pyproject.toml` unless you are adding or removing dependencies.

### "Permission denied" running dev.sh

```bash
chmod +x scripts/dev.sh
```

---

## Related

- [`docs/troubleshooting.md`](./troubleshooting.md) — runtime issues (port conflicts, LLM errors, DB connection, Aspire traces)
- [`docs/architecture.md`](./architecture.md) — system overview and agent patterns
- [Project README](../README.md)
