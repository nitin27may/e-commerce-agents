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
uv run --project agents/python python -m scripts.generate_embeddings
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

## Rolled back to an older checkout after running on `agent-framework-core` 1.22

Core 1.22 reads everything earlier releases stored, but the chat history and
workflow checkpoints it writes can carry fields (`hosted`, `merge_mode`) that
pre-1.22 runtimes cannot read. If you move a database that has already been used
on 1.22 back to an older checkout and resuming a run or loading a conversation
fails, rebuild the local data with `./scripts/dev.sh --clean`. Upgrading needs
no such step.

## UI changes not showing in the running stack

The frontend is a built container. Rebuild it:
`docker compose up -d --build --no-deps frontend` (or run `cd web && pnpm dev`
on a free port).

## Aspire dashboard empty (no traces)

Open http://localhost:18888. Ensure `OTEL_ENABLED` is on and the OTLP endpoint
points at the Aspire container. See [telemetry.md](./telemetry.md).

## Azure deployment: first-run failure catalogue

Twenty-nine ways a first `scripts/azure-up.sh` goes wrong, and what already guards against each. None of this has run against a real subscription yet ([azure-deployment.md](azure-deployment.md) lists the assumptions), so the guards are the intent, not a track record. When one fires, the message names the fix.

| # | Failure | Symptom | Guard |
|---|---|---|---|
| 1 | gpt-4.1 deployment refused | model deployment fails: deprecated, closed to new customers | default is gpt-5.1 (2025-11-13); `check_model` rejects a Deprecated or Deprecating lifecycle before provisioning |
| 2 | gpt-5.x answers 400 on `temperature` | every chat turn fails | `LLM_TEMPERATURE` is empty for `gpt-5*` model names (Bicep derives it); the app omits the field when empty (Azure P2) |
| 3 | No TPM quota | deployment fails with `InsufficientQuota` | `check_model` reads `az cognitiveservices usage list`; use `AZURE_OPENAI_LOCATION` or lower `chatCapacity` in the profile |
| 4 | Parallel model deployments conflict | `RequestConflict` on the second deployment | `dependsOn` chain chat, embedding, mini (asserted in `test_infra_bicep.py`) |
| 5 | Postgres offer-restricted in the region | `SkuNotAvailable` or the SKU is missing from the portal | `check_postgres_sku` lists `az postgres flexible-server list-skus` and prints alternates |
| 6 | `CREATE EXTENSION vector` not allowed | migrate fails: extension is not allow-listed | `azure.extensions=VECTOR,PGCRYPTO` is set before the database and the job (test asserts the order) |
| 7 | Postgres password rejected or breaks the URL | provisioning rejects it, or `DATABASE_URL` mis-parses | generator: 32 alphanumerics with upper, lower and digit; no URL encoding needed (`scripts/test-azure-secrets.sh`) |
| 8 | Postgres unreachable or no TLS | `no pg_hba.conf entry` / SSL required | `AllowAllAzureServices` firewall rule (demo), `sslmode=require` in `DATABASE_URL`, `require_secure_transport` on; the .NET stack honours `sslmode` (Azure P2) |
| 9 | `max_connections` exhausted | `too many connections` under load | `DB_POOL_MIN_SIZE=1`, `DB_POOL_MAX_SIZE=4` on every app (Azure P2) |
| 10 | Key Vault RBAC propagation | revision fails to start: secret reference cannot be resolved, minutes after the role assignment | role assignment is in the Key Vault module, which the apps depend on; `azure-up` retries `azd provision` once after 60 s |
| 11 | Deployer lacks role-assignment rights | `AuthorizationFailed` on `roleAssignments/write` | `prereqs` checks for Owner, or Contributor plus User Access Administrator, before anything is created |
| 12 | Soft-deleted name collision | `VaultAlreadyExists` / `FlagMustBeSetForRestore` on redeploy | `check_soft_deleted` in prereqs and preprovision; `azure-down` purges and verifies; no purge protection in the demo profile |
| 13 | Worker or job crash-loops on placeholder secrets | `ENVIRONMENT=production` refuses a short or default secret | worker and job get the full Key Vault secret set; the generator never emits a placeholder (asserted in the tests) |
| 14 | Re-run wipes data | `seed.py` truncates when users exist | the job runs `seed --if-empty`; it never truncates in Azure |
| 15 | Semantic search silently falls back to full text | relevant products missing from search | `db_init` embeds when `product_embeddings` is empty and a provider is configured; smoke asserts the count |
| 16 | IVFFlat index built before data | poor recall | `REINDEX` after the embeddings step |
| 17 | A2A call redirected http to https | specialist calls fail or lose the POST body | registry uses `https://ca-<svc>.internal.<defaultDomain>` (asserted in the tests) |
| 18 | Wrong `ORCHESTRATOR_URL` | frontend logs in, every API call 502s | built by Bicep from the environment domain; smoke logs in through the proxy |
| 19 | One shared rate-limit bucket | everyone gets 429 after a few requests | `RATE_LIMIT_TRUSTED_PROXY_HOPS=2` on the orchestrator, `WEB_TRUSTED_PROXY_HOPS=1` on the frontend; verify with two client IPs on the first deploy |
| 20 | SSE buffered, or cut at 240 s | chat appears all at once, or ends mid-stream | `MAF_STREAM_TIMEOUT_SECONDS=120`, `HITL_RESUME_TIMEOUT_SECONDS=200`; smoke times the first frame (under 15 s) and counts frames (over 10) |
| 21 | Specialist cold start over the 60 s A2A timeout | first request after idle fails | every app `minReplicas >= 1` |
| 22 | Public URL abused | someone else spends your model quota | frontend ingress restricted to the deployer IP, generated staff passwords, `COST_BUDGET_MODE=enforce`, TPM cap, budget alert. `PUBLIC_ACCESS=open` warns loudly |
| 23 | Azure Policy denies public Postgres or local auth | deployment denied by a policy assignment | run `azd provision --preview` first; the reference profile (private networking, later PR) is the fallback; `AZURE_OPENAI_AUTH=identity` disables local auth |
| 24 | Job log lag | `az containerapp job logs show` is empty right after a failure | `postprovision` prints the command and the Log Analytics query instead of the log; wait a minute or two |
| 25 | No metrics in App Insights, or telemetry goes nowhere | traces appear, metrics do not; or an exporter error at startup | managed OpenTelemetry carries traces and logs only (documented); no app sets `OTEL_EXPORTER_OTLP_ENDPOINT`, the agent injects it; a setting of ours would override it |
| 26 | Agent card or product card links point at the wrong origin under `A2A_TRANSPORT=a2a` | callers get a card naming `localhost` | `A2A_PUBLIC_BASE_URL` is set per specialist to its own internal URL |
| 27 | Next.js listens on the wrong address | ingress health check fails, 502 from the frontend | `HOSTNAME=0.0.0.0` and `PORT=3000` on `ca-frontend` |
| 28 | Services answer 503 on `/ready` right after provisioning | readiness probe failing before the database job | expected until `job-db-init` finishes; `postprovision` waits for it. Only readiness depends on `/ready`; liveness and startup use `/health`, so nothing restarts |
| 29 | `POST /api/orchestration/compare` times out | 504 from the ingress on a long comparison | documented best-effort: the request can exceed the 240 s ingress limit; run fewer modes per call |

Other things to try first: `az containerapp revision list --name ca-orchestrator --resource-group rg-<env> -o table` for revision health, `az containerapp logs show --name ca-orchestrator --resource-group rg-<env> --follow false`, and `azd env get-values` for what the hooks saw (it contains secrets; do not paste it).
