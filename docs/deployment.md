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

The platform runs 11 services organized into 4 profile groups. Services without a profile start by default.

```mermaid
graph TB
    subgraph default["Default (always start)"]
        style default fill:#ecfdf5,stroke:#10b981,stroke-width:2px
        db["PostgreSQL 16 + pgvector<br/>:5432"]
        redis["Redis 7<br/>:6379"]
        aspire["Aspire Dashboard<br/>:18888"]
    end

    subgraph seed["Profile: seed"]
        style seed fill:#fef3c7,stroke:#f59e0b,stroke-width:2px
        seeder["Seeder<br/>(run once, exits)"]
    end

    subgraph agents["Profile: agents"]
        style agents fill:#e0f2fe,stroke:#0ea5e9,stroke-width:2px
        orchestrator["Orchestrator<br/>:8080"]
        product["Product Discovery<br/>:8081"]
        order["Order Management<br/>:8082"]
        pricing["Pricing & Promotions<br/>:8083"]
        review["Review & Sentiment<br/>:8084"]
        inventory["Inventory & Fulfillment<br/>:8085"]
    end

    subgraph frontend_profile["Profile: frontend"]
        style frontend_profile fill:#e0f2fe,stroke:#0ea5e9,stroke-width:2px
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
| `frontend` | `frontend` | Next.js web application |

**Usage examples:**

```bash
# Infrastructure only
docker compose up -d

# Infrastructure + agents
docker compose --profile agents up -d

# Everything
docker compose --profile agents --profile frontend up -d

# Run the seeder
docker compose --profile seed run --rm seeder
```

## 4. dev.sh Script

The `scripts/dev.sh` script is the recommended way to start the development environment. It handles build ordering, health checks, seeding, and prints a summary when ready.

### Flags

| Flag | Description |
|------|-------------|
| *(no flags)* | Full rebuild: stops existing containers, builds all images, starts infrastructure, seeds the database, starts all agents, starts the frontend |
| `--clean` | Nuclear option: removes all containers, volumes (including DB data), and orphans, then does a full rebuild |
| `--seed-only` | Ensures infrastructure is running, then re-runs the seeder against the existing database. Useful after schema changes or to reset sample data. |
| `--infra-only` | Starts only `db`, `redis`, and `aspire`. Does not start agents or frontend. Use this when running agents locally via `uvicorn`. |
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

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
```

### LLM Provider

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LLM_PROVIDER` | Yes | `openai` | LLM provider: `openai` or `azure` |

### OpenAI Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | Yes (if `openai`) | -- | Your OpenAI API key |
| `LLM_MODEL` | No | `gpt-4.1` | Chat completion model name |

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
| `POSTGRES_DB` | No | `agentbazaar` | PostgreSQL database name |
| `POSTGRES_USER` | No | `agentbazaar` | PostgreSQL user |
| `POSTGRES_PASSWORD` | No | `agentbazaar` | PostgreSQL password |
| `DATABASE_URL` | No | `postgresql://agentbazaar:agentbazaar@db:5432/agentbazaar` | Full connection string. In Docker, `db` resolves to the Compose service. For local dev, use `localhost`. |

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
| `OTEL_SERVICE_NAME` | No | `agentbazaar.orchestrator` | Service name reported to OTLP. Each agent overrides this in `docker-compose.yml`. |

### General

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ENVIRONMENT` | No | `development` | Runtime environment identifier |
| `AGENT_REGISTRY` | No | *(JSON map)* | JSON object mapping agent names to their internal Docker network URLs. Used by the orchestrator to discover specialist agents. |
| `NEXT_PUBLIC_API_URL` | No | `http://localhost:8080` | Orchestrator URL as seen by the browser. The frontend uses this for API calls. |

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
| `builder` | `node:22-alpine` | Build Next.js (`pnpm build`), inlines `NEXT_PUBLIC_API_URL` at build time |
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
export DATABASE_URL=postgresql://agentbazaar:agentbazaar@localhost:5432/agentbazaar
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

The frontend starts on `http://localhost:3000` and expects the orchestrator at `http://localhost:8080` (configurable via `NEXT_PUBLIC_API_URL`).

**Seed the database locally:**

```bash
cd agents
DATABASE_URL=postgresql://agentbazaar:agentbazaar@localhost:5432/agentbazaar \
  uv run python -m scripts.seed
```

**Generate embeddings locally:**

```bash
cd agents
DATABASE_URL=postgresql://agentbazaar:agentbazaar@localhost:5432/agentbazaar \
OPENAI_API_KEY=sk-your-key \
  uv run python -m scripts.generate_embeddings
```

## 8. Port Map

| Port | Service | Protocol | Notes |
|------|---------|----------|-------|
| 3000 | Next.js Frontend | HTTP | Browser-facing UI |
| 5432 | PostgreSQL | TCP | pgvector enabled |
| 6379 | Redis | TCP | Session cache, rate limiting |
| 8080 | Orchestrator (Customer Support Agent) | HTTP | API gateway -- all user requests enter here |
| 8081 | Product Discovery Agent | HTTP | A2A endpoint, called by orchestrator |
| 8082 | Order Management Agent | HTTP | A2A endpoint, called by orchestrator |
| 8083 | Pricing & Promotions Agent | HTTP | A2A endpoint, called by orchestrator |
| 8084 | Review & Sentiment Agent | HTTP | A2A endpoint, called by orchestrator |
| 8085 | Inventory & Fulfillment Agent | HTTP | A2A endpoint, called by orchestrator |
| 18888 | Aspire Dashboard | HTTP | OpenTelemetry traces and logs UI |
| 18890 | Aspire OTLP Receiver | gRPC | Mapped from container port 18889 |

## 9. Health Checks

### Docker Health Checks

All services have built-in health checks defined in `docker-compose.yml` or the Dockerfile:

| Service | Check | Interval | Timeout | Retries |
|---------|-------|----------|---------|---------|
| PostgreSQL | `pg_isready -U agentbazaar` | 5s | 3s | 5 |
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
docker compose exec db pg_isready -U agentbazaar

# Check Redis
docker compose exec redis redis-cli ping

# View OpenTelemetry traces
open http://localhost:18888
```

## 10. Troubleshooting

### Agent fails to start: "Connection refused" to database

**Cause**: The agent started before PostgreSQL was ready.

**Fix**: Use `dev.sh` which waits for health checks. If running manually, ensure `db` is healthy first:

```bash
docker compose up -d db
docker compose exec db pg_isready -U agentbazaar   # Wait until this returns 0
```

### "OPENAI_API_KEY not set" or LLM errors

**Cause**: Missing or invalid API key in `.env`.

**Fix**: Verify your `.env` file has a valid key:

```bash
grep OPENAI_API_KEY .env   # Should show your key, not the placeholder
```

If using Azure, ensure `LLM_PROVIDER=azure` and all `AZURE_OPENAI_*` variables are set.

### Port already in use

**Cause**: Another process or a previous Docker run is occupying the port.

**Fix**:

```bash
# Find what's using the port
lsof -i :8080

# Stop all AgentBazaar containers
docker compose --profile seed --profile agents --profile frontend down
```

### Database schema out of date

**Cause**: `init.sql` only runs on first volume creation.

**Fix**: Destroy the volume and re-initialize:

```bash
./scripts/dev.sh --clean
```

This removes the `pgdata` volume, re-creates the database from `init.sql`, and re-seeds.

### Seeder fails with "relation does not exist"

**Cause**: The database volume was created before the latest `init.sql` schema.

**Fix**: Same as above -- run `./scripts/dev.sh --clean` to rebuild from scratch.

### Frontend shows "Failed to fetch" or CORS errors

**Cause**: The orchestrator is not running, or `NEXT_PUBLIC_API_URL` points to the wrong address.

**Fix**:

1. Verify the orchestrator is healthy: `curl http://localhost:8080/health`
2. In Docker, `NEXT_PUBLIC_API_URL` should be `http://localhost:8080` (the browser calls this, not the container)
3. If running the frontend locally but agents in Docker, the default value works as-is

### Aspire Dashboard shows no traces

**Cause**: `OTEL_ENABLED` is `false` or the endpoint is wrong.

**Fix**:

1. Verify `OTEL_ENABLED=true` in `.env`
2. In Docker, `OTEL_EXPORTER_OTLP_ENDPOINT` should be `http://aspire:18889` (container-to-container)
3. For local dev, use `http://localhost:18890` (the host-mapped port)

### Docker build is slow / cache invalidation

**Cause**: Changing `pyproject.toml` invalidates the dependency install layer.

**Fix**: The Dockerfile is structured so dependency installation is cached independently from source code. If you only changed Python source files, the `uv sync` layer is reused. Avoid modifying `pyproject.toml` unless adding/removing dependencies.

### "Permission denied" running dev.sh

**Fix**:

```bash
chmod +x scripts/dev.sh
```
