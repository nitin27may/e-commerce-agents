# PLAN — the one plan file

> Repo-committed working artifact, per the "Working Artifacts Location" rule in the project
> `CLAUDE.md`. **This is the only plan file.** It used to be twenty, then nine; the rest are in
> git history, which is where finished plans belong.
>
> The published, reader-facing version of the forward work is
> [`docs/roadmap.md`](../../docs/roadmap.md). This file is the working detail behind it: blockers,
> acceptance criteria, and the constraints that have already cost us time.

**Status: 2026-11-19 (reconciled; see below).** **The LangGraph backend (LG1–LG3) is parked** by maintainer decision; the
LG rows below are kept for the record only. [Part IV](#part-iv--the-november-2026-audit-everything-that-needs-no-azure) is new: a
full audit produced issues #80–#104, and that part plans every one that needs no Azure resource.
Parts I–III below are as of 2026-09-01. **Phase A is done** — see below. v1.3.0 is cut and both backends are live.
This revision **resequences the forward work**. The previous plan ordered Azure (phases A–E) ahead
of the cross-framework comparison, with the comparison not starting "until Azure lands". That order
is kept in direction and loosened in degree: the LangGraph work now starts after **Phase B**, not
after Phase E, and it starts as a one-agent spike rather than a full third backend.

**What changed the sequencing** — Foundry hosted agents became a **framework-agnostic** managed
runtime. Agents built with MAF, LangGraph or the Copilot SDK deploy to the same runtime without
rewrites, and `langchain_azure_ai.agents.hosting` exposes a compiled LangGraph graph over the same
Responses/Invocations protocols Phase D targets for MAF. Microsoft also publishes the
Foundry-to-LangGraph **A2A** interop pattern, which is the architecture ADR 0001 already commits us
to. Azure therefore stopped being a competing priority and became the substrate the comparison runs
on. Build Azure first and the third backend inherits a deployment target; build the third backend
first and the deployment story gets written twice.


## Reconciliation, 2026-11-19

Checked against `main` at `4ea66a7`, not against this file's own "built" headings. Issues #81, #83, #84, #88 and
#104 were closed with evidence; #80, #82 and #102 were already closed. [#103](https://github.com/nitin27may/e-commerce-agents/issues/103)
carries the per-issue checklist. What the check found:

**Still to build (each verified absent in the code)**

| Item | Issue | State |
|---|---|---|
| ~~D-9b inspection, resolution, timeline~~ | #95 | **Built 2026-11-19** with D-9a, behind `RETURN_LIFECYCLE_ENABLED`: `await-inspection`, `resolve_return` (idempotent payout, amount only lowered), staff list/decide routes and `/admin/returns`, the overdue-inspection nudge (admins are told; the customer is never denied for slowness), `GET /api/runs/{id}/timeline` and its `/runs` rendering. D-9a: `await-carrier-scan`, `shared/return_lifecycle.py`, `worker/return_handlers.py`, the admin scan simulator, migration 0013; `docs/return-lifecycle.md`. No `issue-label` executor: `initiate_return` already issues the label. **#95 is done except a live-stack run** (see its issue). |
| B-15 history compaction | #98 | Not built. |
| ~~B-17 DevUI entry point~~ | #98 | **Built 2026-11-19**: `scripts/devui.py`, run with `uv run --with agent-framework-devui==...` and **not** added to the lock (DevUI requires `fastapi<0.163`; an extra in the shared lock downgraded the orchestrator's production FastAPI from 0.163.1 to 0.162.7). `docs/devui.md`, parity row 42. |
| ~~B-19 typed card frames~~ | #98 | **Decided against, 2026-11-19.** The browser already hides unclosed fences while streaming, validates every card with Zod (null-dropping, passthrough, `products` arrays to comparison cards), collapses duplicates and drops bad ones. Server-side frames would add a second path: seven card schemas mirrored in Pydantic and C# records, and a move from `content: string` to message parts across the chat page, the shop assistant and history loading, with the fence parser kept as the fallback. Little user-visible gain for that. Revisit if a non-web consumer (an A2A client) needs typed cards. The one real gap is that a bad card is dropped silently, with a console warning that is off in production. |
| B-16 `GroupChatBuilder`, D-5b customer confirm, M-2, V-1 `ops-responder`, E-3b, E-4, E-7, E-8 | #98 #92 #93 #94 #97 | **Not on main and on no remote branch.** The "held on their branches until R1" wording below was wrong: the only remote branches were already merged and are deleted. Treat them as unbuilt, not parked. |
| E-9 `docs/eval-scorecard.md`, E-10, P-7 model scorecard, P-8 `docs/model-providers.md` | #97 #96 | Files do not exist. |
| B-18 Aspire AppHost | #90 | No `AppHost` project. |
| Azure: Bicep, `azure.yaml`, Foundry | #89 #91 | Not started (phases B-E). |
| C-18 capstone `run.sh`, C-20 notebooks, new chapters | #100 | Neither file exists. |
| Hero GIF and demo video | #87 | Only PNG screenshots exist. |
| Secret scanning job | #85 | CodeQL, ruff and the Playwright smoke exist; no secret scan. |
| Static hosted preview | #99 | Not confirmed. Error boundaries and `e2e/a11y.spec.ts` exist. |

**Plan names that differ from what was built** (the feature exists; the path in the table rows is wrong):
`web/e2e/a11y.spec.ts` (not `a11y-smoke.spec.ts`), `web/src/app/(app)/admin/approvals/page.tsx` (not
`/approvals`), `docker-compose.demo.yml` + `scripts/dev.sh --demo` and the `Boot (replay, keyless)` job in
`compose-smoke.yml` (there is no `docker-compose.nokey.yml`, `demo/` directory or `demo-tour.yml`; confirm
before relying on C-7 and C-8 being built).

**Process lesson.** Dependabot major bumps that each edit `web/pnpm-lock.yaml` do not compose when merged
back to back: three web PRs passed CI individually and broke `main`'s frozen install together (fixed in #151).
Merge one, let the next rebase, wait for green. Two timing-dependent tests were also fixed that day (#148
a fixture date, #149 and #150 racing `.NET` tests); a fixture with an absolute timestamp is a time bomb.

---

## Consolidated plan, 2026-11-20

Checked against `main` at `f380bba` (#157 DevUI is the newest commit). **This block supersedes the
ordering in Parts I and II and the Phase B text in section B below.** Parts III and IV stay as the
constraints and build log. Git holds no half-done work: no open PRs, no stashes, no extra worktrees,
and one stale branch (`docs/readme-accuracy-audit`, July, 398 commits behind, superseded by #82).

### Decisions needed first

1. **R0 recording session ($1-3).** Cheapest unblock: C-8, C-9b, C-10, C-11, C-18 and the tutorial fixtures wait on it.
2. **D-3, .NET parity line.** Decides NET-36, B-18 and the .NET halves of B-15, B-16 and D-5b.
3. **D-1, Azure spend ceiling.** Azure is independent of everything else but stays parked until this is set.
4. **Chat model on Azure: gpt-5.1 (approved 2026-11-20).** gpt-4.1 is closed to new customers; it stays an override.
5. **LangGraph (LG1-LG3) stays parked.** #103 recommends one LangGraph specialist behind A2A instead of a third backend.

### Pending items

**Key-free, no Azure (start now)**

| ID | Issue | Work | Size |
|---|---|---|---|
| DOC-R | #103 | Fix stale claims: roadmap (MCP 2.x "blocked", PyPI, Claude), parity rows 3, 8, 12, 23, plan section 15 (RFC 7591 is built), README "What's next" | 3h |
| DOCS-SYNC | #82 | Apply the 2026-11-20 drift audit (below) | 8h |
| YAML-1 | #86/#97 | Group-chat panelist and judge prompts into YAML, byte-identical (`group_chat_mode.py:34,39`, `llm_judge.py:29`) | 4h |
| B-15 | #98 | History compaction, off by default, both stacks; Python rehydration is capped at 50 rows today | 10h |
| SAN-2 | #97 | Sanitize `recall_memories` and `get_user_profile` (`guardrails/config.py:49,52`) | 3h |
| NET-36 | #92 | .NET handler for `approval.timeout_apply` (parity row 36) | 8h |
| CI-H | #85/#84 | Secret-scan job, `maf-canary.yml`, MAF badge check, pricing-promotions baseline (0.727 measured vs 0.680 committed) | 5h |
| SMOKE-MX | #88 | Boot `py-net` and `net-py` mixed stacks in compose-smoke | 4h |
| B-18 | #90 | Aspire AppHost and ServiceDefaults | 14h |
| C-7, C-18, C-9b, DIST-X | #86/#104 | `docker-compose.nokey.yml`, `dev.sh --no-key`, capstone `run.sh`, devcontainer start, remove `NEXT_DIST_DIR` | 11h |
| C-20 | #100 | Notebooks for chapters 01-04 under replay | 8h |
| AUTH-KR, AUTH-AM | section 15 | Signing-key rotation (migration 0014), route/token matrix doc and test | 14h |
| WEB-T, WEB-S, WEB-C | section 17 | Type `api.ts`, `useSyncExternalStore` for auth and cart, report dropped cards; both lint rules back to `error` | 16h |
| UI-AUDIT | #99 | Read-only UI audit, ranked fixes signed off before the demo GIF is recorded | 4h |
| SEED-D, TUT-RH, E-9a, P-8, DTS, RL-LIVE, PS1 | various | Re-seed fixture miss, commit the rehash script, scorecard generator, `docs/model-providers.md`, Durable Task write-up, live-stack return run, run `dev.ps1` | 24h |

**Azure preparation that needs no subscription** (Phase B rewritten, see below): P1, P2, P3, I1-I4.

**Needs a live key** (R0 first, then one R1 sitting so nothing is recorded twice): C-6/A-6b/tutorial fixtures (R0), C-8, C-10, C-11, D-5b, B-16, M-2, V-1a, E-3b, E-4, E-6, E-7, E-8, E-10, typed product filters, B2 (dual-stack parity in CI), P-7 (R2).

**Needs Azure:** I5 (first real deploy), I6 (reference profile), I7 (build from source), then C, D, E.

### Parallel lanes (one worktree each)

| Lane | Work | Owns | Collides with |
|---|---|---|---|
| L1 Docs | DOC-R, DOCS-SYNC, P-8 | docs, README, parity rows 3/8/12/23, this file | any lane adding a parity row |
| L2 Prompts | YAML-1 | `config/prompts/group-chat-*.yaml`, `_eval/`, `group_chat_mode.py`, `llm_judge.py`, .NET `GroupChatMode.cs` | replay fixtures must not change |
| L3 Python runtime | B-15, SAN-2, P2 | `shared/agent_host.py`, `shared/config.py`, `guardrails/config.py` | L7 and L12 on `shared/config.py` |
| L4 .NET runtime | B-15, SAN-2, NET-36 | rehydrator, sanitizer config, approvals hosted service | L5 on `Directory.Packages.props` |
| L5 Aspire | B-18 | AppHost, ServiceDefaults, `.sln` | L4 (serialize merges) |
| L6 CI | CI-H, SMOKE-MX, TUT-RH, SEED-D | `.github/workflows/`, `compose-smoke.sh`, baselines, `seed.py` checkpoints | L8 and L11 on `seed.py` |
| L7 Auth | AUTH-KR | `auth_server/`, migration 0014, .NET JWKS refresh | L3 on `shared/config.py` |
| L8 Demo | C-7, C-18, C-9b, DIST-X | compose overlay, `dev.sh`/`dev.ps1`, devcontainer | L11 (Dockerfile) |
| L9 Web | WEB-T/S/C, UI-AUDIT fixes | `web/src/lib`, components, `eslint.config.mjs` | `web/pnpm-lock.yaml`: hold Dependabot web merges |
| L10 Tutorials | C-20 | notebooks | none |
| L11 Azure runtime | P1 (`db-init` image, `seed --if-empty`, staff password) | `Dockerfile.dbinit`, `scripts/db_init.py`, `seed.py`, build-images matrix | L6, L8 |
| L12 Azure code | P2, P3 (temperature, DB pool, `sslmode`, managed identity) | `shared/factory.py`, `agent_host._run_options`, .NET factories | L3 |
| L13 Azure infra | I1-I4 | `infra/`, `azure.yaml`, `scripts/azure/`, `azure-up.sh`/`azure-down.sh` | none (new files) |

Pre-assigned: migration 0014 to AUTH-KR, 0015 reserved for D-5b. Parity rows 43 B-15, 44 B-18, 45 AUTH-KR, 46 YAML-1; Azure deployment takes the next free row. Every lane running `replay-evals.sh` shares one `flock` file; never `pkill`. Every key-free PR passes `fixture_drift` (exit 0) and replay with zero misses before and after. Stage explicit paths, never `git add -A`. Agree file ownership with the other session before L3, L7, L8, L10 start (Part III).

### Order of work

- **Wave 1 (about 2 days, parallel, key-free, no fixture changes):** L1, L2, L3 (B-15 Python, SAN-2), L6 (CI-H, SMOKE-MX), L11 (P1), L13 (I1), UI-AUDIT.
- **Wave 2 (about 3 days):** L4 if D-3 is "parity", L7, L8, L9, L12 (P2, P3), L13 (I2-I4), L6 (SEED-D, TUT-RH, E-9a). Once SMOKE-MX is green, flip `A2A_IDENTITY=token` in a release.
- **Wave 3:** UI fixes from the audit, then R0 (C-6, A-6b, tutorial fixtures, then C-8, C-9b, C-18, C-10, C-11). In parallel and keyless: L5 B-18, WEB-S, WEB-C, L10, P-8. Then one R1 sitting with every keyed item ready on a branch, then regenerate the eval scorecard and land B2.
- **Azure (starts when D-1 is set and `az login` is fresh):** I5 first real deploy, I6 reference profile, I7 build-from-source, then Foundry (C), hosted agents (D), publish (E).

### Phase B rewritten: one-command Azure deploy (#89)

**Goal.** `./scripts/azure-up.sh` (wraps `azd up`) yields a public URL where a signed-in user completes a keyed chat turn with product cards and one approval. `./scripts/azure-down.sh` leaves zero resources and nothing soft-deleted. Nothing exists yet: no `infra/`, no `azure.yaml`, no `azd` or `bicep` installed.

**Findings that change the earlier design**

1. gpt-4.1 and gpt-4.1-mini are Deprecated and closed to new customers. Default chat model is **gpt-5.1 (2025-11-13)**; gpt-4.1 remains an override. gpt-5.x rejects `temperature`, so P2 makes an empty `LLM_TEMPERATURE` omit it on both stacks.
2. gpt-4.1-mini is not deployed by default: nothing reads it. `deployMiniModel` is opt-in.
3. Azure Cache for Redis is closed to new customers. Demo: `redis:8-alpine` as an internal-TCP container app. Reference: Azure Managed Redis. Rate limiting fails open either way.
4. A public URL with anonymous chat and a published admin password is an open spend tap. Defaults: frontend ingress restricted to the deployer IP, generated staff passwords, `COST_BUDGET_MODE=enforce`, a TPM cap, a resource-group budget alert.
5. The seeder cannot run in Azure as it is: `scripts/` and `docker/postgres/` are bind-mounted, not in an image; `seed.py` truncates when users exist; the precomputed embeddings file is not committed. P1 fixes all three.

**Topology.** One resource group per azd environment. Container Apps environment with: frontend (the only external ingress, IP-restricted), orchestrator, five specialists, outbox worker (min 1), Redis container, manual `job-db-init`; auth-server and MCP servers opt-in. PostgreSQL Flexible 16 with `azure.extensions=VECTOR,PGCRYPTO`. One AIServices account with chat and `text-embedding-3-small` deployments, created by the Bicep. Key Vault (RBAC), user-assigned managed identity, Log Analytics and App Insights, budget alert. Images from GHCR by pinned release tag. Python stack only; .NET images are not published, recorded as a parity row.

**Cost (estimates; Phase E publishes measured numbers).** Demo about $120-380 per month ($4-12 a day), about $0.02-0.03 per chat turn. Reference about $500-800 per month (zone-redundant Postgres in a VNet, Managed Redis, ACR).

**Bicep layout.** `azure.yaml`; `infra/main.bicep` (subscription scope), `main.parameters.json`, `bicepconfig.json` (linter rules as errors), `profiles/demo.json`, `profiles/reference.json`, `modules/` (monitoring, identity, keyvault, postgres, openai, redis-container, redis-managed, cae, container-app, apps, db-init-job, budget, acr); `scripts/azure/` (`prereqs`, `preprovision`, `postprovision`, `smoke`, each as `.sh` and `.ps1`). Naming: `resourceToken = toLower(uniqueString(subscription().id, environmentName, location))` on globally unique resources; container apps named `ca-<service>` without the token (32-character limit). Separate `location` and `openAiLocation`. Pin API versions with `az provider show` while writing I1.

**Secrets and config wiring**

- `preprovision` generates once per azd environment, only if unset: `POSTGRES_ADMIN_PASSWORD` (alphanumeric, three character classes, so `DATABASE_URL` needs no encoding), `JWT_SECRET`, `AGENT_SHARED_SECRET`, `A2A_IDENTITY_KEY` (distinct), `OAUTH_SEED_KEY`, `AUTH_SIGNING_KEY_ENCRYPTION_KEY` (64 hex each), `SEED_STAFF_PASSWORD` (20 alphanumeric). Never `newGuid()` (rotates every deploy) or `uniqueString()` (not secret).
- Secrets go to Key Vault and are injected as Container Apps secrets with `keyVaultUrl` plus the managed identity, consumed through `secretRef`. No secret is a Bicep output; the admin password is printed from the azd env by `postprovision`.
- Bicep computes: `DATABASE_URL` (`?sslmode=require`), `AZURE_OPENAI_ENDPOINT` and key, `AGENT_REGISTRY` (`https://ca-<svc>.internal.<domain>`), `ORCHESTRATOR_URL`, `CORS_ALLOWED_ORIGINS`, per-specialist `A2A_PUBLIC_BASE_URL`, `REDIS_URL`.
- Static: `ENVIRONMENT=production` (turns on the secret validator, so the worker and job need the full secret set), `LLM_PROVIDER=azure`, `HOSTNAME=0.0.0.0`, `WEB_TRUSTED_PROXY_HOPS=1`, `RATE_LIMIT_TRUSTED_PROXY_HOPS=2` (verify in I5), `HITL_RESUME_TIMEOUT_SECONDS=200` and `MAF_STREAM_TIMEOUT_SECONDS=120` (both under the 240 s ingress limit).
- Do not set `OTEL_EXPORTER_OTLP_ENDPOINT`; the managed OTel agent injects a gRPC endpoint. Metrics do not reach App Insights (documented).
- End-of-deploy outputs: `FRONTEND_URL`, `ORCHESTRATOR_INTERNAL_URL`, `AZURE_KEY_VAULT_NAME`, `AZURE_RESOURCE_GROUP`, `ADMIN_LOGIN_EMAIL`, `AZURE_OPENAI_ENDPOINT`, `AZURE_CHAT_DEPLOYMENT`, `AZURE_POSTGRES_FQDN`, `APPLICATIONINSIGHTS_NAME`.
- Auth: first go uses the OpenAI key in Key Vault. P3 adds `AZURE_OPENAI_AUTH=identity` with `AZURE_CLIENT_ID` and the `Cognitive Services OpenAI User` role; flip the default only after a verified Azure run (release blocker for Phase E). `azure-identity` is the one dependency risk: gate it with `uv lock --check`, a lock diff showing no MAF or FastAPI change, replay-evals and an image smoke import.

**Mechanics**

- Database order: server, then `azure.extensions` configuration (serialized), then database, then `AllowAllAzureServices` firewall rule (demo), then apps and job. `scripts.db_init` runs `migrate`, `seed --if-empty` (never TRUNCATE in Azure), embeddings with `REINDEX` when the table is empty, then `ANALYZE`.
- Container Apps has no `depends_on`: apps start and serve 503 on `/ready` until the job lands the schema. Verify in I5 that the ARM PUT does not block on readiness; if it does, move migrate into an orchestrator init container.
- Every app `minReplicas: 1` (a Python cold start can exceed the 60 s A2A timeout); the worker is always min 1; orchestrator scales to 3 on HTTP concurrency 20. `POST /api/orchestration/compare` can exceed the 240 s limit and is documented best-effort.
- `azure-down.sh` runs `azd down --purge --force`, re-checks `az keyvault list-deleted` and `az cognitiveservices account list-deleted`, purges leftovers, and exits non-zero if anything remains.
- `prereqs.sh`: `az`, `azd`, `bicep` floor versions, `containerapp` extension, `jq`, `curl`; provider registrations (App, OperationalInsights, Insights, CognitiveServices, DBforPostgreSQL, KeyVault, ManagedIdentity, Consumption, Cache for reference); role-assignment write rights (Owner, or Contributor plus User Access Administrator); Postgres SKU availability, model name, version, `GlobalStandard` SKU, lifecycle and quota in the region.

**First-run failure catalogue (29 items, each with a guard)**

| Failure | Guard |
|---|---|
| gpt-4.1 deployment refused | gpt-5.1 default; lifecycle preflight |
| gpt-5.x 400 on `temperature` | P2 omission; Bicep derives it from the model name |
| No TPM quota | usage preflight, `openAiLocation`, capacity param |
| Parallel model deployments conflict | `dependsOn` chain |
| Postgres offer-restricted in region | `list-skus` preflight with alternates |
| `CREATE EXTENSION vector` not allowed | allowlist before the job |
| Postgres password rejected or breaks the URL | generator guarantees classes, alphanumeric only |
| Postgres unreachable or no TLS | firewall rule, `sslmode=require`, .NET honours it (P2) |
| `max_connections` exhausted | `DB_POOL_*` (P2), demo 1/4 |
| Key Vault RBAC propagation | role assignment in an earlier module; one retry of `azd provision` |
| Deployer lacks role-assignment rights | prereq RBAC check |
| Soft-deleted name collision | `list-deleted` check, purge, no purge protection in demo |
| Worker or job crash-loops on placeholder secrets | full secret set on both |
| Re-run wipes data | `seed --if-empty` |
| Semantic search silently full-text | embeddings step in `db_init`, smoke asserts count |
| IVFFlat built before data | `REINDEX` after seed |
| A2A call redirected http to https | registry uses `https://...internal.<domain>` |
| Wrong `ORCHESTRATOR_URL` | built by Bicep, smoke logs in through the proxy |
| Shared rate-limit bucket | proxy-hop settings, verified with two client IPs |
| SSE buffered or cut at 240 s | stream cap 120 s, frame-timing check in I5 |
| Specialist cold start over 60 s | min replicas 1 |
| Public URL abused | IP restriction, generated passwords, enforce budget, TPM cap, budget alert |
| Azure Policy denies public PG or local auth | `azd provision --preview`; reference profile as fallback |

The full 29-row catalogue (job log lag, telemetry, card origin under `a2a`, Next bind address, and so on) goes into `docs/troubleshooting.md` with I3.

**Validation chain.** (1) Offline on every PR, no Azure credentials: `az bicep build` and lint on every profile and flag combination, `shellcheck` and PSScriptAnalyzer, and a pytest on the compiled ARM JSON asserting the env-var table per app, probe limits and that every secret is a Key Vault reference. (2) What-if via `azd provision --preview`, `workflow_dispatch` only, OIDC, never on `pull_request`. (3) `smoke.sh`: revisions healthy and job succeeded, frontend 200, admin and alice login, one keyed chat turn with a product card, one approval with no model (a return over $500 in `workflow:return-replace`, decided by support, timeline shows finalize), SSE timing (first frame under 15 s, more than 10 frames). (4) Playwright with `E2E_BASE_URL=$FRONTEND_URL`. (5) `.github/workflows/azure.yml`: offline on PR; dispatch what-if; dispatch deploy-and-test behind a required reviewer, `azure-down` in an `always()` step, leftover-resource assertion. No schedule, ever.

**Work breakdown (about 73h, 9 working days)**

| PR | Scope | Size | Verified |
|---|---|---|---|
| P1 | `db-init` image, `scripts/db_init.py`, `seed --if-empty`, `SEED_STAFF_PASSWORD`, build-images matrix and smoke import | 7h | offline |
| P2 | Optional `LLM_TEMPERATURE`, `DB_POOL_MIN/MAX_SIZE`, .NET `sslmode`, gpt-5.1 cost rows | 6h | offline |
| P3 | `AZURE_OPENAI_AUTH=key\|identity`, `AZURE_CLIENT_ID`, `azure-identity` | 7h | offline code, Azure for the flip |
| I1 | `infra/` skeleton and offline CI job | 9h | offline |
| I2 | Container Apps environment, apps, job, opt-in auth and MCP | 9h | offline build |
| I3 | `azure.yaml`, hooks, up and down scripts | 8h | offline, then Azure |
| I4 | `smoke.sh`, Playwright env, `azure.yml` | 7h | offline, then Azure |
| I5 | First real deploy from a clean subscription, fix-forward, measured SSE and proxy hops, one keyed eval run on gpt-5.1 (re-recorded, never copied) | 6h plus wall clock | Azure |
| I6 | Reference profile: VNet, private Postgres, Managed Redis, ACR, `A2A_IDENTITY=token`, identity default | 10h | Azure |
| I7 | Build-from-source mode with `remoteBuild` | 4h | Azure |

Each PR carries its own Tests, UI, Seed, Env and Docs lines: P1 `configuration.md` and `deployment.md`; P2 `configuration.md` and the parity matrix; P3 `security-guide.md`; I1-I3 `docs/azure-deployment.md` (draft), README "Deploy to Azure", `troubleshooting.md`; I5 `deployment.md` App Insights section (B6), a parity row "Azure deployment: Python stack only", CHANGELOG. `.gitignore` gains `.azure/`. P1, P2 and P3 touch files other lanes own; confirm ownership first. The job image needs the next `:main` publish before I2 can reference it.

**Open risks.** Prompts and evals were tuned on gpt-4.1; gpt-5.1 behaviour is unmeasured until I5. Five behaviours need a live subscription: ARM readiness blocking, proxy-hop counts, the OpenAI API version for gpt-5.1 tool calling, MCP host headers on internal FQDNs, and the managed OTel agent on a preview API version. Consumption egress IPs are not static, which matters only for Phase D allow-listing. D-1 is unanswered; the demo costs about $4-12 a day while it is up.

### Documentation and README sync (audit of 2026-11-20, nothing fixed yet)

28 drift findings, 8 README gaps. High: README, `CITATION.cff` and others say "A2A-style ... #88" though A2A 1.0 shipped (D-6); `docs/adding-an-agent.md` teaches a nonexistent `A2AAgentHost` instead of `shared.agent_host.create_agent_app` (D-7); `architecture.md` and `telemetry.md` repeat it and omit `/a2a/v1`, the agent card and the identity token (D-8). Medium: migration range and table count (D-1 to D-3), Redis 7 versus 8 (D-5), metrics and deployment doc claims (D-9, D-10), env-var catalogue (D-11), API route counts (D-14), docs missing from the site nav and README table (D-16), stale `docs/index.md` and `roadmap.md` and `CHANGELOG` (D-18 to D-20), `generate_embeddings` run from the wrong directory (D-25). README gaps: no table of contents, features, project layout, testing or configuration section; no hero GIF (#87); diagram and MAF badge use indigo; no release badge; `CODE_OF_CONDUCT` and `SECURITY` not linked. Missing community files: `SUPPORT.md`, a dependency-review workflow (CONTRIBUTING claims one), a secret-scan job. Fix order: the three high items, then the medium group as one docs-sync PR, then the README restructure after the hero GIF exists. The README follows an open-source layout (pitch, badges, hero GIF, table of contents, features, architecture diagram in the house palette, quick start with prerequisites and the keyless path, configuration, project layout, testing, docs index, roadmap, contributing, security, license, support).

---

# Part I — The sequence

Ordered. Each phase states what it unblocks, so an item that moves shows what it drags with it.

| # | Phase | Size | Gate to start | Runs in parallel with |
|---|---|---|---|---|
| A | ~~Azure unblock — frontend proxy + agent registry~~ | **done** | — | — |
| B | Azure Container Apps + Bicep + teardown + managed identity | ~4 d | A (done) | B2 |
| B2 | Dual-backend parity gate into CI | ~1 d | now | B |
| C | Foundry as model provider | ~2 d | B, [decisions](#0-decisions-required-before-phase-c) | LG1 |
| LG1 | **LangGraph Rung 1** — one specialist behind A2A | ~4 d | B | C, D |
| D | Foundry Hosted Agents — MAF *and* LangGraph | 3–5 d | C, LG1 | — |
| E | Publish `docs/azure-deployment.md` | ~2 d | D | — |
| LG2 | **LangGraph Rung 2** — full Python third backend | TBD, sized by LG1 | LG1 findings + explicit go | — |
| LG3 | **LangGraph Rung 3** — three-stack parity gate | ~2 d | LG2, B2 | — |

Everything below Part I is **unsequenced backlog** — real work, no position claimed.

---

## 0. Decisions required before Phase C

Three questions gate the back half. Phases A, B and B2 are correct under every answer, so they
start now; C onward assume the defaults below until told otherwise. Recorded here rather than
carried in conversation, because a plan that depends on an unwritten assumption is a plan that gets
relitigated.

- [ ] **D-1 — Azure spend ceiling.** Phases B–D burn money continuously (ACA, Foundry,
      Application Insights); the LangGraph work burns only eval tokens. The .NET eval suite is
      currently deferred over **~$1.50**, which is the strongest available signal that spend, not
      time, may be the binding constraint. If it is, LG1 moves ahead of B and the Azure phases wait.
      *Default assumed: spend is not the binding constraint, order stands.*
- [ ] **D-2 — primary objective.** Repository credibility as a worked Azure reference, or reach for
      the article series. "MAF vs LangGraph" is the higher-traffic reader question; "MAF on ACA" is
      the more useful artifact. The order below optimises for the second and gets the first as a
      by-product at LG1. *Default assumed: reference first.*
- [ ] **D-3 — does .NET stay at full parity?** A third backend plus a parity-locked .NET stack means
      three stacks in every gate, forever. Freezing .NET at the v1.3 feature set is a defensible
      answer and would need to become a recorded decision in §17 either way — including in
      `docs/parity-matrix.md`, which currently reads as an open commitment.
      *Default assumed: .NET stays at parity; revisit at LG2.*

---

## A. Azure unblock — **done**

**Unblocked: everything.** Both items were contract changes that any additional backend
inherits, which is why they came before Phase B rather than during it.

### A.1 — the frontend no longer knows its backend's address

`NEXT_PUBLIC_API_URL` was compiled into the client bundle, and the Container Apps FQDN does not
exist until provisioning. The browser now calls its own origin and
`web/src/app/api/[...path]/route.ts` forwards `/api/*` to **`ORCHESTRATOR_URL`**, a server-side
variable read per request.

- [x] Catch-all route handler — streams the response body straight through, forwards the client's
      abort signal, preserves upstream status (a 401 flattened to 502 would log the user out
      instead of triggering `api.ts`'s refresh-and-replay), and strips hop-by-hop headers.
- [x] `api.ts` defaults to a relative base; `NEXT_PUBLIC_API_URL` kept only as a direct-call
      escape hatch. Download links go through a new `apiUrl()` helper.
- [x] `ORCHESTRATOR_URL` wired through `web/Dockerfile` (build arg deleted), all three compose
      files, `.env.example`, `docs/configuration.md` and `docs/deployment.md`.
- [x] Nine unit tests for the proxy, plus a real `next start` against a stub orchestrator: SSE
      frames arrive at the rate they are emitted (0.4 s apart, not batched at the end),
      `accept-encoding` reaches the backend as `identity`, `Authorization` survives, and an
      unreachable orchestrator returns 502 rather than hanging.
- [x] **Verified against the live compose stack**, which is what actually closes this. Login,
      authenticated reads and a streamed Azure OpenAI chat turn all through the proxy. Timed
      against a control on the same turn: **8.09 s to first frame proxied vs 7.93 s direct, 227 vs
      215 delta frames** — the proxy costs ~160 ms on an 8 s turn and adds no buffering.
- [x] **Playwright, 10 of 12 specs, through the proxy** — `ui-smoke`, `all-roles`,
      `shopping-flow`, `orchestration-parity` (including *the orchestration graph renders for a
      workflow mode*, which is the live-graph criterion), `chat-generative-ui`,
      `chat-followup-context`, `chat-ui-verify`, `ui-features`, `chat-all-users`, `chat-shopping`.
      All green. `demo-recording` was not run — its return-turn HITL gap is a known open item, not
      a Phase A regression — and `readme-screenshots` generates assets rather than gating.

**Two things worth carrying forward.** `next.config.ts` `rewrites()` cannot do this: Next
evaluates it during `next build` and bakes the result into `routes-manifest.json`, so the
destination would have been a build-time constant — the same problem in a new place. And
*deleting* the browser's `accept-encoding` is not enough, because undici substitutes its own
default when the header is absent; it has to be pinned to `identity`. That one was invisible in
unit tests and only showed up against a real server.

**Three pre-existing test defects surfaced and were fixed.** Each failed *identically* against a
control frontend built to bypass the proxy (`NEXT_PUBLIC_API_URL` escape hatch, calling the
orchestrator directly), so none was caused by this work — that control is the only reason the
claim is worth anything:

- `chat-generative-ui` asserted uniqueness where the app renders one card per result. `5★`,
  `Region`, `Code` and `Discount` each resolved to two elements on a live run. The author had
  already hit this for `Warehouse`, scoped it with `.first()`, and written down the reasoning —
  *"the claim here is that a real DataTable rendered, not that exactly one did"* — then assumed
  `Region` was unique to the stock table. It is not. Same fix, applied consistently.
- `chat-shopping`'s order-card assertion had the same shape.
- `chat-shopping`'s add-to-cart flow selected `[class*="grid"] > a`. The product grid stopped
  rendering anchors when the cards moved to `onClick` + `router.push`, so the selector matched
  nothing and the test burned its full 90 s timeout. Broken for as long as that refactor is old.

The direction matters: all three now assert **presence**, which is the criterion the parity gate
already states, rather than uniqueness the model is free to violate by doing more of its job.

**This retires a constraint.** `NEXT_DIST_DIR` existed because a warm build directory could serve
a second dev server the first one's baked API URL — one of the two ways a dual-backend run could
lie. A build now encodes nothing about the backend, so that failure class is gone. The *other*
way it can lie has not gone away, it moved: a frontend started with the wrong `ORCHESTRATOR_URL`
fails in exactly the same shape, so `assertFrontendTalksToOrchUrl` stays and its comment now says
why.

### A.2 — the agent registry fails loudly

The reported problem was "`AGENT_REGISTRY` is hardcoded host:port". The actual one was larger and
is now [a row in `reported-vs-actual.md`](../../docs/reported-vs-actual.md): **a validating parser
already existed on both stacks, was tested, and no production call site used it.** All four sites
re-parsed the JSON by hand, and three of them swallowed a malformed value into an empty registry —
which builds, serves, passes a health check, and cannot route.

- [x] `shared/factory.py::parse_agent_registry` — the pure validator, with the cached
      `get_agent_registry` layered over it. Rejects malformed JSON, a non-object, a blank URL and
      a scheme-less one, naming the offending agent. **Scheme and host are checked, the port is
      not** — a managed endpoint does not have one, and requiring it would reject exactly the
      deployment this validates for.
- [x] `orchestrator/agent.py` and `orchestrator/handoff.py` both use it; the silent
      `except JSONDecodeError: return {}` in the handoff path is gone.
- [x] .NET mirrored: `AgentSettingsLoader.ParseAgentRegistry` throws instead of returning an empty
      dictionary, and `HandoffMode.Registry()` delegates to it rather than deserializing its own.
- [x] Tests on both stacks, asserting the same accepted and rejected inputs — a stack that accepts
      what the other rejects is a parity gap.
- [x] **Verified, no code change needed:** the A2A client concatenates
      (`url.rstrip("/") + "/message:send"`) with no port or scheme assumption, so an
      `https://…azurecontainerapps.io` endpoint with no port already works.

### A.3 — managed identity, moved to Phase B

Not a blocker and it was wrong to file it as one: a key in Key Vault deploys fine. It moves into
Phase B, where the Bicep already provisions the vault and the identity. It stays a **release**
blocker for Phase E — key-only Azure guidance is the first thing a reviewer would flag.

Two findings that shrink it:

- **`agent-framework-openai` already supports Entra.** `OpenAIChatCompletionClient` takes
  `credential: AzureCredentialTypes | AzureTokenProvider` and resolves it to
  `azure_ad_token_provider` internally, so Python is `credential=DefaultAzureCredential()` in place
  of `api_key=` at two call sites in `shared/factory.py`, plus relaxing `_validate_azure()` under
  `AZURE_OPENAI_AUTH=identity`.
- **.NET already references `Azure.Identity` 1.21.0.** It is a constructor overload in
  `ChatClientFactory` and `EmbeddingClientFactory`.
- **The risk is the dependency, not the auth.** `azure-identity` is not in
  `agents/python/pyproject.toml`, and adding it means a `uv sync` — the operation that has wiped
  `agent_framework/__init__.py` in this repo before. Do it deliberately, with `patch_maf.py` to
  hand. (It happened again during this phase: a bare `uv run` rebuilt the venv from scratch and
  removed ruff. `uv sync --all-packages --extra dev` restored it.)

## B. Azure Container Apps (~4 d)

`infra/` Bicep, `azure.yaml`, `azure-up.sh` and `azure-down.sh` written and tested **first**.

### The agreed target topology

Decided, not proposed. Phase B builds the Container Apps half of it; the agents move to Foundry at
Phase D.

| Component | Target | Why |
|---|---|---|
| Next.js frontend | **Container Apps** | External ingress; the only public surface |
| **All six agents** | **Microsoft Foundry hosted agents** | Foundry owns the runtime. Landed at Phase D |
| MCP servers (product, inventory) | **Container Apps** | Already OAuth 2.1 resource servers, so they are defended by tokens rather than by network isolation — which is what makes them reachable from a Foundry-hosted agent without a private-link design |
| PostgreSQL + pgvector | **Azure Database for PostgreSQL Flexible Server** | A database container has no durable-storage story worth publishing. pgvector is an extension here, not an image |
| Redis | **Azure Cache for Redis** | Rate limiting only; a containerized fallback stays documented for demo cost |
| auth-server | **Container Apps** | Not an agent, and "no external IdP" is a selling point — it stays self-hosted rather than becoming Entra |
| seeder | **Container Apps Job** | Runs once and exits. As a service it restart-loops |

**Because all six agents go to Foundry, the A2A question stops being optional.** Phase D's
acceptance already requires an A2A call to be proven or explicitly recorded as not working; under
this topology a negative answer does not just get recorded, it forces a choice — reach specialists
over hosted MCP instead, or have the orchestrator speak Foundry's protocol to them. Two
consequences to design for in Phase B rather than discover in Phase D:

- **Foundry egress to Container Apps must be proven early.** Every specialist and both MCP servers
  are reached from inside the Foundry runtime. If that path does not exist, the topology does not
  either. Cheapest possible probe, run during Phase B.
- **Identity headers.** `X-User-Email`, `X-User-Role` and `x-session-id` ride on every A2A call and
  land in ContextVars every tool reads. Invocations is schema-free pass-through and should carry
  them; Responses is OpenAI-compatible and will not. That decides the protocol, not just the taste.
- **HITL resume across sandboxes** stays the largest unknown: `workflow:return-replace` pauses on
  `ctx.request_info` and resumes from a checkpoint in a *later* HTTP request. Hosted agents give
  each session its own sandbox. Durable sessions make this better than today; non-durable ones
  break it. Spike this before committing Phase D's shape.

- [ ] **B4/B5 — database ordering.** pgvector must be enabled before `init.sql` runs, and the seeder
      job must be ordered before index creation. (This is the same failure class as the IVFFlat
      empty-table bug fixed in v1.1 — an index built before its data is silently wrong, not loudly
      broken.)
- [ ] **B6 — telemetry has no Azure sink documented.** Application Insights section in the
      deployment doc.

*Acceptance:* from a clean subscription, `azure-up.sh` produces a working public URL where a
signed-in user completes a chat turn with product cards and one approval gate; Playwright passes
against that URL via `E2E_BASE_URL`; `azure-down.sh` leaves zero resources **and no soft-deleted
vault**.

**Cost and teardown are part of the deliverable, not an afterthought.** A reader who cannot cheaply
undo it will not try it, so `azure-down.sh` is written here alongside the provisioning, not after.

## B2. Dual-backend parity gate into CI (~1 d) — parallel with B

**Moved ahead of the third backend.** It was previously filed under eval completeness, after the
cross-framework work. That was the wrong order: adding a third stack to a parity harness nobody runs
automatically triples the number of claims resting on a gate run by hand.

- [ ] Get `scripts/e2e-both-stacks.sh` running in CI. It runs only locally today because it needs
      both stacks up against a seeded database. [ADR 0005](../../docs/adr/0005-dual-stack-parity.md)
      records this as its own honest weakness.

*Acceptance:* a PR that removes a capability from one stack fails CI on that PR, demonstrated by
actually removing one and watching it go red before restoring it.

## C. Foundry as model provider (~2 d)

- [ ] `LLM_PROVIDER=foundry` on both stacks, embeddings endpoint resolved, one hosted web-search
      tool on `review-sentiment` as the demonstration.

*Acceptance:* the eval smoke suite passes under the Foundry provider, and a live turn shows the
hosted tool called in the Application Insights trace.

## LG1. LangGraph Rung 1 — one specialist behind A2A (~4 d)

**The cheapest thing that produces the cross-framework content, and the thing that sizes LG2.**
Starts after B, in parallel with C and D.

Because ADR 0001 put A2A between the orchestrator and every specialist, the third backend is not
all-or-nothing. Replace exactly one specialist — `review-sentiment` is the candidate: fewest tools,
no money-moving actions, no HITL gate, and it is already the agent slated to carry the Foundry
hosted tool at C — with a LangGraph implementation behind the identical A2A contract. The MAF
orchestrator does not change.

- [ ] LangGraph `review-sentiment` serving the same `/message:send` contract, the same
      `config/prompts/review-sentiment.yaml`, the same database.
- [ ] Register it through the `AGENT_REGISTRY` seam built at B3, so it is selectable rather than
      hardcoded.
- [ ] Run the existing `review_sentiment` eval dataset against it and record the score beside the
      MAF Python and MAF .NET numbers. **Re-recorded, never copied** — the same rule that governs
      the .NET baselines, for the same reason.

**Sizing input for LG2, established by reading the code:** the tool layer is far less MAF-coupled
than the previous plan assumed. All ten files under `shared/tools/` couple to the framework through
**exactly one line each** — `from agent_framework import tool` — with bodies that are plain async
Python over asyncpg and `Annotated[..., Field(...)]` parameters. LangChain's `@tool` reads that
signature style natively, so the tool surface ports on a decorator swap or a thin adapter. Prompts
are YAML and framework-neutral. `shared/db.py`, `auth.py`, `oauth/`, `search.py` and `context.py`
import no framework at all. What is genuinely expensive is the orchestration layer: the five modes,
`hitl.py`, `context_providers.py`, the guardrail middleware chain, `checkpoint_storage.py` and
`remote_agent.py`. LG1 exists to turn that estimate into a measurement.

*Acceptance:* a live chat turn where the MAF orchestrator routes to the LangGraph specialist over
A2A and the frontend renders its cards with no client change; one eval dataset scored on the
LangGraph implementation; a written note of what the port actually cost per layer.

## D. Foundry Hosted Agents (3–5 d)

- [ ] Orchestrator packaged for Invocations, specialists reached via hosted MCP, a Responses variant
      built alongside with the graph loss documented.
- [ ] **New at this revision:** deploy the LG1 LangGraph specialist to the same hosted runtime via
      `langchain_azure_ai.agents.hosting`. This is the payoff for sequencing Azure first — one
      runtime, two frameworks, and the comparison becomes a property of the deployment rather than
      an essay.

*Acceptance:* both MAF variants deployed and callable; the Invocations variant drives the existing
frontend unchanged; one A2A call to an ACA specialist either proven to work or **explicitly recorded
as not working, with the reason**; the LangGraph agent answering through the same hosted runtime.

## E. Publish (~2 d)

- [ ] `docs/azure-deployment.md` on the site with **verified cost numbers, not estimates**, covering
      all three topologies.

| Topology | Who owns the runtime | Who owns the model |
|---|---|---|
| 0 — Docker Compose | you | you |
| 1 — Azure Container Apps | you | you |
| 2 — Foundry as model provider | you | Foundry (model + hosted tools) |
| 3 — Foundry Hosted Agents | Foundry | Foundry |

One deployment is a tutorial. The same six agents in three topologies, with what each costs, is a
reference — the difference between the topologies *is* the content.

## LG2. LangGraph Rung 2 — full Python third backend (size set by LG1)

**Gated on LG1's findings and an explicit go decision.** Python only, as scoped. Same frontend, same
database, same prompt corpus, same eval datasets — so the differences are attributable to the
framework rather than to the problem. That is the artifact that is genuinely hard to find elsewhere.

- [ ] LangGraph orchestrator plus the remaining four specialists.
- [ ] HITL through LangGraph `interrupt()` + checkpointer. Worth calling out in the write-up: this
      is a *better* fit than MAF .NET's `RequestPort` workaround (see Constraints), and a real
      asymmetry between the three implementations rather than a tie.
- [ ] Guardrail middleware chain and grounding ledger reimplemented or adapted.
- [ ] Decide and record whether all five orchestration modes are ported or a documented subset is.

*Acceptance:* every eval dataset scored on all three backends from re-recorded fixtures; the mode
matrix stated honestly, including what was not built and why.

## LG3. LangGraph Rung 3 — three-stack parity gate (~2 d)

- [ ] Extend `web/e2e/orchestration-parity.spec.ts` and `scripts/e2e-both-stacks.sh` to three
      stacks. **Requires B2 first** — see the reasoning there.

*Acceptance:* `PARITY_GAPS.langgraph` empty **while every test in the spec asserts presence**, the
same exit criterion applied to .NET.

---

# Part II — Unsequenced backlog

Real work with no position claimed in Part I.

## 11. Open items from plan 20

- [ ] **The .NET eval suite.** 6 of 7 datasets ported; record-on-miss and the `IEmbeddingProvider`
      seam are merged. Needs a live key: record fixtures for all six, generate .NET baselines —
      **re-recorded, never copied from Python**, since .NET has a different mode set and its
      absolute scores legitimately differ — and add a CI job gating on *baseline regression* rather
      than an absolute floor, because the score is a property of the recording session (measured
      spread: 8 points across four identical recordings). `red_team` needs its own schema and
      evaluator and stays separate. **Deferred for budget (~$1.50), not difficulty** — which is why
      it is also the evidence behind decision D-1.
- [ ] **The demo clip.** Eight attempts found five defects that each let the run exit 0 while
      silently dropping the approval and resume beats: wrong mode for the gate, a regex that cannot
      span `" & "`, locators keyed off button text that changes after the first switch, four
      simultaneous data constraints, and a return that can only be initiated once per order. All
      five are fixed and the spec now **throws** instead of logging. *Still open:* the return turn
      does not reach the HITL gate and the run hits its 600 s cap with no `hitl_requests` row.
      The prompts are fine — FTS ranks `Allbirds Wool Runners` at 0.67.

## 12. Eval and gate completeness

The dual-backend gate moved out of this section into **B2**. What is left:

- [ ] **An eval gate for the MCP path** — run each dataset twice, native tools versus MCP, and fail
      CI if the MCP run scores below the native baseline. MCP is offered as an alternative
      data-access layer with nothing measuring whether it is as good.
- [ ] **A red-team evaluator.** `red_team.json` is scored by keyword matching, which means very
      little; it needs its own schema and judge.

## 13. Retrieval and the tool surface

- [ ] **Typed filter DSL** — replace `search_products`' flat parameter list with a structured
      `ProductFilters` model. Text-to-SQL was considered and rejected
      ([ADR 0002](../../docs/adr/0002-no-text-to-sql.md)): `user_email`/`user_role` scoping lives in
      ContextVars and dynamic SQL would bypass that contract. A typed DSL gives the model
      flexibility at the boundary while keeping SQL generation server-side and auditable.
      *Cheaper before LG2 than after* — every tool signature ported is one more copy to change.
- [ ] **Publish the two MCP servers to PyPI**, so any MCP client can run them against any
      PostgreSQL database without this codebase. That is the honest test of whether they are a real
      integration surface or internal plumbing with a protocol on top.
- [ ] **Prompt caching** — cache system prompts and tool schemas per agent. Worth doing *now*
      rather than earlier because the cost counter that shipped in v1.3 can measure it.

## 14. Cross-framework comparison — the options not taken

The rung ladder in Part I is the middle option below. The other two remain open and separable.

- [ ] **Claude and other providers as a third model backend.** One chat client per stack behind the
      existing `LLM_PROVIDER` switch. Both backends keep their orchestration; only the model
      changes. Mostly answers "is this locked to OpenAI?". **Worth doing before LG2 finishes**: a
      three-framework comparison run on a single model family is confounded by nothing, and this is
      the switch that proves it.
- [ ] **Agentic workflows on the repository itself** — coding agents for eval recording,
      documentation-drift checks, review. Ships nothing in the product; improves the rate at which
      everything else gets done.

## 15. OAuth — later phases

Phases A–D shipped and are live-verified: the authorization server, user login brokered by the
orchestrator, client-credentials inter-agent auth, and the MCP servers as OAuth 2.1 resource
servers — all on both stacks. The design and per-phase notes for what shipped are documented in
[`docs/security-guide.md`](../../docs/security-guide.md).

- [ ] **Key rotation.** A single active signing key per `kid`, with no automatic rotation, is the
      known gap. `AUTH_SIGNING_KEY_ENCRYPTION_KEY` and per-service `OAUTH_CLIENT_SECRET` must come
      from a secret store in any real deployment; the `OAUTH_SEED_KEY` dev default must never ship.
      **Phase B forces this** — Key Vault is in the Bicep either way, so rotation stops being
      theoretical the moment there is a secret store to rotate into.
- [x] **RFC 7591 dynamic client registration** — done: `auth_server/register.py`, opt-in with
      `AUTH_ALLOW_DYNAMIC_REGISTRATION`, scope-limited to `client_credentials` and the two MCP read
      scopes, behind a `client:register` bearer. Not open registration.
- [ ] **Audit matrix** covering which routes accept which token type.

## 16. Blocked, waiting on upstream

- [ ] **MCP 2.x migration** — blocked on `agent-framework-core`. Listed rather than deleted, because
      an item that vanishes looks like a decision nobody made.
      **Update 2026-11-15: no longer blocked.** Core 1.23+ requires `mcp>=2.2`, so the MAF upgrade
      forces this migration; it is PR B-6 in Part IV.

## 17. Known debt

- [ ] **Frontend lint debt.** `no-explicit-any` and `react-hooks/set-state-in-effect` are errors again
      (API layer typed in `web/src/lib/api.ts`; auth and cart are `useSyncExternalStore` stores in
      `auth-store.ts` / `cart-store.ts`, page loaders go through `useLoadEffect`). What is left is the
      `@next/next/no-img-element` warnings (use `next/image` where practical) and a handful of unused
      imports.
- [ ] **The chat page hard-crashes on an unexpected API shape.** Found during plan 20's UI
      verification; unowned. **Blocks nothing until LG1**, at which point a second framework starts
      producing payloads and an unexpected shape stops being hypothetical.
- [ ] **`embeddings=0` in the default local stack**, so `semantic_search` is lexical-only until
      `scripts/generate_embeddings` has run. Correct behaviour, surprising default.

## 18. Recorded decisions — not pending work

Listed so they are not rediscovered as gaps.

- **Superseded 2026-11-15: the LangGraph backend is parked** (Part IV §5, IV-4). The three lines
  below record the earlier decision and no longer describe planned work.
- **The third backend is LangGraph, Python only.** Not the Claude Agent SDK, and not a .NET
  equivalent. Python only keeps the comparison to one variable.
- **The third backend ships as rungs, not as a release.** LG1 is a spike that must produce a number
  and a cost note; LG2 does not start on momentum.
- **Azure precedes the third backend, but only through Phase B.** The previous plan gated the
  comparison on all of A–E. Phase D carries the most unknowns and the LangGraph work does not depend
  on it.
- **No .NET container images are published.** The demo path stays Python-only; a visitor is there
  for the features, not the backend language. `--dotnet` remains build-from-source.
- **No Langfuse sink on .NET.** OTel already carries GenAI spans to Aspire; a second exporter would
  be additive only.
- **Anonymous storefront conversations are not persisted** on either stack, so follow-ups there have
  no context at any tier. A product decision, not a bug.
- **The .NET stack seeds and authenticates with Python images.** `seeder` and `auth-server` are
  shared rather than duplicated: the seeder is the single source of demo data, and a second
  implementation would have to produce byte-identical rows or the dual-backend parity gate becomes
  meaningless. Neither service is an agent, so neither demonstrates anything about MAF.
- **Superseded 2026-11-15:** .NET MAF 1.28.0 ships `MagenticWorkflowBuilder` (Part IV §7), so the
  "unavailable on .NET" half of the next line is no longer true. The app still has no Magentic mode.
- **Magentic orchestration exists in neither stack**, and on .NET it is unavailable rather than
  unbuilt: Magentic is Python-only in MAF v1. Chapter 16's .NET tests reflect over
  `Microsoft.Agents.AI.Workflows` and assert the gap still exists, with a control test so a failed
  assembly load cannot masquerade as the same result. Not a parity gap, because neither side has it.
  An earlier version of this line said .NET ships `MagenticWorkflowBuilder`; it does not, and that
  error had propagated into `docs/parity-matrix.md` and `docs/roadmap.md`.
- **Text-to-SQL was considered and rejected.** See ADR 0002.

## 19. The pattern worth carrying forward

**The reported problem has been smaller than the actual one every single time**, and every time the
difference was found by running something rather than reading it. That table is now a published
page — [`docs/reported-vs-actual.md`](../../docs/reported-vs-actual.md) — with eight rows. It is the
most useful thing in this repo for anyone deciding how much to trust an issue title.

---

# Part III — Constraints and verification

## Constraints that still bite

Carried forward because each was learned the hard way and still governs future work.

**Live runs catch what tests cannot.** The .NET orchestrator could never route — `EcommerceContextProvider`
returned a fresh `AIContext`, discarding the caller's messages and clearing every tool — and 418
tests passed while it was broken. It was found by pointing `AZURE_OPENAI_ENDPOINT` at a logging proxy
and reading what actually reached the model. Every item above must be exercised against a running
stack, not just unit-tested. **This is also the load-bearing argument for the Part I order:** a third
backend with no deployment target can only ever be exercised on a laptop.

**Two ways a dual-backend run can lie; one is now closed.** The Playwright base URL override is
`E2E_BASE_URL`; a run setting anything else silently drives whichever frontend is on `:3000`. That
one still bites. The second — `NEXT_PUBLIC_*` inlined at build time, so a second `next dev` booting
off a warm build directory served the first one's API URL — was closed by Phase A: a build encodes
nothing about the backend now. The *shape* of that failure survives as a wrong `ORCHESTRATOR_URL`,
so `assertFrontendTalksToOrchUrl` stays and still fails at login when the frontend's token is
rejected by `ORCH_URL`. `NEXT_DIST_DIR` is kept but is no longer load-bearing — and it has a cost worth knowing before
anyone follows the dual-stack dev instructions: a `next build` with a custom dist dir **rewrites the
committed `web/tsconfig.json`**, adding that directory to `include` and reformatting the file. Found
by using it during Phase A verification. Deleting the option is now a defensible cleanup. **Both failure modes
get worse at three stacks** — LG3 must extend these assertions, not just add a third URL.

**A shared failure is not a parity gap.** Diffing the two backends' failure sets is what makes a
dual-stack run interpretable. Three assertions were "fixed" in the wrong direction before this was
applied — the inventory test's badge assertion depends on which of two equally-valid tools the model
picked, and its heading assertion (`product_name || "Stock & Fulfillment"`) failed on a *different*
backend each time. At three stacks the diff is three-way and the rule holds harder, not less.

**MAF .NET has no `ctx.request_info` equivalent.** Pausing from inside an arbitrary executor is not
possible; it requires a dedicated `RequestPort` node. Python's two-call `execute()`/resume-via-
`responses={...}` maps to one long-lived `StreamingRun` cached across the pause — verified
empirically: break out of the event stream on `RequestInfoEvent` *without disposing the run*, then
`SendResponseAsync` and open a fresh `WatchStreamAsync()`. LangGraph's `interrupt()` plus a
checkpointer is a third shape again, and the three-way difference is publishable content at LG2.

**In-workflow HITL resume needs the first stream drained.** Breaking out at the first `request_info`
— the obvious move for an HTTP handler — leaves the workflow with `_is_running=True`, and the
resuming `run()` raises `RuntimeError: Workflow is already running`.

**More than one agent can share this working tree.** Another Claude session works in this repo
concurrently. A `git add -A` while its uncommitted work was present swept 35 of its files into a
.NET commit; the branch it thought it had was empty, so from its side the work had simply vanished.
Stage explicit paths, never `-A`, and commit or stash before switching branches — `git checkout`
drags uncommitted work across and that is the actual failure mode, not file overlap. The two
branches shared zero files.

Ownership as agreed: `agents/dotnet/**` and `docker/postgres/init.sql` here; `tutorials/**`,
`.env.example`, `CLAUDE.md`, `agents/python/shared/*` there. `docs/quick-start.md` and `README.md`
are shared and split by section — model selection and the free-tier path there, Windows/WSL2,
PowerShell and `dev.ps1` here. The `ChatClientFactory` → `IChatClient` refactor spans
`agents/dotnet/src/` and six `Program.cs` files, so it wants one uninterrupted window; announce it.
**Phase A and LG1 both touch `agents/python/shared/*`, which is the other session's path** — agree
ownership before starting either.

**The repository is the source of truth; the site is a rendering.** `scripts/build_docs_site.py` is
strictly one-directional. Nothing may live only on the site, and no Jekyll front matter may be
committed into the repo's markdown — it renders as a metadata table on GitHub and would duplicate
every page's H1. `--check` enforces link resolution, nav parentage, and title uniqueness under a
parent (just-the-docs matches nav by title, so a collision silently collapses the tree).

## Verification

Per the execution discipline established for the .NET work: **verify locally, treat CI as
confirmation.** CI's unique value is the independently-seeded database — the two bugs it caught that
local runs could not were both environment-specific by nature.

```bash
# .NET
cd agents/dotnet && dotnet test ECommerceAgents.sln          # 598 passing

# Python
cd agents/python && uv run pytest                            # 838 passing

# Web
cd web && npx vitest run && npx tsc --noEmit && npx eslint .  # 176 passing

# Docs site
uv run python scripts/build_docs_site.py --check             # 97 pages, 0 broken links
uv run python scripts/check_tutorial_readmes.py --check      # 34/34 chapters

# Dual-backend gate — the definition of done for parity
scripts/e2e-both-stacks.sh -- e2e/orchestration-parity.spec.ts
```

**The exit criterion for parity is `PARITY_GAPS.<stack>` being empty *while every test in the spec
asserts presence*.** It is empty for .NET today, and the demo-clip item is the proof that "empty" is
only as strong as the tests in the file — closing resume means adding a test first, watching it
fail, then fixing it. The same criterion applies to LangGraph at LG3.

---

# Part IV — The November 2026 audit: everything that needs no Azure

Added 2026-11-15. A full audit (Python, .NET, web, tutorials, docs, CI, plus a landscape review)
produced issues #80–#104; [#103](https://github.com/nitin27may/e-commerce-agents/issues/103) is the
tracking issue. This part plans **every one of them that needs no Azure resource** — 22 of the 24 work items. The
two that do, #89 (`azd up` to Container Apps) and #91 (Foundry), stay where they were: Part I
phases B–E.

Parts I–III are unchanged by this except where noted:

- **§16 is out of date.** MCP 2.x is no longer blocked upstream — and upgrading MAF past core 1.22
  *forces* it. It is now PR B-6.
- **§11 "The demo clip" has a new lead.** The return flow makes no model call, so the stall is
  reproducible with no key. See C-1.
- **§17 `embeddings=0`** is PR A-6a.
- **Part I is not resequenced here.** Where this part recommends doing something before Phase B
  (the zero-key demo, the trust fixes), that is a recommendation for the maintainer, recorded in §5.

## 1. How this was planned, and who builds it

| Activity | Model | How |
|---|---|---|
| Planning | **Opus** | Five `planner` subagents, one per workstream, each required to verify the issue's claims in code before planning around them. Re-plan the same way when a PR's premise turns out wrong. |
| Building | **Sonnet** | The main loop under `opusplan` (see `.claude/agents/README.md`), or one Sonnet agent per PR in its own worktree when PRs are independent. One PR per branch; no PR spans workstreams. |
| Testing | **Sonnet** | The `test-runner` subagent after every PR: the commands named in that PR's row, then the gates in §6. |
| Review | Opus, repo default | `code-auditor` on anything sized L, anything touching auth, approvals or money, and every PR that changes a wire format. Not requested for this plan; it is what `.claude/agents/` already configures, and can be dropped. |

**The planning pass earned its cost by contradicting the issues.** Every workstream found the filed
problem was not the real one — the pattern §19 already records. The five that change the work most:

1. **The return workflow creates the return, issues the label and marks the order returned *before*
   the approval gate.** Rejecting only skips a discount (D.0). #92 was filed as "the customer can
   approve their own request"; that is true and is the smaller problem.
2. **On the .NET stack, anyone who can reach `:3000` can act as any user, including admin**, with the
   compose default secret: the orchestrator accepts `X-Agent-Secret` + `X-User-Email` from any caller
   and the frontend proxy forwards those headers from the browser (A.0). Not in #81 as filed.
3. **Python's tool-output sanitizer lists two tools that do not exist and omits seven that do**,
   including `search_reviews`, which returns raw review text (E.0).
4. **Cost is overstated about 3×** (cached input billed at full price) while specialist spend is
   not counted at all (E.0).
5. **There is no single jump to current MAF.** Core 1.23 forces MCP 2.x, so the upgrade is two
   stages with the fixture corpus checked between them (B.0).

Three issue claims were wrong and are corrected in the workstreams: the streaming bug is Python-only
(.NET is already correct); Durable Task cannot demonstrate durability without Azure, so #95 is built
on Postgres checkpoints and timers; structured output is the wrong tool for chat cards (B-19 instead).

## 2. Rules every PR in this part follows

- **No Azure.** Nothing here provisions or calls an Azure resource.
- **No live key behind an automatic trigger.** New CI jobs run on `LLM_PROVIDER=replay` and assert
  that no `*_API_KEY` is set. Recording is always a human running a script locally.
- **Fixtures are the constraint that orders everything.** A replay fixture is keyed by a hash of the
  messages, tool schemas and instructions. So: never edit `config/prompts/_shared/` in passing; never
  reword a `@tool` description in passing; keep anything new (usage, approval ids, model config) out
  of the text the model sees. After B-1, run `uv run python -m evals.fixture_drift` before and after
  any change near prompts or tools — exit 0 or 1 is fine, 2 means the change belongs in a recording
  session.
- **Schema changes are migrations** once A-10 lands. Until then a schema PR says so and needs
  `./scripts/dev.sh --clean`.
- **Both stacks, or a row in `docs/parity-matrix.md`.** Each workstream lists what is recorded rather
  than ported.
- **The path-ownership split in Part III still applies.** Several PRs touch
  `agents/python/shared/*`, `tutorials/**`, `.env.example` and `README.md`; agree ownership before
  starting, stage explicit paths, never `git add -A`.
- **Next.js 16:** read `web/node_modules/next/dist/docs/` before any frontend PR that uses a
  framework convention (C-12, A-4, B-8).

## 3. Key-free work versus recording sessions

**Most of this plan needs no key at all.** The PRs that do are batched into three sessions so nothing
is recorded twice. Every session is a person running a script on a local stack.

| Session | When | Key | Records | Unblocks | Estimate |
|---|---|---|---|---|---|
| **R0 — the tour** | After wave 1 and B-3 | OpenAI or Azure OpenAI (the existing one) | The ~10-prompt demo tour (C-6), the catalog embeddings export (A-6b), and the default-prompt fixtures for tutorial chapters 04, 05, 07, 12, 13, 14, 16 (found by C-16) | C-7 → C-11, C-18 | $1–3 |
| **R1 — everything else, one sitting** | When B-6 has merged and B-16, D-5b, M-2 are *ready on their branches* — those three change what the model sees, so the replay gate fails until their fixtures are refreshed. Record against the ready branches and merge code and fixtures together | Same | New eval cases: multi-turn, role, approval, argument (E-3b); red-team v2 + judge verdicts (E-4); guardrail FP/FN (E-6); MCP-path suites (E-7); all six .NET datasets and baselines (E-8, closes §11); five-mode benchmark (E-10); re-record of cases touching gated tools (D-5b); `merchant_ops` dataset (M-2); group-chat fixtures (B-16); a tour re-check | E-9 and every row marked R1 | $5–9 |
| **R2 — other vendors** | After P-3, P-4, P-5 | Anthropic, Google; optionally local Ollama at $0 | Wiring smoke for each provider; the model scorecard, one fixture directory per model (P-7) | P-3–P-5 sign-off, P-7 | $1–3 per provider |

Estimates are the planners' and assume OpenAI's automatic prompt caching; treat them as order of
magnitude. Take the modal result of a recording, not the best roll (`evals/fixtures/README.md`).

**R0 is deliberately early and R1 deliberately late.** The tour is insulated from most later churn —
three of its eleven beats use no fixtures at all, and its prompts avoid the approval-gated tools — so
recording it early buys the zero-key demo months sooner at the risk of one cheap re-record. Everything
else waits until the upgrades and new tools have stopped invalidating fixtures.

## 4. Sequence

Waves are ordered; PRs inside a wave are independent unless the workstream's own "Order" line says
otherwise.

| Wave | What | PRs | Key |
|---|---|---|---|
| **0 — stop the bleeding** — **built 2026-11-15**, see §7 | Small, independent, each fixes something that is wrong today | A-1 (streaming), A-2 (secrets, impersonation, loopback ports), D-1 (no self-approval), E-1 (sanitizer), B-9 (stream fallback re-runs tools), C-1 (keyless approval repro), A-5a, A-5b (stale claims), A-3 | none |
| **1 — gates and foundations** | What later waves stand on | A-7a → A-7b (ruff), A-8a/b/c, A-10 (migrations) → A-11 (`/ready`) → A-9, A-12, A-4, A-6a (embeddings), B-1 (fixture drift) → B-2, B-4 → B-5 (.NET MAF), C-2 → C-3 (tour determinism), C-12, C-14, C-16 → C-17 (tutorials), C-19, E-2a → E-2b, E-3a, E-3c, P-1, B-10, A-13 | none |
| **2 — first upgrade, demo surface, approvals core** | | B-3 (core 1.22), C-4 → C-5, D-2 → D-3 → D-4 → D-5a, D-7, D-8, E-2c → E-2d, E-5, P-2, P-4, P-6, M-1, X-1 | none |
| **R0** | Record the tour | C-6, A-6b | **yes** |
| **3 — the demo ships** | | C-7 → C-8 → C-9, C-10 → C-11, C-13, C-18, B-11 (build only; its run needs a key) | none |
| **4 — second upgrade, real A2A, flows** | | B-6 (core 1.26 + MCP 2) → B-7, B-8, B-12 → B-13 → B-14, B-15, B-18, B-19, D-5n, D-6, D-9a → D-9b, M-3 → M-4, V-1 → V-2, B-17, P-3, P-5, P-8, C-20. **Not built (an earlier revision said "held on their branches"; no such branches exist, see the Reconciliation section). Each needs R1 recordings:** D-5b, B-16, M-2's dataset, E-3b, E-4, E-6, E-7, E-8 — each invalidates or needs fixtures, so it merges with its recording, not before | none for what merges |
| **R1** | Record everything else | see §3 | **yes** |
| **5 — publish the numbers** | | E-9, E-10, P-7 (after R2), new tutorial chapters from #100 | R2 for P-7 |

**If only the key-free work is wanted**, waves 0–2 are complete without a key, and most of wave 4
merges without one. What waits for R1 is anything that changes what the model sees (D-5b, B-16) and
the datasets and baselines R1 produces.

**Cross-workstream edges that are easy to miss**

- A-10 (migrations) precedes every schema PR: D-3, M-1, V-1, E-2d, A-12.
- A-1 precedes E-2c (same lines in `chat.py`) and B-11.
- D-1 changes who can approve, so the tour spec (C-6) approves as admin.
- A-6a keeps the replay embedding path, which the tour depends on (C.0 item 4).
- B-3 precedes D-8, so resume is validated once against the 1.22 checkpoint format.
- B-12/B-13 precede any cross-agent approval; it should use A2A `input-required`.
- New tools from D, M and V must pass E-1's coverage test.
- B-6 precedes P-3 (Anthropic on Python), E-7 (MCP gate) and any revisit of X-2.

## 5. Decisions for the maintainer

Each workstream ends with its own table; defaults are assumed unless changed. Four cut across them:

- [ ] **IV-1 — Does the zero-key demo go ahead of Part I Phase B?** This part assumes yes: waves 0–3
      before Bicep. The case: a visitor cannot evaluate the repo today without a key and twelve
      containers, and official `azd` multi-agent samples sit in the low hundreds of stars, so a
      deployment is required for credibility but is unlikely to be what makes the repo found.
      *Default assumed: yes.*
- [ ] **IV-2 — Recording budget.** R0 + R1 come to roughly $6–12, R2 to $1–3 per extra vendor. D-1 in
      §0 already flags spend as possibly the binding constraint; this is the number to decide it on.
      *Default assumed: R0 and R1 approved, R2 optional.*
- [ ] **IV-3 — How far does .NET follow?** This part ports the approvals API (D-5n), real A2A (B-13)
      and the trust fixes, and *records rather than ports* the SLA worker, the return lifecycle beyond
      approval, merchant ops and event autonomy. That is a partial answer to D-3 in §0 and should be
      made on purpose. *Default assumed: as listed.*
- [x] **IV-4 — LangGraph is parked.** Decided by the maintainer 2026-11-15: no LangGraph backend.
      LG1, LG2 and LG3 in Part I are parked, not scheduled, and nothing in this part depends on them.
      This supersedes the LangGraph lines in §18 and the LG rows in Part I's table. The reader-facing
      copies — `docs/roadmap.md` and README "What's next" — still describe it and are corrected in
      A-5b.

## 6. Verification for this part

In addition to Part III's commands:

```bash
# After B-1 — the fixture corpus is still valid
scripts/replay-evals.sh
cd agents/python && uv run python -m evals.fixture_drift       # 0 clean, 1 offline-fixable, 2 re-record

# After A-7b
cd agents/python && uv run ruff check . && uv run ruff format --check .

# After A-10
uv run python scripts/migrate.py                                # idempotent on an existing volume

# After C-7 — the whole point
./scripts/dev.sh --demo --no-key                                # no OPENAI_API_KEY in the environment
cd web && pnpm exec playwright test e2e/demo-tour.spec.ts --trace off

# After B-12 / B-13
cd agents/python && uv run pytest tests/test_a2a_conformance.py
scripts/e2e-both-stacks.sh --mixed py-net -- e2e/orchestration-parity.spec.ts
```

**Done, for this part, means:** from a fresh Codespace with no secrets, the tour completes including
one approval that a customer cannot grant themselves; the official A2A client completes a streamed
task against a Python and a .NET specialist; `fixture_drift` exits 0 on current MAF; and every number
on the eval scorecard was produced by a command in this file.


## 7. Build log

What building found, wave by wave. Plan rows above are left as written; this section is where they
are corrected.

### Wave 0 — built 2026-11-15

All nine steps built by Sonnet builders, each required to confirm the problem in code first.

| Step | Outcome |
|---|---|
| A-1 | As planned. Python-only bug confirmed; `shared/sse.py` adds `sse_frame` and an `aiter_sse_events` reader. .NET gained `SseFraming.Frame` for `\r` handling. |
| A-2 | As planned. **The .NET impersonation path was real**: the shared-secret branch ran before Bearer handling and never checked `_isOrchestrator`. **Python was not affected** — its orchestrator uses Bearer-only `require_auth`. |
| A-3 | As planned; `scripts/lib/env-secrets.sh` + `scripts/test-env-secrets.sh`. **`dev.ps1` was never executed** (no `pwsh` in the build environment) — hand-reviewed only. |
| A-5a | As planned. `group_chat.py` moved to public imports; `pre_purchase.py` and `return_replace.py` still use private ones (B-2). |
| A-5b | As planned, plus one unplanned correction: the roadmap claimed the .NET MCP host serves both domains; it serves inventory only. |
| B-9 | (d) was partly wrong: .NET `A2AClient.StreamAsync` never re-posted. It now yields an interruption note after a mid-stream drop, matching Python. The `history` parameter stays on `A2AClient` because handoff mode's in-memory context may not be in Postgres. |
| C-1 | **The suspicion was wrong.** The return flow pauses and resumes with no model call and `request_id` is extracted correctly; the test is kept as a regression guard. **The demo-clip stall (§11) is still unexplained** and is not this. |
| D-1 | As planned, both stacks. Only `web/e2e/demo-recording.spec.ts` approved as the requesting customer; it now approves as admin but could not be run. |
| E-1 | As planned. Allowlist went from 12 keys (2 phantom) to 17, equal to .NET. **Follow-up, not done:** `recall_memories` and `get_user_profile` return user-authored text and are unsanitised on both stacks. |

**Review of this wave found two defects in what it had just built**, both raised by Copilot against
the plan text and both true of the code:

- B-9's "retry only if no frame arrived" was not safe: a read timeout with zero frames does not
  prove the specialist did nothing. The retry is now limited to failures that prove the request was
  never processed.
- D-1 made resume admin-only, which silently changed *whose* identity the post-approval steps run
  under: the loyalty-discount step read the approving admin's tier. Resume now executes as the
  requester and records the reviewer.

An independent diff review then found four more, all fixed before merge: an admin could still
approve their *own* return (resume never compared approver to requester); a claimed approval stayed
in `processing` forever if the request was cancelled mid-resume; the stream reader still dropped
text after Unicode line separators that `httpx.aiter_lines()` splits on; and the sanitizer missed the
`body_preview` field that carries review text.

**Left open by this wave, needs a schema change (so it waits for A-10):** a process crash between
claim and resume still strands the row in `processing`. `hitl_requests` has no column recording when
a row was claimed, so a stale claim cannot be detected. Add `claimed_at` and a TTL takeover as the
first migration after A-10; D-3's `approvals` table should carry it from the start. The resumed steps
are side-effect free today (`get_loyalty_tier` reads, finalize edits in-memory state), which is what
makes releasing the claim on failure safe — D-8 changes that and must make those steps idempotent.

Six further corrections from the same review were to this plan and are applied in place: R1 timing
(fixture-changing steps merge with their recordings, not before), B-4/B-5 ordering, B-11's key
requirement, replay-miss propagation in C-4, new tables as migrations only, and specialist usage on
the stream endpoint in E-2a.

### Wave 1 — built 2026-11-15/16

| Step | Outcome |
|---|---|
| B-4 | .NET MAF 1.18.0 → **1.28.0**, nothing pinned short. Production needed **no source change**: none of the HITL, checkpoint or telemetry areas flagged in Part III forced a redesign. `global.json` at the repo root pins SDK 10.0.100 with `rollForward: latestFeature`. |
| B-5 | All 62 tutorial projects on central package management at production's versions, no source fixes. |
| — | **Magentic now exists in .NET.** `Microsoft.Agents.AI.Workflows` 1.28.0 exports `MagenticWorkflowBuilder` and its events; chapter 16's tripwire fired as designed and was inverted rather than deleted (it now asserts the types exist). §18's "Magentic exists in neither stack … unavailable on .NET" is **superseded**: the app still has no Magentic mode on either stack, but that is now a choice, not an upstream limit. Chapter 16's .NET side is "not yet ported" — a rewrite against the .NET API, not a transliteration. |
| — | DevUI .NET is still prerelease-only (`1.28.0-preview`); chapter 20b stays not ported. |
| C-16 | 24 of 32 chapter entry points run with no key, enforced by a `tutorials-keyless` CI job. **Seven need a recorded fixture for their default prompt** — 04, 05, 07, 12, 13, 14, 16 — and are on the runner's skip-list with that reason; record them in R0. 20b is a live-only server. Chapter 18 also calls no model (not listed in #104); chapter 23 is not a long-running server and runs keyless. `.env.example` sets `LLM_PROVIDER=openai`, which disables the fallback for anyone who copies it — documented, not changed. |
| C-17 | As planned. No time estimates were invented; the paths carry none. |
| A-10 | As planned. First real migration is `0002_hitl_claimed_at.sql`, which closes wave 0's stale-claim gap: a `processing` row whose claim is older than `HITL_CLAIM_TTL_SECONDS` (600) can be taken over, both stacks. **Limit:** a resume genuinely still running past the TTL can finish alongside the new claimant — harmless while the post-gate steps are side-effect free; D-8 must guard it. |
| A-11 | `/ready` on every service, both stacks, same JSON. **Found by testing:** with Postgres paused the probe hung for minutes because it borrowed a pooled connection; each probe now opens its own short-lived connection. A plain endpoint was used on .NET instead of `AddHealthChecks` so the shape matches Python. Only the .NET orchestrator image lacked curl. |
| A-9 | Written, **never run end to end**: Docker Hub returned 429 on image pulls in the build environment. Exercised only against host-run Python services. CI is its first real run. |
| A-6a | As planned. Also: `EmbeddingClientFactory` (.NET) no longer crashes at startup with no key. The precomputed file does not exist yet (A-6b, R0). |
| C-2 | As planned. First attempt broke a fixture — tour orders for Electronics shifted the counts in a recorded "trending electronics" answer — so the tour orders are Home and Clothing. `--reset-tour-user` also clears idempotency keys for the (now stable) tour order ids. `seed_workflow_checkpoints` still uses `uuid4`. |
| B-1 | As planned, with one wrong claim: adding `response_format` to the key **did** change one hash — tutorial 29's planner call sets it — re-keyed offline. The script tracks its own PIDs instead of `pkill`. `fixture_drift` cannot detect prompt drift (nothing stable to diff against). |
| C-3 | Every one of the 102 fixtures changed hash (each instruction block contains example ids and dates); renamed offline, contents untouched. |
| E-2a/b | Specialist usage now reaches `usage_logs` through both endpoints, both stacks; the orchestrator eval suite's recorded input tokens went 38,659 → 99,847. **The .NET orchestrator had been logging zero tokens for every turn.** Cross-stack pairing is asserted by a shared literal, not exercised. No `model`/cost columns yet (E-2d). On .NET the *streaming* cost gate still does not tally. |
| E-3a | Five new case fields; ~25 existing cases gained argument assertions from committed fixtures, prompts unchanged. HITL is detected from a new `hitl_requests` row because the middleware sits outside the step recorder. |
| E-3c | As planned. `total_tokens_in/out` already existed in the report. |
| A-4 | As planned on CORS, capture default and pins (`uv` 0.11.32, `aspire-dashboard` 13.6.5, verified by manifest). **Next 16 cannot tell a route handler the client's address**, so by default none is forwarded and all anonymous callers share one bucket; `WEB_TRUSTED_PROXY_HOPS` restores per-client limits behind a real proxy. `scripts/e2e-both-stacks.sh` raises the auth limit for the stack it starts, or the parity specs would hit 429. |
| A-12 | Migration is `0003`. **Two .NET holes nobody had filed, both closed:** signup honoured a client-supplied `role` (anyone could sign up as admin), and read `full_name` where the client sends `name`. Passwords over 72 bytes returned 500 on both stacks. OAuth-mode rotation recorded as parity row 29. |
| B-2 | 17 app modules and 15 tutorial files moved to public imports; two names allowlisted (`encode_checkpoint_value`, `decode_checkpoint_value`). `patch_maf.py` deleted. |
| B-10 | Nothing in the running app used the loader. `visualize_workflows.py` now renders the real `pre-purchase` and `return-replace` graphs; the diagram drift-check CI job had a broken invocation and is fixed. `MermaidExporter.cs` went with the .NET loader. |
| P-1 | As planned. Found: `docker-compose.yml` and `docker-compose.dotnet.yml` never forwarded `LLM_BASE_URL`, so a compatible endpoint only worked host-run or via the demo file; all three forward it now, and the Python two forward `LLM_API`. |
| C-12, C-14, A-8a/b/c, C-19, A-13 | As planned. `ui-smoke` runs with no backend (9/9). The MCP integration job is a per-package matrix because the two `tests/conftest.py` files cannot be collected together. CodeQL / dependency-review action tags were not verifiable offline. |

Carried forward from this wave:

- The pricing-promotions baseline is stale (0.727 measured vs 0.680 committed, on `main` too). Refresh it in R1.
- Re-seeding an already-seeded database makes one product-discovery case miss its fixture; a fresh database is clean. `dev.sh` re-seeds on every start, so C-7's no-key demo must seed fresh or this must be fixed first.
- `.env.example` sets `LLM_PROVIDER=openai`, which disables the tutorials' keyless fallback for anyone who copies it.
- `dev.ps1` has now been edited by three steps and executed by none.

### Wave 2 — built 2026-11-16

| Step | Outcome |
|---|---|
| B-3 | Python MAF core 1.14.0 → **1.22.0** (`-openai` 1.17.0, `-orchestrations` 1.3.2 — the highest that resolve under `core<1.23`). No application code changed and **nothing needed a live recording**. 57 eval fixtures and 22 tutorial fixtures were re-keyed offline: at 1.21 an error `function_result` gained an `exception` field (1 fixture); at 1.22 every `function_call` gained `"hosted": false` (56). **`fixture_drift` reported clean while the replay suite missed fixtures**: core 1.21 stamps a random `af-call-<uuid>` on each function call and 1.22 copies it to the result, so the next turn's request never matched. Both replay clients now drop those ids from the key. Tutorial 08's MCP fixture had its stored *request* rewritten to 1.21's tool-result shape while keeping the recorded *response* — a candidate for a real re-record in R0. A 1.14 checkpoint still decodes under 1.22; the reverse does not hold. `encode_/decode_checkpoint_value` are still private. Tutorial fixtures have no drift tool; they were rebuilt with an uncommitted script — worth committing one before B-6. |
| D-2 | As planned. The auth-server has no role allowlist (it copies `users.role` into the token), so it needed no change. `seed_support_user` is appended and draws no random numbers. |
| D-3 | Migration `0006`. `approvals` carries `claimed_at`/`claimed_by` from the start; the self-approval rule is a database CHECK as well as repository code; `approval_events` is append-only by trigger. |
| D-4 | One YAML read by both engines, 89 golden cases run on both stacks, and each stack asserts every rule is hit by a case. The default thresholds are the builder's, not derived from anything: >30 days since delivery, ≥3 returns in 90 days, refund >0.5× lifetime spend, amount >500, or a <30-day account with >100 → manager; ≤50 in good standing, or ≤150 for silver/gold → auto; else support. Python rejects an unquoted numeric `policy_version`, .NET accepts it. |
| D-5a | One decide path; the legacy resume and admin approve/deny routes are shims over it. **Resume widened from admin-only to staff** (support or admin, checked against the assigned level). `auto` is recorded, not executed, and still goes to a person — acting on it is D-5b, which needs re-recorded fixtures. A bad amount (above the amount under review) or an unknown resolution is 422 before anything is claimed. |
| D-7 | Migration `0011` (0006 had no parked state and no role-addressed notifications). Timers are scheduled inside `repo.create`/`transition`, so no caller can forget them. **Gap found at integration, fixed:** the worker's top-level timeout only flipped the approval to `expired`, leaving a paused return resumable; a separate `approval.timeout_apply` row now resumes it as a rejection (separate because the breach handler holds the approval row lock, so resuming in place would deadlock). The worker image has never been built locally (registry blocked). |
| D-8 | As planned, and the dataclass spike passed: `ReturnDecision` round-trips through `PostgresCheckpointStorage` and a fresh-process resume at core 1.22 (`FileCheckpointStorage` refuses custom types). Migration `0010` expires approvals paused under the old shape. `initiate_return` now honours `refund_method = store_credit`. **Not done:** `returns.approval_id` / `returns.resolution` columns (D-9). The .NET resume route still carries only approve/reject. |
| E-2c | As planned. Workflow modes emit an explicit zero marked `no_llm_calls`. The Python group-chat moderator is a local synthesiser; the .NET one is an LLM call — a real difference between the stacks. No .NET tests for handoff or group-chat usage (non-injectable client factory). |
| E-2d | **No cached-input price was entered** — the repo documents none and none was invented — so cost is *still* overstated for cache-heavy runs. The mechanism is in place; a maintainer must enter rates from the provider's pricing page. Migration `0007`. **Wrong in the wave-1 log:** the .NET cost gate did not merely fail to tally on streams — nothing ever started the running total, so it was always zero, blocking path included. Fixed. |
| E-5 | First measured numbers for the injection detector, against 36 synthetic injections and 502 natural texts: 0 false positives on natural text, recall 52.8% (19/36), precision 76% once ten hand-written near-misses are added. Sanitizer recall 58.3%. Off by default in the eval script. `security-guide.md` had claimed the red-team suite "continuously measures" the detector; it does not, and no longer says so. |
| C-4 | As planned plus two things the plan missed: group-chat panelists swallowed a replay miss and wrote the fixture hash into the transcript; a failed workflow executor's `error` event named the hash. `ReplayChatClient` has no chat-middleware layer, so the tool loop is stopped by a function middleware raising `MiddlewareTermination`. A miss now also sends the `metadata` frame, so the conversation id survives. |
| C-5 | Built; covered by component tests, `tsc` and build only — never seen in a browser. Nine draft chips; every one currently produces the miss callout because no tour is recorded. |
| P-2 | No shipped prompt file sets a model; a test pins the composed prompt byte-for-byte. On .NET cost still falls back to the global model, not the per-agent one. |
| P-4 | `Anthropic` NuGet 13.8.0, no dependency bump. Model comes from `ANTHROPIC_MODEL` (compose forwards `LLM_MODEL` defaulting to gpt-4.1). **Never made a live call**; Claude models are unpriced. Python rejects the provider with a message pointing at .NET. |
| P-6 | Migration `0008`. The dimension was hardcoded in six places. `agent_memories` is not truncated by the reset script (real rows; only its unused vector column is nulled). Two prompt files still tell the model the dimension is 1536 — left, because changing prompt text invalidates fixtures. The Ollama path is documented, not exercised. |
| M-1 | Migration `0009`. Costs are price × a per-category ratio (0.50–0.78) and the limits (15% margin floor, 10%/day, 25 lines) are the builder's placeholders. Every query touching `products`/`promotions` selects named columns, so no tool output changed. |
| X-1 | Both packages build, pass `twine check --strict`, install into a clean venv and answer `initialize`. Found: the documented Claude Desktop config launched an HTTP server where stdio is needed (added `--transport stdio`); READMEs linked the wrong GitHub account. Maintainer-only: PyPI trusted publishers, the `pypi`/`testpypi` environments, name availability. |

Carried forward from wave 2:

- **#106 is unmerged** behind one high-severity CodeQL alert with no location (the four located ones are fixed). It now carries waves 1 and 2.
- Migrations arrived out of order (0011 before 0008–0010). The runner applies late lower numbers cleanly; verified on a fresh database and on one already at 0011.
- Concurrent builders collided three times on `docs/parity-matrix.md` row numbers. Give each builder its row number up front.
- Enter real cached-input and Claude prices in `shared/cost.py` / `CostEstimator.cs`.
- The tour's approval step must be done as admin: the tour order routes to `manager`.
- `dev.ps1` has now been edited by five steps and executed by none.

### Wave 3 — built 2026-11-16

| Step | Outcome |
|---|---|
| B-6 | Python core 1.22.0 → **1.26.0**, `-openai` **1.18.2**, `-orchestrations` **1.4.2**, `mcp[cli]` **>=2.2,<3** (resolves 2.3.0); exactly the plan's set, mirrored in `tutorials/`. **Plan errors / gaps:** (1) core declares `tenacity` only in its `all` extra but imports it lazily for MCP catalog recovery from 1.25, so every MCP connect failed with a bare `ModuleNotFoundError` until `tenacity` became a direct dependency. (2) The MCP 2.x client is built on `httpx2`; `MCPStreamableHTTPTool(http_client=...)` rejects `httpx.AsyncClient`, so `build_mcp_http_client()` had to change (OAuth-mode MCP only; no test covered it before — `tests/test_mcp_client_tool.py` now does, over a real socket). (3) The server port was larger than "rename FastMCP": `host` moved from the constructor to `streamable_http_app(host=...)` (the DNS-rebinding 421 guard), `AuthSettings` warns unless `validate_token_resource` is set (set False, the 1.x behaviour), a path-less issuer no longer gets a trailing slash in protected-resource metadata, and the client types went snake_case (`server_info`, `is_error`, a 2-tuple from `streamable_http_client`). (4) No telemetry test needed changing for the 1.25 span rename: none asserted a workflow span name. **No prompt, tool description, signature or output changed. `fixture_drift` exits 0 (102 fixtures), `replay-evals.sh --setup` has no missing fixtures and every score equals its baseline except pricing-promotions 0.727 (stale baseline 0.680).** The only fixture touched is tutorial 08's tool-result turn: the MCP 2.x client adds `_meta`/`resultType` to the `function_result`, which is in the key, so the stored request was rewritten and the file re-keyed offline (response byte-identical; the model-visible text is unchanged). Not run: `docker compose --profile mcp up` (ghcr.io is blocked here, so the images cannot be built) — instead the product MCP console script was run against a seeded Postgres and driven through `MCPStreamableHTTPTool` with a Docker-network `Host` header. DevUI stays at 1.0.0b260813 (resolves and its tests pass under core 1.26; the newer b261112 is a separate bump). The two MCP packages' own version numbers were not bumped; do that when they are next released, since they now require mcp 2. |

### Wave 4 — D-5n built 2026-11-16

| Step | Outcome |
|---|---|
| D-5n | The unified approvals API on .NET, over the same tables (no migration): `Routes/ApprovalRoutes.cs` (the five routes, Python's paths, JSON, status codes and `{"detail"}` bodies), `Approvals/ApprovalsService.cs` (the one decide path), `Shared/Approvals/{ApprovalRepository,ApprovalIntake,Outbox}.cs` (guarded transitions, fenced TTL claims, escalate, timers in the same transaction). The legacy resume and admin approve/deny routes are shims over it; **resume moved from admin-only to staff bounded by the assigned route**, as on Python. `HitlGate` and the paused-return chat route open policy-routed `approvals` rows; the gate's own decision routes a return, not a second evaluation. 77 new .NET tests (903 → 980) across `ApprovalRepositoryTests`, `ApprovalRoutesTests`, `ApprovalCrossStackTests` and the gate/pause tests. **Cross-stack proof:** a .NET test dumps the rows .NET writes from Postgres and compares them to a committed fixture; `test_approval_dotnet_rows.py` loads that fixture and runs the real Python worker handlers (reminder, escalation, stale timer) over it, 4 passed. |

**Plan errors and findings from D-5n:**

- **The plan row said the parity e2e exercises approvals, so removing the fixed gaps from `web/e2e/parity-gaps.ts` was part of the step. `PARITY_GAPS.dotnet` was already empty** — no approvals test is in that gate, so there was nothing to delete and the gate does not prove this port. The proof is the .NET suite plus the cross-stack fixture; adding an approvals case to the parity gate belongs with D-6's `approvals.spec.ts`.
- **"Confirm" is not ported because it does not exist on `main`:** D-5b (customer confirm) is held on its branch until R1, so neither stack has `pending_confirm`, `/confirm` or `event: approval`. Recorded in the matrix as not on either stack.
- **A gated .NET tool call could never have been approved.** `HitlGate` stored the pipeline's PascalCase name (`CancelOrder`) and camelCase argument keys (`orderId`), while `HitlActionExecutor`, the policy kinds and Python key on `cancel_order` / `order_id`; approving a real .NET request returned "Auto-execution not configured". The old tests seeded rows by hand in the snake_case shape, so they never saw it. The gate now stores Python's shape (the `HitlGateTests` expectation changed with it, deliberately).
- **The .NET executor had drifted from Python's:** `process_refund` updated `orders` by `order_id` (the tool takes a `return_id`) and `initiate_return` could insert a second return for an order. Both now match Python's executor, and the executor is keyed by approval id through `IdempotencyGuard.RunOnceAsync`.
- **Real limit, recorded as row 36:** the shared worker resumes a paused return with Python's `ReturnReplaceMode` and Python's checkpoint storage, so a *.NET* return cannot be auto-expired at the top level (the row would be retried and parked). Reminders, escalation and tool-approval expiry work. Reasoned from the two storage implementations, not run end to end.
- Also: a resume now holds two claims (the approval's and the `hitl_requests` row's), so the .NET takeover tests had to age both, exactly as the Python helper was changed to do.

### Wave 4 — B-12 built 2026-11-16


| Step | Outcome |
|---|---|
| B-12 | Built as 12a (server) and 12b (client), Python only. **Spec 1.0, JSON-RPC binding, `a2a-sdk[http-server]` 1.3.0** (its `PROTOCOL_VERSION_CURRENT`; 0.3 is only a compatibility shim there and is not switched on). Custom `SpecialistExecutor` over `_run_agent_native_stream`; `tests/test_a2a_conformance.py` shows the model's messages are equal on both wires. Mounted at `/a2a/v1`; the legacy routes are byte-for-byte unchanged and the card moved from a four-field stub to a spec card. `A2A_TRANSPORT=legacy\|a2a` (default `legacy`) on `call_specialist_agent` and `RemoteSpecialistChatClient`. **Verified:** conformance and transport tests with the official client over `ASGITransport` and a real uvicorn socket, on Python 3.13 and 3.12 (the image's); the replay gate with `A2A_TRANSPORT=a2a` and with `legacy`, both exit 0, zero missing fixtures, every score equal to its baseline except pricing-promotions 0.727 (stale 0.680); the six routing-eval specialist calls went over `/a2a/v1` in the first and `/message:send` in the second. `fixture_drift` exits 0 (102 fixtures). **Plan errors and gaps found:** (1) "text deltas with `append=true`" — the SDK rejects `append=true` for an artifact that does not exist, so the first chunk is `append=false` and the last carries `last_chunk`. (2) The SDK server requires an initial `Task` event before any status update. (3) Plan carried only *steps* in extension metadata; usage and the replay miss need the same treatment (three URIs) or the orchestrator's cost accounting and the demo's miss handling regress. (4) The SDK merges status metadata into `Task.metadata` last-write-wins per key, so one steps update per step would lose all but the last on a blocking call: steps carry an `offset` and the terminal update repeats all of them. (5) "`contextId` carries the session id": the SDK generates a UUID `contextId` when the caller sends none, indistinguishable from a session id, so invented ids are prefixed `a2a-ctx-` and ignored; the header stays a fallback. (6) **The SDK writes SSE JSON with raw U+2028/U+2029/U+0085, which its own client splits lines on** (`httpx.aiter_lines`): a model reply holding one reached an SDK client as truncated JSON. The dispatcher is wrapped to escape them; the conformance test pins it. (7) The SDK's in-memory task store keeps every task forever — a slow leak of conversations — so it is replaced by a bounded per-caller store. (8) The SDK client wraps every `httpx` error in `A2AClientError`, which would have silently defeated B-9's "retry only what provably never ran"; `root_cause` unwraps it. (9) The equivalence test caught a defect in the first client: the live-step marker was set on a copy, not the dict `current_steps` holds, which sends each step to the browser twice. (10) The card moved `url` into `supportedInterfaces`; nothing in either stack read the old field. **Not verified:** the `agent-framework-a2a` claim in the plan row (that its executor builds a session from `contextId`) was taken from the plan, not re-checked — the package was not installed. **Differences to know:** a client disconnect no longer cancels the specialist's run (the SDK owns the task; `CancelTask` stops it and the orchestrator does not call it yet); activated extensions are not echoed in an `A2A-Extensions` response header; `A2A_TRANSPORT=a2a` against a .NET specialist fails as a card problem (parity row 37). `docker compose` was not run (images cannot be built here). |

**Review fixes (Wave 4, after the independent review of D-5n and B-12).** All three warnings and eight suggestions were confirmed in code and fixed, one commit each (or a tight group), no migration, no prompt/tool change.
- *A2A (W1, W2, S3, S4, S5, S6):* the client refuses a card whose interface origin differs from the registry entry (no message or credential sent, card not cached) and never follows redirects; a streamed call that is dropped, times out or is cancelled sends a bounded best-effort `CancelTask` (a *blocking* call cannot: the task id arrives only with the response, so the per-caller cap is what bounds those); the server caps running tasks per process and per caller (`A2A_MAX_ACTIVE_TASKS` 32, `A2A_MAX_ACTIVE_TASKS_PER_CALLER` 8), refusing with a JSON-RPC error before a task exists and again atomically in the executor; the public card lists only tools `customer` may call, read from `requires_role` (it now records its roles on the wrapped function); S6 was reachable (a failing task store answered `InternalError(str(e))`), so a handler subclass answers the SDK's fixed text and logs through `log_safe`. S4 and S5 were comment/docstring errors only.
- *Approvals, both stacks (W3, S1, S2, S7, parity):* the decider check is re-run after the claim on the tool approve/reject/request-info paths (the workflow path already did); an executed-but-unrecorded tool action is finished by a retry and by `expire` (Python's worker serves .NET too) instead of 409 / expired; a lost claim on the workflow path is a 409; .NET legacy tool rows get snake_case input keys, or are refused with 422 and flagged once when keys collide. Python's rank is case-insensitive and an unknown route fails closed to a manager, as on .NET.
- *S8:* `a2a-sdk` and `httpx2` joined the `maf` Dependabot group; the `actions` group takes minor/patch only.
- **Limits worth knowing:** `A2A_PUBLIC_BASE_URL` must now name the origin the registry uses, or the client refuses the card; the cap's busy refusal is code `-32603` (the spec has no busy code) and the orchestrator treats any returned JSON-RPC error as "may have run", so it reports "could not be confirmed" rather than retrying.

### Wave 4 — B-13, B-14 (and B-11, which had not been built) built 2026-11-16

| Step | Outcome |
|---|---|
| B-13 | Built as 13a (server), 13b (client) and 13c (cross-process interop). **Spec 1.0, JSON-RPC binding, official A2A .NET SDK: `A2A` and `A2A.AspNetCore` 1.0.0-preview4**, pinned exactly in `agents/dotnet/Directory.Packages.props` and added to the Dependabot `maf` group (a preview, decision B-d6). Every .NET specialist (`AgentHost.Build`) now serves `POST /a2a/v1` and a spec card beside the untouched `/message:send` + `/message:stream`; `Shared/A2A/Protocol/`: `SpecialistA2AExecutor` (the same `RunAgentWithHistoryStreamingAsync` the legacy stream uses, so the model sees identical input), `SpecialistA2AServer` (cap pre-check, fixed `Internal error`), `OwnedBoundedTaskStore`, `ActiveTaskLimiter`, `SpecialistAgentCard`, `A2AProtocolClient`. `A2A_TRANSPORT=legacy\|a2a` (default `legacy`) on `A2AClient`, hence on the tool router and the handoff mesh's `RemoteSpecialistChatClient`, with the Python client's rules (registry origin only, no redirects, cancel of an abandoned stream, retry only what never ran). **Verified:** the official A2A .NET SDK client against a real in-memory specialist host (real auth middleware and step recorder, scripted model), mirroring the Python conformance cases; `scripts/a2a-interop.sh` (keyless, a CI job): the official Python `a2a-sdk` client and `shared.a2a_client` against a real .NET specialist process, and the official .NET client and `A2AClient` against a real Python one, 18 + 10 tests, both directions green; the replay gate with `A2A_TRANSPORT=a2a` and with legacy, exit 0, every score equal to its baseline except pricing-promotions 0.727 (stale 0.680). **Plan errors and gaps found:** (1) the plan offered `Microsoft.Agents.AI.Hosting.A2A.AspNetCore` as an alternative: it exists (1.28.0-preview.261106.1) but hosts an `AIAgent` through MAF's own session path, the thing B-12 rejected; it was **not evaluated or installed** here. (2) "Contract test with a JSON golden file under `tests/contracts/a2a/`" was **not built**: the live cross-process check replaced it and found what a golden file written from one stack's assumptions would not (item 5). (3) The .NET SDK does **not** merge a status update's metadata into the task (Python's does), so a blocking `SendMessage` lost steps and usage; the store republishes them. (4) The SDK's `ITaskStore` passes no caller, and a message carrying a `taskId` would continue **another user's task**: ownership is read from the ambient forwarded identity and every read path goes through it (a test per path). (5) **The Python SDK's metadata is a protobuf `Struct`: every number reaches a .NET reader as `5.0`.** The first .NET readers wanted integers and silently dropped a Python specialist's steps and usage; fixed and pinned (`A2AWireTests`). (6) The .NET SDK invents a GUID `contextId` that `Guid.TryParse` accepts (Python's is the same shape); the executor uses `ClientProvidedContextId` instead of a prefix. (7) The card needs "only tools `customer` may call", but .NET enforces roles with guard clauses, not metadata: a `[RequiresRole]` attribute (metadata only) was added to the seven guarded tool methods and `A2ARoleMetadataTests` fails when a guard has none or they disagree. (8) **Pre-existing, found by the run:** over the *legacy* wire the .NET orchestrator dropped every step of a Python specialist (`tool_name` vs `toolName`); `TolerantStepConverter` reads either. The reverse (a Python orchestrator reading a .NET specialist's camelCase legacy steps) is **not fixed**; reasoned from the code, not run. (9) The shared HTTP client refuses a request the SDK's own client already marked as sent, so the guarded handler forwards a copy. (10) The orchestrator's named HTTP client followed redirects on the legacy wire too, carrying `X-Agent-Secret`; it no longer does, on either wire. **Differences to know:** the .NET server echoes the activated extensions in an `A2A-Extensions` response header (the SDK does it), the Python server does not yet; the .NET orchestrator has no `replay_miss` UX (the client recognises the marker, the chat route does not act on it); the .NET SDK serves spec 0.3 as well as 1.0; a blocking call dropped by the client leaves a `working` task on .NET until it is evicted (reasoned from the SDK source, not run). **Not done:** the legacy endpoints stay (one release after both default to `a2a`), `input-required`, push notifications. |
| B-14 | Built as 14a (Python) and 14b (.NET). **`A2A_IDENTITY=headers\|token\|both`, default `both`, on both stacks.** The orchestrator signs the user's identity into a short-lived (5 min, `A2A_IDENTITY_TTL_SECONDS`) JWT sent as `X-User-Context`: HS256, header `typ=a2a-user-context+jwt`, claims `iss`, `aud` = the specialist's registry name, `sub`/`email`/`role`/`sid`, `act.client_id=orchestrator`, `iat`/`exp`/`jti`; a verifier refuses lifetimes over 15 minutes, any other algorithm, any other `typ`. Key: HKDF-SHA256 of `A2A_IDENTITY_KEY`, else `AGENT_SHARED_SECRET`, identical on both stacks (`shared/identity_token.py`, `Auth/UserContextToken.cs`), pinned by `agents/python/config/identity/testdata/user-context.v1.json`, which Python mints and .NET verifies (and a drift test re-mints). `AgentAuthMiddleware` on both stacks: a token that does not verify is refused (never ignored in favour of the headers), a header that contradicts a valid token is refused, `token` mode reads no header, `both` falls back to the headers when there is no token; `GUARDRAILS_STRICT_IDENTITY` still judges what a token carries. `build_a2a_headers(audience)` / `A2AClient.ApplyCredentialsAsync` send what the mode says; the web proxy strips `X-User-Context`. **Verified:** Python `tests/test_identity_token.py` (45) and .NET `UserContextTokenTests` + `A2AClientIdentityTests` (51) cover a forged `X-User-Email` beside a valid token for another user, an expired token, a token for another specialist, another key, alg none, HS512, a login JWT, a tampered payload, headers-only in `token` mode, `headers` mode ignoring a token, `both` still serving an old headers-only orchestrator; the interop run mints on one stack and serves on the other in `token` mode, both directions; the replay gate over a Python stack with `A2A_TRANSPORT=a2a A2A_IDENTITY=token` (every call carries a token and no `X-User-*` header, and is refused without one), exit 0, scores equal to the baselines. **Plan errors and decisions:** (1) the plan row had the **auth-server** do an RFC 8693 token exchange and specialists validate against **JWKS**; the auth-server only runs in `AUTH_MODE=oauth` (not in the default compose topology) and JWKS is an oauth-only path, so that design would have put an IdP on the default call path. Following the brief, the orchestrator mints and the key is symmetric and HKDF-derived from material both stacks already hold. **Consequence, stated plainly: a compromised specialist, which holds the key to verify, could mint a token for any user; asymmetric signing is the follow-up, and it needs key distribution that does not exist outside oauth mode.** What it does buy is in `docs/a2a.md`. (2) The plan sent the token as `Authorization`; that header carries the service token in oauth mode, so it is `X-User-Context`. (3) **The default stays `both`, not `token`:** the compose smoke paths cannot be run where this was built (base images cannot be pulled), and the mixed-stack run needs a key; `token` is the next release's flip, after those pass. (4) The web proxy had to strip the new header (not in the plan). (5) `build_a2a_headers` gained an `audience` argument; one existing test double (`_wrong_credentials` in `test_a2a_transport.py`) had its signature widened, its assertion unchanged. (6) The two stacks disagreed on out-of-range numeric settings (Python's `Field(ge, le)` rejects, .NET would have clamped); .NET now rejects at startup too. |
| B-13/B-14 review fixes | A security review of B-13/B-14 found, and these commits fix: **(W1)** the .NET user-context verifier checked only `exp`: `nbf` ignored, an `iat` in the future accepted (`iat=now+100000, exp=iat+900` passed the lifetime rule and stayed valid for days), no `sub` accepted, float `iat`/`exp` accepted; Python refused all four. Both stacks now apply every time rule against one pinned instant (Python no longer lets PyJWT read the wall clock) and `user-context.v1.json` carries a `negative` list of 24 tokens (each with the spec it was signed from; a drift test re-signs it) that both stacks' tests iterate. **(W2)** the docs, docstrings, `CLAUDE.md` and `.env.example` said a distinct `A2A_IDENTITY_KEY` stops a caller holding only `X-Agent-Secret` from choosing its user; under the default `both` it does not (omit the token, forge `X-User-*`): it holds only with `A2A_IDENTITY=token` **and** a distinct key. Corrected everywhere, plus a startup warning on both stacks (`settings.a2a_identity_key_not_distinct`, never the key) for `token` with the key empty or equal to the shared secret. **(S1)** `jti` is audit-only; no replay cache, stated in `a2a.md` and the security guide (a captured request replays at the same specialist until `exp` + 30 s). **(S2)** .NET `ParseUsage` took negative counts and `(int)n` wrapped a steps `offset`: a negative count is now absent (also in `RunUsage.Add`, covering the legacy wire) and an offset outside 0..int.MaxValue ignores the batch; Python clamped negatives already but raised on an infinity and `int()`-converted any offset, now fixed the same way. **(S3)** each real specialist's public card is built from its real tools and checked (.NET: five `*CardTests` over `SpecialistCardCheck`; Python: every `create_*_agent()`); the order-management guards are `RequiresRole("customer", "seller")`, so a customer may call them and the card correctly lists them. **(S4)** a caller with no resolved user (empty email, or `system`) shared one task owner and could list or read other anonymous callers' tasks: now task-not-found for get, subscribe and continue-by-id, empty list; still served; `CancelTask` stays open to them (the cancel of an abandoned anonymous stream is a legitimate call and needs an unguessable id). Enforced in the handler/server, not the store, because the SDK re-reads the stored task to answer a blocking `SendMessage`. |
| B-11 | **Not built before B-12 as the plan said; built here.** `docker-compose.mixed.yml` + `scripts/lib/mixed-env.sh` + `scripts/e2e-both-stacks.sh --mixed [py-net\|net-py]`: one orchestrator in front of the other stack's five specialists, each specialist's `build` replaced (`!override`) by the other stack's Dockerfile and context, both stacks' DB/Redis spellings set, `AGENT_REGISTRY` unchanged, `BACKEND_STACK` follows the orchestrator. **Verified: `docker compose config` for both variants (and the three existing files) only; the stack was NOT booted** (images cannot be built here) **and the Playwright run was NOT run** (it needs a live key; C-6's recordings cannot serve a .NET specialist). The script's stack selection was exercised against a stub `docker`. **Plan/design errors:** `extends` from the other stack's file was tried first and merges badly (the base's `dockerfile` survives next to the other stack's `context`), so it was dropped for explicit `build: !override`. `A2A_IDENTITY*` and (for .NET) `A2A_TRANSPORT` are now passed through both compose files' shared environment anchor. |

**Verification (Wave 4, B-13/B-14).** `dotnet build` 0 warnings 0 errors; `dotnet test` **1139 passed, 10 skipped** (988 passed before; the 10 skipped are the env-gated interop tests, which pass under `scripts/a2a-interop.sh`: 10 of 10); Python `uv run pytest` **1863 passed, 22 skipped** (1818 passed, 4 skipped before; the 18 new skips are the env-gated interop tests, 18 of 18 passing under the script); `ruff check` and `ruff format --check` clean; `fixture_drift` exit 0 (102 fixtures); `scripts/replay-evals.sh --setup` on a fresh Postgres + Redis, serialized with `flock`: exit 0 and every score equal to its baseline except pricing-promotions 0.727 (stale 0.680), for the default, for `A2A_TRANSPORT=a2a`, and for `A2A_TRANSPORT=a2a A2A_IDENTITY=token`; `docker compose config -q` for `docker-compose.yml`, `.dotnet.yml`, `.demo.yml` and both mixed variants; `bash -n` on the three scripts touched.

**Verification (B-13/B-14 review fixes).** `dotnet build` 0 warnings 0 errors; `dotnet test` **1190 passed, 10 skipped**; Python `uv run pytest` **1973 passed, 22 skipped**; `ruff check` and `ruff format --check` clean; `scripts/a2a-interop.sh` 18 of 18 and 10 of 10; `fixture_drift` exit 0 (102 fixtures); `scripts/replay-evals.sh --setup` with `A2A_TRANSPORT=a2a A2A_IDENTITY=token`, serialized with `flock` on a fresh Postgres + Redis: exit 0, no missing fixture, every score equal to its baseline except pricing-promotions 0.727 (stale 0.680); the new startup warning appeared in its output.

---

### Wave 4 — P-3, P-5 built 2026-11-16

| Step | Outcome |
|---|---|
| P-3 | Python `LLM_PROVIDER=anthropic` through MAF's own connector, **`agent-framework-anthropic==1.0.0b261112`** (`agent_framework.anthropic.AnthropicClient`). **Plan error: "Confirm the class name" and "pin like the other MAF packages" understated one fact: there is no stable release of the package, every version is a beta**, so the pin is an exact pre-release (uv accepts an exact `==` pre-release pin without `--prerelease`). It needs core `>=1.24` and `anthropic>=1.11,<3`; the lock gained exactly three packages (`agent-framework-anthropic`, `anthropic` **2.8.0**, `docstring-parser`) and no existing version moved (core 1.26.0, openai 1.18.2, orchestrations 1.4.2, mcp, a2a-sdk, httpx2 unchanged). It is **not** added to the tutorials (their keyless path needs none, and the tutorial test uses `anthropic` as its "unknown provider" sample). The dependabot `maf` group already matches `agent-framework*`; a comment records it. **Two things the plan's key-free test line missed:** the Anthropic SDK 2.x is built on `httpx2`, so the stub transport is `httpx2.MockTransport` (an `httpx.MockTransport` is rejected with a `TypeError`); and the connector falls back to `max_tokens=1024` when a call sets none, which truncates specialist answers, so `shared/anthropic_client.py` raises it to .NET's 4096 (a thin subclass overriding the connector's `_prepare_options`; a test fails loudly if a MAF bump breaks that). The SDK sends the key as `Authorization: Bearer`, as the .NET package does, and the connector uses the SDK's `beta.messages` namespace. Wired: `get_chat_client`, per-agent `model:` `provider: anthropic` (an agent leaving Claude must name its `model`, as on .NET), `REPLAY_RECORD_PROVIDER=anthropic` in the recorder, `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` (default `claude-sonnet-5-5`, the .NET default) in `shared/config.py` and all three compose files. **Embeddings, as .NET:** no `EMBEDDING_PROVIDER` means the stack starts, the chat client warns once, `get_embeddings_client` raises a message naming what to set (so `semantic_search` uses its full-text arm), the seeder skips, and `generate_embeddings.py` exits *before* its `DELETE`. 25 stubbed-transport tests (blocking and streaming tool round trips, usage 42/16, factory, per-agent, embeddings, seeder, recorder record-then-replay). **Never run against the live API** (R2 owes the smoke run); Claude is unpriced in `shared/cost.py`, as on .NET. Docs and parity row 33 corrected ("needs core >= 1.24 / repo on 1.22" was stale: core is 1.26.0). Two existing tests asserted the old rejection and were rewritten for the new behaviour, not weakened. |
| P-5 | Docs plus a stubbed test only; no provider code (P-1 did the work). `docs/configuration.md` has the Gemini recipe (`LLM_BASE_URL=.../v1beta/openai/`, `LLM_API=chat_completions`, the Gemini key in `OPENAI_API_KEY`, set `EMBEDDING_*` explicitly or the embedding call also goes to the Gemini base URL); `tests/test_gemini_compat_endpoint.py` asserts the request path, bearer key, model and a `tool_calls` round trip. **The plan's caveats (unlisted parameters silently ignored, tool-schema depth limits, structured-output drift) are about Google's server, were not verifiable here, and are not asserted**: the doc points at Google's compatibility page instead. |

### Wave 4 — M-3, M-4 built 2026-11-16 (M-2 left for the keyed session)

| Step | Outcome |
|---|---|
| M-3 | Built as `docs/merchant-proposals.md` describes: `shared/merchant/proposals.py` (pure candidates, guards, storage), `execute.py` (re-check guards on live facts, run once per line under `proposal_line:{id}`), `orchestrator/proposals_service.py` + `routes/proposals.py`, web `/seller/proposals`. **No migration:** the M-1 tables and `approvals.source = 'proposal'` already fit. **Plan errors / gaps found:** (1) the plan never said who the *requester* is for a system-generated batch, and `approvals_no_self_approval` makes that load-bearing. Decision: one approval per batch, requester the system actor `merchant-ops@system`, the seller is `subject_email` and the reviewer, so the CHECK and `repo.transition` stay untouched and the system can never decide. (2) The plan's "seller decides, own products" cannot be a policy *route*: `approvals.route` is `auto|support|manager` and `assigned_role` is `support|admin` (CHECK). The `merchant_proposal` kind therefore exists in `approvals.yaml` only for `editable_fields`, `sla_hours` and `on_timeout` (its mandatory unconditional rule is never stored as a route; it has a golden case because the golden-coverage test requires one), and the seller decides through `/api/seller/proposals/{id}/decide`; `/api/approvals/{id}/decide` answers 422 for source `proposal` (as .NET already did) and the staff list excludes them. (3) The M-3 row's "per line accept/edit/reject" is one request deciding a whole batch (a line left out is rejected), recorded after execution like tool approvals, so an interrupted decide is retryable. (4) The seeded data is overstocked everywhere (every product has 2,000+ days of cover) and about half of the markdown candidates trip the margin floor or an active promotion, so an unbounded batch is 19 of 23 lines blocked; a batch is capped to 8 markdowns and 3 blocked lines for context (`GenerationRules`). (5) Restock lines are per product but stock is per warehouse: the line carries the total and execution writes one `restock_schedule` row to the warehouse furthest below its threshold. The heuristics (90-day window, 180/365 days of cover, 5/10 %, refill to 3x threshold, 7-day lead) are the builder's placeholders. |
| M-4 | `merchant.daily_run_due` in `worker/merchant_handlers.py`, behind `MERCHANT_OPS_SCHEDULE_ENABLED` (default false) and `MERCHANT_OPS_RUN_HOUR_UTC` (6). Per-date dedupe key; each seller in its own savepoint; the next run is booked from `max(now, this row's run_date)` (a row processed early would otherwise collide with its own key; found by a test). **Plan error:** "a scheduled run spends LLM money" is not true yet: generation is deterministic, so the flag guards the M-2 agent that does not exist. It is off anyway. Also added: reminder and expiry notifications to the *seller* (the requester is the system actor), and expiry marks the proposal `cancelled`. |
| M-2 | **Not built; needs a keyed recording session.** No `@tool`, agent, prompt or dataset was added, so no replay fixture changed (`fixture_drift` 102 fixtures, no drift; evals zero missing fixtures). When M-2 wraps `generate_proposal` as `propose_markdown` / `propose_restock`, the output carries seller-authored product names and needs a `SANITIZE_TOOLS` entry plus its .NET twin (`test_guardrails_tool_coverage.py`). The M-2 verification must also pick `agent_catalog` display rows after checking that `agent_catalog` does not drive routing. |

**Review fixes (M-3/M-4 and cross-stack).** C1 restock edits can no longer raise a line (limits `max_restock_quantity`/`max_restock_order_value` in `merchant_limits.yaml`, an edit-bound rule at decide time and in `apply_line`, out-of-range values a 422 not a 500); C2 a miss signal is acted on only by an orchestrator whose own provider is `replay`, on every path of both stacks; W1 every decision on a proposal approval is a 422 on both stacks, detail read 404, .NET list excludes them; W2 the SLA breach defers while a decide is in flight and closes truthfully when lines ran; W3 per-seller advisory lock on generation (a unique index is not expressible: line status does not say the proposal is open); W4 `price_moved` and `restock_already_scheduled` blocks, conditional UPDATE; S1 legacy-wire steps bounded; S2 web decimals parsed and edits bounded; S3 deleted-product test (already clean: the line cascades). **Plan error:** the original M-3 rule "re-check guards" did not cover edits raising a line or staleness.

**Review leftovers (2026-11-16).** (1) Every staff approvals route (decide incl. `request_info`, answer-info, detail) now answers a `proposal`-source approval with the same 404 `Approval not found` a missing id gets, on both stacks: it was a 422 for decide/answer-info and a 404 for detail, so staff could tell a proposal id from an unused one (supersedes the "422" in W1 above and in the M-3 row). (2) A price edit equal to the price the line started from is a 422 at decide time ("no change — reject the line instead"); `apply_line` treats an unchanged price as `executed` with `changed: false`, no UPDATE and no `price_history` row; the seller card disables it. A restock edited to 0 was already a 422 (test added). (3) `_already_proposed` is product-level, so a product with any open line in an open batch gets no new line of either kind; decided/expired batches do not count. (4) Replay gate parity: .NET's loader, `ChatClientFactory` and `ReplayMiss.IsActive` all trim and ignore case; Python's factory only lower-cased and the gate used `== "replay"`, so `Replay` replayed but the gate said no and ` replay ` crashed the factory. `Settings.LLM_PROVIDER` is now stripped and lower-cased, the factory and `replay_mode_active()` normalise the same way, and .NET's loader trims; a test on each stack uses the same inputs.

**Verification (Wave 4, M-3/M-4).** Python `uv run pytest` **2045 passed, 22 skipped** (1973 passed before; 72 new, in the three merchant test files plus the golden case); `ruff check` and `ruff format --check` clean; `fixture_drift` 102 fixtures, no drift; `scripts/replay-evals.sh --setup` on a fresh Postgres + Redis, serialized with `flock`: exit 0, no missing fixture, every score equal to its baseline except pricing-promotions 0.727 (stale 0.680); web `tsc --noEmit`, `pnpm lint` (0 errors, 64 warnings, none in new files), `pnpm test` **229 passed** (was 223), `pnpm build`, and the backend-free Playwright command (ui-smoke, not-found, a11y; desktop + mobile) **49 passed, 1 skipped** with `/seller/proposals` in the smoke and axe specs (the first axe run found a light-theme contrast failure on the `info` badge tone, so the page uses `warning` and `neutral`). Live keyless walk-through on a migrated and seeded database with the orchestrator on :18080 under `LLM_PROVIDER=replay`: generate (7 lines, 3 blocked), a repeat adds nothing; approve one price line and the restock, reject another price line: price 599.99 -> 540.00 with one `price_history` row, one `restock_schedule` row of 24, the rejected product untouched; repeating the approve is 409 and changes nothing; the other seller gets 404 on get and decide and an empty list; a whole-batch rejection leaves every price unchanged.

### Wave 4 — cross-stack gap fixes built 2026-11-16

| Step | Outcome |
|---|---|
| Legacy-wire steps, Python reading .NET | **Fixed; it was worse than the B-13 note said.** The Python orchestrator's legacy reader (`orchestrator/agent.py::call_specialist_agent`) read only the Python spelling. A .NET specialist's blocking `/message:send` writes camelCase steps (`toolName`, `durationMs`, null `agent`), so the steps were not dropped by the key check but arrived as dicts the rest of the orchestrator could not read; and its `/message:stream` writes a *plural* `event: steps` frame (PascalCase list) that the Python reader did not know, so **the steps JSON was appended to the answer text the orchestrator's model reads and shown to the user**. New `shared/legacy_wire.py` (the mirror of `TolerantStepConverter`: names compared ignoring case and underscores, missing agent filled with the specialist's name, plural frame split into live `step` frames). Usage is **not** affected (both stacks name its fields explicitly); pinned by test. Shapes taken from `AgentHost.cs`; `scripts/a2a-interop.sh` now drives the orchestrator's own `call_specialist_agent` (blocking and streaming) against the real .NET specialist (18 to 20 Python-side tests). |
| .NET chat route acts on a replay miss | **Fixed, both wires, blocking and streaming.** Port of Python's `chat.py` behaviour: `replay_miss: {message}` on `/api/chat`, `event: replay_miss` + `metadata` + `done` on the stream, no assistant message, no usage row, the unanswered user message deleted (`PrepareConversationAsync` now keeps its id). Needed more than the route: (1) `A2AClient` returned an apology for a miss on every path, so it now raises `SpecialistReplayMissException` (legacy 424 / frame, A2A failed task; `A2AEvent` gained `ReplayMiss`); (2) .NET specialists' legacy routes answered 500 for a miss, now 424 / frame like Python; (3) **MEAI's tool loop swallows a tool's exception** (the model then reads it and calls again, found by the first test), so `CallSpecialistAgent` records the miss in `RequestContext` and sets `FunctionInvokingChatClient.CurrentContext.Terminate`, and the route turns the record back into the miss (Python's `_SpecialistMissFunctionMiddleware`); (4) `HandoffMode` ignored workflow error events and returned an empty answer, now raises the miss. The miss text is Python's fallback (the tour manifest is not ported). Tests: `ReplayMissChatTests`, `ReplayMissModesTests`, `A2AReplayMissTests`, and the interop script (.NET client vs Python miss, both wires, 10 to 12). |
| Python `A2A-Extensions` response header | **Fixed.** The `a2a-sdk` 1.3.0 server reads the request header into `requested_extensions` but never writes a response header; `_SafeJsonRpcDispatcher` now sets `A2A-Extensions: uri, uri` (listed by the caller AND in `ALL_EXTENSION_URIS`; none when empty), blocking and streaming. Format taken from the live .NET server (comma + space). The .NET SDK echoes in no stable order, so the interop tests compare sets; Python uses declared order. Tests: `test_a2a_conformance.py` (echo) and both interop directions (Python 18 to 22, .NET 12 to 14). Also fixed a 122-column comment line in the item-1 interop test that ruff flagged. |

### Wave 4 — V-1, V-2 built 2026-11-16 (the LLM `ops-responder` left for the keyed session)

| Step | Outcome |
|---|---|
| V-1 | Built as `docs/event-autonomy.md` describes: migration `0012` (`agent_decisions`, `store_credit_ledger`, both append-only by trigger), `config/policies/autonomy.yaml` + `shared/autonomy/policy.py` (strict loader, golden cases in `config/policies/testdata-autonomy/cases.yaml`), `shared/autonomy/engine.py`. **Not `@within_budget` under `@tool`, and no `@tool issue_goodwill_credit`:** the plan's decorator-on-a-tool shape would add a tool to an agent (fixture-invalidating) and the keyless step has no agent calling it. The engine is plain code the worker calls; when the keyed `ops-responder` exists it can wrap `handle_event`'s pieces. **Plan errors / gaps found:** (1) the plan sums `agent_decisions` "for the day" and says nothing about *concurrency*, which is the whole point of a budget: two events could each read "room left" and both spend it. Decision: the check and the draw run in one transaction under `pg_advisory_xact_lock(hash(budget, UTC day))`, tested with a widened read-to-draw window and a negative control (lock removed: both spend). (2) The plan's tiers (`auto`, `auto_notify`, `needs_approval`) were left undefined: `auto` acts alone and tells the customer, `auto_notify` also tells staff, and an autonomous tier that does not fit a cap *becomes* `needs_approval` with the tripped cap recorded, never dropped. (3) The plan says "reversal writes the negating entry" but `agent_decisions` has `reversed_at`/`reversed_by` columns in the DDL: an append-only log cannot have its rows updated, so reversal is a **new row** (`entry_type = 'reversal'`, `reverses_id`), and an approved action is a new `approved_execution` row; the admin state is derived by joins. A reversal does **not** return the day's budget (stated in the YAML and the docs). (4) The plan names no requester for a system-generated approval; as for M-3, the system actor `autonomy@system` (so `approvals_no_self_approval` holds and any staff member may decide). `approvals.source = 'event'` already existed in 0006. (5) `approvals.yaml` gained kind `autonomous_action` (policy_version 2026-11-16.3, three golden cases, run on both stacks) with **no `auto` route**: the engine has already decided it may not act alone. The pinned `dotnet_approval_rows.json` carries the policy version, so it was regenerated (24 changed lines, all timestamps/ids/the version). (6) Bounded edits: a reviewer may only lower `amount` (two decimals, positive). `request_info` is refused (nobody can answer the system). (7) An event acts once at three layers: the outbox `dedupe_key`, `run_once` keyed by the event, and a unique `event_key` on the decision (the decision commits on its own connection before `run_once` completes, so a crash between the two is healed by the unique key). (8) Per-order cap: a *second new* event on an already-credited order asks a person; a credit already waiting for approval makes a further event `no_action` (`approval_already_open`). (9) **Store credit has no spend path anywhere** (the return flow's `refund_method = store_credit` only records text), so the ledger is the balance and nothing consumes it; reversal refuses (409) if the balance is below the credit, for when a spend path exists. |
| V-2 | `POST /api/admin/events/simulate` (scenarios `small`/`medium`/`large` = 3/6/12 days, or an explicit order and delay; 404 unless `AUTONOMY_ENABLED` **and** `AUTONOMY_SIMULATOR_ENABLED`), `GET /api/admin/decisions`, `POST /api/admin/decisions/{id}/reverse` (works with the feature off), web `/admin/decisions`. Acceptance met: the three scenarios land in three tiers ($5 auto, $15 auto_notify, $40 needs_approval), shown end to end. **Plan errors:** `/admin/events` and `/admin/decisions` are API routes `/api/admin/...` (and a page only for decisions); the plan's `EVENT_AGENT_MODE` switch is **not built** (there is only the deterministic mode; `agent` mode needs a key). The plan's price-drop, back-in-stock and order-risk events stay cut (D.3); no production producer of `order.shipment_delayed` exists, so the simulator is the only source. The SLA handler (`worker/approval_handlers.py`) delegates `event` approvals to `worker/autonomy_handlers.py`: it waits while a decide holds the claim, records an approved credit that ran but was never recorded instead of expiring it, and otherwise expires it and tells staff (not the customer, who never knew). A .NET inbox would list an `event` approval but cannot decide it (parity row 40). |

**Placeholder numbers chosen (all in `autonomy.yaml`, none derived):** credit tiers 2 days = $5.00, 5 days = $15.00, 10 days = $40.00; `auto` up to $10 and 2+ days, `auto_notify` up to $25 and 5+ days; per-action cap $25; per customer per day 1 action / $25; per order 1 action; global per day 20 actions / $200; approval SLA 24 h, a credit over $100 routes to a manager; the simulator's scenario delays 3, 6, 12 days; the state filter looks through the newest 500 decisions.

**Review fixes (V-1/V-2), 2026-11-16.** A review of the build found and these commits fixed: **C1** staff could approve a credit to themselves (the requester is the system actor, the beneficiary was never checked): the decider may never be the approval's subject on either stack (named exemption for seller-decided proposals), a DB CHECK `approvals_no_beneficiary_decision`, the engine never acts alone for a staff beneficiary, the simulator refuses a non-customer or own order. **W1** the per-order cap was guarded by a per-day lock only: an advisory lock on the order too (lock order budget, order, rows) in the engine and in the approved execution. **W2** the approved credit ignored the approval row: it now locks it and pays only for the live claim holder of a `pending_review` approval, else 409. **W3** a second event on a credited order opened an approval that could never be approved: it is `no_action: order_already_credited`, `per_order_max_count` is one ceiling for both paths. **W4a** the new FKs would break the demo reset: the engine and simulator never touch the tour user. **W4b** .NET could park an event approval: it now refuses every decision on one (422). Also: TRUNCATE of the append-only tables is refused when they hold rows, with `reset_autonomy_log()` as the one named reset (seeder, `clean_db`); approved executions honour the policy's `reversible`; the decisions list filters by state in SQL with limit/offset; the simulator answers 404 before the role check. **Plan error found again:** an append-only table with foreign keys to `users`/`orders` is a hidden constraint on every reset path (seeder TRUNCATE, tour reset); decide those paths when the table is designed.

**Review round 2 (V-1/V-2), 2026-11-16.** Seven more comments, all real, one commit each. **R1** a reject after a credit that committed but was never recorded closed the approval as rejected while the customer kept the money: it is now `409` ("approve to record it"); the SLA handler and `request_info` were already safe and the tool recovery path (reject `409`, expiry records) is pinned by a test. **R2** `run_once` frees its reservation only on `Exception`, so a cancelled approve leaves it in progress and the retry got `{"error": ...}`, read as a failed credit and closed `failed`: approve now replays the ledger row directly and treats a result with no `success` key as `409` with the claim handed back. **R3** a retry's edits were recorded as applied although the first amount was replayed: conflicting edit `409`, otherwise the paid amount is recorded. **R4** `Decimal.quantize` outside the handler gave a `500` for `1e30`; the return-approval path also accepted NaN and overflowed on huge ints (both `422` now). .NET (`TryGetDecimal`, no quantize) and the merchant path (bounds before quantize) were checked and are unchanged. **Web:** latest-request id + `AbortController` against out-of-order responses, a Refresh button and a bounded post-scenario poll, and paging (`limit`/`offset`/total, load-more). Tests: `tests/test_autonomy_approval_recovery.py`, `web/src/app/(app)/admin/decisions/page.test.tsx`.

**Verification (V-1/V-2).** Python `uv run pytest` **2323 passed, 26 skipped** (2149 passed before; 174 new, in the four `test_autonomy_*` files); `ruff check` and `ruff format --check` clean; .NET `dotnet test tests/ECommerceAgents.Shared.Tests --filter "FullyQualifiedName~Policy|FullyQualifiedName~Approval"` **184 passed**; `fixture_drift` 102 fixtures, no drift, exit 0; `scripts/replay-evals.sh --setup` on a fresh Postgres + Redis, serialized with `flock`: exit 0, no missing fixture, every score equal to its baseline except pricing-promotions 0.727 (stale 0.680); web `tsc --noEmit`, `pnpm lint` (0 errors, 64 warnings, as before), `pnpm test` **241 passed** (was 229), `pnpm build`, and the backend-free Playwright command (ui-smoke, not-found, a11y; desktop + mobile) **55 passed, 1 skipped** with `/admin/decisions` in the smoke and axe specs. Live keyless walk-through (migrated, seeded database; orchestrator on :18080 under `LLM_PROVIDER=replay`, worker running, a policy copy with a global cap of 4): small/medium/large events -> $5 and $15 credited alone with customer notifications (and a staff heads-up for the $15), the $40 opened an approval and credited nothing; the same event again (HTTP and a redelivered outbox row) left 3 decisions and 2 ledger rows; support approving with an edit to $50 got 422, then $30 credited exactly $30 (budget use unchanged: a person authorised it); reversing the first action wrote a reversal row and a -$5 ledger entry (balance 0, nothing deleted), a second reverse was 409, the budget stayed at 2/4; two more small events used the cap (4/4) and the next in-budget $5 event opened an approval with `budget:global_daily_count`. **Not run:** the worker image build (registry blocked, as before) and anything against a live model.

## Workstream A — restore trust (#80, #81, #82, #83, #85, #102)

**Goal:** close the six trust issues with key-free PRs — one embedding export excepted — with Python
and .NET kept at parity.

### A.0 What planning found that the issues did not say

- **#80 is Python-only.** .NET already sends one `data:` line per line of text (`AgentHost.cs:214`,
  `ChatRoutes.cs:257-280`) and rejoins them (`A2AClient.cs:173-190`); so does `api.ts:415-435`. The
  bug is `agent_host.py:358`, the reader at `orchestrator/agent.py:95-139` (each `data:` line treated
  as its own chunk) and `chat.py:635/655/659/677`. Both stacks still mishandle `\r`. Evals never use
  the streaming path, so the fix needs no re-record.
- **#81 has a worse hole than the ones filed: impersonation on the .NET stack.** The .NET
  orchestrator uses the shared `AgentAuthMiddleware`, which accepts `X-Agent-Secret` +
  `X-User-Email`/`X-User-Role` from any caller (`AgentAuthMiddleware.cs:129-146`), and the Next proxy
  forwards those headers from the browser. With the compose default secret, anyone reaching `:3000`
  can act as any user, including admin. Nothing legitimate sends that header to the orchestrator.
- **#81, also new:** the proxy forwards the client's `X-Forwarded-For` unchanged (spoofable); when
  absent, all anonymous traffic shares the frontend container's IP and one 30/min bucket.
- **#81, downgraded:** CORS is low severity — the browser goes through the same-origin proxy and the
  token is a Bearer token in localStorage, not a cookie. The content-capture default that is actually
  `true` is compose's `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT:-true`, not `config.py`.
  `:latest` pins also appear in `docker-compose.dotnet.yml` and `Dockerfile.mcp`. .NET's secret
  compare is not constant-time either.
- **#83:** `semantic_search` is already hybrid, so missing rows degrade to text-only; the hard
  failure is the embedding API call raising before any SQL runs. And `seed.py:943` truncates
  `product_embeddings` while `dev.sh` re-seeds on every start, so a manually generated set is wiped
  on the next run.
- **#102:** db, redis, aspire and MCP services already have compose healthchecks; agent services do
  not. `dev.sh:395`'s "apply the schema in place" advice cannot work (`CREATE TABLE products` has no
  `IF NOT EXISTS`). Local-mode refresh tokens are stateless JWTs, so rotation needs a table.
- **#82:** `group_chat.py` *imports* private `agent_framework._workflows.*` modules — the imports are
  the thing to fix, not only the comment. Not re-checked by the planner: README "What's next", the
  detail of the .NET README, the tutorial counts.

### A.1 PRs

| PR | Issue | Scope | Size | Key |
|---|---|---|---|---|
| **A-1** SSE framing | #80 | New `agents/python/shared/sse.py::sse_frame(data, event=None)` — splits on `\r\n\|\r\|\n`, one `data:` line per line, one blank line. Use at `agent_host.py:309,320,358,361,370` and `chat.py:635,655,659,677` (JSON frames are already single-line; leave them). `orchestrator/agent.py`: collect `data:` lines and on the blank line join with `"\n"` and dispatch. .NET: normalise `\r\n`/`\r` → `\n` before splitting in `AgentHost.cs:214` and `ChatRoutes.WriteFrameAsync`. Tests: `tests/test_sse_framing.py` (round-trip `"a\n\nb"`, `"\n"`, `"x\r\ny"`, a product-card fence); extend `tests/test_streaming_steps.py` (FakeChatClient specialist via `httpx.ASGITransport`, read by `call_specialist_agent`, exact equality); lone-`"\n"` case in `web/src/lib/api.chatStream.test.ts`; a .NET `\r` test. JSON-encoding the chunks was rejected: it breaks `api.ts` and the .NET `A2AClient` on the same day, and #88 replaces this framing. Single-line chunks stay byte-identical, so deploy order does not matter. | M | no |
| **A-2** Secret defaults and exposure | #81 | Add `change-me-in-production-use-random-256-bit-key` to `_UNSAFE_SECRET_DEFAULTS` and .NET `UnsafeDefaults`. `hmac.compare_digest` at `auth.py:110`; `CryptographicOperations.FixedTimeEquals` in .NET. .NET: when `_isOrchestrator`, reject `X-Agent-Secret` with 401. `route.ts`: strip `x-agent-secret`, `x-user-email`, `x-user-role` from forwarded requests. Bind 5432, 6379, 8081-8085, 8090, 9000/9001, 18888/18890 to `127.0.0.1:` in all three compose files (nothing breaks: `dev.sh`/`dev.ps1` wait on localhost, evals run uvicorn on the host, testcontainers use their own ports). Tests: config validator, .NET orchestrator auth test, `route.test.ts`. Check: `./scripts/dev.sh` and `--dotnet` both reach "ready". | S/M | no |
| **A-3** Generated secrets | #81 | `dev.sh`/`dev.ps1`: when `.env` is created or still holds a placeholder, replace `JWT_SECRET`, `AGENT_SHARED_SECRET`, `OAUTH_SEED_KEY` (`openssl rand -hex 32`, Python `secrets` fallback; `RandomNumberGenerator.Fill` on PowerShell). Logs existing users out, nothing else. Acceptance: a fresh clone produces no `settings.secret_unsafe` warning. | S | no |
| **A-4** CORS, rate limiting, capture, pins | #81 | `CORS_ALLOWED_ORIGINS` (default `http://localhost:3000`), `allow_credentials=False`, both stacks. `_client_ip` takes the right-most `X-Forwarded-For` entry after `RATE_LIMIT_TRUSTED_PROXY_HOPS` (default 1) and the proxy appends the real client IP — **read `web/node_modules/next/dist/docs` first** for what Next 16 exposes; mirror in `ChatRoutes.cs:543`. `rate_limit_auth` (10/60 s, own bucket) on signup/login/refresh; `rate_limit_chat` on `/api/orchestration/compare` and `/resume`. Compose content capture defaults to `false`. Pin `uv` and `aspire-dashboard`. Tests: `tests/test_rate_limit*.py` on the existing `RedisContainer`. | M | no |
| **A-5a** Code-side doc fixes | #82 | `hitl.py` docstring; `modes/__init__.py:57` message; `REPLAY_FIXTURES_DIR` default → `evals/fixtures/replay` (and `.env.example`); rename the `evals.yml` job "Full (real LLM + judge, manual dispatch)"; `group_chat.py` → public imports if `uv run python -c "from agent_framework import Executor, handler, WorkflowBuilder, WorkflowContext"` succeeds, else fix the comment. Run `uv run pytest` and the chapter 22 tests. | S | no |
| **A-5b** Markdown fixes | #82 | Parity rows 14, 15, 16 (recount 16 from `check_tutorial_coverage.py`); SECURITY.md → 1.3.x; CONTRIBUTING lines 18 and 30 + the `tests.yml:176-180` comment; `tutorials.yml` header counts; quick-start line 307; README "What's next" and `docs/roadmap.md` (remove the LangGraph phases LG1–LG3 — parked, see IV-4); README wording "A2A-style HTTP (`/message:send`)" until #88 and "MCP optional, flag-gated"; a `docs/frontend.md` section from CLAUDE.md's Phase 1.6 text. Gate: `python scripts/build_docs_site.py --check && python3 scripts/check_tutorial_readmes.py --check && python3 scripts/check_tutorial_coverage.py --check`. | M | no |
| **A-6a** Embeddings from the seeder + fallback | #83 | `scripts/embedding_text.py` (shared `build_embedding_text`). `seed_product_embeddings()` in `seed.py`, after other seed steps and before `ANALYZE`: under `replay`, use `shared.replay_embeddings.embed_text` (the query side uses the same scheme); under `openai`/`azure` with a matching `EMBEDDING_MODEL`, load `scripts/data/product_embeddings.v1.jsonl.gz` and insert rows whose text hash matches; otherwise skip and log. Then `REINDEX INDEX idx_product_embedding`. **Must not call `random`** (it would shift `random.seed(42)` data and miss every fixture). Pass `LLM_PROVIDER` into the seeder in all compose files. `semantic_search`: on an embedding-call failure run the existing full-text query alone and log; output keys unchanged. `find_similar_products`: no embedding → same-category by rating. **Do not edit any `@tool` description** (part of the fixture key). .NET: same fallbacks in `ProductTools.cs:302-373`, `EmbeddingProvider.cs`. Tests: `clean_db` per loader branch and fallback; .NET on `PostgresFixture`. Fixture check: the replay smoke recipe in `evals/fixtures/README.md` reports zero misses. | M | no |
| **A-6b** Commit precomputed vectors | #83 | `generate_embeddings.py --export scripts/data/product_embeddings.v1.jsonl.gz` (one JSONL row per product: name, text hash, model, base64 float32; ~0.3 MB gzipped). Run by a human, never CI. | S | **yes**, < $0.01 |
| **A-7a** `ruff format` | #85 | Mechanical only; add `.git-blame-ignore-revs`. Needs a quiet window. | L (mechanical) | no |
| **A-7b** `ruff check` + lint job | #85 | Fix the 135 errors; `per-file-ignores` for genuine N-rule cases; add `lint` to `publish-main.needs`. UP rules can rewrite `@tool` annotations — re-run the replay smoke evals and revert any change that causes a miss. | M | no |
| **A-8a** pytest-timeout + MCP job | #85 | `pytest-timeout` in the dev extra, `timeout = 120`, `uv lock`. New job `uv run pytest packages/*/tests -m integration`. MCP-enabled *evals* change tool schemas and need recording — deferred. | S | no |
| **A-8b** Playwright ui-smoke job | #85 | `pnpm build && pnpm start &`, `playwright install --with-deps chromium`, `playwright test e2e/ui-smoke.spec.ts`. Confirm the spec really stubs `**/api/**` before merging. | S | no |
| **A-8c** Security scanning | #85 | `codeql.yml` (python, javascript-typescript, csharp with `build-mode: none`) + `dependency-review.yml`. Secret scanning and push protection are repository settings — the maintainer's to switch on. | S | no |
| **A-9** Keyless compose boot smoke | #85 | Matrix over the Python and .NET compose files with `LLM_PROVIDER=replay`: wait for `/ready` on every service, log in, `GET /api/products`. A real chat turn needs fixtures for that path — covered by C-8. | M | no |
| **A-10** Migrations | #102 | Freeze `docker/postgres/init.sql` as baseline 0001 (still mounted in `initdb.d`); new changes in `docker/postgres/migrations/NNNN_*.sql`. `scripts/migrate.py` (asyncpg): advisory lock; `schema_migrations(version, checksum, applied_at)`; empty DB → apply init.sql; old volume (`products` exists, no rows) → record 0001 without re-running; each migration in its own transaction; fail on checksum mismatch. Seeder command becomes `migrate && seed` in all three compose files. `dev.sh`/`dev.ps1`: replace the `search_vector` check with the migrate run. Point both eval workflows, Python `conftest._schema_applied`, both MCP conftests and .NET `PostgresFixture.cs` at init.sql + `migrations/*`. Tests: empty DB, old volume, idempotent re-run, checksum tamper. Chosen over Alembic (needs SQLAlchemy), EF migrations (.NET-only) and dbmate (extra binary) because the Python seeder is already shared by both stacks. | M | no |
| **A-11** `/ready` + healthchecks | #102 | `/health` stays static (correct for liveness). `/ready`: DB `SELECT 1` with 2 s timeout + a `schema_migrations` row; Redis reported but non-fatal (rate limiting fails open). `{"status","checks"}`, 503 when not ready, same shape on both stacks; add to public paths. .NET `AddHealthChecks` with a custom DB check. Compose healthchecks on `/ready` and `depends_on: service_healthy` agents → orchestrator → frontend. The .NET aspnet image has no curl — add it in the six Dockerfiles. | M | no |
| **A-12** Refresh rotation + signup validation | #102 | Migration 0002: `refresh_tokens(jti, user_email, family_id, expires_at, revoked_at, replaced_by)`. `/api/auth/refresh` rotates and returns `{access_token, refresh_token}` (additive); reuse of a revoked token revokes the family. `api.ts` stores the new token. Flag `AUTH_REFRESH_ROTATION` (default true). Signup: lower-case + format-check email; password 8–72 bytes (bcrypt's limit — over it likely 500s today); name 1–100 chars; `HTTPException(400, str)` on both stacks. Mirror in `AuthRoutes.cs`, `JwtTokenService`. OAuth-mode rotation out of scope — record in the parity matrix. | M/L | no |
| **A-13** Definition of done | #85 | CONTRIBUTING + `pull_request_template.md` list ruff, pytest, `pnpm test`, ui-smoke, `dotnet test`, the docs checks. | S | no |

Test commands throughout: `cd agents/python && uv sync --all-packages --extra dev && uv run pytest <files>`;
`cd web && pnpm test`; `cd agents/dotnet && dotnet test ECommerceAgents.sln`.

**Order:** A-1, A-5a, A-6a are independent and ship first. A-2 → A-3, A-4. A-7a → A-7b → A-8a/b/c.
A-10 → A-11 → A-9; A-10 → A-12. A-6a → A-6b. A-5b can go early; each later PR updates its own docs.
A-13 last.

### A.2 Decisions (defaults assumed unless changed)

| # | Decision | Default |
|---|---|---|
| A-d1 | Compose secrets: refuse to start (`${JWT_SECRET:?}`) or placeholder + warning? | Keep the placeholder, now on the unsafe list, plus generated secrets; revisit with #86 |
| A-d2 | Published ports | Bind to loopback |
| A-d3 | Auth rate limit when Redis is down | Fail open, error log |
| A-d4 | Freeze `init.sql` as the baseline | Yes; all future changes are migrations |
| A-d5 | Commit ~0.3 MB of gzipped vectors | Yes |
| A-d6 | Old refresh tokens with no `jti` after A-12 | Reject; forces one re-login |
| A-d7 | When to run the mass `ruff format` | A quiet window agreed with any concurrent session |
| A-d8 | Add curl to the .NET images | Yes |

---

## Workstream B — framework currency and protocol (#84, #98, #88, #90)

**Goal:** bring both stacks to current MAF without throwing away the fixture corpus, then replace
the A2A look-alike with the real protocol behind a flag, with a mixed-stack run as the regression net.

### B.0 What planning found that the issues did not say

1. **Private imports are 37 lines across 17 non-test modules** in `agents/python` (not ~25), plus 4
   test files and about 40 more lines across 15 tutorial files. Some docs and package READMEs show
   private imports too.
2. **There is no single jump to 1.26.** Core 1.23 is marked breaking for the MCP 2.x foundation:
   core's `all` extra moves from `mcp<2,>=1.24` (1.22.0) to `mcp<3,>=2.2.0` (1.26.0), and this repo
   pins `mcp[cli]>=1.27.0,<2` with both FastMCP servers depending on it. **§16 "MCP 2.x blocked on
   core" is out of date — it is unblocked, and upgrading past 1.22 forces it.** Latest compatible
   siblings: `-openai` 1.18.2 (needs core ≥1.26), `-orchestrations` 1.4.2 (needs core ≥1.23).
3. **Tutorial 23 is out of date.** `agent-framework-a2a` 1.0.0b261112 (beta) and
   `agent-framework-hosting-a2a` 1.0.0a261106 (alpha) exist; both need core ≥1.22 and `a2a-sdk>=1.0`.
   `a2a-sdk` is 1.3.0 (spec 1.0, with a 0.3 compatibility mode). .NET `A2A` is `1.0.0-preview4`;
   `Microsoft.Agents.AI.A2A` and `…Hosting.A2A.AspNetCore` are `1.28.0-preview.261106.1`.
4. **The legacy endpoints squat on the real A2A paths.** A2A 1.0's HTTP+JSON binding is
   `POST /message:send` and `POST /message:stream` — the same paths, incompatible bodies. Both cannot
   be mounted at root.
5. **.NET still sends `history` in the request body** (`OrchestratorTools.cs:72,76` →
   `A2AClient.cs:270-281`); Python stopped. The two stacks already send different payloads.
6. **`workflow_loader` is not isolated dead code.** It has tests, is used by
   `scripts/visualize_workflows.py` and docs, and has a .NET twin (`DeclarativeWorkflow.cs`).
7. **The stream fallback is worse than filed.** `orchestrator/agent.py:141` catches any `Exception`
   after deltas and steps have already reached the browser, then re-POSTs `/message:send` — re-running
   side-effecting tools and repeating the text.
8. **`response_format` is not part of the replay key.** Adding it to a call would silently replay
   old prose fixtures that then fail to parse: a wrong answer, not a reported miss.
9. Next.js latest 16.4.6 (repo 16.2.2). Aspire 13.6.5: Python via `Aspire.Hosting.Python`
   (`AddUvicornApp`, `.WithUv()`), JavaScript via `Aspire.Hosting.JavaScript`
   (`AddJavaScriptApp(...).WithPnpm()`); `AddNpmApp` was removed in 13.

**Unverified, check at build time:** the .NET MAF changelog 1.18 → 1.28; whether core 1.23+ fails on
import with mcp 1.x; whether `AddPythonModule` exists; whether .NET `A2A` preview4 supports the
HTTP+JSON binding; the .NET group-chat manager class name.

### B.1 PRs

Commands, from the repo root —
**PY** `cd agents/python && uv sync --all-packages --extra dev && uv run pytest && uv run ruff check . && uv run ruff format --check .` ·
**TUT** `uv run --project tutorials pytest tutorials/ -m "not integration"` ·
**NET** `cd agents/dotnet && dotnet test ECommerceAgents.sln` ·
**WEB** `cd web && pnpm exec vitest run && pnpm exec tsc --noEmit && pnpm lint` ·
**REPLAY** `scripts/replay-evals.sh` (added in B-1).

| PR | Issue | Scope | Size | Key |
|---|---|---|---|---|
| **B-1** Fixture-drift diagnostics + key hardening | #84 | `scripts/replay-evals.sh` lifted from `evals.yml:122-178` (CI then calls the script). `evals/fixture_drift.py`, per fixture: rebuild `Message.from_dict` from stored messages and diff the re-serialisation ("serialisation drift"); load `response` with `ChatResponse.from_dict` ("response schema drift"); compare stored `tools` with live `to_json_schema_spec()` per `create_*_agent()` (format-only = "spec drift", description/parameter change = "semantic"). `--fix` rewrites the stored request for the first and third categories then calls `rehash_fixtures.main()`. Exit 0 clean, 1 offline-fixable, 2 needs re-recording. `_canonical_request` in both replay clients adds a `response_format` key **only when one is set**, so existing hashes do not change. Tests: `tests/test_fixture_drift.py`, `test_replay_client.py::test_response_format_changes_key`, `::test_no_response_format_hash_unchanged`. | S/M | no |
| **B-2** Private → public MAF imports at 1.14 | #84 | Import from top-level `agent_framework` or `agent_framework.orchestrations`; anything not public at 1.14 (likely `_checkpoint_encoding`) goes in an explicit allowlist with a comment. Delete `patch_maf.py` and its Dockerfile line. `tests/test_no_private_maf_imports.py`: an AST scan against the allowlist. Tutorials (`maf_bootstrap.py` + 15 chapters) in sibling PR **B-2t**. Import paths are not hashed, so REPLAY stays clean. | M | no |
| **B-3** Python MAF stage 1: core 1.22.x | #84 | Step 1: core 1.21.x with the highest `-openai`/`-orchestrations` that `uv lock` resolves under `core<1.23`; run `fixture_drift`. Step 2: core 1.22.x (changes content identity, replay and stream-merge semantics — the likely source of any hash drift; ship it as its own PR if it drifts and step 1 did not). Fix telemetry tests for the 1.15 GenAI conventions. 1.22 checkpoint records are unreadable by older runtimes — dev databases need `./scripts/dev.sh --clean`. Mirror pins in `tutorials/pyproject.toml`; `uv lock` in both. Accept: PY, TUT, REPLAY pass; `fixture_drift` exits 0 or 1 after `--fix`. **Exit 2 means stop and move the work into recording session R1.** | M | no (if drift is offline-fixable) |
| **B-4** .NET MAF 1.18.0 → 1.28.0 | #84 | Three `Microsoft.Agents.AI*` versions in `Directory.Packages.props`; `global.json` pinning SDK 10.0.x with `rollForward: latestFeature`. Accept: NET passes; a manual `./scripts/dev.sh --dotnet` turn (key for the manual check only). | S/M | no |
| **B-5** Tutorials .NET version unification | #84 | `tutorials/Directory.Packages.props` with central package management at B-4's versions; remove `Version=` from 68 references in 30 `.csproj` files. `for p in tutorials/*/dotnet/tests/*.csproj; do dotnet test "$p"; done`. | M (mechanical) | no |
| **B-6** Python stage 2: core 1.26 + MCP 2.x | #84, §16 | **One atomic PR** (one shared workspace venv). Pins: core 1.26.0, `-openai` 1.18.2, `-orchestrations` 1.4.2, `mcp[cli]>=2.2,<3`. Port `packages/mcp-product` and `mcp-inventory` off the old `mcp.server.fastmcp` location. Re-test `MCPStreamableHTTPTool` (1.25 adds a 30 s default timeout and discovery limits). 1.25 renames workflow spans (`gen_ai.workflow.name`) — update telemetry tests. Tutorials 08, 20b and DevUI in **B-6t**. Accept: PY, TUT, REPLAY pass; `docker compose --profile mcp up` passes its MCP smoke tests. Risk high; a manual MCP-mode turn needs a key. | L | no |
| **B-7** Dependabot, canary, badge | #84 | `.github/dependabot.yml` weekly: `uv` (`/agents/python`, `/tutorials`, group `agent-framework-*`), `nuget` (`/agents/dotnet`, `/tutorials`, group `Microsoft.Agents.*`), `npm` (`/web`), `github-actions`. `.github/workflows/maf-canary.yml`: weekly cron, `uv lock --upgrade-package` for the three MAF packages, then PY, REPLAY, `fixture_drift`; `LLM_PROVIDER: replay`, **no secrets**, a first step that fails if any `*_API_KEY` is non-empty; opens or updates an issue on failure (`issues: write`). `scripts/check_maf_badge.py --check` compares a static README badge with the pins, run in `tests.yml`. | S | no |
| **B-8** Next.js 16.2.2 → 16.4.6 | #84 | Bump `next` and `eslint-config-next`; read `node_modules/next/dist/docs/` for 16.3/16.4 changes; `pnpm build` and confirm `web/tsconfig.json` is not rewritten. | S | no |
| **B-9** Truth fixes | #98 | (a) `modes/__init__.py` docstring ("six" → five) and the line-57 message. (b) Remove the ignored `tools=` parameter from `create_agent_app` and its five callers. (c) `call_specialist_agent` falls back to `/message:send` **only if the stream failed before the first frame**; after any frame, return partial text plus an error note and never re-post. (d) Same rule in `A2AClient.StreamAsync`; stop sending body `history` (`HistoryRehydrator` already covers it). Tests: `tests/test_call_specialist_stream_fallback.py` (`httpx.MockTransport` streams one `step` then raises; assert `/message:send` is never hit); an `A2AClientTests` case with a fake `HttpMessageHandler`. Python wire format unchanged. | S | no |
| **B-10** Delete the declarative loader | #98 | Remove `shared/workflow_loader.py`, `config/workflows/{text-pipeline.yaml,SCHEMA.md}`, `tests/test_workflow_loader.py`, .NET `DeclarativeWorkflow.cs` and its tests (grep for other references first). Fix `scripts/visualize_workflows.py` and `docs/workflows/README.md`. Tutorial 19 keeps its own copy. Record in the parity matrix. | S | no |
| **B-11** Mixed-stack compose profile | #88 | `docker-compose.mixed.yml` overrides rebuilding the five specialists from the other stack's Dockerfile; `MIXED=py-net\|net-py`; `AGENT_REGISTRY` unchanged. `scripts/e2e-both-stacks.sh --mixed`. Run `e2e/orchestration-parity.spec.ts` against both mixed variants and both pure stacks. **Lands before the protocol change, as its regression net.** Needs B-9(d). The compose profile and script are key-free; **the Playwright run needs a live key** — C-6's Python recordings cannot serve it, because the .NET replay client uses different request hashes and response formats and the parity spec signs in as Alice, not the tour user. A keyless version needs its own recordings per stack combination (R1). **Built 2026-11-16 (config-validated only; not booted), see §7.** | M | **yes**, to run |
| **B-12** Conformant A2A 1.0, Python | #88 | **12a** — `a2a-sdk[http-server]` 1.x mounted at `/a2a/v1` (legacy endpoints stay at root). JSON-RPC interface. A custom `AgentExecutor` calling the existing `_run_agent_native_stream` path — **not** `agent-framework-a2a`'s `A2AExecutor`, which builds a MAF session from `contextId` and so changes the messages the specialist model sees and every fixture hash. `contextId` carries the session id, replacing `x-session-id`. Lifecycle submitted → working → completed/failed; `input-required` reserved for the approval work. Text deltas as `TaskArtifactUpdateEvent` with `append=true`. Steps as `TaskStatusUpdateEvent.metadata["https://<repo>/a2a/ext/steps/v1"]`, declared in `capabilities.extensions`, switched on by the `A2A-Extensions` header. Card at `/.well-known/agent-card.json` with `supportedInterfaces` and skills built from each `FunctionTool`'s name and description. **12b** — the client in `orchestrator/agent.py` and `shared/remote_agent.py` uses the SDK client behind `A2A_TRANSPORT=legacy\|a2a` (default `legacy`). `tests/test_a2a_conformance.py`: the official client over `ASGITransport` against a specialist app on replay — card schema, SendMessage, SendStreamingMessage, task states, extension metadata. Accept: REPLAY passes with `A2A_TRANSPORT=a2a`, zero misses. | L (two PRs) | no |
| **B-13** Conformant A2A 1.0, .NET | #88 | `A2A.AspNetCore` preview4 or `Microsoft.Agents.AI.Hosting.A2A.AspNetCore` (`MapA2A`), pinned exactly. Same executor rule, extension URI, card and flag. Contract test: replay the Python conformance cases against .NET with a JSON golden file under `tests/contracts/a2a/`. Then B-11 with `A2A_TRANSPORT=a2a`. Remove the legacy endpoints one release after both stacks default to `a2a`. **Built 2026-11-16, see §7 (the golden file was replaced by a live cross-process check).** | L | no |
| **B-14** Identity as a signed token | #88 | `auth-server` (shared by both stacks): RFC 8693 token exchange returning a JWT with `sub` = user email, `role`, `sid`, `act.client_id` = orchestrator, `aud` = the specialist, 5-minute TTL. The orchestrator exchanges once per turn per specialist and sends it as `Authorization`. `AgentAuthMiddleware` on both stacks validates against JWKS and sets ContextVars / `RequestContext`. Flag `A2A_IDENTITY=headers\|token\|both`, default `both` for one release then `token`. Tests: a forged `X-User-Email` with a valid token for another user is rejected; an expired token is rejected. **Built 2026-11-16 as an orchestrator-minted HS256 token, not an auth-server exchange; default `both`, see §7.** | M | no |
| **B-15** History compaction | #98 | MAF's compaction strategy on rehydrated history (confirm the class name at 1.26); `MessageCountingChatReducer` on .NET. `HISTORY_COMPACTION_MAX_MESSAGES`, default `0` (off). `fixture_drift` checks the longest fixture conversation stays under any later default. | S/M | no |
| **B-16** Group chat on `GroupChatBuilder` | #98 | Python `GroupChatBuilder` with a round-robin selector and a deterministic moderator, keeping `GroupChatState`; .NET `AgentWorkflowBuilder` group chat. Changes requests, so the group-chat fixtures and the parity turn are re-recorded in R1. | M | **yes** (R1) |
| **B-17** DevUI entry point | #98 | `scripts/devui.py` with a dev-only dependency group: five specialists and two workflows, dev identity bound to ContextVars from `DEVUI_USER_EMAIL`. Test: entities build. No .NET equivalent — record the gap. Chatting in it needs a key. | S | no (to build) |
| **B-18** Aspire AppHost | #90 | `agents/dotnet/src/ECommerceAgents.AppHost` (`Aspire.AppHost.Sdk/13.6.5`) and `ECommerceAgents.ServiceDefaults` (OTel, health checks, discovery; fold in the existing OTel setup). `AddPostgres` on `pgvector/pgvector:pg16` with `init.sql`; `AddRedis`; seeder and auth-server via `AddDockerfile` on `agents/python/Dockerfile`; orchestrator and specialists via `AddProject<>`, with parameter `AgentStack=python` swapping to `AddUvicornApp(...).WithUv()` and a per-specialist override (a third way to run mixed stacks); MCP host; frontend via `AddJavaScriptApp("web", "../../../../web", "dev").WithPnpm()` with `ORCHESTRATOR_URL`; `AGENT_REGISTRY` built from endpoint references. `AppHostModelTests` builds the model with `DistributedApplicationTestingBuilder` **without starting it** (no Docker). Compose stays the no-SDK path. | M/L | no (to build) |
| **B-19** Typed card frames | #98 | The alternative to structured output. The orchestrator's SSE writer (`chat.py`, `ChatRoutes`) holds back text inside a recognised fence and emits `event: card` with the JSON validated against Pydantic / C# record mirrors of `chat-schemas.ts`; the web client renders typed frames and keeps fenced-block parsing as a fallback. Prompts do not change, so fixtures do not change. | M | no |

**Structured output — decided against on the chat path.** With `response_format` the whole reply is
JSON, so token-level prose streaming becomes partial JSON needing incremental parsing on both
stacks; specialist text passes through the orchestrator model, so cards would have to survive two
schema hops; OpenAI-compatible endpoints vary in support; and it forces a full re-record on both
stacks. Use it only where code consumes the output: `evals/scorers/llm_judge.py` and group-chat
panelist stances after B-16. **Magentic — not built.** The reason given at planning time (Python-only, so a permanent parity
gap) stopped being true when B-4 landed: see §7. B-9 removed the advertising of a mode that does not
exist.

**Order:** B-1 → B-2 → B-3 → B-6 → B-7; B-4 → B-5 (the .NET upgrade is independent of the Python
one and sits in wave 1); B-9 → B-11 → B-12 → B-13 → B-14 (B-12 also
needs B-3; B-11 needs A-1); B-6 → B-16, B-18.

### B.2 Decisions (defaults assumed unless changed)

| # | Decision | Default |
|---|---|---|
| B-d1 | Version stepping | Jump within a band, stop at breaking boundaries: 1.21 → 1.22 → 1.26 with MCP 2 |
| B-d2 | A2A transport | JSON-RPC only until the .NET preview's HTTP+JSON support is verified |
| B-d3 | Server executor | Custom, to keep fixtures stable; revisit when `agent-framework-a2a` is GA |
| B-d4 | Legacy endpoint lifetime | One release after both stacks default to `a2a` |
| B-d5 | Identity rollout | `A2A_IDENTITY=both` for one release, then `token` |
| B-d6 | Preview packages in the .NET app | Accept, pinned exactly, grouped in Dependabot |
| B-d7 | Structured output | Not on the chat path; B-19 instead |
| B-d8 | Group chat | Migrate (B-16) after the upgrade |
| B-d9 | Magentic and the declarative mode | Delete the loader (B-10). **Magentic: premise changed 2026-11-15** — .NET MAF 1.28.0 ships `MagenticWorkflowBuilder` (found by B-4), so it is no longer a permanent parity gap. Not scheduled; a maintainer decision (see §7) |

---

## Workstream C — let people see it (#86, #87, #99, #104, #100)

**Goal:** a visitor with no API key runs a tour covering every orchestration mode, including one
approval pause and resume, in Codespaces or locally. The recorded clip and the tutorials work the
same way.

### C.0 What planning found that the issues did not say

1. **Two of the five modes make no LLM call.** `workflow:pre-purchase`, `workflow:return-replace` and
   `resume()` are plain Python executors over tools (`orchestrator/modes/workflow_mode.py:117-159,
   210-369`). So the approval beat needs no fixtures, and **the demo-clip stall (§11) is not a model
   problem — it can be reproduced with no key today.** Likely cause, unverified:
   `pending_request_id = getattr(event, "request_id", None)` (`workflow_mode.py:245`); if MAF 1.14
   carries the id elsewhere on the event, `chat.py:239` never writes the `hitl_requests` row, which
   is the exact symptom. C-1 settles it.
2. **The keyless chapter audit is smaller than #104 says.** Chapters 09, 10, 17, 19, 20, 22, 30, 31
   import no chat client at all; only 20b (a DevUI server) is hardwired to OpenAI/Azure
   (`main.py:33-44`).
3. **`ScriptedChatClient.cs` is a test double**, linked into test projects, not `Program.cs`.
   `dotnet test` is keyless in CI; a keyless `dotnet run` would need a new seam in every chapter.
4. **The #83 dependency runs the other way.** Under replay, query vectors come from the hashing
   vectorizer (`shared/replay_embeddings.py`), so product vectors must use the same scheme or the
   semantic arm returns noise silently. The tour needs `generate_embeddings` under
   `LLM_PROVIDER=replay` (key-free, already done at `evals.yml:120`), not #83's real vectors. The
   real constraint on #83 is that it must not remove the replay path — A-6a keeps it.
5. **Where a replay miss surfaces:** the generic apology at `chat.py:534` and `:609`; a specialist
   miss is a 500 at `agent_host.py:289-293`, which `orchestrator/agent.py:163-165` turns into a
   string for the model, whose next call then misses too.
6. **Group-chat panel prompts are hardcoded in Python** (`group_chat_mode.py:31-42`), against the
   repo rule. Moving them byte-identically to YAML leaves fixture hashes unchanged.
7. **C-8 would be the first E2E job in CI.** Acceptable because it needs no key.

### C.1 Determinism analysis for the replay tour

The eval harness is stable because it uses an email that is not in the database (context providers
inject nothing), an empty session and `history=[]` (`harness.py:107,116-118`); the cache key scrubs
UUIDs and timestamps out of *tool* messages only (`replay_client.py:245`), rewrites call ids to
ordinals (`:176`) and collapses month buckets (`:158`); product ids are deterministic uuid5
(`seed.py:314`). A signed-in tour breaks every one of those assumptions.

| # | Source of nondeterminism | Pin |
|---|---|---|
| N1 | `RecentOrdersProvider` (`context_providers.py:113-145`) puts random order UUIDs (`seed.py:509`) and now-relative `created_at` (`seed.py:491`) into the instructions, which are hashed verbatim (`replay_client.py:113`) | uuid5 order ids (C-2); scrub UUIDs and timestamps in `instructions` and system messages (C-3) |
| N2 | `UserProfileProvider` (`:58-83`) includes `total_spend`, which checkout changes (`legacy.py:2331`) | Dedicated tour user + reset |
| N3 | `AgentMemoriesProvider` (`:173-204`); `memory_tools.py:32` writes during a run | Tour prompts avoid "remember"; reset deletes memories |
| N4 | History: orchestrator reads it (`chat.py:299`), specialists rehydrate it (`agent_host.py:175`) | Each chip opens a new conversation except one scripted follow-up; reset deletes tour conversations |
| N5 | "Return for order {uuid}" (`workflow_mode.py:260`) is persisted into history | uuid5 order ids |
| N6 | Tool outputs with now-relative numbers: `days_remaining` (`return_tools.py:83`), restock dates (`seed.py:653`) | LLM tour prompts avoid them; the offset-seed run in C-8 checks |
| N7 | Every seed `datetime.now()` (`seed.py:437,453,483,593,612,634`) | `_now()` honouring `SEED_CLOCK_OFFSET_DAYS`, plus N1's scrub |
| N8 | A return can be initiated once per order (`return_tools.py:52-59,159`) | `reset_tour_user()` |
| N9 | Config that changes prompts or schemas: `MCP_ENABLED`, role, `HANDOFF_AUTONOMOUS_MODE`, `AGENT_REGISTRY`, guardrail flags | Hardcoded in the no-key overlay, not `${VAR:-}` |
| N10 | `response_id=uuid4()` in handoff (`remote_agent.py:132`) | Check `Message.to_dict()` omits it; if replay misses, drop `message_id`/`response_id` in normalisation. The two-pass run catches it |
| N11 | Embedding scheme | Replay vectors generated by the seeder |
| N12 | Later prompt, tool or tool-output changes | C-8 fails; re-record |

### C.2 PRs

| PR | Issue | Scope | Size | Key |
|---|---|---|---|---|
| **C-1** Keyless approval repro | #87 | `agents/python/tests/integration/test_return_replace_live_path.py`: `clean_db` inserts a user, a delivered order over $500 and a `delivered` status-history row 5 days old; `LLM_PROVIDER=replay` with an **empty** fixtures dir (proves no model call); `POST /api/chat/stream` with mode `workflow:return-replace`; assert a `hitl_requests` row with non-null `request_id`; resume; assert `finalize` ran. If it fails on `request_id`, fix the extraction at `workflow_mode.py:245`. | S | no |
| **C-2** Seed determinism + tour user | #86 | `scripts/seed.py`: explicit uuid5 order ids (consume no `random` draws); `_now()` honouring `SEED_CLOCK_OFFSET_DAYS`; `seed_tour_user()` appended last with its own `random.Random(7)` — `tour@example.com`, most-recent order delivered, over $500, 5 days old, plus a shipped order; `--reset-tour-user`. Reset SQL in new `agents/python/shared/demo_tour.py`. Tests: `tests/integration/test_seed_tour.py` (seeding twice gives equal ids; reset restores eligibility). Gate: the replay eval smoke still passes — tour orders may shift aggregates datasets read. | M | no |
| **C-3** Hash scrubs instructions | #86 | `_normalize_for_hash` applies `_scrub` to `instructions` and `role == "system"` messages; run `uv run python -m evals.rehash_fixtures`. Tests in `tests/test_replay_client.py`: instructions differing only in uuid/date hash equal; different prose hashes differently; all 102 eval fixtures still resolve. | M (mostly renames) | no |
| **C-4** Backend demo surface | #86 | Settings `DEMO_TOUR_ENABLED=false`, `DEMO_TOUR_PATH`. `config/demo/tour.yaml`: ~10 chips (`id`, `label`, `prompt`, `mode`, `new_conversation`) + `miss_message`. `GET /api/demo/tour` (404 when off) and `POST /api/demo/reset` (`require_auth`, tour user only) in `routes/orchestration.py`. Catch `ReplayFixtureMissingError` in `chat.py` at 351, 528, 602 → `event: replay_miss`. **A specialist miss needs explicit propagation, not just a route-level catch:** `agent_host.py` returns 424 on the blocking endpoint and, on the stream endpoint (whose status is already sent), emits an explicit `event: replay_miss` frame; both A2A consumers (`call_specialist_agent` and `shared/remote_agent.py`) recognise the 424 and the frame and re-raise as a miss instead of turning it into tool text. Tests: a specialist miss in tool mode and in handoff mode each produce `replay_miss` at the browser. `dev.sh` preflight: provider openai, no key, no `--no-key` → exit with a hint. | M | no |
| **C-5** Tour chips + miss callout | #86 | `web/src/lib/demo-tour.ts`, `components/chat/demo-tour-chips.tsx`; empty state in `chat/page.tsx` + "Reset demo"; `api.ts` routes `replay_miss` to a callout that re-offers the chips. Demo mode is detected at runtime from the 404, so the build still encodes nothing about the backend. `cd web && pnpm test && pnpm exec tsc --noEmit && pnpm lint`. | M | no |
| **C-6** Record the tour | #86 | `web/e2e/demo-tour.spec.ts` + `web/e2e/helpers/tour.ts` (follows the manifest; waits on `button[aria-label="Stop"], button:has(svg.lucide-square)`; asserts no `replay_miss`; approves on `/runs` **as admin**, per D-1). `scripts/record-tour.sh`: `--clean` with `RECORD=true` on all six services sharing a bind-mounted `agents/python/demo/fixtures/replay/`, then two keyless passes on fresh reseeds with clock offsets 0 and 13. Tour: (1) tool product search, (2) follow-up in the same conversation, (3) orders, (4) coupons, (5) stock, (6) handoff, (7) pre-purchase — no fixture, (8) group-chat, (9) return-replace — no fixture, (10) `/runs` approve and resume — no fixture, (11) compare on chip 1 (reuses its key only if compare passes `history=[]` — check). Acceptance: both keyless passes complete with zero misses. | L files, S code | **yes**, once, ~$1–3 |
| **C-7** Images, compose, flag | #86 | Dockerfile `COPY demo/ demo/`. New `docker-compose.nokey.yml` overlay: `LLM_PROVIDER=replay`, `REPLAY_FIXTURES_DIR=/app/demo/fixtures/replay`, `DEMO_TOUR_ENABLED=true`, the N9 values pinned, seeder runs `scripts.seed && scripts.generate_embeddings` under replay. Demo compose: `auth-server` behind an `oauth` profile; frontend waits on `orchestrator: service_healthy`. `dev.sh --no-key` / `dev.ps1 -NoKey`, with `--demo` and from source. `docker compose -f docker-compose.demo.yml -f docker-compose.nokey.yml config -q`. | M | no |
| **C-8** CI tour job | #86 | `.github/workflows/demo-tour.yml`: PR-triggered on a paths filter, **no secrets**, asserts `OPENAI_API_KEY` is unset. Postgres service, seed + replay embeddings, six uvicorn processes (the `evals.yml:117-130` pattern), `pnpm build && pnpm start`, `playwright test e2e/demo-tour.spec.ts` in a matrix over `SEED_CLOCK_OFFSET_DAYS: [0, 13]`. | M | no |
| **C-9** Devcontainer + badge | #86 | `.devcontainer/devcontainer.json`: `docker-in-docker:2` (not docker-outside-of-docker — the demo compose bind-mounts `./scripts` and `init.sql`, which would resolve on the wrong host); `hostRequirements {cpus: 4, memory: "8gb"}`; `forwardPorts [3000, 18888]`; `postStartCommand: IMAGE_TAG=${IMAGE_TAG:-main} ./scripts/dev.sh --demo --no-key`. Trimmed stack is 9 long-running containers, ~2 GB. Codespaces badge in the README. Needs images that contain the fixtures, i.e. a build from `main` after C-7. Validated by hand. | S | no |
| **C-10** Deterministic recording | #87 | `demo-recording.spec.ts` uses the tour helper and reset, drops the `/api/orders` filter, keeps the `throw`; `DEMO_TAKE=nohitl` fallback. `scripts/record-demo.sh`: mp4 via `ffmpeg -c:v libx264 -crf 23 -pix_fmt yuv420p -movflags +faststart`; gif via palettegen at 960 px, 10 fps, failing over 5 MB. Commit `docs/images/demo.gif` and `demo-poster.png`; the mp4 is a release asset, not in git. | M | no |
| **C-11** Screenshots + README top | #87 | `readme-screenshots.spec.ts` adds mode switcher, graph, compare, `/runs` approval against the no-key stack. README top: pitch, GIF, three links (Codespaces, `--demo --no-key`, tutorials). | S | no |
| **C-12** Error boundaries | #99 | `app/error.tsx`, `app/global-error.tsx` (own `<html>`/`<body>`), `app/not-found.tsx`, `(app)/error.tsx`. **Read `web/node_modules/next/dist/docs/` first.** Vitest render tests + a Playwright check that `/nope` shows the 404 page. | S | no |
| **C-13** axe + mobile | #99 | `@axe-core/playwright`, `e2e/a11y-smoke.spec.ts` on `/login`, `/shop`, `/shop/assistant`, failing on serious/critical. Project `mobile-${backend}` with `devices["Pixel 7"]` and `testMatch: /(a11y\|ui)-smoke/`. Added to C-8. | S | no |
| **C-14** Cleanup | #99 | Grep, then delete the five template SVGs in `web/public`; frontend in `docker-compose.yml` → `service_healthy`. | S | no |
| **C-15** Static preview | #99 | **Deferred.** The catch-all route handler cannot work under `output: 'export'`, `[id]`/`[slug]` need `generateStaticParams`, and the docs site already owns the repo's Pages site. The feasible version is an `msw` mock mode replaying SSE frames captured in C-6. Codespaces and the GIF cover the need. | L | no |
| **C-16** Tutorials key rule | #104 | `tutorials/_shared/llm.py`: explicit `LLM_PROVIDER` wins; else openai if a key is present; else replay with a printed notice. Migrate chapter mains in two PRs; add a replay branch to 20b. `scripts/run_tutorials_keyless.py` with a skip-list (servers: 20b, 23) and 120 s per chapter, as a step in `tutorials.yml`. | 2×M | no |
| **C-17** Tutorials README + root billing | #104 | Open with "Run chapter 1 now" and the verified replay command; learning paths; "Regenerating the status table" moves under Contributing; coverage markers untouched; `TIERS` in `build_docs_site.py:167` updated in the same PR. Tutorials get equal billing in the root README. Gate: `check_tutorial_coverage.py --check`, `check_tutorial_readmes.py --check`, `build_docs_site.py --check`. | S | no |
| **C-18** Capstone script | #104 | `tutorials/21-capstone-tour/run.sh` wraps `dev.sh --demo --no-key`. | S | no |
| **C-19** Contributor on-ramp | #100 | `CITATION.cff`, `.github/CODEOWNERS`, a CONTRIBUTING good-first-issue section, `.github/ISSUE_TEMPLATE/good_first_issue.yml`. | S | no |
| **C-20** Notebooks for chapters 01–04 | #100 | Run under replay, executed in CI with `nbclient`. Key-free only if notebook prompts are byte-identical to the chapters'. | M | no (if identical) |

**New chapters from #100 (sequencing only):** structured output and memory have no dependencies but
need a key once to record; security waits on §15 key rotation; providers wait on E-6; durable is last.

**Order:** C-1 → C-10. C-2 → C-3 → C-4 → C-5 → C-6 → C-7 → C-8; C-4 → C-7; C-7 → C-9, C-18; C-13 →
C-8; C-6 → C-10 → C-11; C-16 → C-17, C-20.

### C.3 Decisions (defaults assumed unless changed)

| # | Decision | Default |
|---|---|---|
| C-d1 | Tour identity | Dedicated `tour@example.com` with reset, not Alice |
| C-d2 | Scrub instructions in the fixture hash (C-3) | Yes; the offline rehash is cheap |
| C-d3 | Codespaces image tag | `main` until the next release, then `latest` |
| C-d4 | Aspire in Codespaces | Keep (traces are part of the pitch); drop first if memory is tight |
| C-d5 | Static preview | Defer |
| C-d6 | Keyless `dotnet run` for tutorials | No; document `dotnet test` as the keyless .NET path |
| C-d7 | mp4 | Release asset only |

---

## Workstream D — realistic approvals, merchant ops, event autonomy, long-running returns (#92–#95)

**Goal:** one approvals, policy and timer foundation that all four issues sit on. It closes the
self-approval hole first, and every PR leaves the demo working end to end.

### D.0 What planning found that the issues did not say

Checked against the code. Each one changes the shape of the work.

1. **The return workflow creates the return before the approval gate.** The graph is
   `check → initiate-return → search → hitl-gate → discount → finalize`
   (`workflows/return_replace.py:261-268`). When the gate pauses, the `returns` row exists, a label
   URL has been issued and the order is already `returned`. Rejecting only skips the loyalty
   discount. This is the main correctness bug in the workstream.
2. **Workflow mode bypasses the tool middleware.** `ReturnReplaceMode._resolve_tools()` calls
   `initiate_return.func` directly, so the same action needs approval in tool mode and is ungated
   under $500 in workflow mode. The gate compares `order_total` to `RETURN_HITL_THRESHOLD`, not the
   refund amount.
3. **Self-approval is wider than the resume route.** `approval-card.tsx` and `/runs` both show
   Approve to the customer, and `/api/admin/hitl/requests/{id}/approve` never checks approver ≠
   requester.
4. **Python resume has no atomic claim.** It reads `pending` and updates after the resume, so a
   double-click resumes twice. .NET already claims first (`OrchestrationRoutes.cs:95`).
5. **Not every admin approval executes a real DB operation.** `place_backorder` returns success and
   writes nothing; the `initiate_return` branch inserts a different row from the one the tool writes.
6. **`power_user` is seeded but not in `_ALLOWED_ROLES`** (`shared/auth.py:35`).
7. **No seller-facing agent exists.** `shared/tools/seller_tools.py` is attached to no agent.
8. **Two #93 limits cannot be built on today's schema.** `products` has no cost column (no margin
   floor), `promotions` has no link to products (only `rules JSONB`), and there is no store-credit
   table.
9. **State loss on resume is Python-only.** .NET keeps the full `WorkflowState` across the pause.
10. **Durable Task cannot honestly demonstrate durability without Azure.**
    `agent-framework-durabletask` 1.0.0 needs `agent-framework-core>=1.19` (pinned: 1.14);
    `Microsoft.Agents.AI.DurableTask` 1.16.0 needs `Microsoft.Agents.AI` 1.23.0 (pinned: 1.18.0).
    The local emulator (`mcr.microsoft.com/dts/dts-emulator`) keeps state in memory only and wants
    port 8080. **Decision: build #95 on Postgres checkpoints plus Postgres timers**; a DTS spike is
    optional and comes after #84.

### D.1 Shared foundation

**Tables** — as numbered migrations only (`docker/postgres/migrations/NNNN_*.sql`). A-10 freezes
`init.sql` as a checksummed baseline, so nothing below may be added to it; fresh databases get the
baseline followed by these migrations.

```sql
approvals(id uuid pk, source text check (source in ('tool','workflow','proposal','event')), source_ref uuid,
  kind text, requester_email text not null, subject_email text, conversation_id uuid,
  amount numeric(10,2), policy_version text, route text check (route in ('auto','support','manager')),
  assigned_role text, status text not null, -- pending_confirm|pending_review|needs_info|approved|
                                           -- approved_with_edits|rejected|expired|executed|failed
  recommendation jsonb, decision jsonb, decided_by text, due_at timestamptz, escalation_level int default 0,
  created_at timestamptz default now(), updated_at timestamptz default now(),
  check (decided_by is null or decided_by <> requester_email));
approval_events(id bigserial pk, approval_id uuid fk, actor_email text, actor_role text,
  action text, payload jsonb, created_at timestamptz default now());   -- append-only audit
outbox(id bigserial pk, topic text, aggregate_id uuid, payload jsonb, dedupe_key text unique,
  available_at timestamptz default now(), attempts int default 0, locked_until timestamptz,
  processed_at timestamptz, last_error text);                          -- events and timers
notifications(id uuid pk, user_email text, kind text, title text, body text, link text,
  approval_id uuid, read_at timestamptz, created_at timestamptz default now());
agent_decisions(id uuid pk, outbox_id bigint, agent_name text, action text, budget_key text,
  amount numeric, tier text, input jsonb, outcome jsonb, reversible bool,
  reversed_at timestamptz, reversed_by text);
store_credit_ledger(id uuid pk, user_id uuid fk, amount numeric, reason text, decision_id uuid, created_at timestamptz);
proposals(id uuid pk, seller_id uuid, kind text, trigger text, status text, created_at timestamptz);
proposal_lines(id uuid pk, proposal_id uuid fk, product_id uuid, field text, current_value numeric,
  proposed_value numeric, final_value numeric, rationale text, evidence jsonb, guard_result jsonb,
  status text, executed_at timestamptz);
promotion_products(promotion_id uuid, product_id uuid, primary key (promotion_id, product_id));
ALTER TABLE products ADD COLUMN IF NOT EXISTS unit_cost numeric(10,2);
ALTER TABLE returns  ADD COLUMN IF NOT EXISTS approval_id uuid, ADD COLUMN IF NOT EXISTS resolution text,
  ADD COLUMN IF NOT EXISTS scanned_at timestamptz, ADD COLUMN IF NOT EXISTS inspection jsonb;
-- tool_approval_requests and hitl_requests each gain: approval_id uuid
```

`tool_approval_requests` and `hitl_requests` stay as per-source execution records (MAF's resume
token lives in `hitl_requests`). `approvals` is the one inbox both point into.

**Config** — `agents/python/config/policies/` (the .NET Dockerfiles already copy
`agents/python/config`; add this path to the COPY):

- `approvals.yaml` — ordered rule list, first match wins; operators `lt/lte/gt/gte/eq/in` only, no
  expression evaluation. Facts: `amount`, `days_since_delivery`, `loyalty_tier`, `returns_90d`,
  `refund_to_spend_ratio`, `account_age_days`. Per kind: `editable_fields`, `sla_hours`,
  `on_timeout`, `policy_version`.
- `autonomy.yaml` — per `budget_key`: per-action cap, per-day cap, tier thresholds.
- `merchant_limits.yaml` — `margin_floor_pct`, `max_change_pct_per_day`,
  `block_during_active_promotion`, `max_lines_per_batch`.
- `testdata/*.yaml` — golden cases both stacks' tests run, so the two engines stay at parity by test.

**Roles** — add `support` (seed `support.demo@gmail.com`). It decides `route=support`; `admin` acts
as manager. Sellers never approve customer refunds.

**API**

| Route | Who |
|---|---|
| `GET /api/approvals`, `GET /api/approvals/{id}` | support/admin, with events and evidence |
| `POST /api/approvals/{id}/confirm` | requester only |
| `POST /api/approvals/{id}/decide` `{decision: approve\|reject\|request_info, edits?, note}` | staff; never the requester; role at or above `assigned_role` |
| `POST /api/approvals/{id}/info` | requester |
| `GET /api/me/approvals`, `GET /api/me/notifications`, `POST /api/me/notifications/{id}/read` | any signed-in user |
| `GET /api/seller/proposals`, `POST /api/seller/proposals/run`, `POST /api/seller/proposals/{id}/decide` | seller, own products |
| `POST /api/admin/events/simulate`, `GET /api/admin/decisions`, `POST /api/admin/decisions/{id}/reverse` | admin |

`/api/orchestration/{run_id}/resume` and `/api/admin/hitl/*` become staff-only shims over `decide`.

**Events** (`domain.past_tense`): `approval.created|confirmed|routed|reminder_due|sla_breached|info_requested|info_provided|decided|executed`,
`return.label_issued|carrier_scanned|inspected|resolved`, `order.shipment_delayed`,
`merchant.daily_run_due`.

**UI** — `/approvals` inbox replacing `/admin/approvals` (detail drawer: evidence, edit form, audit
timeline); the chat approval card splits into a requester confirm card and a read-only status card;
`/runs` shows the approval timeline with Approve staff-only; new notification bell,
`/seller/proposals`, `/admin/events`, `/admin/decisions`.

**How approve-with-edits reaches the two paths**

- *Paused MAF workflow (Python):* response type changes from `bool` to a `ReturnDecision` dataclass
  `{decision, resolution: refund|store_credit|replacement, amount, note, decided_by, approval_id}`,
  sent as `responses={request_id: ReturnDecision(...)}`. On .NET the port becomes
  `RequestPort<ReturnApprovalRequest, ReturnDecision>`.
- *Request-more-info:* does not touch MAF. The workflow stays paused; the approval moves to
  `needs_info` and a message is written to the customer's conversation; their answer returns it to
  `pending_review`. One MAF pause, not two.
- *Tool-middleware path:* `execute_approved_action(tool, merged_input)`; edits merge only for fields
  on the policy's `editable_fields` allowlist; the idempotency key becomes `approval_id` (edits
  change the input, so an input hash no longer works).

**Worker** — Postgres only: `SELECT … FOR UPDATE SKIP LOCKED` on `outbox`; a timer is a row with a
future `available_at`; rows are enqueued in the same transaction as the business write. Runs as its
own compose service `outbox-worker` on the orchestrator image, so a kill test kills a real process.

### D.2 PRs

Every PR: Python tests `cd agents/python && uv run pytest <file>` (DB fixture `clean_db`, agents on
FakeChatClient); .NET `cd agents/dotnet && dotnet test <proj> --filter <X>`; web
`cd web && pnpm test <file>`. **No PR edits `config/prompts/_shared/`** — one edit there invalidates
the whole fixture corpus.

| PR | Issue | Scope | Size | Key |
|---|---|---|---|---|
| **D-1** Stop self-approval | #92 | Python resume requires admin and claims atomically (port .NET's `UPDATE … RETURNING`); tool-approval approve rejects approver == requester; approval card loses Approve for customers; `/runs` button admin-only; .NET drops the `user_email = @email` branch. Tests: `test_resume_authz.py` (customer 403; two concurrent resumes → one 200, one 409), `HitlRoutesTests`, `OrchestrationRoutesTests`, `approval-card.test.tsx`. **Breaks the #86 tour spec**, which must log in as admin to approve. | S | no |
| **D-2** Support role | #92 | `_ALLOWED_ROLES` gains `support` and `power_user`; `require_staff` in `routes/legacy.py`; seed a support user; auth-server role claim, .NET allowlist, sidebar. Tests: `test_auth_roles.py`, .NET middleware test. | S | no |
| **D-3** Foundation schema + repository | #92/#94 | DDL above minus the #93/#94 tables. `shared/approvals/repo.py`: `create`, guarded `transition(id, from_statuses, to)`, `append_event`. `shared/outbox.py`: `enqueue(conn, topic, payload, available_at, dedupe_key)`. Tests: `test_approvals_repo.py` (transition matrix, self-approval CHECK, append-only audit). | M | no |
| **D-4** Policy engine | #92 | `approvals.yaml` + golden cases. Python `shared/policy/{loader,facts,engine}.py`: `evaluate(kind, facts) -> PolicyDecision(route, reasons, evidence, version)`, facts gathered by SQL reading identity from ContextVars. .NET `Shared/Policy/ApprovalPolicy.cs`. Tests: `test_policy_engine.py` and `ApprovalPolicyTests` both run every golden case; `test_policy_facts.py`. | M | no |
| **D-5a** Unified approvals API | #92 | `orchestrator/routes/approvals.py`; legacy routes delegate; `execute_approved_action` takes `approval_id` + merged edits; recommendation and evidence are deterministic (policy reasons + facts, no LLM). Tests: `test_approvals_routes.py` (confirm by requester only; support refused on `manager` routes; edits outside the allowlist → 422; request-info round trip; executes once under concurrency). | M | no |
| **D-5b** Customer confirm in tool mode | #92 | `HITLFunctionMiddleware` creates the approval in `pending_confirm` and returns `{status:"awaiting_confirmation", approval_id, message}`; on confirm the policy runs (`auto` executes, otherwise `pending_review`); `chat.py` emits `event: approval` after each turn. Gated tools' output changes, so affected `order_management`, `inventory_fulfillment`, `orchestrator_routing`, `red_team` cases must be re-recorded into an empty directory from a fresh seed. | M | **yes**, ~$0.50–1 |
| **D-5n** .NET approvals API + confirm | #92 | Port D-5a/D-5b: `Routes/ApprovalRoutes.cs`; `HitlGate` writes `approvals`. Worth the cost because the parity e2e exercises approvals. Tests: `ApprovalRoutesTests`. | L | no |
| **D-6** Inbox UI | #92 | `web/src/app/(app)/approvals/page.tsx` + drawer; `components/chat/confirm-card.tsx`; `components/notifications-bell.tsx`; typed calls in `api.ts`. Tests: vitest per component; `web/e2e/approvals.spec.ts` on a replay stack. | M | no |
| **D-7** Outbox worker, SLA, notifications | #92/#94 | `agents/python/worker/main.py`: poll loop, topic handlers in a plain dict (not a tool registry); reminder → escalate support→manager → `on_timeout`; writes notifications. Compose service behind `OUTBOX_WORKER_ENABLED` (default true). Tests: `test_outbox_worker.py` (back-date `available_at`, one tick, assert escalation; two workers process each row once). .NET: **record the gap**. | M | no |
| **D-8** Return workflow correctness | #95/#92 | Reorder to `check → policy-gate → initiate-return → search → discount → finalize`; gate calls the policy engine; `ReturnApprovalRequest` embeds the full `WorkflowState` and `on_approval` restores it; response becomes `ReturnDecision`; the pause creates an `approvals` row; expiry resumes with reject; pre-change paused rows are marked `expired`. .NET: change the RequestPort response type. Tests: `test_return_replace_workflow.py` (reject creates no `returns` row; resumed state keeps `return_id` and `replacement_products`; edited amount/resolution honoured), `ReturnAndReplaceWorkflowTests`. **Spike first:** a 20-line test that `ReturnDecision` round-trips through the checkpoint encoder. **Identity:** resume is performed by staff, but every tool after the gate reads identity from ContextVars, so the resumed run must execute as the *requester* taken from the stored approval, with the reviewer recorded separately (wave 0 did this for the existing graph; moving `initiate-return` after the gate makes it load-bearing for the ownership check). Test customer-requests / staff-resumes with the real ownership-checking tools. | M | no |
| **D-9a** Label + carrier scan | #95 | `issue-label` executor, then a second `ctx.request_info(AwaitCarrierScan)` with `hitl_requests.kind='carrier_scan'`; the worker handles `return.carrier_scanned` by resuming with `responses={id: ScanEvent}`; timeout timer expires the return. Respect "drain the first stream before resume". Tests: `test_return_lifecycle.py` incl. a **kill-and-restart test** (run to pause, drop every object, resume from a fresh interpreter via `subprocess`, assert `received`). Manual: `docker compose kill orchestrator outbox-worker`, `up`, simulate scan. | M | no |
| **D-9b** Inspection, resolution, timeline | #95 | `inspection` pause decided by support → refund / store credit / replacement → notification; `/runs` renders a timeline from `approval_events` + checkpoints. .NET: **record as Python-only** (each wait needs its own RequestPort node). | M | no |
| **M-1** Merchant schema + limits | #93 | `proposals`, `proposal_lines`, `promotion_products`, `products.unit_cost`; seed sets cost deterministically by category ratio and links a few promotions; `merchant_limits.yaml`; `shared/merchant/guards.py`. Confirm no tool does `SELECT *` on `products` (existing fixtures stay valid). Tests: `test_merchant_guards.py`. | M | no |
| **M-2** merchant-ops agent, in process | #93 | In-process MAF `Agent` (`merchant_ops/agent.py`, `config/prompts/merchant-ops.yaml`); tools `propose_markdown`, `propose_restock` write proposal lines with guard results, plus the unattached seller tools. **Not** in `AGENT_REGISTRY`, no compose service, not in orchestrator routing — so no existing fixture is invalidated. Verify `agent_catalog` does not drive routing before adding a display row. New `evals/datasets/merchant_ops.json`. | M | **yes**, ~$0.50 |
| **M-3** Batch review + idempotent execution | #93 | Per line accept/edit/reject; on execute re-check guards, key `proposal_line:{id}`, price update writes `price_history`, restock writes `restock_schedule`; ownership via `products.seller_id`; `/seller/proposals`. Tests: `test_proposal_execute.py` (double submit executes once; a guard that now fails → `blocked`). | M | no |
| **M-4** Schedule | #93 | Self-rescheduling outbox timer `merchant.daily_run_due` behind `MERCHANT_OPS_SCHEDULE_ENABLED`, default **false** (a scheduled run spends LLM money). | S | no |
| **V-1** Autonomy budgets | #94 | `autonomy.yaml`; `shared/autonomy.py` with `@within_budget("goodwill_credit")` under `@tool`: sums `agent_decisions` for the day → `auto`, `auto_notify` or `needs_approval` (creates an approval). `@tool issue_goodwill_credit` writes `store_credit_ledger` + `agent_decisions`; reversal writes the negating entry. Tests: `test_autonomy.py`. | M | no |
| **V-2** Delayed-shipment slice | #94 | `/admin/events` simulator → `order.shipment_delayed`; handler calls the tool directly (`EVENT_AGENT_MODE=deterministic`, default) or runs an in-process `ops-responder` agent (`agent` mode; its fixture needs a key, later); notify the customer; `/admin/decisions` with reversal. Acceptance: three seeded delays land in three tiers. | M | no |
| **DTS spike** (optional) | #95 | After #84 only. Compose profile `durable` with the emulator on `8090:8080`; write up whether MAF Python *Workflows* (not only durable agents) run on it. No production code. | S | no |

**Order:** D-1 → D-2 → D-3 → D-4 → D-5a → D-5b → D-6; D-5a → D-5n; D-3 → D-7; D-5a → D-8 → D-9a →
D-9b (D-9a also needs D-7); D-3 → M-1 → M-2 → M-3 → M-4 (M-4 needs D-7); D-4 → V-1 → V-2 (V-2 needs
D-7).

### D.3 Cut, and recorded rather than ported

**Cut or deferred:** order-risk review at checkout (touches the checkout hot path); price-drop and
back-in-stock watches (need a consent UX); damaged-item resolution (duplicates D-9b's inspection);
review-response drafts (need a reply table and LLM fixtures — a later M-5); LLM-written reviewer
recommendations (evidence stays deterministic); customer confirm inside workflow modes (the chat
message counts as intent); Redis wake-ups for the worker; production Durable Task adoption.

**Record in `docs/parity-matrix.md`, do not port to .NET:** the SLA worker (D-7), the return
lifecycle beyond approval (D-9), all of #93 and #94.

### D.4 Decisions (defaults assumed unless changed)

| # | Decision | Default |
|---|---|---|
| D-d1 | Add a `support` role? | Yes |
| D-d2 | Where does policy live? | `agents/python/config/policies/*.yaml`, parity held by golden cases |
| D-d3 | Inbox shape | New `approvals` table; legacy tables linked by `approval_id` |
| D-d4 | #93 shape | In-process merchant-ops agent, no new service |
| D-d5 | Durable Task | Not now; revisit after #84 with the spike |
| D-d6 | Scheduled LLM runs | Off by default |
| D-d7 | Approvals paused before D-8 | Expired by the migration |

**Risks:** `ReturnDecision` serialising through the checkpoint (spike first); fixture misses
surfacing as infrastructure failures (record from a fresh seed — eval runs write approval rows);
collisions on `shared/*` with a concurrent session (stage explicit paths, never `git add -A`).

---

## Workstream E — evals, cost accounting, model providers, protocol experiments (#97, #96, #101)

**Goal:** make the numbers this repo publishes about itself correct, widen what the evals cover
without recording anything twice, and prove provider neutrality with data.

### E.0 What planning found that the issues did not say

1. **Sanitizer bug, Python only — the most direct injection path today.**
   `shared/guardrails/config.py` lists `compare_reviews` and `get_review_trends`, which do not exist,
   and leaves out `search_reviews`, `get_sentiment_by_topic`, `get_sentiment_trend`,
   `compare_product_reviews`, `draft_seller_response`, `compare_products` and `get_order_tracking`.
   .NET's `SanitizeToolsConfig.cs` covers all of them. `search_reviews` returns raw review bodies.
2. **The replay key ignores the model.** `_canonical_request` hashes messages, tools and
   instructions only, so switching provider in replay mode silently replays the gpt-4.1 recordings.
   A scorecard needs one fixture directory per provider/model.
3. **Cost is overstated about 3×.** All 102 fixtures already carry `usage_details`, but
   `estimate_cost` charges cached input at full price — in the sampled fixture 3,712 of 3,812 input
   tokens were cached. Specialist tokens are also missing from orchestrator runs.
4. **The cost ceiling exists but cannot work.** `COST_BUDGET_MODE` has an `enforce` mode and
   `COST_BUDGET_USD_PER_RUN` (`shared/config.py:330-335`); the ceiling defaults to `None` and the
   running total is per process, so the orchestrator's budget never sees specialist spend.
5. **Guardrails are not all observe-only.** Output sanitisation is on by default and rewrites tool
   results; only inbound injection detection is observe-only. `azure_content_safety` is not
   reserved — the settings validator rejects it at startup.
6. **Sellers cannot edit product specs** in Python (only `GET /api/seller/*`), and nothing writes
   reviews. Indirect-injection cases must insert their own adversarial rows.
7. **Usage is lost in more places than filed.** Non-streaming `/api/chat` also logs zero tokens for
   non-tool modes (only `tool_router.py:59` puts `usage` in its result). `evals/results/latest.md`
   covers 2 modes although `benchmark_modes.py` runs 5 by default — the committed file is a partial run.
8. **"Any OpenAI-compatible endpoint" is not true in Python.** The `openai` provider uses
   `OpenAIChatClient`, which is the Responses API; Gemini's compatible endpoint has no `/responses`.
   .NET uses Chat Completions and is unaffected.
9. **Anthropic on Python is blocked on #84.** `agent-framework-anthropic` is beta only
   (1.0.0b261112) and needs core ≥1.24. .NET's `Microsoft.Agents.AI.Anthropic` is stable but
   releases in lockstep with `Microsoft.Agents.AI`; the official `Anthropic` NuGet package (13.8.0)
   needs only MEAI.Abstractions ≥10.5.1, so .NET can go first.
10. **Embedding dimension is fixed in more places than listed:** two `vector(1536)` columns
    (`product_embeddings`, `agent_memories`), and hardcoded in `init.sql`,
    `scripts/generate_embeddings.py`, `shared/replay_embeddings.py`, `.NET EmbeddingProvider.cs:63`.
11. **.NET evals cover less than "six datasets ported" suggests.** `evals-dotnet.yml` runs only the
    orchestrator dataset, there is one baseline and no committed fixtures, and the provider choice is
    replay or azure — with no Azure there is no way to run against a live model.
12. **The judge prompt is hardcoded in Python** (`scorers/llm_judge.py:28`), against the repo rule,
    and the judge is the same model it grades. `docs/agent-quality.md` describes a nightly cron and
    says evals "cannot be PR-blocking"; both are stale.

### E.1 PRs

Smoke command used throughout:
`cd agents/python && LLM_PROVIDER=replay REPLAY_FIXTURES_DIR=evals/fixtures/replay uv run python -m evals.run_evals --agent <a> --dataset evals/datasets/<a>.json --baseline evals/baselines/<a>.json`

| PR | Issue | Scope | Size | Key |
|---|---|---|---|---|
| **E-1** Sanitizer allowlist, enforced | #97 | `shared/guardrails/config.py`: remove the two phantom names, add the seven missing tools, add `NO_SANITIZE: set[str]`. `tests/test_guardrails_tool_coverage.py` imports every agent's tools and asserts each registered name is in exactly one set, and checks parity by parsing `SanitizeToolsConfig.cs` keys (PascalCase → snake_case). Then smoke for review-sentiment and order-management — fixtures are unchanged if seeded reviews hold no injection markers. Acceptance: the test fails on `main`, passes on the branch. | S | no |
| **E-2a** Specialist usage over A2A | #97 | `/message:send` returns `usage: {input_token_count, output_token_count, cached_input_token_count, model}` from `metadata_box["_maf_usage"]` (`agent_host.py:281-287`), **and `/message:stream` emits the same object in a terminal `event: usage` frame** — the browser's default tool-mode path uses the stream endpoint, so the blocking endpoint alone would leave it uncounted. The orchestrator consumes the frame into the accumulator and never forwards it to the model or renders it as text; new `shared/run_usage.py` ContextVar accumulator; `run_completed.usage` becomes `{total, by_agent}`. **Usage must stay out of the text returned to the model**, or every orchestrator fixture misses. Tests: FakeChatClient unit; `clean_db` integration asserting `usage_logs.tokens_in` includes specialist tokens **through `/api/chat/stream`**, not only the blocking endpoint; orchestrator smoke shows zero misses. | M | no |
| **E-2b** .NET specialist usage | #97 | Same `usage` field and the same terminal stream frame, snake_case, from the .NET host. | S | no |
| **E-2c** Usage for non-tool modes and `/compare` | #97 | Handoff and group-chat sum their agents' usage; workflow modes emit `usage={total: 0}` explicitly, so zero is distinguishable from "not captured"; remove the `if mode.name == "tool"` gate (`chat.py:719, 742-748`); `CompareResult` gains `tokens_in`, `tokens_out`, `est_cost_usd`; mirror in .NET. After A-1 (same lines in `chat.py`). | M | no |
| **E-2d** Cost accuracy + schema | #97 | `cached_input_per_1k` pricing; price by the model actually used; migration adding `usage_logs.model`, `tokens_cached_in`, `cost_usd` (through A-10's runner); `cost_budget.would_block` metric in observe mode; `CostEstimator.cs` mirrored. | M | no |
| **E-3a** Eval schema v2 | #97 | New per-case fields: `user_role`/`user_email` (passed to `ProductionRunner`), `expected_tool_args` (subset match on the `tool_input` already in steps — new `evals/scorers/tool_args.py`), `expect_denial`, `expect_hitl`. Add `expected_tool_args` to existing cases; inputs are unchanged, so fixtures still hit. | M | no |
| **E-3b** Multi-turn + per-mode | #97 | `turns: [...]` case shape (each turn's request includes earlier turns' replayed text, so it stays deterministic); for follow-ups over A2A the harness writes a conversation for a seeded user, because rehydration filters on the caller's email; `--mode` on `_run_orchestrator` and `orchestrator_modes.json`; approval cases assert a `request_info` event on `workflow:return-replace`. Harness code merges keyless; the new cases land with R1. | M | **yes** (R1) |
| **E-3c** Token gates | #97 | `evals/prompt_tokens.py` + `baselines/prompt_tokens.json`: count composed system prompt + tool schemas per agent and role with `tiktoken`, fail above 10% growth; `total_tokens_in/out` in baselines, reported as a delta per re-record. Free; runs in `tests.yml`. | S | no |
| **E-4** Red-team v2 + judge | #97 | `safety_evaluator.py`; prompts in `config/prompts/_eval/{completeness-judge,redteam-judge}.yaml`; `EVAL_JUDGE_MODEL` (default `gpt-4.1-mini`, so the judge is not the model it grades). Schema: `attack_vector` (direct / indirect_review / indirect_spec / role), `expected_behavior`, `forbidden_tool_calls`, `canary`. Deterministic checks still gate smoke; judge verdicts go through `get_chat_client`, so once recorded they replay free. | M | **yes** (R1) |
| **E-5** Indirect-injection corpus + FP/FN | #97 | `evals/seed_adversarial.py` inserts reviews and specs with canary tokens (uuid5 ids, fixed-epoch timestamps), run only before the safety suite, which already runs last. `evals/guardrail_confusion.py` runs `contains_injection_markers` over benign inputs + all seeded text (negatives) and the attack corpus (positives) → `results/guardrail-confusion.md`. | M | no |
| **E-6** `GUARDRAILS_INJECTION_PROVIDER=llm_judge` | #97 | A classifier ChatMiddleware through `get_chat_client`, prompt in YAML, observe-only by default; pointed at Ollama it is the local option. FakeChatClient unit tests; real FP/FN from R1. | M | **yes** (R1, numbers only) |
| **E-7** MCP-vs-native gate | #97, §12 | product-discovery and inventory with `MCP_ENABLED=true` against separate fixtures; smoke starts both MCP servers; fail if the MCP run scores more than 0.05 below the native baseline. After B-6. | M | **yes** (R1) |
| **E-8** .NET evals, all datasets | #97, §11 | Add `openai` to the provider options in `evals-dotnet.yml`; loop all six datasets; commit `ECommerceAgents.Evals/fixtures/` and per-dataset baselines; a PR-triggered replay-only job with no secrets; record the missing red-team suite in the parity matrix. | M | **yes** (R1) |
| **E-9** Scorecard page + doc fixes | #97 | `scripts/build_eval_scorecard.py` writes a committed `docs/eval-scorecard.md` from baselines and results, `--check` in `tests.yml`; add to the Reference section in `build_docs_site.py`; correct `docs/agent-quality.md` and `evals/README.md`. | S | no |
| **E-10** Benchmark refresh | #97 | No code: regenerate `latest.md` for all five modes with tokens and cost. | S | **yes** (R1) |
| **P-1** `LLM_API=responses\|chat_completions` | #96 | `shared/factory.py` and the replay recorder: the `openai` provider + `LLM_BASE_URL` can use `OpenAIChatCompletionClient`. Test: `httpx.MockTransport` asserts the request path is `/chat/completions`. Unblocks Gemini, vLLM, older Ollama. | S | no |
| **P-2** Per-agent model in YAML | #96 | Optional `model: {provider, model, temperature}` in `config/prompts/{agent}.yaml`; `get_chat_client(agent_name)`; a `current_agent_model` ContextVar for cost. **`prompt_loader` must leave this block out of the composed prompt**, or every fixture is invalidated. .NET `ChatClientFactory.Create(settings, agentName)`. | M | no |
| **P-3** Anthropic, Python | #96 | After B-6. Pin `agent-framework-anthropic` exactly; `LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY`, default `claude-sonnet-5-5`; an Anthropic branch in the recorder. Confirm the class name with `uv run python -c "import agent_framework.anthropic as m; print(dir(m))"`. Key-free test: `httpx.MockTransport` returns a canned `tool_use`, assert the next request carries the `tool_result` — proves wiring, translation, usage mapping, replay round-trip; **cannot** prove tool-choice quality, caching, real error shapes, streaming edge cases. | M | no (R2 to sign off) |
| **P-4** Anthropic, .NET | #96 | `Anthropic` 13.8.0's `.AsIChatClient(model)` in `ChatClientFactory`; add `anthropic` to `KnownProviders`; check MEAI compatibility with `dotnet list package --include-transitive`. No MAF bump needed. Stub `HttpMessageHandler` test. | M | no (R2 to sign off) |
| **P-5** Gemini via the compatible endpoint | #96 | Uses P-1: `LLM_API=chat_completions`, `LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/`. Document: no Responses API, unlisted parameters silently ignored, tool-schema depth limits, structured-output drift. | S | no (R2 to sign off) |
| **P-6** Decouple embeddings | #96, #83 | `EMBEDDING_PROVIDER` (openai / azure / openai_compatible / replay), `EMBEDDING_BASE_URL`, `EMBEDDING_MODEL`, `EMBEDDING_DIM` (default 1536); pass `dimensions=` where supported; fail fast at startup if the column's `atttypmod` does not match; `scripts/reset_embeddings.py --dim N` truncates, alters both columns, rebuilds ivfflat; an `embedding_model` column; pgvector indexes cap at 2,000 dimensions, so 3,072-dimension models need `dimensions=` or `halfvec`; local option Ollama `nomic-embed-text` (768). Replay and .NET constants read the setting. After A-6a. | M | no |
| **P-7** Model scorecard | #96 | `evals/model_scorecard.py` + `.github/workflows/model-scorecard.yml` (`workflow_dispatch` only). Fixtures in `evals/fixtures/replay-<provider>-<model>/` with `RECORD=true`, so reruns are free. Tool-call and argument accuracy, routing, latency (live only), tokens, cost → `docs/model-scorecard.md`. | M | **yes** (R2) |
| **P-8** Provider docs + LiteLLM recipe | #96 | `docs/model-providers.md`; a `litellm` compose profile with `config.yaml`, used with `LLM_API=chat_completions`. Caveat: cache-token fields get remapped. | S | no |
| **X-1** Publish the MCP servers to PyPI | #101 | **Go.** `project.urls`, authors, LICENSE, READMEs stating the required schema; `uv build` + `twine check` in CI; `.github/workflows/publish-mcp.yml` on `mcp-*-v*` tags using trusted publishing (`id-token: write`, a `pypi` environment); TestPyPI first. The maintainer registers the trusted publisher; name availability is unchecked. | M | no |
| **X-2** MCP Apps | #101 | **No-go for now.** Python SDK 1.x support unconfirmed and no host here would render it. Revisit after B-6; smallest slice is one `ui://product-card` resource on `get_product_details`. | — | — |
| **X-3** ACP/UCP checkout | #101 | **No-go for now**; the specs are still moving. Smallest slice if revisited: `routes/acp.py` behind `AGENTIC_CHECKOUT_ENABLED=false`, a `MockPaymentProvider`, .NET recorded as a gap. | — | — |

**Order:** E-1 first and alone. E-2a → E-2b, E-2c → E-2d. E-3a → E-3b. P-1 → P-5, P-8. P-2, P-3,
P-4, E-3a, E-2d → P-7. E-9 after R1.

### E.2 Decisions (defaults assumed unless changed)

| # | Decision | Default |
|---|---|---|
| E-d1 | Recording budget | Approve R1 in full (see Part IV §3) |
| E-d2 | Judge model | `gpt-4.1-mini` |
| E-d3 | Token gates | Static prompt-token check fails above 10% growth; recorded-token deltas informational |
| E-d4 | Cost ceiling | Keep `observe`, set `COST_BUDGET_USD_PER_RUN=0.50` so `would_block` shows when it would have fired |
| E-d5 | Non-1536 embeddings | The reset script, not a schema migration |
| E-d6 | Anthropic on Python | Wait for B-6; there is no path around the core upgrade |
| E-d7 | Anthropic on .NET | The raw `Anthropic` SDK now; move to `Microsoft.Agents.AI.Anthropic` later |
| E-d8 | .NET red-team suite | Record as a gap |
| E-d9 | #101 | X-1 go; X-2 and X-3 no-go, revisit X-2 after B-6 |
