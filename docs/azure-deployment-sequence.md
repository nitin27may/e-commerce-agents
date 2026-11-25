---
title: Azure deployment sequence (free trial)
---

# Azure deployment (free trial): one command

`scripts/azure-up.sh`, with no arguments, takes an empty Azure subscription to a running, tested copy of the demo and prints the URL. This page is what that command does, in order, what each stage costs, what it was measured to do, and how to take everything down again.

**Status: deployed and tested from a clean subscription.** The last clean run, with no arguments, provisioned in 7 minutes 32 seconds and printed the summary banner (URL, accounts, what to try) at 10 minutes 30 seconds. The smoke test passed (product cards from the real model, the first stream frame in 5.3 seconds, a return paused for approval, approved and executed), and so did the database, chat-turn and direct model checks. The teardown was run and verified clean the same way ([teardown](#stage-13-teardown-explicit-separate-last)).

`<subscription-id>`, `<env>` and `<region>` below are placeholders. Do not paste your own account name, tenant, subscription id or resource group into the repo, an issue or a commit.

## The command

```bash
az login && azd auth login
scripts/azure-up.sh                  # PowerShell: not supported for this profile; use bash (macOS, Linux, WSL)
# test the URL it prints, then:
scripts/azure-down.sh --env <env>    # the env name is printed in the banner
```

It shows numbered progress (`[1/5]` checks, `[2/5]` secrets, `[3/5]` provision, `[4/5]` smoke test, `[5/5]` done) with elapsed time, and ends with a banner that holds the frontend URL, the accounts, two prompts to try, and the verify and teardown commands. **Every value has a default**, printed on a `using:` line before anything is created, and each can be overridden by a flag or an environment variable (flag, then variable, then default):

| Setting | Flag | Variable | Default |
|---|---|---|---|
| Environment name (names `rg-<env>`) | `--env` | `ECA_ENV` | `eca-` plus 6 hex characters of the subscription id, so a re-run updates the same environment |
| Region | `--location` | `ECA_LOCATION` | the first of `centralus`, `westus3`, `northcentralus`, `canadacentral`, `eastus2`, `eastus`, `westus2`, `southcentralus` where PostgreSQL `Standard_B1ms` is offered to the subscription |
| Model account region | `--openai-location` | `ECA_OPENAI_LOCATION` | the same region |
| Profile | `--profile` | `ECA_PROFILE` | `free-trial` (`demo` and `reference` are larger) |
| Image tag | `--image-tag` | `ECA_IMAGE_TAG` | `sha-<7>` of the newest of the last 25 `main` commits whose images exist on GHCR (checked anonymously) |
| Chat model | `--chat-model`, `--chat-model-version` | none | `gpt-4.1-mini` `2025-04-14` (from the profile) |
| Seeded accounts | `--generated-passwords` | `ECA_DEMO_PASSWORDS` | the documented demo accounts; the flag generates one password for the staff accounts instead |
| Sanity check at the end | `--no-verify` | `ECA_VERIFY` | on |
| No Azure OpenAI quota | `--llm replay` or `--llm openai --llm-base-url ... --llm-model ...` | none | off; the key comes from `LLM_API_KEY`, never an argument |
| Frontend access | `--public-open`, `--allowed-ips a,b` | none | only this machine's public IP |

The accounts are the documented seed accounts (`admin.demo@gmail.com` / `admin123`, `support.demo@gmail.com` / `support123`, `seller.demo@gmail.com` / `seller123`, `power.demo@gmail.com` / `power123`, the customers with `customer123`; the full list is in [the demo guide](demo-guide.md)). They are seeded with their documented passwords only while the frontend is IP-restricted, which is the default: the template ignores the setting when the frontend is open to the internet, and `--public-open` with the demo passwords falls back to a generated staff password. `--dry-run` prints every command and changes nothing; `--preview` runs a what-if and creates nothing.

## What it does not need from you

- **No model key.** The apps call the model with their managed identity (see [Keyless](#keyless-which-credentials-remain)).
- **No Docker.** The images are public on GHCR.
- **No Entra app registration**, no service principal, no federated credential.
- **No secret to invent.** Every secret is generated on the first run, before the first provision, and reused afterwards.

## What the free-trial profile deploys

`DEPLOY_PROFILE=free-trial` (`infra/profiles/free-trial.json`) is the demo topology shrunk to the cheapest shape the template can express:

| Piece | Free-trial setting | Why |
|---|---|---|
| Container apps | frontend, orchestrator, five specialists: **0 to 1 replica**, scale to zero; orchestrator 0.5 vCPU, 1 GiB | A replica that does not exist costs nothing. The worker (outbox) and Redis stay at 1 replica because nothing HTTP can wake them |
| Redis | `redis:8-alpine` container app, 0.25 vCPU | No Managed Redis. Rate limiting fails open, so Redis is optional, but a configured `REDIS_URL` with nothing behind it adds a connect failure to every request, so the container stays |
| Images | public GHCR at an immutable tag: `sha-<7>` of the main commit you mean to run (a release tag works; `latest` and `main` are refused) | No registry (ACR), no Docker on your machine |
| PostgreSQL | Flexible Server 16, Burstable `Standard_B1ms`, 32 GB (the minimum), no HA, 7 day backups | The cheapest SKU that runs pgvector |
| Key Vault | Standard, no purge protection, 7 day soft delete | A purge-protected vault keeps its name for 90 days |
| Log Analytics | 30 days, **0.1 GB per day cap** (the API minimum is 0.023) | Caps ingestion, so a noisy day cannot cost more than about 0.28 USD |
| Foundry | AIServices account `foundry-<token>` (**local auth disabled, identity only**), a Foundry project with the Application Insights connection, `gpt-4.1-mini` `2025-04-14` (GlobalStandard, 100K TPM) and `text-embedding-3-small` (GlobalStandard, 30K TPM), each deployment named after its model | The deployment name must equal the model name: the cost guard prices a turn by the name Azure reports. 10K TPM caused 429 retries (47 seconds to the first frame); 100K gives 2.5 seconds |
| Budget | **10 USD**, alerts at 50, 80 and 100 percent actual and 100 percent forecast, sent to subscription Owners and Contributors | Notifies; it does not stop spending. Raised from 5 for Foundry; the maintainer cap is 10 |
| Not created | API Management, Managed Redis, ACR, VNet, private endpoints, per-app identities, auth server, MCP servers | Each costs by the hour or needs a feature a trial may lack |

```mermaid
flowchart LR
    user([Browser]):::ext --> web
    subgraph rg["rg-&lt;env&gt;"]
        subgraph ca["Container Apps environment (Consumption)"]
            web["ca-frontend<br/>0 to 1, IP restricted"]:::core --> orch["ca-orchestrator<br/>0 to 1"]:::core
            orch --> spec["five specialists<br/>0 to 1 each"]:::core
            orch --> redis[("ca-redis<br/>1 replica")]:::infra
            worker["ca-outbox-worker<br/>1 replica"]:::infra
            job[["job-db-init<br/>manual"]]:::infra
        end
        pg[("PostgreSQL B1ms, 32 GB")]:::core
        foundry["Foundry: AIServices account + project<br/>gpt-4.1-mini, embeddings"]:::ext
        kv["Key Vault standard"]:::sec
        log["Log Analytics, 0.1 GB/day cap"]:::infra
        bud["Budget 10 USD"]:::infra
    end
    orch --> pg
    spec --> pg
    worker --> pg
    job --> pg
    spec --> foundry

    classDef core fill:#0b5cab,stroke:#08427a,color:#ffffff
    classDef ext fill:#f2a900,stroke:#a87400,color:#1a1a1a
    classDef sec fill:#c0392b,stroke:#8e2a20,color:#ffffff
    classDef infra fill:#6b7280,stroke:#4b5563,color:#ffffff
```

## The model provider on a free trial

The profile deploys the Foundry resources by default: the AIServices account, a Foundry project and two model deployments. The chat model is `gpt-4.1-mini` (GlobalStandard). It is in the `Legacy` lifecycle but is still deployable, and it was the cheapest chat model a new pay-as-you-go subscription had quota for: `gpt-5.4-nano` had zero quota there, and the quota row is spelled `gpt4.1-mini` by Azure (the preflight accepts both spellings).

If the subscription has no quota, or you want zero model spend, `--llm replay|openai` skips the account, the deployments and the Foundry project, and the apps use `externalLlmProvider`:

| `--llm` | What runs | Cost | What works |
|---|---|---|---|
| (omitted, the default) | Foundry account and project, `gpt-4.1-mini`, `text-embedding-3-small` | per token on the Azure budget | everything, including live chat turns |
| `replay` | `LLM_PROVIDER=replay`, `EMBEDDING_PROVIDER=replay`, `COST_BUDGET_MODE=observe`. No model is called | 0 USD in model tokens | everything that calls no model: login, catalogue, search (keyless vectors), cart, orders, approvals. Chat prompts answer that they were not recorded |
| `openai` | `LLM_PROVIDER=openai`, `LLM_BASE_URL`, key from Key Vault secret `llm-api-key`, embeddings `replay` unless you change `EXTERNAL_EMBEDDING_PROVIDER` | the provider's charges, outside the Azure budget | chat turns |

A model on your laptop (Ollama, LM Studio) is **not reachable** from Container Apps. The `openai` path needs a public HTTPS OpenAI-compatible endpoint; a key you export is stored in the azd environment in clear text (git-ignored, `.azure/`).

## Limits a new subscription meets

These were all met on the clean run and are handled by the script or the template; they are listed so a failure is recognisable.

| Limit | What happens | Handled by |
|---|---|---|
| One Container Apps environment per subscription (a new pay-as-you-go subscription) | a second environment fails with a quota error | tear one environment down before creating another; the default environment name is stable per subscription so a re-run reuses it |
| `Standard_B1ms` PostgreSQL restricted in a region (`eastus`, `eastus2` on the test subscription) | `Provisioning is restricted in this region` | the region picker skips restricted regions; `--location` overrides |
| Zero quota for a chat model | `InsufficientQuota` | the model is a profile setting (`--chat-model`); `--llm replay` needs none |
| A freshly created model account answers badly for a few minutes (TLS, then 401 or 403 until the role assignment propagates, then 404 while the deployment settles) | the first direct model call fails | `sanity.sh` retries those for up to four minutes |
| azd resolves template parameters before running hooks | secrets generated in a hook arrive one run late and later runs rotate the database password | the secrets are generated by `azure-up.sh` before the first provision |
| azd prints `key not found` on standard output and exits 1 | an unset value looked present | `env_get` returns a value only when azd exits 0 |

## The sequence the script runs

```mermaid
sequenceDiagram
    autonumber
    actor M as Maintainer
    participant Az as az / azd
    participant Sub as Subscription
    participant Job as job-db-init
    participant App as Container apps

    M->>Az: az login, az account set, azd auth login
    M->>Az: tool versions
    M->>Sub: az provider register (7 namespaces)
    Sub-->>M: Registered (1 to 10 min)
    M->>Az: quota and SKU reads (read only)
    M->>Az: azure-up.sh --dry-run (prints, runs nothing)
    M->>Az: azure-up.sh --preview (what-if, creates nothing)
    M->>Az: azure-up.sh (defaults chosen, secrets generated, provision)
    Az->>Sub: resource group, Postgres, Key Vault, apps, budget
    Az->>Job: postprovision starts db-init
    Job->>Sub: migrate, seed if empty, replay vectors
    Az->>App: quick smoke test
    M->>App: smoke.sh --no-model
    M->>App: evals.run_evals --target http (optional, fuller)
    M->>App: sanity.sh (db, model, turn, eval)
    Note over M: review the results, nothing is torn down for you
    M->>Az: azure-down.sh (separate, explicit step)
```

Costs below are for `eastus2`, USD, from the public Azure Retail Prices API on **2026-11-23** (`https://prices.azure.com/api/retail/prices`, no sign-in), list prices, no discounts:

| Meter | Price |
|---|---|
| Container Apps vCPU active / idle | 0.000024 / 0.000003 per second |
| Container Apps memory, active or idle | 0.000003 per GiB-second |
| Container Apps HTTP requests | 0.40 per million |
| PostgreSQL Flexible Server Burstable B1ms | 0.017 per hour |
| PostgreSQL storage | 0.115 per GB-month |
| PostgreSQL backup storage (LRS) | 0.095 per GB-month |
| Log Analytics, Analytics Logs ingestion | 2.76 per GB (the API also lists a 0.00 row; whether it is a free grant is not verified) |
| Key Vault operations | 0.03 per 10,000 |
| **`gpt-4.1-mini`, global standard: input / output** | **0.40 / 1.60 per 1M tokens** (the row in `agents/python/config/pricing/models.yaml`) |
| `text-embedding-3-small`, global | 0.02 per 1M tokens (0.00002 per 1K) |
| Foundry account, project, idle deployments | no standing meter found; tokens only (not verified beyond the API listing) |

**Free grants.** Verified in Microsoft's documentation ([Billing in Azure Container Apps](https://learn.microsoft.com/en-us/azure/container-apps/billing), updated 2026-03-25): each calendar month, per subscription, the first 180,000 vCPU-seconds, 360,000 GiB-seconds and 2 million HTTP requests of the Consumption plan are free; a revision scaled to zero replicas incurs no resource charge; idle rates apply only to a revision held at its minimum replica count. **Not verified** (the Microsoft pages did not expose the figures when fetched; verify in the portal): the free-account credit amount and period, the Log Analytics free ingestion, the Key Vault free operations, and the PostgreSQL B1ms allowance. The maintainer reports about 750 free hours of B1ms on a free account; this page has not confirmed it, and the estimates below subtract none of it, so they are upper bounds. If the allowance applies, take 12.41 USD a month (0.017 an hour) off.

### Estimate for the profile

| Item | Basis | USD |
|---|---|---|
| PostgreSQL compute | 0.017 x 730 h | 12.41 / month |
| PostgreSQL storage | 32 GB x 0.115 | 3.68 / month |
| Outbox worker, 1 replica idle | (0.5 vCPU x 0.000003 + 1 GiB x 0.000003) x 2,628,000 s | 11.83 / month |
| Redis, 1 replica idle | (0.25 x 0.000003 + 0.5 x 0.000003) x 2,628,000 s | 5.91 / month |
| Specialists, orchestrator, frontend | scaled to zero | 0 while idle |
| Foundry account, project, deployments | no tokens, no standing charge | 0 while idle |
| Log Analytics | at most 0.1 GB x 2.76 x 30 (the cap) | up to 8.28 / month |
| **Left up, idle** | | **about 33.8 fixed, up to 42.1 with the log cap reached** |
| **Per hour, idle** | 33.83 / 730 | **0.046** |
| **Per day, idle, with the log cap** | 24 x 0.046 + 0.276 | **1.39** |

At that rate the 10 USD budget lasts about **7 days** (10 / 1.39), and a month costs about 4 times the budget. Foundry did not change the idle figure; it only adds tokens when something calls the model. This profile is for a test and a teardown, not for leaving up. The idle worker and Redis are the largest compute lines; the free monthly vCPU and GiB grants absorb part of them, by an amount this page does not claim.

### Tokens and cost of the sanity run (estimate)

| Check | Model calls | Tokens (estimate) |
|---|---|---|
| `model`: one direct completion | 1 | about 20 in, up to 200 out |
| `turn`: "show me my recent orders" | orchestrator plus one specialist, with tool results fed back | about 10,000 in, 1,500 out |
| `eval`: 3 cases of `orchestrator_routing.json` | the same shape, three times | about 30,000 in, 4,500 out |
| **Total** | | **about 40,000 in, 6,200 out** |

40,000 x 0.40 / 1M + 6,200 x 1.60 / 1M = 0.016 + 0.010, **about 0.03 USD**. The prompt sizes are guesses (system prompt, role blocks and tool schemas dominate); the eval run prints the real token counts and estimated cost, so the first run replaces the guess. Embedding queries add well under 0.001.

### Cost of a 2 hour test

| Item | Basis | USD |
|---|---|---|
| Fixed resources, 2 h | 0.0463 x 2 | 0.093 |
| Eight HTTP apps active for 30 minutes | 8 x (0.5 x 1800 x 0.000024 + 1 x 1800 x 0.000003) | 0.216 |
| `job-db-init`, 10 minutes (estimate) | 0.5 x 600 x 0.000024 + 1 x 600 x 0.000003 | 0.009 |
| Log Analytics, cap for 2 h | 0.1 GB x 2.76 x 2 / 24 | up to 0.023 |
| Sanity run (stage 12) | tokens above | about 0.03 |
| Manual chat during the test | 20 turns x about 0.003 | about 0.06 |
| **Total, upper bound before any free grant** | | **about 0.43** |

The Container Apps lines fall inside the free monthly grants (50 vCPU-hours), so the realistic bill for the test is the Postgres lines plus tokens, roughly 0.2 USD (less if the B1ms allowance applies). Costs can lag in the portal by several hours; check Cost Management the next day.

---

## The stages, one by one

`azure-up.sh` runs these for you; they are listed so each can be run, checked or understood on its own. The stage commands below name `eastus2` and `sha-<7>` as examples; the default run picks its own region and tag.

### Stage 1. Sign in

```bash
az login
az account show --query name -o tsv          # confirm it is the NEW subscription before going on
az account set --subscription <subscription-id>
azd auth login                               # separate sign-in; az login does not cover azd
```

- **Creates:** nothing. **Cost:** none. **Time:** a minute (estimate).
- **Verify:** `az account show --query "{name:name,state:state}" -o json` prints state `Enabled`. `az role assignment list --assignee "$(az ad signed-in-user show --query id -o tsv)" --query "[].roleDefinitionName" -o tsv` lists `Owner` (the template creates role assignments).
- **Roll back:** `az logout`; `azd auth logout`.
- **Failure modes:** [#34](troubleshooting.md#azure-deployment-first-run-failure-catalogue) (azd signed out, no subscription chosen), [#11](troubleshooting.md#azure-deployment-first-run-failure-catalogue) (no role-assignment rights).

### Stage 2. Tool versions

```bash
az version --query '"azure-cli"' -o tsv      # 2.67 or newer
azd version                                  # 1.18 or newer
az bicep version                             # 0.34 or newer; az bicep install if missing
jq --version && curl --version | head -n 1
```

- **Creates:** nothing. **Cost:** none. **Time:** under a minute.
- **Verify:** the versions above. `scripts/azure/prereqs.sh` repeats the check and refuses an old tool.
- **Roll back:** not needed.
- **Failure modes:** an old `az` lacks `az containerapp job` flags; `azd` below 1.18 does not read `main.parameters.json` defaults the same way.

### Stage 3. Provider registration (you run it)

A new subscription has the providers unregistered. The free-trial path needs these seven (drop `Microsoft.CognitiveServices` only with `--llm`, which skips the Foundry account):

```bash
az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.OperationalInsights
az provider register --namespace Microsoft.Insights
az provider register --namespace Microsoft.DBforPostgreSQL
az provider register --namespace Microsoft.KeyVault
az provider register --namespace Microsoft.ManagedIdentity
az provider register --namespace Microsoft.CognitiveServices      # the Foundry account and its model deployments

az extension add --name containerapp --upgrade --only-show-errors
```

`Microsoft.Authorization` and `Microsoft.Consumption` (the budget) were already registered on the subscription the clean run used; check yours. `Microsoft.ContainerRegistry` and `Microsoft.Network` are **not** needed by this profile (`scripts/azure-up.sh --profile free-trial` skips them).

- **Creates:** provider registrations on the subscription. Free.
- **Time:** 1 to 10 minutes each (estimate; they run in parallel).
- **Verify:**
  ```bash
  az provider list --query "[?namespace=='Microsoft.App' || namespace=='Microsoft.DBforPostgreSQL' || namespace=='Microsoft.KeyVault' || namespace=='Microsoft.OperationalInsights' || namespace=='Microsoft.Insights' || namespace=='Microsoft.ManagedIdentity'].{ns:namespace,state:registrationState}" -o table
  ```
  Every row says `Registered` (add `Microsoft.CognitiveServices` to the filter).
- **Roll back:** `az provider unregister --namespace <ns>` once the namespace holds no resources. Leaving them registered costs nothing.
- **Failure modes:** [#33](troubleshooting.md#azure-deployment-first-run-failure-catalogue) (`MissingSubscriptionRegistration`). Until `Microsoft.App` is registered, `az containerapp list-usages` and `az cognitiveservices usage list` answer with an error or an empty list, so the quota check in stage 4 means nothing before this stage is done.

### Stage 4. Quota and region check (read only)

```bash
az postgres flexible-server list-skus --location eastus2 -o json \
  | jq -r '.[0] | (.reason // "no restriction"), ([.supportedServerEditions[] | select(.name=="Burstable") | .supportedServerSkus[].name] | join(" "))'
az containerapp list-usages --location eastus2 -o table          # after stage 3
az cognitiveservices usage list --location eastus2 -o table      # after stage 3; look for OpenAI.GlobalStandard.gpt-4.1-mini (listed as gpt4.1-mini) and text-embedding-3-small, empty or zero means no quota
```

- **Creates:** nothing. **Cost:** none. **Time:** a minute.
- **Verify:** the first command prints `no restriction` and a list that includes `Standard_B1ms`. On the test subscription `centralus` was unrestricted and `eastus` and `eastus2` were not (see [Limits a new subscription meets](#limits-a-new-subscription-meets)).
- **Roll back:** not needed.
- **Failure modes:** [#5](troubleshooting.md#azure-deployment-first-run-failure-catalogue) (Postgres restricted in the region: pick another `--location`), [#3](troubleshooting.md#azure-deployment-first-run-failure-catalogue) (no TPM quota: use `--llm`). `scripts/azure/prereqs.sh` runs the Postgres check and, without `--llm`, the model and quota check.

### Stage 5. Dry run (prints, runs nothing)

```bash
scripts/azure-up.sh --env <env> --location eastus2 --profile free-trial --image-tag sha-<7> --dry-run
```

- **Creates:** nothing; every `az` and `azd` command is printed. **Cost:** none. **Time:** seconds.
- **Verify:** the output ends with `azd provision --environment <env> --no-prompt`, has `az cognitiveservices model list --location eastus2`, and (with `--llm replay`) `azd env set DEPLOY_OPENAI false` with no model listing.
- **Roll back:** not needed.
- **Failure modes:** `--llm openai` without `--llm-base-url` or without `LLM_API_KEY` in the environment is refused with a message; a floating `--image-tag latest` is refused ([#22](troubleshooting.md#azure-deployment-first-run-failure-catalogue) family).

### Stage 6. What-if

```bash
scripts/azure-up.sh --env <env> --location eastus2 --profile free-trial --image-tag sha-<7> --preview
```

This runs the prerequisite checks (registering any provider you skipped), creates the **local** azd environment and generates the secrets into `.azure/<env>/.env`, then `azd provision --preview`, which is a what-if. It creates nothing in Azure. Expect about 34 creates: the resource group, Log Analytics, Application Insights, the identity, Key Vault with four secrets, Postgres with its configuration, database and firewall rule, the environment, nine container apps, the job and the budget. Role assignments show as "Unsupported" (their names are computed at deployment time); that is normal.

If you would rather read it without azd, `az deployment sub what-if --location eastus2 --parameters infra/ci/free-trial-replay.bicepparam` shows the same shape with CI placeholders, and `az deployment sub validate` with the same arguments checks the template against ARM. Both were run in the rehearsal and succeeded.

- **Creates:** local files only. **Cost:** none. **Time:** 1 to 3 minutes (estimate).
- **Verify:** the command exits 0 and the summary has no `Delete` or `Modify` lines for a fresh environment.
- **Roll back:** `azd env delete <env>` removes the local environment.
- **Failure modes:** a purged-name collision ([#12](troubleshooting.md#azure-deployment-first-run-failure-catalogue)); an Azure Policy that denies public Postgres or local auth ([#23](troubleshooting.md#azure-deployment-first-run-failure-catalogue)). Whether `azd provision --preview` also runs the `preprovision` hook is unverified, which is why `--preview` runs `preprovision.sh` itself.

### Stage 7. Provision

```bash
# Default: Foundry with gpt-4.1-mini. No Azure OpenAI quota? add --llm replay (no model) or, for your own endpoint:
#   export LLM_API_KEY=...           (the key is never an argument)
#   --llm openai --llm-base-url https://<host>/v1 --llm-model <model>
scripts/azure-up.sh --env <env> --location eastus2 --profile free-trial --image-tag sha-<7>
```

- **Creates:** everything in [What the profile deploys](#what-the-free-trial-profile-deploys), then runs the `postprovision` hook (stages 8 to 10). The frontend answers only your public IP.
- **Cost:** about 0.046 USD per hour from the moment Postgres exists (see the estimate). **Time:** 15 to 30 minutes (estimate; Postgres dominates).
- **Verify:**
  ```bash
  az group show --name rg-<env> --query properties.provisioningState -o tsv      # Succeeded
  az containerapp list --resource-group rg-<env> --query "[].{n:name,min:properties.template.scale.minReplicas,max:properties.template.scale.maxReplicas}" -o table
  az consumption budget list --query "[].{n:name,amount:amount}" -o table        # 10
  ```
  `azd env get-value FRONTEND_URL` prints the URL.
- **Roll back / stop spending:** `scripts/azure-down.sh --env <env>` (stage 13, after the sanity check and your review). If provisioning stops half way, the resources that exist still bill: run the teardown before debugging for long.
- **Failure modes:** [#10](troubleshooting.md#azure-deployment-first-run-failure-catalogue) (Key Vault role propagation, retried once after 60 s), [#12](troubleshooting.md#azure-deployment-first-run-failure-catalogue), [#6](troubleshooting.md#azure-deployment-first-run-failure-catalogue), [#7](troubleshooting.md#azure-deployment-first-run-failure-catalogue), [#8](troubleshooting.md#azure-deployment-first-run-failure-catalogue), and the free-trial rows 39 to 44 there. A trial's cap on Container Apps environments or cores shows as a quota error from `Microsoft.App`; it cannot be read in advance (see below).

### Stage 8. Database bootstrap (`job-db-init`)

Run by `postprovision`; to run it again:

```bash
az containerapp job start --name job-db-init --resource-group rg-<env> --query name -o tsv
```

- **Creates:** schema, 45 tables, pgvector extension, indexes. **Cost:** a few cents of Container Apps compute. **Time:** 5 to 15 minutes (estimate; with `replay` embeddings it is the short end).
- **Verify:** `az containerapp job execution list --name job-db-init --resource-group rg-<env> --query "sort_by(@, &properties.startTime)[-1].properties.status" -o tsv` prints `Succeeded`.
- **Roll back:** the job is idempotent (`seed --if-empty`); deleting the resource group removes the data.
- **Failure modes:** [#6](troubleshooting.md#azure-deployment-first-run-failure-catalogue) (`vector` not allow-listed), [#13](troubleshooting.md#azure-deployment-first-run-failure-catalogue), [#14](troubleshooting.md#azure-deployment-first-run-failure-catalogue), [#24](troubleshooting.md#azure-deployment-first-run-failure-catalogue) (job log lag), [#37](troubleshooting.md#azure-deployment-first-run-failure-catalogue).

### Stage 9. Seed check

The seeder is part of stage 8. Prove the data is there through the same path a browser uses:

```bash
FRONTEND_URL="$(azd env get-value FRONTEND_URL)" E2E_STAFF_PASSWORD="$(azd env get-value SEED_STAFF_PASSWORD)" scripts/azure/smoke.sh --quick
```

- **Creates:** nothing (it logs in as admin and alice and lists products). **Cost:** none beyond requests. **Time:** about a minute, plus up to 5 minutes for a cold start.
- **Verify:** it prints `PASS: quick smoke`, with `products listed (N total)` where N is above zero.
- **Roll back:** not needed.
- **Failure modes:** [#36](troubleshooting.md#azure-deployment-first-run-failure-catalogue) (revisions still starting), [#18](troubleshooting.md#azure-deployment-first-run-failure-catalogue), and a 403 from the frontend when your IP changed (`azd env set DEPLOYER_IP <ip>`, then run stage 7 again).

### Stage 10. Smoke test

```bash
FRONTEND_URL="$(azd env get-value FRONTEND_URL)" E2E_STAFF_PASSWORD="$(azd env get-value SEED_STAFF_PASSWORD)" \
  AZURE_RESOURCE_GROUP=rg-<env> DB_INIT_JOB_NAME=job-db-init scripts/azure/smoke.sh --no-model
```

`--no-model` runs the quick checks plus the approval flow (a return paused in `workflow:return-replace`, decided by a reviewer, shown on the run timeline) and skips the one keyed chat turn that needs a model. Drop `--no-model` once the stack has a real model.

- **Creates:** a return and an approval row in the demo data. **Cost:** none. **Time:** 1 to 5 minutes.
- **Verify:** `PASS: full smoke`. If the approval step fails under replay, the quick smoke in stage 9 is the replay-compatible gate; the approval step is meant to need no model but has not run under replay.
- **Roll back:** the demo data goes with the resource group.
- **Failure modes:** [#20](troubleshooting.md#azure-deployment-first-run-failure-catalogue) and [#28](troubleshooting.md#azure-deployment-first-run-failure-catalogue); with scale to zero a specialist's first request is a cold start (row 40).

### Stage 11. Evaluation against the deployed stack (optional, fuller than the sanity eval)

The http eval target needs only the URL. Use the frontend URL (it proxies `/api/*` to the orchestrator, which has no public address):

```bash
cd agents/python
export EVAL_STAFF_PASSWORD="$(azd env get-value SEED_STAFF_PASSWORD)"
LLM_PROVIDER=replay uv run python -m evals.run_evals --target http \
  --base-url "$(azd env get-value FRONTEND_URL)" --stack python --chat-provider azure --chat-model gpt-4.1-mini \
  --agent orchestrator --dataset evals/datasets/stack_parity.json --pass-threshold 0 --records-out /tmp/freetrial-eval.jsonl
```

`stack_parity.json` holds cases that call no model, so it also runs on a `--llm replay` deployment. `orchestrator_routing.json` calls the model; the sanity check runs three of its cases. A full model dataset spends tokens on the 10 USD budget: estimate it with `--estimate` first.

- **Creates:** conversations and run records in the demo database. **Cost:** requests only for `stack_parity`; tokens for a model dataset. **Time:** 1 to 3 minutes (estimate; cold starts first).
- **Verify:** a per-suite report and exit 0 with `--pass-threshold 0`; `/tmp/freetrial-eval.jsonl` has one record per case.
- **Roll back:** the data goes with the resource group.
- **Failure modes:** a 429 backs off and retries; a replay miss is reported, never silently scored; the frontend IP allow-list must include the machine running the eval.

### Stage 12. Sanity check (last, before you decide anything)

```bash
scripts/azure/sanity.sh --env <env>                 # all four checks
scripts/azure/sanity.sh --env <env> --only model    # one check; db | model | turn | eval
scripts/azure/sanity.sh --resource-group rg-<env>   # after a portal deployment: set E2E_STAFF_PASSWORD to the password you chose
```

It proves the chain from the database to a model answer, prints PASS, FAIL or SKIP per check, handles no key, and exits non-zero if any check failed:

| Check | What it does | How it reaches the database without exposing it |
|---|---|---|
| `db` | Admin and the seeded customer log in through the frontend; counts products, lists orders, reads the approvals inbox (a late migration) | Read-only GETs through the deployed path, frontend to orchestrator to its connection pool. No firewall rule, no `az containerapp exec`, no SQL client. A count over the API is what is possible with the database private to the apps |
| `model` | One chat completion on the deployed Foundry endpoint, `max_completion_tokens` 200, with **your own Entra token** (`az account get-access-token --resource https://cognitiveservices.azure.com`); no key is read, because the account has local authentication disabled | Not the database. Needs the deployer's Cognitive Services OpenAI User role (the template grants it from `AZURE_PRINCIPAL_ID`; in the portal tick `grantDeployerAccess`). SKIP when the environment has no model account |
| `turn` | "Show me my 3 most recent orders" as the seeded customer through `/api/chat/stream`; needs a route to a specialist, a specialist tool step (which read the database) and an answer | The specialist's own tools read Postgres |
| `eval` | The first 3 cases of `orchestrator_routing.json` over the http target, `--repeats 1`; needs no case errored and every case routed to a specialist; prints groundedness and token counts (not graded) | The orchestrator's tools |

- **Creates:** nothing in Azure. It writes rows to the deployed database (a chat conversation and run records for the seeded users), listed in its summary; they go with the environment. **Deletes nothing and tears nothing down.**
- **Cost:** about 0.03 USD of tokens (estimate above). **Time:** 3 to 8 minutes (estimate; the first calls wake scaled-to-zero apps, each call waits up to `SANITY_WAIT_SECONDS`, 300).
- **Verify:** the last lines read `sanity: 4 passed, 0 failed, 0 skipped` and exit code 0 (`echo $?`).
- **If a check fails:** the script prints what it created (nothing in Azure, which rows) and the command to run that check again (`--only <check>`). The environment stays up and keeps billing; nothing is rolled back automatically. Decide after reading the output: fix and re-run, or tear down.
- **Failure modes:** `db` empty means stage 8 did not finish (`az containerapp job start ...`); `model` HTTP 401 or 403 means the deployer's role is missing or still propagating (a few minutes), HTTP 400 or 404 the model name or API version; `turn` can exceed the 60 second A2A timeout on a cold chain (troubleshooting row 40: run it again once); `eval` on a `--llm replay` deployment reports replay misses.

**Do not tear down until the maintainer has reviewed the results.** The sanity check ends by saying so; the teardown is stage 13 and is only ever run by hand.

### Stage 13. Teardown (explicit, separate, last)

Run it only after you have read the sanity output. Nothing else ever calls it, and the goal of the whole exercise is that when everything works, **everything is deleted and nothing keeps billing or holds a name**.

```bash
scripts/azure-down.sh --env <env> --dry-run   # prints exactly what it will delete, deletes nothing
scripts/azure-down.sh --env <env>             # deletes, then proves it
scripts/azure/verify-clean.sh --env <env>     # read only; run it again any time, for example the next day
```

`azure-down.sh` does, in order: force-deletes the Log Analytics workspace (a plain delete leaves it recoverable, holding its name, for 14 days); `azd down --purge --force` (the resource group and everything in it); purges the soft-deleted Key Vault, AIServices account and API Management service; deletes the subscription-scope deployment records of the environment; deletes a subscription-scope budget with the environment's name if one exists; runs `verify-clean.sh`; and only if that is clean removes the local azd state `.azure/<env>`, which holds the generated secrets in clear text (`--keep-local` keeps it). If anything is left it stops, says which class, and keeps the local state so you can run `scripts/azure-down.sh --env <env> --purge-only`.

#### What survives a resource group delete

| Class | Why it survives | What removes it | How `verify-clean.sh` shows it |
|---|---|---|---|
| Log Analytics workspace | soft-deleted for 14 days, keeps its name, so redeploying the same environment name fails | `az monitor log-analytics workspace delete --force true` before the group goes | `workspace` |
| Key Vault | soft-deleted; the name is held until purged. The demo and free-trial profiles have no purge protection, so it can be purged at once; the reference profile turns purge protection on, which cannot be purged early (90 days, not billed, use a new `--env`) | `az keyvault purge` | `vault` (a purge-protected one is reported with its date) |
| Foundry / AIServices account, its project and connection | the account is soft-deleted and holds its name; the project and the App Insights connection live inside it and go with it | `az cognitiveservices account purge` | `account` |
| API Management | soft-deleted, only when `deployGateway` was on | `az apim deletedservice purge` | `apim` |
| Subscription deployment records | `Microsoft.Resources/deployments` at subscription scope are not in the group (no cost) | `az deployment sub delete`, for names `<env>`, `<env>-...` or outputs naming `rg-<env>` | `deployments` |
| Budget | `Microsoft.Consumption/budgets`. The template deploys it **in the resource group**, so it goes with the group; a copy at subscription scope would not | the group delete; a subscription-scope `budget-<env>` is deleted explicitly | `budget` (both scopes) |
| Role assignments | the template creates none outside the group; those on the vault, account and identities go with them. Orphans of deleted principals would show as `Unknown` | the group delete | `roles` (anything still scoped under `rg-<env>`) |
| Local azd state | `.azure/<env>` on your machine, secrets in clear text | `azure-down.sh` removes it last | `local` |
| Resource provider registrations | not environment state, free | left alone (`az provider unregister` if you want) | not checked |

No Entra app registration, service principal or federated credential is created by anything in this repo's deployment, so there is none to delete ([why](azure-deployment.md#do-you-need-an-entra-app-registration)).

- **Cost:** stops every charge. **Time:** 5 to 20 minutes (estimate; the group delete dominates).
- **Closing proof:** `verify-clean.sh` prints one line per class and ends with `verify-clean: every class is CLEAN for '<env>'` and exit code 0:
  ```text
  CLEAN  group        rg-<env> does not exist
  CLEAN  budget       budget-<env> does not exist (group and subscription scope)
  CLEAN  vault        no soft-deleted Key Vault
  CLEAN  account      no soft-deleted AIServices account
  CLEAN  apim         no soft-deleted API Management service
  CLEAN  workspace    no soft-deleted Log Analytics workspace
  CLEAN  deployments  no subscription deployment record
  CLEAN  roles        no role assignment under rg-<env>
  CLEAN  local        no local azd state for <env>
  ```
  A query that fails is `LEFT ... could not query`, never `CLEAN`, except where the provider is not registered (then nothing can exist). The format above is what the script printed against an environment that never existed; a real teardown has not been run.
- **Roll back:** none; redeploy from stage 7.
- **Failure modes:** [#38](troubleshooting.md#azure-deployment-first-run-failure-catalogue) (`azd down` fails or the environment is not on this machine: `az group delete --name rg-<env> --yes`, then `scripts/azure-down.sh --env <env> --purge-only`), and a soft-deleted workspace from a group deleted by hand (it keeps its name 14 days: recover it with `az monitor log-analytics workspace recover` and delete it with `--force true`, or use another `--env`).

## Keyless: which credentials remain

The free-trial profile uses managed identity wherever the codebase supports it. The apps and the db-init job run as one user-assigned identity:

| Call | How it authenticates |
|---|---|
| Apps to the model (Foundry / Azure OpenAI), chat and embeddings | `AZURE_OPENAI_AUTH=identity`: Entra token, the identity holds Cognitive Services OpenAI User on the account; **local authentication is disabled on the account** (`disableLocalAuth`) |
| Apps to the Foundry project | the identity holds Azure AI User on the project |
| Container Apps secrets | Key Vault references, resolved by the identity (Key Vault Secrets User, RBAC, no access policies) |
| Image pulls | anonymous, public GHCR: no registry credential, no ACR |
| You to the model (sanity check) | your own Entra token; the template grants the deployer Cognitive Services OpenAI User |
| You to the vault | Key Vault Secrets Officer for the deployer, by role |
| App registrations | none |

**Override.** `openAiLocalAuth` (azd `AZURE_OPENAI_LOCAL_AUTH`) is `auto` by default: disabled when the apps use identity (free-trial, reference), enabled for the key-based demo profile. `enabled` keeps keys on the account for a tool that cannot use Entra ID.

**Postgres stays on a password.** Entra authentication exists on Flexible Server and `asyncpg` accepts a callable password, but this codebase builds its connections from one static `DATABASE_URL` in at least eight Python entry points (`shared/db.py`, `shared/readiness.py`, `shared/checkpoint_storage.py`, the outbox worker, both MCP servers, `auth_server`, the migrate, seed and db-init scripts) and the .NET `DatabasePool`, and the migrations would have to run as an Entra role. That is a code change across both stacks and a new image release, and the pinned `v1.4.0` images cannot do it. So the admin password remains, as a generated secret that is never shown: the template generates it (azd: the preprovision hook; portal: a parameter default), it is stored only inside the Key Vault secret `database-url` (the free-trial profile drops the separate `postgres-admin-password` copy, which nothing read), and the apps read it by Key Vault reference. azd also keeps it in `.azure/<env>/.env`, in clear text, until teardown removes that directory.

Every secret the free-trial deployment still contains, and whether it can go:

| Secret (Key Vault name) | What it protects | Remove or derive? |
|---|---|---|
| Postgres admin password (inside `database-url`) | the database | Not removable now: needs Entra database auth in both stacks and a new image (above). Generated, never printed |
| `jwt-secret` | signs user access tokens (the app's own HS256 auth server; there is no Entra login) | Not removable. It could be derived from another root secret, but that only moves the secret |
| `agent-shared-secret` | `X-Agent-Secret` between the orchestrator and the specialists; also the root of the signed user-context token key | Not removable without replacing the service-to-service scheme (an app change); the identity-token mode already derives its key from it |
| `seed-staff-password` | the seeded admin, support and seller logins; read only by `job-db-init` | You choose it. Removable after the first successful bootstrap, but a re-run of the job needs it. The seeded customers' passwords are public by design |
| *Dropped in this profile:* `azure-openai-key` | the model account key | Gone: identity auth, local auth disabled |
| *Dropped:* `postgres-admin-password` | a duplicate of the password in `database-url` | Gone |
| *Dropped:* `a2a-identity-key` | A2A user-context token key | Gone: `A2A_IDENTITY=both` falls back to the agent secret. Needed distinct only for `token` mode |
| *Dropped:* `oauth-seed-key`, `auth-signing-key-encryption-key` | the optional OAuth auth server | Gone unless `deployAuthServer` is on (they are read only with `AUTH_MODE=oauth`) |
| *Only with `--llm openai`:* `llm-api-key` | your external model endpoint | Not removable: that provider has no Azure identity |

Platform-held keys outside the repo and the vault: the Container Apps environment holds the Log Analytics shared key to ship console logs (the `log-analytics` destination needs it; sending through diagnostic settings would avoid it, unverified here), and the Application Insights connection string (an instrumentation key, an identifier rather than an access secret) is used by the managed OpenTelemetry agent and by the Foundry project's tracing connection. Whether App Insights can be switched to Entra-only ingestion with the managed agent is unverified, so it is left on.

## The portal button instead of azure-up.sh

`infra/azuredeploy.json` is the compiled template behind the README's Deploy to Azure button ([how it handles the azd hooks](azure-deployment.md#deploy-to-azure-button)). For this profile: open the button, choose the region `eastus2`, set `profile` to `free-trial`, `environmentName`, `imageTag` `sha-<7>` (the main commit, see the runbook), `seedStaffPassword` (12 characters or more) and your public address in `deployerIp`; after the deployment run the `DB_INIT_START_COMMAND` output, then stages 9, 10 and 12 (`sanity.sh --resource-group rg-<env>` with `E2E_STAFF_PASSWORD` set). It checks nothing in advance (stages 3 to 6 do not happen), so a quota or region problem shows as a failed deployment with partial resources: delete the resource group. The button is unverified.

## Findings from the real runs

What the first real deployments found, all fixed in the template or the scripts and pinned by a test where one can be written offline.

| Finding | Fix |
|---|---|
| The Key Vault Secrets User role id was mistyped, so every secret reference failed | corrected; `tests/test_infra_role_ids.py` checks every GUID literal in `infra/` against a list of verified built-in roles |
| A stale `DEPLOY_OPENAI=false` from an earlier `--llm replay` run silently skipped Foundry | every run now sets and prints the setting |
| The database password drifted between runs, and `SEED_STAFF_PASSWORD` was reported missing | the secrets are generated before provision (azd reads parameters before hooks) and a missing azd key reads as empty |
| A name of the form `ai-<token>` was refused by a Cognitive Services pre-flight ("unusual activity") | the account is named `foundry-<token>` |
| The cost guard stopped chat ("model has no price on file") because the deployment was called `chat` | deployments are named after their models |
| The first chat turn took 47 seconds at 10K TPM | chat capacity 100K TPM (2.5 seconds to the first frame) |
| The smoke prompt asked for laptops; the seed has none | it asks for wireless headphones |
| The region picker never picked a region (`grep -q` in a `pipefail` pipeline exits the writer with SIGPIPE) | here-strings |
| The model check failed on a brand-new account | retry window, above |
| The quota check missed the `gpt4.1-mini` row spelling | falls back to the hyphenless name |

## Known limits

- **The Postgres admin password stays a password.** `DATABASE_URL` is one static string in at least eight Python entry points; Entra database authentication needs a code change on both stacks. The password is generated and held only in Key Vault and the git-ignored azd state.
- **The HTTP eval route assertion fails over the deployed stack**: the orchestrator's own `call_specialist_agent` step is not recorded in the run steps the HTTP target reads, so `sanity.sh --eval` is opt-in. Run evals locally against the same Foundry deployment.
- **Generative UI cards** do not render in the deployed frontend yet; chat answers and product cards do.
- **Cold start.** With every HTTP app at zero replicas, the first request after idle can exceed the 60 second A2A timeout; the second succeeds.
- **The free grants** (account credit, 12 month Postgres, Log Analytics) and the exact bill are not claimed: costs lag in the portal by hours. The estimate above is list price.
- **The PowerShell mirrors** (`azure-up.ps1`, `prereqs.ps1`, `preprovision.ps1`) do not have the free-trial, `--llm`, `--preview` or sanity options; use the bash scripts.
