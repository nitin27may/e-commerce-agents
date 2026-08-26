# Troubleshooting

Common issues running the stack locally. Start everything with `./scripts/dev.sh`
(or `./scripts/dev.ps1` on Windows — every `dev.sh --flag` below has a `dev.ps1 -Flag`
equivalent: `--clean` → `-Clean`, `--seed-only` → `-SeedOnly`, `--infra-only` → `-InfraOnly`)
(see [deployment.md](./deployment.md)).

## Docker build fails on the Python agents (`agent-framework` resolution)

Symptom: a wall of `agent-framework … cannot be used` / `agent-framework-azure-ai-search`
conflicts during `uv sync`. Cause: re-resolving the pre-release MAF graph against
live PyPI. **Fix is already in the Dockerfile** — it syncs from the committed
`agents/python/uv.lock` with `uv sync --frozen`. If you hit this, ensure
`uv.lock` is present and run a clean build:

```bash
./scripts/dev.sh --clean        # nuke volumes + rebuild
# or
docker compose build --no-cache orchestrator
```

To refresh deps deliberately: `cd agents/python && uv lock` (commit the new lock).

## Port already in use (5432 / 6379 / 8080 / 3000 / 18888)

Another stack (or a host Postgres/Redis) holds the port. Find and stop it:

```bash
lsof -nP -iTCP:5432 -sTCP:LISTEN     # who's listening
docker compose down                   # stop this stack
```

The compose services use 5432 (Postgres), 6379 (Redis), 8080 (orchestrator),
8081–8085 (specialists), 3000 (frontend), 18888 (Aspire).

## Chat returns an error / "encountered an issue"

The LLM is never mocked. Set a real key in the repo `.env`:

```bash
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
LLM_MODEL=gpt-4.1
```

(or the `AZURE_OPENAI_*` vars for Azure). Restart the orchestrator + agents.

## Login fails with "Missing Authorization header"

You're hitting the wrong backend, or the orchestrator is pointed at a different
DB. Confirm `:8080` is the e-commerce orchestrator (`curl localhost:8080/health`
→ `{"service":"orchestrator"}`) and that the DB was seeded (`./scripts/dev.sh --seed-only`).

## Public storefront shows no products / redirects to login

Product browse + chat are anonymous via `optional_auth`. If anonymous
`GET /api/products` returns 401, the orchestrator image predates that change —
rebuild it: `docker compose up -d --build --no-deps orchestrator`.

## DB connection refused / empty data

Postgres not ready or not seeded. `docker compose ps` (db healthy?), then
`./scripts/dev.sh --seed-only`. The seeder is deterministic (`random.seed(42)`).

## Embeddings missing (semantic search empty)

```bash
cd agents/python && uv run python -m scripts.generate_embeddings
```

## `products.search_vector does not exist`

Symptom: every product search fails and the agent replies "there was an error
retrieving results from the database". The agent logs show
`UndefinedColumnError: column p.search_vector does not exist`.

Cause: full-text search added a generated `tsvector` column to `products`.
`docker/postgres/init.sql` only runs on an **empty** data directory, so a
database created before that change never got the column.

Fix, without losing your data:

```bash
docker compose exec -T db psql -U ecommerce -d ecommerce_agents <<'SQL'
ALTER TABLE products ADD COLUMN IF NOT EXISTS search_vector tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(name, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(brand, '')), 'B') ||
        setweight(to_tsvector('english', coalesce(description, '')), 'C')
    ) STORED;
CREATE INDEX IF NOT EXISTS idx_products_search ON products USING GIN (search_vector);
SQL
```

Postgres backfills the column for every existing row, so no re-seed is needed.
Verify with:

```bash
docker compose exec -T db psql -U ecommerce -d ecommerce_agents \
  -c "SELECT count(*) AS products, count(search_vector) AS indexed FROM products;"
```

Alternatively `./scripts/dev.sh --clean` rebuilds from `init.sql` — simpler, but
it drops all local data.

## UI changes not showing in the running stack

The frontend is a built container. Rebuild it:
`docker compose up -d --build --no-deps frontend` (or run `cd web && pnpm dev`
on a free port).

## Aspire dashboard empty (no traces)

Open http://localhost:18888. Ensure `OTEL_ENABLED` is on and the OTLP endpoint
points at the Aspire container. See [telemetry.md](./telemetry.md).
