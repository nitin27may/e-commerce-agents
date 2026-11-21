---
title: Azure deployment (draft)
---

# Azure deployment (draft)

Bicep templates and scripts that deploy the Python stack to Azure Container Apps, for anyone who wants to run the demo in their own subscription.

**Status: written, compiled and linted, never deployed.** Everything here was checked offline: the Bicep builds and lints, the compiled wiring is asserted by `agents/python/tests/test_infra_bicep.py`, the scripts pass `bash -n` and a dry run, and the secret generator has its own test. **Nothing has run against a real subscription**, and every behaviour that needs one is listed under [Assumptions to confirm](#assumptions-to-confirm-on-a-real-subscription). Python stack only: the .NET images are not published. For local and Compose paths see [deployment.md](deployment.md).

## Deploy

```bash
az login && azd auth login
scripts/azure-up.sh --env demo1 --location eastus2 --image-tag v1.4.0     # PowerShell: scripts/azure-up.ps1 -EnvName ...
# when you are done (the demo costs roughly 4 to 12 USD a day while it is up):
scripts/azure-down.sh --env demo1
```

`azure-up` runs `scripts/azure/prereqs.sh`, creates the azd environment, then `azd up`. The azd hooks do the rest: `preprovision` generates secrets and records your IP, `postprovision` runs the database job, prints the login and runs the smoke test. The frontend is reachable **only from your public IP** unless you pass `--public-open` or `--allowed-ips`. The admin, support and seller accounts get a generated password (`azd env get-value SEED_STAFF_PASSWORD`); customers keep the published demo passwords, so keep the frontend restricted.

## Topology

```mermaid
flowchart LR
    user([Browser]):::ext --> web
    subgraph rg["rg-&lt;env&gt;"]
        subgraph ca["Container Apps environment"]
            web["ca-frontend<br/>external, IP restricted"]:::core --> orch[ca-orchestrator]:::core
            orch -- "https, internal FQDN" --> spec["ca-product-discovery, order-management,<br/>pricing-promotions, review-sentiment,<br/>inventory-fulfillment"]:::core
            orch --> redis[("ca-redis<br/>internal TCP")]:::core
            worker[ca-outbox-worker]:::core
            job[["job-db-init<br/>manual"]]:::infra
        end
        pg[(PostgreSQL Flexible Server 16 + pgvector)]:::core
        oai[Azure OpenAI - AIServices]:::ext
        kv[Key Vault - RBAC]:::sec
        uami[User-assigned identity]:::sec
        log[Log Analytics + App Insights]:::infra
        bud[Budget]:::infra
    end
    orch --> pg
    spec --> pg
    worker --> pg
    job --> pg
    spec -- key or Entra ID --> oai
    uami -- Secrets User --> kv
    ca -. "secretRef via identity" .-> kv
    ca -. logs, OTel .-> log

    classDef core fill:#0b5cab,stroke:#08427a,color:#ffffff
    classDef ext fill:#f2a900,stroke:#a87400,color:#1a1a1a
    classDef sec fill:#c0392b,stroke:#8e2a20,color:#ffffff
    classDef infra fill:#6b7280,stroke:#4b5563,color:#ffffff
```

## What it creates

| Resource | Name | Notes |
|---|---|---|
| Resource group | `rg-<environmentName>` | tagged `azd-env-name` |
| Log Analytics + App Insights | `log-<token>`, `appi-<token>` | daily ingestion cap from the profile |
| User-assigned identity | `id-<token>` | resolves Key Vault references; Entra caller for the model when `AZURE_OPENAI_AUTH=identity` |
| Key Vault | `kv-<token>` | RBAC; holds every secret listed below |
| PostgreSQL Flexible Server 16 | `psql-<token>` | database `ecommerce_agents`, admin `ecomadmin`, `azure.extensions = VECTOR,PGCRYPTO`, TLS required |
| Azure OpenAI (AIServices) | `ai-<token>` | `chat` (default gpt-5.1) and `embedding` (text-embedding-3-small); optional `chat-mini` |
| Container Apps environment | `cae-<token>` | Consumption profile, Log Analytics, managed OpenTelemetry to App Insights |
| Container apps | `ca-frontend`, `ca-orchestrator`, `ca-product-discovery`, `ca-order-management`, `ca-pricing-promotions`, `ca-review-sentiment`, `ca-inventory-fulfillment`, `ca-outbox-worker`, `ca-redis` | no token in the name (32 character limit); opt-in: `ca-auth-server`, `ca-mcp-product`, `ca-mcp-inventory` |
| Container Apps job | `job-db-init` | manual trigger, 15 minute timeout |
| Budget | `budget-<environmentName>` | notifies subscription Owners and Contributors, no email in the repo |

`<token>` is `toLower(uniqueString(subscription().id, environmentName, location))`, 13 characters.

## Wiring

Images come from `ghcr.io/nitin27may/e-commerce-agents/<service>:<imageTag>`. The tag is pinned (`IMAGE_TAG`, no default; `latest` and `main` are refused) so a re-deploy never changes what runs. The GHCR packages must be public.

| Concern | Value |
|---|---|
| Secrets | `database-url`, `jwt-secret`, `agent-shared-secret`, `a2a-identity-key`, `oauth-seed-key`, `auth-signing-key-encryption-key`, `azure-openai-key` (key auth only), `seed-staff-password` (job only). Each is a Key Vault reference (`keyVaultUrl` plus the identity), consumed through `secretRef`. No secret is a Bicep output |
| `ENVIRONMENT=production` | turns the secret validator on, so the worker and the job carry the whole set; a placeholder crash-loops them |
| Registry | `AGENT_REGISTRY` is `https://ca-<svc>.internal.<defaultDomain>` per specialist; plain http is redirected by the environment and an A2A POST does not survive that |
| Frontend | `ORCHESTRATOR_URL=https://ca-orchestrator.internal.<domain>`, `HOSTNAME=0.0.0.0`, `WEB_TRUSTED_PROXY_HOPS=1` |
| Orchestrator | `CORS_ALLOWED_ORIGINS` is the frontend URL, `RATE_LIMIT_TRUSTED_PROXY_HOPS=2` |
| Specialists | `A2A_PUBLIC_BASE_URL` is each one's own internal URL |
| Timeouts | `MAF_STREAM_TIMEOUT_SECONDS=120`, `HITL_RESUME_TIMEOUT_SECONDS=200`, both under the 240 s ingress limit |
| Spend | `COST_BUDGET_MODE=enforce`, `COST_BUDGET_USD_PER_RUN=0.50`, a TPM cap in the profile, a budget alert |
| Model | `LLM_PROVIDER=azure`; `LLM_TEMPERATURE` is empty for gpt-5.x (they reject it) |
| Telemetry | `OTEL_ENABLED=true`; `OTEL_EXPORTER_OTLP_ENDPOINT` is never set, the managed OpenTelemetry agent injects it. Traces and logs reach App Insights, metrics do not |
| Scale | every app `minReplicas >= 1` (a Python cold start can exceed the 60 s A2A timeout), orchestrator scales to 3 on 20 concurrent requests, worker is one replica |
| Probes | startup and liveness on `/health`, readiness on `/ready` (503 until `job-db-init` has migrated), all inside the Container Apps limits |

## Profiles

`infra/profiles/demo.json` and `reference.json` carry the SKUs and counts; pick one with `DEPLOY_PROFILE` (default `demo`). The reference profile still deploys the same Container Apps topology: the VNet, private Postgres and Managed Redis are not built.

| | demo | reference |
|---|---|---|
| PostgreSQL | Burstable B1ms, 32 GB, no HA | General Purpose D2ds_v5, 128 GB, zone-redundant HA, geo-redundant backup |
| Key Vault | no purge protection, 7 day soft delete | purge protection, 90 day soft delete |
| Log Analytics cap | 1 GB/day, 30 days | 5 GB/day, 90 days |
| OpenAI capacity (K TPM) | chat 50, embedding 20 | chat 100, embedding 50 |
| Replicas | 1 to 2 (orchestrator 1 to 3) | 2 to 6 (orchestrator 2 to 10) |
| Budget | 150 / month | 800 / month |

Cost (estimates, not measured): demo roughly 120 to 380 USD a month, about 0.02 to 0.03 USD per chat turn; reference 500 to 800.

## azd environment variables

Set by `azure-up` or by hand with `azd env set`.

| Variable | Default | Meaning |
|---|---|---|
| `AZURE_LOCATION`, `AZURE_OPENAI_LOCATION` | required, location | region for everything; region for the model account |
| `DEPLOY_PROFILE` | `demo` | `demo` or `reference` |
| `IMAGE_TAG` | none | pinned GHCR tag, `vX.Y.Z` or `sha-<7>` |
| `PUBLIC_ACCESS` | `restricted` | `open` lets anyone reach the frontend |
| `DEPLOYER_IP`, `ALLOWED_IPS` | detected, empty | `/32` allowed through the frontend ingress; extra CIDRs, comma separated. Restricted with neither allows nobody |
| `AZURE_CHAT_MODEL`, `AZURE_CHAT_MODEL_VERSION` | `gpt-5.1`, `2025-11-13` | gpt-4.1 is deprecated and closed to new customers |
| `AZURE_OPENAI_API_VERSION` | `2025-04-01-preview` | API version the apps call |
| `AZURE_OPENAI_AUTH` | `key` | `key` or `identity` (needs an image built with `AZURE_OPENAI_AUTH` support; disables local auth on the account) |
| `DEPLOY_MINI_MODEL`, `DEPLOY_AUTH_SERVER`, `DEPLOY_MCP` | `false` | opt-in model deployment and apps |
| `POSTGRES_ADMIN_PASSWORD`, `JWT_SECRET`, `AGENT_SHARED_SECRET`, `A2A_IDENTITY_KEY`, `OAUTH_SEED_KEY`, `AUTH_SIGNING_KEY_ENCRYPTION_KEY`, `SEED_STAFF_PASSWORD` | generated | `preprovision` creates each only if unset and never overwrites. Postgres password: 32 alphanumerics with three character classes (no URL encoding in `DATABASE_URL`). Staff password: 20 alphanumerics. The rest: 64 hex characters, `A2A_IDENTITY_KEY` distinct from `AGENT_SHARED_SECRET` |

`.azure/` (azd's environment store, which holds these secrets in clear text) is git-ignored. Do not copy it into a repo.

## Offline checks

```bash
cd infra
bicep build main.bicep && bicep lint main.bicep
for f in modules/*.bicep; do bicep lint "$f"; done
for f in ci/*.bicepparam; do bicep build-params "$f" --stdout > /dev/null; done
cd ../agents/python && uv run pytest tests/test_infra_bicep.py      # compiles, snapshots every fixture, asserts the env-var table
cd ../.. && scripts/test-azure-secrets.sh                            # secret formats, idempotence, never overwrite
scripts/azure-up.sh --env demo1 --location eastus2 --image-tag v1.4.0 --dry-run   # prints every az/azd command, runs none
```

`bicep snapshot` evaluates a `.bicepparam` against `main.bicep` locally, which is how the tests see real values without a subscription. `.github/workflows/infra-offline.yml` and `azure.yml` run the same on pull requests, with no Azure credentials. PowerShell scripts (`*.ps1`) mirror the bash ones; they have not been parse-checked where `pwsh` is not installed.

## Gotchas and known issues

- **The apps start before the schema exists.** Container Apps has no `depends_on`; services answer 503 on `/ready` until `job-db-init` finishes. `postprovision` starts the job and waits for it. Whether the ARM PUT blocks on readiness is unverified; if it does, move `migrate` into an orchestrator init container.
- **Serialisation.** Model deployments on one account, and server parameters plus the database on one Flexible Server, are chained with `dependsOn`. Parallel writes fail with busy or conflict errors.
- **Purge protection is one way.** The demo profile omits it. A soft-deleted vault or model account keeps its name until purged: `azure-down` purges and then verifies.
- **Redis** is `redis:8-alpine` with no persistence and no password, reachable only inside the environment. Rate limiting fails open without it. Azure Cache for Redis is closed to new customers.
- **Consumption egress IPs are not static.** Matters only for allow-listing from outside.
- **`POST /api/orchestration/compare`** can run past the 240 s ingress limit and is best-effort.
- **Key auth first.** With `AZURE_OPENAI_AUTH=key` the account key is copied into Key Vault by the Bicep (`listKeys`), so it appears in the deployment history of the resource group. Identity auth removes it; flip the default only after a verified Azure run.
- **Budget start date** defaults to the first of the current month via `utcNow`, so redeploying in a later month moves it.
- **Troubleshooting:** the first-run failure catalogue and its guards are in [troubleshooting.md](troubleshooting.md#azure-deployment-first-run-failure-catalogue).

## Assumptions to confirm on a real subscription

- gpt-5.1 `2025-11-13` accepts the requested capacity in the region; a `gpt-5.1-mini` name exists for `DEPLOY_MINI_MODEL`. text-embedding-3-small `1` accepts `GlobalStandard` (else set `embeddingSkuName`).
- The image tag contains the settings these templates rely on: an empty `LLM_TEMPERATURE` omits the field, and `DB_POOL_*`. An older image fails settings validation on an empty `LLM_TEMPERATURE`.
- `AZURE_OPENAI_API_VERSION` `2025-04-01-preview` supports gpt-5.1 tool calling on the Responses API.
- An empty environment-variable value is accepted by Container Apps (`LLM_TEMPERATURE`).
- `Microsoft.App/managedEnvironments@2025-02-02-preview` accepts `appInsightsConfiguration` and `openTelemetryConfiguration`, and the Python SDK's exporter accepts the endpoint the agent injects.
- Key Vault references with the identity resolve on first revision creation after one RBAC propagation retry; versionless `keyVaultUrl` is accepted.
- Internal ingress FQDN is `<app>.internal.<defaultDomain>`; internal TCP ingress is reachable as `ca-redis:6379`; a `command` override on the Redis image works.
- `RATE_LIMIT_TRUSTED_PROXY_HOPS=2` gives each client its own bucket; `WEB_TRUSTED_PROXY_HOPS=1` is right for the Container Apps ingress.
- The MCP servers answer 2xx on `/mcp` (or the protected-resource metadata) for probes, and accept the internal FQDN as `Host`.
- azd evaluates `main.parameters.json` after the `preprovision` hook (the generated secrets reach the deployment).
- `az cognitiveservices usage list`, `model list`, `keyvault list-deleted` and `account list-deleted` return the JSON shapes `scripts/azure/lib/checks.sh` parses.
- `AZURE_PRINCIPAL_ID` is set by azd for a signed-in user; the deployer role assignment is skipped when it is empty.
- The AllowAllAzureServices firewall rule and the `source` value `user-override` for `azure.extensions` are accepted.
- Budget notifications with `contactRoles` and no `contactEmails` are accepted.
