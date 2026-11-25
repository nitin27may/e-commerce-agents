# Roadmap

This page lists what is planned next for E-Commerce Agents, what each release shipped, and what is
deliberately not planned. It is for anyone deciding whether the project covers what they need today.

The current release is **1.4.0** (2026-11-21); **1.5.0 is staged** on `main` and not yet cut. Both backends, Python and .NET, run
end to end: an orchestrator and five specialist agents (plus an opt-in, Python-only merchant-ops specialist), auth, telemetry, and one
Next.js frontend that either backend serves. The storefront is public: anyone can browse, search and use the AI shopping
assistant (`/shop`) without an account, while cart checkout, orders, tracking and returns require
sign-in. A key-free preview of the web app, replaying recorded answers, is published under `/preview` of the docs site. Differences
between the two backends are in the [parity matrix](parity-matrix.md), and the per-release record, including the known limits, is in
the [changelog](https://github.com/nitin27may/e-commerce-agents/blob/main/CHANGELOG.md).

## What's next

The order follows one constraint: **this repository's value is as a worked reference for building
multi-agent systems**, not as an e-commerce product. When the choice is between more features and
the same features explained by running them, it goes to the second. Nothing here is a commitment
to a date. Everything that needs no Azure subscription is built; what is left either needs the Azure
run, a model key this repository does not have, or an external system.

| # | Item | Why it is here |
|---|---|---|
| 1 | [First real Azure deployment](#first-real-azure-deployment) | The Container Apps path is written and checked offline. It has never run against a real subscription. It is the last step, and several other items wait on it |
| 2 | [The .NET 11 move](#the-net-11-move) | The .NET 11 move is prepared as draft PR #331. A separate change that needs no key and no Azure |
| 3 | [Model-calling parity run](#model-calling-parity-run) | The keyless parity gate runs in CI; a comparison that calls a model does not. Also the .NET live baselines |
| 4 | [Foundry features that need an Azure credential](#foundry-features-that-need-an-azure-credential) | Foundry evaluators logging to a project, the safety evaluator, hosted agents. They wait on item 1 |
| 5 | [Publish the Azure reference](#publish-the-azure-reference) | Topologies with measured costs. The difference between them is the content |

Azure heads this list because a deployment story is what a worked reference needs most, and because
live runs catch what tests cannot. It runs last, after the evaluation and documentation work that is now merged, so the
deployment is measured against a finished project.

The November 2026 audit produced issues #80 to #104, tracked in
[issue #103](https://github.com/nitin27may/e-commerce-agents/issues/103), which closes last.

### First real Azure deployment

`infra/` (Bicep), `azure.yaml`, `scripts/azure-up.sh` and `scripts/azure-down.sh` deploy the Python
stack to Azure Container Apps with PostgreSQL Flexible Server, Azure OpenAI and Key Vault, with
optional Foundry, gateway, per-app identity and reference-profile modules. The
Bicep builds and lints, and the scripts pass offline tests. **Nothing has run against a real
subscription.** [azure-deployment.md](azure-deployment.md) describes the topology and lists every
assumption a real run has to confirm.

Cost and teardown are part of the deliverable: a reader who cannot cheaply undo a deployment will
not try it. The run uses a free trial with a 10 USD cap and a key-free model path. It is done when four things hold:

- The stack comes up and a sanity script passes, with the model reached through a bearer token.
- No model key exists anywhere in the deployment.
- Teardown leaves nothing behind, checked by a script.
- Spend stays under the cap, and the measured figure is recorded.

The Postgres admin password stays, because Entra authentication for the database needs code on both stacks.
Deployed evals, the `azure.yml` workflow end to end and the deployment chapter of the course follow once the run passes.

### The .NET 11 move

The .NET stack targets `net11.0` on draft PR #331 (target framework, `global.json`, Dockerfile base images, the SDK in CI, the
runtime-versioned packages). It is done when every .NET test and both mixed-stack boots pass on that PR. It is its own change,
separate from the Azure run: merge it after the Azure deployment, and roll back with a single revert.

### Model-calling parity run

The keyless half is done: `stack-parity.yml` boots both stacks under replay on every pull request and
fails when they diverge on orchestration modes that call no model. What remains needs a key:

- [ ] Compare the two stacks on turns that call a model, and get `scripts/e2e-both-stacks.sh` (the live
      browser suite) into a manual-dispatch job. It needs both stacks up against a seeded
      database. [ADR 0005](adr/0005-dual-stack-parity.md) records the standing weakness.
- [ ] Record the .NET eval fixtures and generate its live baselines (about 1.50 USD of tokens), then gate pull
      requests on baseline regression rather than an absolute floor. Baselines are re-recorded, never copied from Python.

### Foundry features that need an Azure credential

There is no `LLM_PROVIDER=foundry`: the `azure` provider pointed at a Foundry (AIServices) account is Foundry for OpenAI models
([model-providers.md](model-providers.md#foundry)). What waits for a real subscription:

- [ ] The Foundry evaluators adapter logging a run to a project (`--log-to-project`) and the Foundry safety evaluator.
- [ ] **Hosted agents**, staged by [ADR 0006](adr/0006-foundry-hosted-agents.md) (Proposed): a reachability spike first, then one
      agent, then the rest. The frontend, the MCP servers and the auth-server stay on Container Apps. Three questions are open,
      and the first is the largest unknown: can the Foundry runtime reach Container Apps at all; does the protocol carry the
      identity headers every tool reads from ContextVars; and can a workflow that pauses for approval resume in a later request
      when each session owns its own sandbox. The spike either proves one A2A call to a Container Apps specialist works or records
      that it does not, with the reason.

### Publish the Azure reference

[azure-deployment.md](azure-deployment.md) with **verified cost numbers rather than estimates**,
covering the topologies below. The goal is a reference for taking a multi-agent system to Azure, with
this app as the worked example and the trade-offs written down.

| Topology | Who owns the runtime | Who owns the model |
|---|---|---|
| Docker Compose | you | you |
| Azure Container Apps | you | you |
| Container Apps with a Foundry project and gateway | you | Foundry (model, evaluation, tracing) |
| Foundry hosted agents | Foundry | Foundry |

### Recorded decision: .NET stays at parity

A parity-locked .NET stack means two stacks in every gate. The decision on record is that it stays at parity, with the exceptions the
[parity matrix](parity-matrix.md) names row by row (merchant proposals, event autonomy, the return lifecycle, the merchant-ops
specialist and the turn-level cost ceiling are Python only by decision).

### Waiting on something outside the repository

- **A real carrier feed** for event autonomy and the return lifecycle ([#299](https://github.com/nitin27may/e-commerce-agents/issues/299)).
  Today the admin simulator is the only producer. Fulfilment that honours `orders.on_hold` waits on it.
- **Dependency review** needs the dependency graph and Dependabot alerts enabled in the repository settings
  ([#303](https://github.com/nitin27may/e-commerce-agents/issues/303)).
- **Anthropic and Gemini rows on the model scorecard** need a key for each
  ([#300](https://github.com/nitin27may/e-commerce-agents/issues/300)). Anthropic stays stub-tested by decision.
- **Judge-calibration labels** are drafted and need the maintainer's review before the judge choice is final.

### Unsequenced backlog

Real work with no position claimed against the items above.

- [ ] **Typed filter DSL.** Replace the flat parameter list on `search_products` with a structured
      `ProductFilters` model. Text-to-SQL was considered and rejected
      ([ADR 0002](adr/0002-no-text-to-sql.md)): `user_email`/`user_role` scoping lives in
      ContextVars, and dynamic SQL would bypass that contract. A typed DSL keeps SQL generation
      server-side and auditable.
- [ ] **Publish the two MCP servers to PyPI.** The packages and a publish workflow exist; nothing
      is uploaded yet. Publishing lets any MCP client run them against any PostgreSQL database
      without this codebase. See [MCP Integration](mcp-integration.md).
- [ ] **Prompt caching.** Cache system prompts and tool schemas per agent, measured against the
      cost counter that shipped in v1.3.
- [ ] **Scheduled signing-key rotation.** Rotation itself is built (on demand, with an overlap window),
      as are the route/credential matrix and RFC 7591 dynamic client registration
      (`auth_server/register.py`, opt-in with `AUTH_ALLOW_DYNAMIC_REGISTRATION`, scope-limited). What is
      left is running rotation on a schedule.
- [ ] **Course chapters still unwritten:** durable workflows, Foundry evals, AG-UI and the deployment chapter.
- [ ] **A narrated walkthrough video** and the social preview upload
      ([#318](https://github.com/nitin27may/e-commerce-agents/issues/318)).
- [ ] **Exploratory, parked:** MCP Apps cards and an agentic-checkout adapter
      ([#101](https://github.com/nitin27may/e-commerce-agents/issues/101)).

Nothing is blocked on an upstream release.

---

## What has shipped

One pattern has shaped every release: the reported problem was smaller than the actual one, and
the difference was found by running something rather than reading it. The nine cases are in
[Reported vs actual](reported-vs-actual.md).

### v1.5 (staged on `main`, not yet released; as of 2026-11-24)

The [changelog](https://github.com/nitin27may/e-commerce-agents/blob/main/CHANGELOG.md) `[Unreleased]` section has the entries and the known limits.

- **Customer confirmation before a gated action** ([#302](https://github.com/nitin27may/e-commerce-agents/issues/302), [#307](https://github.com/nitin27may/e-commerce-agents/issues/307)), on both stacks: the customer confirms the exact action in plain words before it parks for staff ([approvals.md](approvals.md)).
- **Merchant-ops specialist and a PO-value manager tier** ([#298](https://github.com/nitin27may/e-commerce-agents/issues/298), Python): an opt-in sixth specialist that explains seller proposals and triages reviews, and a restock batch over a threshold that routes to a manager ([merchant-proposals.md](merchant-proposals.md)).
- **More event autonomy** ([#299](https://github.com/nitin27may/e-commerce-agents/issues/299), Python): price-drop, back-in-stock, damaged-item and order-risk events, monthly budgets and an optional ops-responder ([event-autonomy.md](event-autonomy.md)).
- **A key-free hosted preview, a README GIF and a no-key tour in CI** ([#301](https://github.com/nitin27may/e-commerce-agents/issues/301), [#86](https://github.com/nitin27may/e-commerce-agents/issues/86), [#87](https://github.com/nitin27may/e-commerce-agents/issues/87)) ([preview.md](preview.md)).
- **Evals that can run live:** run records, an `http` target, live datasets with resolved facts and multi-turn cases, per-mode orchestrator scoring, indirect-injection and red-team judge suites, an MCP-versus-native gate, six stored live baselines, a manual live job, Foundry evaluators and community-submitted results ([agent-quality.md](agent-quality.md)).
- **Run profiles and `python -m scripts.doctor`:** twelve provider setups applied with `--profile`, and a redacted check of the effective configuration ([run-profiles.md](run-profiles.md)).
- **Pricing in one file, a token budget and a turn-level cost ceiling:** no price is written in code on either stack ([configuration.md](configuration.md)).
- **A keyless stack-parity gate in CI** and gateway attribution headers on every model call.
- **Four more course chapters** (33 to 36: prompt injection and red team, structured output, memory, model providers), 38 in all.
- **Azure options, offline only:** the reference profile, per-app identities, an AI gateway and a Foundry project in Bicep ([azure-deployment.md](azure-deployment.md)).

### v1.4

Released as 1.4.0; the [changelog](https://github.com/nitin27may/e-commerce-agents/blob/main/CHANGELOG.md)
has the full entries and the known limits.

- **A2A 1.0 on both stacks** ([#88](https://github.com/nitin27may/e-commerce-agents/issues/88)). Every specialist serves the A2A 1.0 JSON-RPC binding at `/a2a/v1` and an agent card at `/.well-known/agent-card.json`, beside the original `/message:send` wire. `A2A_TRANSPORT=legacy|a2a` picks what the caller speaks, and user identity can travel as a signed, short-lived `X-User-Context` token (`A2A_IDENTITY`). Cross-process interop runs keyless in both directions (`scripts/a2a-interop.sh`). The .NET side uses a preview SDK, pinned exactly. See [a2a.md](a2a.md).
- **Approvals** ([#92](https://github.com/nitin27may/e-commerce-agents/issues/92)). One inbox and one decide path on both stacks, routed by a policy file rather than a prompt. It has an append-only audit trail, claim fencing, and SLA reminders, escalation and timeouts driven by an outbox worker. See [approvals.md](approvals.md).
- **Merchant proposals** ([#93](https://github.com/nitin27may/e-commerce-agents/issues/93), Python). Deterministic markdown and restock suggestions per seller, reviewed line by line through the approvals machinery and executed at most once. See [merchant-proposals.md](merchant-proposals.md).
- **Event autonomy** ([#94](https://github.com/nitin27may/e-commerce-agents/issues/94), Python). Budgets for what the system may do on its own when an event arrives, an append-only decision log, reversal, and a path from an over-budget action to an approval. Off unless `AUTONOMY_ENABLED`. See [event-autonomy.md](event-autonomy.md).
- **Return lifecycle** ([#95](https://github.com/nitin27may/e-commerce-agents/issues/95), Python). An approved return waits for the carrier's first scan across restarts, then for support's inspection, then pays out once. Off unless `RETURN_LIFECYCLE_ENABLED`. See [return-lifecycle.md](return-lifecycle.md).
- **Anthropic provider and a separate embeddings provider** ([#96](https://github.com/nitin27may/e-commerce-agents/issues/96)). `LLM_PROVIDER=anthropic` works on both stacks, covered by stubbed-transport tests only; it has never made a live call. `EMBEDDING_PROVIDER` / `EMBEDDING_*` are configured independently of the chat provider. See [configuration.md](configuration.md).
- **MAF DevUI entry point** ([#98](https://github.com/nitin27may/e-commerce-agents/issues/98)). Browse the specialists and workflows in DevUI without touching the lock file. See [devui.md](devui.md).
- **An Azure deployment path** ([#89](https://github.com/nitin27may/e-commerce-agents/issues/89)). Bicep, `azd` hooks and up/down scripts for Azure Container Apps, with managed identity for Azure OpenAI (`AZURE_OPENAI_AUTH=identity`) on both stacks. Checked offline and against a fake credential only; see [What's next](#first-real-azure-deployment).
- **One frontend image for every environment.** The browser calls the frontend's own origin, and a route handler forwards `/api/*` to `ORCHESTRATOR_URL`, read per request. The orchestrator needs no public ingress and CORS is gone. Measured cost: about 160 ms on an 8-second turn, with the same number of stream frames.
- **The agent registry fails loudly.** One validator per stack rejects a malformed `AGENT_REGISTRY` at startup. A bad value stops the process instead of producing a stack that passes its health check and cannot route. Scheme and host are checked; the port is not, because a managed endpoint has none.

### v1.3

- **Tutorial .NET coverage** ([#20](https://github.com/nitin27may/e-commerce-agents/issues/20)). Every chapter that ships code ships both languages, both tested in CI: 334 .NET tests across 31 projects at release. The one gap is chapter 20b, because `Microsoft.Agents.AI.DevUI` is prerelease-only. The status table in `tutorials/README.md` is generated from disk by `scripts/check_tutorial_coverage.py` and gated in CI.
- **Composer UX** ([#4](https://github.com/nitin27may/e-commerce-agents/issues/4)). The mode chips collapse into one toolbar picker. Suggested prompts are derived from the assistant's last message: its typed card payload first, its closing question second, a static list as the fallback. It is a pure function over text the client has, with no LLM call.
- **In-chat approval card.** The user who caused a pause can approve or reject it in the chat thread instead of on `/runs`. Both stacks emit `event: run` (`{run_id, pending_approval}`) once the turn is persisted and before `[DONE]`.
- **Cost metrics as first-class counters.** The per-turn cost estimate that enforces the budget ceiling is emitted as `ecommerce.llm.cost.usd`, with tokens split by direction beside it. Instrument and meter names match on Python and .NET, so one dashboard covers both. Nothing user-scoped is attached to the attributes.
- **Streaming tool calls end to end.** Timeline steps arrive while the answer is being written, not after it: both specialist hosts drain steps before each chunk and both orchestrators forward them live. Cards are deliberately not rendered from those payloads, because the answer text carries the same data and every card would show twice.

### v1.2

- **All five orchestration modes work in the container images.** `workflow:pre-purchase`, `workflow:return-replace` and `group-chat` were missing from every Docker image, which did not copy `workflows/`.
- **Hybrid product search.** `search_products` is Postgres full-text search over a weighted `tsvector`, and `semantic_search` fuses that lexical arm with the pgvector cosine arm by reciprocal-rank fusion. A natural phrase matches. To upgrade an existing database, run `./scripts/dev.sh --clean` (drops local data) or apply the column in place; see [Troubleshooting](troubleshooting.md#productssearch_vector-does-not-exist).
- **A release pipeline and published images.** Container images for every service on GHCR, gated on the test suite, with `release.yml`, `scripts/bump_version.py` and a weekly registry retention policy. `docker-compose.demo.yml` pulls instead of building.
- **The site is legible to machines.** `llms.txt`, `llms-full.txt` and `robots.txt` are generated from the same page set as the site, so they cannot drift from it.
- **An orchestration-mode benchmark harness** (`evals/benchmark_modes.py`). It drives the real HTTP API rather than calling modes in-process, so it exercises auth, guardrails, sanitization and grounding.

### v1.1

- **Follow-up questions keep their context.** Specialists rehydrate the conversation from Postgres on every browser turn, on both stacks, with the query scoped to the caller's own conversation. Before this, Python specialists received no history on browser-originated turns.
- **.NET runs appear in Aspire's GenAI view.** .NET emits the `invoke_agent` span convention Aspire selects on, with Npgsql instrumentation, a meter provider, a log bridge and session/conversation enrichment.
- **The .NET tutorials have a CI gate.** A job builds and tests every tutorial `.csproj`.
- **Semantic search returns relevant results.** The IVFFlat index is built after the data it indexes, and pgvector is exercised under `LLM_PROVIDER=replay`, so CI covers it.
- **Promotions apply.** Bundles, buy-X-get-Y and flash sales compute correctly. The seeder and the reader had used different key names in `promotions.rules`, so no promotion applied before this.
- **The docs site is indexable.** Every page has its own meta description, keywords, `TechArticle` JSON-LD, `lastmod` and a social image, and every diagram has an accessible title.

### v1.0

- **Agent evaluators.** Scripted eval sets (precision@k, recall@k, answer faithfulness, tool-call correctness) across all six agents, run against the seeded catalog. The `smoke` job gates every pull request with deterministic scorers over committed replay fixtures (`LLM_PROVIDER=replay`): no API key, no cost, and a failure when a suite regresses more than 5% against its baseline. The `full` job uses a real key and the LLM judge, on manual dispatch only. The harness (`evals/harness.py`) runs the same orchestration modes a real request does, so guardrails, sanitization and HITL gates are exercised.
- **Prompt injection prevention.** `shared/guardrails/` is wired into the middleware stack for all agents and enabled by default (`GUARDRAILS_ENABLED=true`). It runs observe-first (`GUARDRAILS_FAIL_OPEN=true`), flagging and logging injections. Set `GUARDRAILS_BLOCK_ON_INJECTION=true` for hard blocking once you have measured false-positive rates in your environment.
- **Session memory and context persistence.** `store_memory` / `recall_memories` tools (`shared/tools/memory_tools.py`), surfaced to the orchestrator through `shared/context_providers.py`. Per-user preferences, recent intents and history carry across turns.
- **Full .NET / C# backend.** The same orchestrator and five specialists plus an MCP host, the same A2A protocol, PostgreSQL schema and prompts, with nine test projects. Parity is gated by a dual-backend Playwright suite (`web/e2e/orchestration-parity.spec.ts`) that drives one frontend against both backends and asserts the *presence* of each capability. See [`agents/dotnet/`](../agents/dotnet/) and the [parity matrix](parity-matrix.md).
- **Distributed tracing across every agent.** OpenTelemetry throughout (`shared/telemetry.py`), GenAI semantic conventions, a Langfuse sink, and `trace_id` correlated into `usage_logs`, so a row in the admin usage table links to its trace. Spans nest across A2A hops, so one chat turn reads as a single tree in the [Aspire Dashboard](http://localhost:18888), and the chat shows the same routing as a live agent-activity timeline. The dashboard runs stock; this repo ships no pre-built views.
- **MCP data-access layer.** `mcp-product` (:9000: search, details, comparison, trending, price history) and `mcp-inventory` (:9001: stock, warehouses, shipping, carriers) are standalone Python packages in a uv workspace, served over MCP streamable HTTP. With `MCP_ENABLED`, `product-discovery` and `inventory-fulfillment` swap their direct-asyncpg tools for `MCPStreamableHTTPTool` with no behavior change. The .NET stack ships one host (`ECommerceAgents.Mcp`) on :9001 covering inventory only (`check_stock`, `get_warehouses`, `estimate_shipping`). See [MCP Integration](mcp-integration.md).
- **Self-hosted OAuth2 authorization server.** Opt-in `AUTH_MODE=oauth`, with the token issuer inside this repo (`agents/python/auth_server/`, built on `authlib`) and no external identity provider. It provides RS256 signing with a JWKS endpoint, user login through the resource-owner-password grant brokered by the orchestrator, and client-credentials service tokens in place of the A2A shared secret. Both MCP servers are OAuth 2.1 resource servers (audience/scope validation, `.well-known/oauth-protected-resource`, `WWW-Authenticate` challenge). Python and .NET both validate it, verified end to end against a live stack. `AUTH_MODE=local` (self-issued JWT + shared secret) stays the zero-config default. See the [security guide](security-guide.md).
- **Server-side grounding.** The model's claims are checked against Postgres before the answer leaves. Product and order ids in card blocks are verified to exist and to carry the quoted price, and a fact-check badge reports how many claims were verified. `GROUNDING_MODE` is `annotate` by default (`shared/grounding/`, `Shared/Grounding/`).
- **Orchestration modes, live.** The same question can be answered by a tool router, a handoff mesh, two workflow graphs or a group-chat round table, selected per request from the composer. The graph animates node by node from real SSE events, and "compare modes" runs one prompt through several and reports latency side by side.
- **Idempotency on money-moving actions.** An `idempotency_keys` table plus an `@idempotent` decorator on returns, refunds and checkout, so a resubmitted approval cannot double-execute. Approval writes fail *closed*.
- **Resilience and rate limiting.** Bounded retries with jittered backoff and a per-endpoint circuit breaker on every A2A call (`shared/http_resilience.py`, mirroring the .NET Polly pipeline), and a Redis sliding-window limiter on both chat routes, keyed by user and by IP for anonymous traffic.
- **Generative UI.** Every agent response is rendered by the shape of its data: cards, tables, charts, badges. An unrecognized or malformed payload renders nothing rather than raw JSON. Light and dark themes throughout.

---

## What is deliberately not planned

Recorded so these are not mistaken for oversights:

- **No third backend.** A LangGraph backend was planned and then parked by maintainer decision on
  2026-11-15. Nothing is planned in that direction.
- **No .NET demo compose file.** The `.NET` images are published (seven `<service>-dotnet` images, amd64,
  from `build-images.yml`), but `docker-compose.demo.yml` stays Python-only, because a visitor is there for
  the features, not the backend language. `./scripts/dev.sh --dotnet` builds from source.
- **No Langfuse sink on .NET.** OTel already carries GenAI spans to Aspire; a second exporter
  would be additive only.
- **Anonymous storefront conversations are not persisted** on either stack, so follow-ups there
  have no context at any tier. A product decision, not a bug.
- **Magentic orchestration is wired into neither stack's orchestrator.** .NET can build it:
  `Microsoft.Agents.AI.Workflows` 1.28.0 exports `MagenticWorkflowBuilder`. Chapter 16's .NET
  side is a status stub whose tests pin that the types exist. Not a parity gap, because neither
  side has it.
