---
title: Azure deployment (draft)
---

# Azure deployment (draft)

Bicep templates and scripts that deploy the Python stack to Azure Container Apps, for anyone who wants to run the demo in their own subscription.

**Status: written, compiled and linted, never deployed.** Everything here was checked offline: the Bicep builds and lints, the compiled wiring is asserted by `agents/python/tests/test_infra_bicep.py`, the scripts pass `bash -n` and a dry run, and the secret generator has its own test. **Nothing has run against a real subscription**, and every behaviour that needs one is listed under [Assumptions to confirm](#assumptions-to-confirm-on-a-real-subscription). The Python stack is the default; the .NET stack (`--stack dotnet`) is deployable too, verified offline only. For local and Compose paths see [deployment.md](deployment.md).

**Azure is optional.** Nothing outside `infra/`, `azure.yaml`, `scripts/azure*` and `.github/workflows/azure.yml` needs it. The default dev, demo and eval paths run with `LLM_PROVIDER=openai`, `replay` or any OpenAI-compatible endpoint and never touch Azure.

## First deploy on a new subscription

A checklist for a clean subscription. It has not been run end to end yet: [what was and was not verified](#assumptions-to-confirm-on-a-real-subscription).

**You need**

- An Azure subscription where you are **Owner** (or Contributor plus User Access Administrator): the template creates role assignments (Key Vault Secrets User, Cognitive Services OpenAI User). A pay-as-you-go or free-trial account is fine; a free trial may refuse the model quota or the Postgres SKU, and then the preflight says so before anything is created.
- `az` 2.67 or newer, `azd` 1.18 or newer (`azd version`), Bicep 0.34 or newer (`az bicep install`), `jq`, `curl`, and `bash` (or PowerShell with the `.ps1` mirrors). The released images are public on GHCR, so no Docker is needed.
- Rough cost: the demo profile is about 4 to 12 USD a day while it is up (these are estimates, not measurements). The reference profile (zone-redundant Postgres, Managed Redis, private endpoints, ACR) costs several times that. A budget alert is created, but it only notifies; it does not stop spending. Run the teardown when you are done.

**Commands, in order**

```bash
az login                                     # opens a browser; then, if you have several subscriptions:
az account set --subscription <subscription-id>
azd auth login                               # azd keeps its own sign-in; az login does not cover it

scripts/azure-up.sh --env <env> --location <region> --image-tag v1.4.0 --dry-run   # prints every command, changes nothing
scripts/azure-up.sh --env <env> --location <region> --image-tag v1.4.0             # PowerShell: scripts/azure-up.ps1 -EnvName ...
```

`<env>` is lowercase letters, digits and dashes (it names the resource group `rg-<env>`). Pick a `<region>` where the subscription can create Postgres Flexible Server and where `gpt-5.1` has quota (`eastus2` and `swedencentral` are common). `--image-tag` must be a published tag; `v1.4.0` is the latest release. Add `--openai-location <region>` to put only the model account in another region.

**What each step does**

| Step | What happens | Creates | Typical time |
|---|---|---|---|
| `prereqs.sh` | Checks tools and both sign-ins, registers resource providers and waits for them, checks you can create role assignments, checks the Postgres SKU, model availability and quota (chat and embeddings), and soft-deleted names | Nothing except provider registrations and the `containerapp` CLI extension | Under a minute; 2 to 10 minutes on a subscription that has never registered the providers |
| azd environment | `azd env new` pinned to the active subscription; the settings go into `.azure/<env>/.env` | Local files only | Seconds |
| `preprovision` hook | Generates every secret once (never overwritten), records your public IP for the frontend allow-list, repeats the region checks | Local files only | Seconds |
| `azd provision` | Deploys `infra/main.bicep` at subscription scope | Resource group, Log Analytics, App Insights, identity, Key Vault, Postgres + pgvector, the Azure OpenAI account with chat and embedding deployments, Container Apps environment, 8 apps, the db-init job, budget (reference profile adds a VNet, private endpoints, Managed Redis and ACR, in two passes) | 15 to 30 minutes (estimate; Postgres and the model account dominate) |
| `postprovision` hook | Starts `job-db-init` (migrations, seed, embeddings) and waits, prints the login, runs the quick smoke test (waits up to 5 minutes for revisions and the frontend) | Database content | 5 to 15 minutes (the embeddings step dominates) |

When it finishes it prints the frontend URL. Sign in as `admin.demo@gmail.com` with `azd env get-value SEED_STAFF_PASSWORD`. The frontend answers only your public IP; if it changes, `azd env set DEPLOYER_IP <ip>` and run the script again (it is idempotent: secrets, environment and resources are reused).

**If a step fails:** read the message, fix the cause and re-run the same command. The failures a new subscription meets are: a resource provider still registering (the script waits up to 20 minutes, `PROVIDER_WAIT_SECONDS`), no `gpt-5.1` quota (request it, or `AZURE_CHAT_MODEL=<name> AZURE_CHAT_MODEL_VERSION=<version> scripts/azure-up.sh ...`), a region that restricts Postgres (pick another `--location`), and a soft-deleted name from an earlier attempt (`scripts/azure-down.sh --env <env> --purge-only`). More in [troubleshooting.md](troubleshooting.md#azure-deployment-first-run-failure-catalogue).

**Tear down** (everything, including the soft-deleted Key Vault and model account that would block a re-deploy):

```bash
scripts/azure-down.sh --env <env>            # azd down --purge --force, purges leftovers, then proves rg-<env> is gone
```

The reference profile turns on Key Vault purge protection, which cannot be undone: after teardown its vault stays soft-deleted for the retention period (90 days) and **that `--env` name cannot be deployed again until then**. Use a new name. The teardown reports this and exits 0, because nothing is billed for it.

## Deploy

```bash
az login && azd auth login
scripts/azure-up.sh --env demo1 --location eastus2 --image-tag v1.4.0     # PowerShell: scripts/azure-up.ps1 -EnvName ...
# when you are done (the demo costs roughly 4 to 12 USD a day while it is up):
scripts/azure-down.sh --env demo1
```

`azure-up` runs `scripts/azure/prereqs.sh`, creates the azd environment, then `azd provision` (the template declares no services, so `azd up` would add nothing). The azd hooks do the rest: `preprovision` generates secrets and records your IP, `postprovision` runs the database job, prints the login and runs the smoke test. The frontend is reachable **only from your public IP** unless you pass `--public-open` or `--allowed-ips`. The admin, support and seller accounts get a generated password (`azd env get-value SEED_STAFF_PASSWORD`); customers keep the published demo passwords, so keep the frontend restricted.

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
        apim["API Management gateway<br/>optional: deployGateway"]:::core
        proj["Foundry project<br/>optional: deployFoundryProject"]:::core
        appids["Per-app identities<br/>optional: deployPerAppIdentities"]:::sec
    end
    orch --> pg
    spec --> pg
    worker --> pg
    job --> pg
    spec -- key or Entra ID --> oai
    uami -- Secrets User --> kv
    ca -. "secretRef via identity" .-> kv
    ca -. logs, OTel .-> log
    spec -. "AZURE_OPENAI_ENDPOINT when the gateway is on" .-> apim
    apim -- "gateway managed identity,<br/>token limits per agent" --> oai
    apim -. "token metrics" .-> log
    proj -- "child of the account" --- oai
    log -. "App Insights connection" .-> proj
    appids -. "oid vouches for the agent name" .-> apim

    classDef core fill:#0b5cab,stroke:#08427a,color:#ffffff
    classDef ext fill:#f2a900,stroke:#a87400,color:#1a1a1a
    classDef sec fill:#c0392b,stroke:#8e2a20,color:#ffffff
    classDef infra fill:#6b7280,stroke:#4b5563,color:#ffffff
```

## Foundry project (optional)

`DEPLOY_FOUNDRY_PROJECT=true` (Bicep `deployFoundryProject`, default false) adds `modules/foundry-project.bicep`: a Foundry project (`Microsoft.CognitiveServices/accounts/projects`) under the same AIServices account, the shared Application Insights component connected to it (so the traces the apps already export show in the project's tracing view), and one role for the app identity, **Azure AI User** on the project. That role carries the data actions a caller needs (run agents, create evaluation runs, read project resources) and none of the management ones: not Azure AI Project Manager, not Contributor. Model calls stay covered by Cognitive Services OpenAI User on the account. Turning the flag on also sets `allowProjectManagement` and a system-assigned identity on the account (the API refuses a project otherwise); with the flag off the account is exactly what it was. Outputs: `AZURE_AI_PROJECT_NAME` and `AZURE_AI_PROJECT_ENDPOINT` (`https://<account>.services.ai.azure.com/api/projects/<project>`); no secret.

**The `azure` provider is Foundry for OpenAI models.** The account this template creates is a Foundry resource (kind `AIServices`), so the `azure` provider pointed at its endpoint already is Foundry for OpenAI models: the same deployments, the same managed-identity or key auth. We do not add `LLM_PROVIDER=foundry`; one is only warranted for a non-OpenAI catalogue model or a project-scoped client, and neither is needed yet.

## AI gateway (optional)

`DEPLOY_GATEWAY` (Bicep `deployGateway`, default false) adds `modules/gateway-apim.bicep`: an API Management instance with one API (`/openai`) in front of the AIServices account. The apps keep `LLM_PROVIDER=azure`; only `AZURE_OPENAI_ENDPOINT` changes, to the `gatewayEndpoint` output (also `AZURE_GATEWAY_ENDPOINT`). No app code or `apps.bicep` change: `main.bicep` hands `apps` the gateway URL through its existing `openAiEndpoint` parameter.

What the policy (`modules/gateway-policy.xml`) does on every call:

- **Per-agent token limits** with `azure-openai-token-limit`, counted per agent name. Set limits with `gatewayAgentTokenLimits` (`{ orchestrator: 120000 }`); the rest get `gatewayDefaultTokensPerMinute` (60000). A name that is not one of the six apps and the worker shares one bucket called `unlisted`, so a made-up header cannot mint a fresh budget.
- **Per-agent token metrics** with `azure-openai-emit-token-metric` to Application Insights (namespace `ecommerce_agents`, dimensions `Agent`, `AgentTrusted`). Request and response bodies are not logged.
- **Managed identity to the account.** The gateway's system-assigned identity holds Cognitive Services OpenAI User on the account; the caller's `api-key` is deleted before the call.
- **Who the caller is.** With `AZURE_OPENAI_AUTH=identity` the gateway validates the caller's Entra token (`validate-azure-ad-token`, audience `https://cognitiveservices.azure.com`, only the app identities' client ids). With `key` (the default) the apps send an APIM subscription key as `api-key`; the gateway module stores it as the `azure-openai-key` secret, replacing the account key the Bicep otherwise copies there.
- **Which agent.** The apps' model client sends `x-agent-name` (added in a separate change). A header is a claim. When the caller's token belongs to one of the per-app identities the gateway uses that identity's agent name instead and ignores the header (`AgentTrusted=true`). Until the apps use their own identities every call is header-asserted (`AgentTrusted=false`).

Parameters: `GATEWAY_SKU` / `gatewaySku` (default `BasicV2`), `gatewayPublisherEmail` (APIM insists on one; the default is a placeholder, set a real address for anything lasting).

Cost and time: **UNVERIFIED offline.** v2 tiers provision in minutes and are the cheap end of the tiers that might support the token policies; classic tiers (`Developer`, `Basic`, `Standard`, `Premium`) take 30 to 45 minutes or more to create and to delete. Check the price on the Azure pricing page before a deploy and run `azure-down` afterwards. The first deployed run must also record which tier accepts the policy (plan item M6.2).

## Per-app identities (design)

`DEPLOY_PER_APP_IDENTITIES` (Bicep `deployPerAppIdentities`, default false; intended for the reference profile) adds `modules/app-identities.bicep`: seven user-assigned identities (`id-<token>-<app>`: orchestrator, the five specialists, outbox-worker), each with Key Vault Secrets User, and Cognitive Services OpenAI User on the account only when there is no gateway. Output `AZURE_APP_IDENTITIES` lists `{ app, id, clientId, principalId }`.

**They are attached to the apps.** With the flag on, the orchestrator, the five specialists and the outbox worker run as their own identity: it is the app's `identityId`, resolves its Key Vault references, and its client id is the app's `AZURE_CLIENT_ID`. The db-init job, the frontend, Redis, the auth server and the MCP servers keep the shared identity. When the image source is ACR, every per-app identity also gets `AcrPull` (the registry pull uses the app's own identity). The gateway can then treat each app's calls as trusted rather than header-asserted. `test_apps_run_as_their_own_identity_when_per_app_identities_are_on` pins it.

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

`infra/profiles/demo.json` and `reference.json` carry the SKUs and counts; pick one with `DEPLOY_PROFILE` (default `demo`). The reference profile adds a VNet, private endpoints, Azure Managed Redis and a Container Registry (details below). Both profiles are written and compiled offline; neither has been deployed.

| | demo | reference |
|---|---|---|
| PostgreSQL | Burstable B1ms, 32 GB, no HA | General Purpose D2ds_v5, 128 GB, zone-redundant HA, geo-redundant backup |
| Key Vault | no purge protection, 7 day soft delete | purge protection, 90 day soft delete |
| Log Analytics cap | 1 GB/day, 30 days | 5 GB/day, 90 days |
| OpenAI capacity (K TPM) | chat 50, embedding 20 | chat 100, embedding 50 |
| Replicas | 1 to 2 (orchestrator 1 to 3) | 2 to 6 (orchestrator 2 to 10) |
| Budget | 150 / month | 800 / month |
| Network | public endpoints, Postgres firewall rule for Azure services | VNet (`vnet-<token>`): Container Apps subnet (delegated, `/24`; `/27` is the minimum) and a private endpoint subnet (`/24`); private endpoints and private DNS zones for Postgres, Key Vault, the AIServices account (three zones) and Redis; public network access off on all four. The frontend ingress stays external behind its IP allow-list |
| Redis | `redis:8` container app (`ca-redis`) | Azure Managed Redis (`Microsoft.Cache/redisEnterprise`, `Balanced_B0`, TLS on port 10000), connection string in Key Vault |
| Images | GHCR at the pinned tag | ACR (Basic) filled by `az acr import` from GHCR, pulled with the identity (`AcrPull`) |
| Model auth | account key from Key Vault | Entra ID through the identity; local auth disabled on the account |
| A2A identity | `both` | `token`, with the distinct `A2A_IDENTITY_KEY` that preprovision generates |

Cost (estimates, not measured): demo roughly 120 to 380 USD a month, about 0.02 to 0.03 USD per chat turn; reference 500 to 800.

## Reference profile, image source and stack

**Reference profile** (`DEPLOY_PROFILE=reference`). Modules: `network.bicep` (VNet, subnets, the six private DNS zones with VNet links), `private-endpoints.bicep`, `redis-managed.bicep`, `acr.bicep`. Postgres, Key Vault and the AIServices account switch to `publicNetworkAccess: Disabled` only in this profile; the demo compiles to the same resources as before (a stored list in `agents/python/tests/data/infra_demo_resources.json` pins it). Redis reaches Python as `REDIS_URL=rediss://:<key>@<host>:10000/0` (secret `redis-url`) and .NET as `ConnectionStrings__Redis` (secret `redis-connection-string`, StackExchange.Redis syntax).

**Image source** (`IMAGE_SOURCE`, `auto` | `ghcr` | `acr`; `auto` is ghcr for demo, acr for reference). With `acr` the apps start only after the registry holds `IMAGE_TAG`, so a deployment is two provisions: `preprovision` sets `DEPLOY_APPS=false` until `ACR_IMAGES_TAG` matches, `postprovision` runs `scripts/azure/acr-images.sh` (import, or build) and stops; the second `azd provision` starts the apps, runs `job-db-init` and the smoke test. `scripts/azure-up.sh` does both for you.

**Build from source.** `scripts/azure-up.sh --build-from-source` implies `acr` and builds every image of the stack with `az acr build` (ACR Tasks, linux/amd64, no local Docker) from the working tree, tagged `src-<sha>` (`-dirty<epoch>` on a dirty tree). `--dry-run` prints every command. `scripts/azure/images.tsv` lists the images and their build inputs; a test keeps it equal to the `build-images.yml` matrix. This is not azd `services:` with `remoteBuild`: the apps need their image at provision time and `azd deploy` needs the apps to exist, and declaring services would make a plain `azd up` rebuild them. Inner loop after the first deploy: `scripts/azure/acr-images.sh build --registry <acr> --tag <new tag> --only orchestrator`, then `azd env set IMAGE_TAG <new tag>` and `azd provision`.

**.NET stack** (`--stack dotnet`, `DEPLOY_STACK`). Runs `orchestrator-dotnet`, the five `<specialist>-dotnet` images and, with `--mcp`, `mcp-inventory-dotnet` (there is no .NET product MCP server, and the .NET specialists do not call MCP). The outbox worker, `db-init`, the auth server and the frontend are the Python images on both stacks. Env differences: `ConnectionStrings__Redis` instead of `REDIS_URL`; no `MAF_STREAM_TIMEOUT_SECONDS`; `OTEL_SERVICE_NAME` ends in `.dotnet`; the orchestrator gets `DOTNET_TIMEOUT_WORKER_ENABLED=true` (parity row 36). Health paths are the same (`/health`, `/ready`; the MCP host has `/health` only). The images are published by `build-images.yml` as `<service>-dotnet` (amd64 only); the first `.NET` smoke run happens in that workflow's first CI run. Parity row 54.

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
| `AZURE_OPENAI_AUTH` | `auto` | `auto` (demo key, reference identity), `key` or `identity` (needs an image built with `AZURE_OPENAI_AUTH` support; disables local auth on the account) |
| `DEPLOY_STACK` | `python` | `python` or `dotnet` |
| `IMAGE_SOURCE`, `BUILD_FROM_SOURCE` | `auto`, `false` | `auto`/`ghcr`/`acr`; source builds go into ACR |
| `DEPLOY_APPS`, `ACR_IMAGES_TAG` | set by the hooks | false until the registry holds `IMAGE_TAG`; the tag the registry was last filled with |
| `DEPLOY_MINI_MODEL`, `DEPLOY_AUTH_SERVER`, `DEPLOY_MCP` | `false` | opt-in model deployment and apps |
| `DEPLOY_FOUNDRY_PROJECT` | `false` | Foundry project under the AIServices account, App Insights connection, Azure AI User for the app identity |
| `DEPLOY_GATEWAY`, `GATEWAY_SKU`, `GATEWAY_PUBLISHER_EMAIL` | `false`, `BasicV2`, placeholder | API Management in front of the account; see [AI gateway](#ai-gateway-optional) |
| `DEPLOY_PER_APP_IDENTITIES` | `false` | one identity per app, attached to the orchestrator, specialists and worker |
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
- **Gateway adds a hop and a second limiter.** Token limits answer 429 with `Retry-After`; the apps retry per the provider settings. Quota on the account (TPM) still applies behind it. The gateway is a public endpoint: in key mode anyone holding the subscription key can spend, exactly like the account key today; entra mode with per-app identities is the stricter setup.
- **Key auth first.** With `AZURE_OPENAI_AUTH=key` the account key is copied into Key Vault by the Bicep (`listKeys`), so it appears in the deployment history of the resource group. Identity auth removes it; flip the default only after a verified Azure run.
- **Budget start date** defaults to the first of the current month via `utcNow`, so redeploying in a later month moves it.
- **Troubleshooting:** the first-run failure catalogue and its guards are in [troubleshooting.md](troubleshooting.md#azure-deployment-first-run-failure-catalogue).

## Assumptions to confirm on a real subscription

Foundry project, gateway and per-app identities (added offline; none has been deployed):

- Microsoft.CognitiveServices `2026-09-01` (projects, connections) is live in the target region. It is the newest the Bicep type set carries; `2025-06-01` (what `openai.bicep` uses) is the fallback.
- An account created with `allowProjectManagement` and a system-assigned identity accepts a project, and flipping the flag on an existing account in place is allowed (otherwise recreate the account).
- The `AppInsights` project connection with `authType: ApiKey` and the connection string as the credential is what the tracing view reads, and Container Apps traces then show there. GenAI semantic-convention fields missing on either stack are not yet checked (plan M5.3).
- Role definition `53ca6127-db72-4b80-b1b0-d745d6d5456d` is **Azure AI User** and is sufficient for the data actions the apps use on a project.
- **API Management tier support for `azure-openai-token-limit` and `azure-openai-emit-token-metric` is unverified.** `BasicV2` is a guess; so are its price and provisioning time. `tokens-per-minute` accepts the literal per-agent values built here, and the policy expressions (`(Jwt)context.Variables[...]`, `Jwt.Claims`, `Split`, double quotes inside attributes) compile in the gateway.
- Custom metric dimensions (`Agent`, `AgentTrusted`) reach Application Insights; a workspace-based component may need custom metric dimensions enabled.
- The OpenAI SDK request paths `/openai/deployments/{d}/chat/completions`, `/embeddings` and `/openai/v1/responses` are the whole surface the apps use; anything else gets a 404 from the gateway.
- A managed-identity token for `https://cognitiveservices.azure.com` is accepted by `validate-azure-ad-token` with that audience, and its `oid` claim is the identity's principal id.
- Streaming (SSE) passes through with `buffer-response="false"` and the 230 s backend timeout.

Earlier assumptions:

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
- `keyvault list-deleted`, `account list-deleted` and `apim deletedservice list` return the JSON shapes `scripts/azure/lib/checks.sh` parses. (`postgres flexible-server list-skus`, `cognitiveservices model list` and `usage list` were checked against live read-only listings, and `az deployment sub validate` and `what-if` accepted the demo and reference parameters; that is the extent of the Azure-side verification.)
- `AZURE_PRINCIPAL_ID` is set by azd for a signed-in user; the deployer role assignment is skipped when it is empty.
- The AllowAllAzureServices firewall rule and the `source` value `user-override` for `azure.extensions` are accepted.
- Reference profile: private endpoint DNS (`privatelink.redis.azure.net` is the Managed Redis zone; the three AIServices zones cover its hostnames); Key Vault secrets written through ARM still succeed with public access off, and Container Apps resolves the references over the private endpoint; the Container Apps subnet size (`/24`) is enough; a Postgres private endpoint (not VNet integration) is accepted by the flexible server API version used.
- Azure Managed Redis `Balanced_B0` exists in the chosen region at an acceptable price, and `Microsoft.Cache/redisEnterprise@2025-07-01` takes `publicNetworkAccess` and `accessKeysAuthentication`. Availability and price must be checked at deploy time.
- `az acr import` from public GHCR works without credentials once the packages are public, and `az acr build` accepts these Dockerfiles and the repo root as context.
- The `.NET` images start and answer `/health` with dummy settings (CI smoke), and the .NET hosts run under `ENVIRONMENT=production` with the Python-shaped secrets, `DATABASE_URL` with `sslmode=require`, and `AZURE_OPENAI_AUTH=identity`.
- The PowerShell hooks mirror the bash ones for the ACR flow; they have not been run (no PowerShell here).
- Budget notifications with `contactRoles` and no `contactEmails` are accepted.
