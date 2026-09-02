# Roadmap

Where this project is, what shipped, and what is deliberately not done yet.
Generated from the same material that used to sit at the bottom of the README,
where it was below line 600 and effectively unread.

The per-release record is in [CHANGELOG.md](https://github.com/nitin27may/e-commerce-agents/blob/main/CHANGELOG.md).
The working list, including gaps this page does not claim to cover, is in
[`.claude/plans/remaining-work.md`](https://github.com/nitin27may/e-commerce-agents/blob/main/.claude/plans/remaining-work.md).

## Project status

**This is v1, and both backends are live.** Each runs end-to-end: an orchestrator plus five specialist agents, auth, telemetry, and a full Next.js frontend that either backend can serve.

The frontend is a **public, agentic e-commerce storefront**: anyone can browse the catalog, search, and use the AI shopping assistant (`/shop`) without an account — product discovery is served anonymously — while account flows (cart checkout, orders, tracking, returns) require sign-in. A built-in **agent-activity timeline** surfaces the multi-agent routing (orchestrator → specialist → tool) live in chat, backed by OpenTelemetry → .NET Aspire. Light/dark theming throughout.

The **.NET / C# backend** at [`agents/dotnet/`](../agents/dotnet/) is a real implementation, not a demonstration slice: it serves the same frontend, the same database and the same prompts as [`agents/python/`](../agents/python/). Parity is enforced rather than asserted — `web/e2e/orchestration-parity.spec.ts` drives one frontend against both backends and asserts *presence* of each capability, because the earlier suite went green against a .NET stack that was missing four whole features. Remaining differences are listed in [`docs/parity-matrix.md`](parity-matrix.md).

---


## What's next

Ordered, with the reason for the order. Everything below is sequenced against one
constraint: **this repository's value is as a worked reference for building multi-agent
systems**, not as an e-commerce product. When a choice is between "more features" and
"the same features, explained by running them", it goes to the second.

Nothing here is a commitment to a date. Items move when what is learned makes them move,
and when that happens the reason is written down rather than the item quietly disappearing.

| # | Phase | Size | Why it is here, in this position |
|---|---|---|---|
| **A** | Azure unblock | **done** | Two code-level blockers that *every* backend inherits. Cheaper before a third one exists than after |
| **B** | Azure Container Apps | ~4 d | The single largest gap. There is no Azure path of any kind today. Managed identity lands here — the Bicep provisions the vault and the identity anyway |
| **B2** | Dual-backend parity gate into CI | ~1 d | Runs beside B. Every parity claim on this page currently rests on a gate nobody runs automatically |
| **C** | Foundry as model provider | ~2 d | Small, and the `LLM_PROVIDER` switch it needs already exists |
| **LG1** | LangGraph — one specialist behind A2A | ~4 d | The cheapest thing that produces the cross-framework comparison, and the thing that sizes the full port |
| **D** | Foundry Hosted Agents | 3–5 d | Most unknowns of any phase. Now hosts *both* frameworks, which is the point |
| **E** | Publish the Azure reference | ~2 d | Three topologies with verified costs. The difference between them is the content |
| **LG2** | LangGraph — full Python third backend | sized by LG1 | High value, largest scope. Does not start on momentum — it needs LG1's number and an explicit go |
| **LG3** | Three-stack parity gate | ~2 d | Needs B2 first, for the reason B2 exists |
| — | [Unsequenced backlog](#unsequenced-backlog) | — | Real work, no position claimed |
| — | [Blocked, waiting on upstream](#blocked-waiting-on-upstream) | — | Tracked so the blocker is visible rather than looking like neglect |

Blockers, acceptance criteria and the open decisions are in
[the consolidated plan](https://github.com/nitin27may/e-commerce-agents/blob/main/.claude/plans/remaining-work.md).

### Why this order changed

The previous version of this page put the cross-framework comparison last and said it "should not
start until the Azure work lands". That was right in direction and too conservative in degree, and
one of its assumptions has since stopped being true.

**Foundry hosted agents became a framework-agnostic runtime.** Agents built with Microsoft Agent
Framework, LangGraph or the Copilot SDK deploy to the same managed runtime without rewrites, and
there is a first-party path (`langchain_azure_ai.agents.hosting`) for exposing a compiled LangGraph
graph over the same Responses and Invocations protocols phase D targets for MAF. Microsoft also
publishes the Foundry-to-LangGraph **A2A** interop pattern, which is the architecture
[ADR 0001](adr/0001-a2a-over-direct-calls.md) already commits this repository to.

So Azure is not a competing priority with the third backend. It is the substrate the comparison runs
on. Build Azure first and the LangGraph backend inherits a deployment target; build the third
backend first and the deployment story gets written twice. But the dependency is on phase **B**, not
on all of A–E — phase D carries the most unknowns and the LangGraph work does not depend on it. Hence
LG1 starting in parallel with C and D rather than after E.

Two other reasons the comparison waits for a deployment story at all:

- **A comparison nobody can run is content, not a reference.** It is only credible if all three
  backends run in the same place, on the same database, prompts and eval datasets. Today "the same
  place" is Docker Compose on a laptop, which is the one comparison a reader can already do
  themselves.
- **Live runs catch what tests cannot** — the constraint that has governed every phase of this
  project. A third backend with no deployment target can only ever be exercised locally.

### A. Azure unblock — done

Two problems in the application code that would each have stopped an Azure deployment. Neither had
anything to do with Azure services; both were changes any additional backend inherits, which is why
they came first.

**The frontend no longer knows its backend's address.** `NEXT_PUBLIC_API_URL` was compiled into the
client bundle at build time, and the Container Apps FQDN does not exist until provisioning — so the
image would have had to be rebuilt after deploying, which is what makes a one-command deploy
impossible. The browser now calls the frontend's own origin, and a catch-all route handler forwards
`/api/*` to `ORCHESTRATOR_URL`, a server-side variable read per request. One image runs in every
environment, the orchestrator needs no public ingress, and CORS is gone.

Two things that only showed up by running it. A `rewrites()` entry in `next.config.ts` cannot do
this — Next evaluates `rewrites()` during `next build` and bakes the destination into
`routes-manifest.json`, which is the same build-time problem in a new place. And *deleting* the
browser's `accept-encoding` before forwarding is not enough, because undici substitutes its own
default when the header is absent; it has to be pinned to `identity`, or the orchestrator is still
free to compress an SSE stream. Verified against a real server: frames arrive at the rate they are
emitted rather than batched at the end.

Verified against the live stack rather than asserted: login, authenticated reads and a streamed
chat turn all through the proxy, and ten of the twelve Playwright specs green against it —
including *the orchestration graph renders for a workflow mode*, which is the live-graph criterion.
Timed against a control on the same turn, the proxy costs about 160 ms on an 8-second turn and
delivers the same number of stream frames.

That control — a frontend rebuilt to call the orchestrator directly — also settled three test
failures that turned up along the way. All three failed identically with and without the proxy, so
none was caused by this work: two specs asserted that exactly one card rendered where the app
legitimately renders one per result, and a third selected product-grid anchors that stopped
existing when those cards moved to a click handler. All three now assert presence, which is the
criterion the parity gate already states.

This also retires a constraint. `NEXT_DIST_DIR` existed because a second dev server booting off a
warm build directory served the first one's baked API URL — one of the two ways a dual-backend run
could report a green ".NET" pass without touching .NET. A build now encodes nothing about the
backend. The failure *shape* survives as a misconfigured `ORCHESTRATOR_URL`, so the guard that
catches it stays.

**The agent registry fails loudly.** Filed as "`AGENT_REGISTRY` is hardcoded host:port". The actual
problem was larger, and is now a row in [Reported vs actual](reported-vs-actual.md): a validating
parser already existed on both stacks, was tested, and no production call site used it. All four
sites re-parsed the JSON by hand, and three swallowed a malformed value into an empty registry —
which builds, serves, passes a health check, and cannot route. There is now one validator per stack,
asserting the same accepted and rejected inputs, and it throws. Scheme and host are checked; the
port is not, because a managed endpoint does not have one.

The third item originally filed here — managed identity for Azure OpenAI — was not a blocker and
moved to phase B. A key deploys fine; it is a release blocker for phase E, not a deployment one.

### B. Azure Container Apps

**The target topology, decided.** Phase B builds the Container Apps half; the agents move to
Foundry at phase D.

| Component | Target |
|---|---|
| Next.js frontend | Container Apps — the only public surface |
| All six agents | Microsoft Foundry hosted agents (phase D) |
| MCP servers | Container Apps. They are already OAuth 2.1 resource servers, so they are defended by tokens rather than network isolation — which is what makes them reachable from a Foundry-hosted agent without a private-link design |
| PostgreSQL + pgvector | Azure Database for PostgreSQL Flexible Server. A database container has no durable-storage story worth publishing |
| Redis | Azure Cache for Redis, with the containerized fallback kept documented for demo cost |
| auth-server | Container Apps. Not an agent, and "no external IdP" is a selling point — it stays self-hosted rather than becoming Entra |
| seeder | A Container Apps **Job**. It runs once and exits; as a service it restart-loops |

Because every agent goes to Foundry, three things get designed for here rather than discovered in
phase D: whether the Foundry runtime can reach Container Apps at all (every specialist and both MCP
servers are called from inside it), whether the protocol carries the identity headers that every
tool reads out of ContextVars, and whether a workflow that pauses for approval can resume in a
later request when each session owns its own sandbox. The last one is the largest unknown in the
whole plan.

`infra/` Bicep, `azure.yaml`, and both `azure-up.sh` and `azure-down.sh` — the teardown written
*first*, not after. **Cost and teardown are part of the deliverable:** a reader who cannot cheaply
undo it will not try it.

Two ordering bugs are already known and will be designed around rather than discovered: pgvector has
to be enabled before `init.sql` runs, and the seeder has to be ordered before index creation — the
same failure class as the IVFFlat-on-an-empty-table bug fixed in v1.1, where an index built before
its data is silently wrong rather than loudly broken. Application Insights is the documented
telemetry sink.

Acceptance is written as something that either happened or did not. Not "deployed successfully", but:
from a clean subscription, a signed-in user completes a chat turn with product cards and one approval
gate at the URL the script printed; Playwright passes against it; and teardown leaves zero resources
and no soft-deleted vault.

### B2. Dual-backend parity gate into CI

**Moved ahead of the third backend.** It used to sit under eval completeness, after the
cross-framework work. That was the wrong order: adding a third stack to a parity harness nobody runs
automatically triples the number of claims resting on a gate run by hand.

- [ ] Get `scripts/e2e-both-stacks.sh` into CI. It runs only locally today because it needs both
      stacks up against a seeded database. [ADR 0005](adr/0005-dual-stack-parity.md) records this as
      its own honest weakness.

### C. Foundry as model provider

- [ ] `LLM_PROVIDER=foundry` on both stacks, embeddings endpoint resolved, and one hosted web-search
      tool on `review-sentiment` as the demonstration. Acceptance is the eval smoke suite passing
      under the Foundry provider and the hosted tool visible in an Application Insights trace.

### LG1. LangGraph — one specialist behind A2A

The cross-framework work is **not all-or-nothing**, and this is the discovery that makes the rest of
the sequence affordable. Because [ADR 0001](adr/0001-a2a-over-direct-calls.md) put A2A between the
orchestrator and every specialist, a single agent can be reimplemented on a different framework
behind the identical contract, with the orchestrator unchanged.

`review-sentiment` is the candidate: fewest tools, no money-moving actions, no approval gate, and it
is already the agent carrying the hosted tool at phase C.

- [ ] A LangGraph `review-sentiment` serving the same `/message:send` contract, the same
      `config/prompts/review-sentiment.yaml`, the same database, registered through the
      `AGENT_REGISTRY` seam built at phase A.
- [ ] Its eval dataset scored and recorded beside the MAF Python and MAF .NET numbers —
      **re-recorded, never copied**, the same rule that governs the .NET baselines.
- [ ] A written note of what the port actually cost, per layer. That note is what sizes LG2.

**The port is likely cheaper than previously assumed.** All ten files under `agents/python/shared/tools/`
couple to the framework through exactly one line each — `from agent_framework import tool` — with
bodies that are plain async Python over asyncpg and `Annotated[..., Field(...)]` parameters, a
signature style LangChain's own `@tool` reads natively. Prompts are YAML and framework-neutral, and
the database, auth, OAuth and search layers import no agent framework at all. What is genuinely
expensive is the orchestration layer: the five modes, the approval gates, the context providers, the
guardrail middleware chain and checkpointing. LG1 exists to turn that estimate into a measurement.

### D. Foundry Hosted Agents

- [ ] The orchestrator packaged for Invocations, specialists reached via hosted MCP, and a Responses
      variant built alongside with the graph loss documented.
- [ ] The LG1 LangGraph specialist deployed to the same hosted runtime. **This is the payoff for
      sequencing Azure first** — one runtime, two frameworks, and the comparison becomes a property
      of the deployment rather than an essay about it.

Acceptance includes one A2A call to a Container Apps specialist either proven to work or
**explicitly recorded as not working, with the reason**.

### E. Publish the Azure reference

`docs/azure-deployment.md`, with **verified cost numbers rather than estimates**, covering four
topologies. The goal is deliberately not "this app runs on Azure" — it is a reference for taking a
multi-agent system to Azure, with this app as the worked example and the trade-offs written down.

| Topology | Who owns the runtime | Who owns the model |
|---|---|---|
| Docker Compose | you | you |
| Azure Container Apps | you | you |
| Foundry as model provider | you | Foundry (model and hosted tools) |
| Foundry Hosted Agents | Foundry | Foundry |

One deployment is a tutorial. The same six agents in several topologies, with what each one costs, is
a reference — **the difference between them is the content.**

### LG2. LangGraph — full Python third backend

Gated on LG1's findings and an explicit go decision. Python only: same frontend, same database, same
prompt corpus, same eval datasets, so the differences are attributable to the framework rather than
to the problem. That is the artifact that is genuinely hard to find elsewhere — the same non-trivial
system built three ways.

Human-in-the-loop is the part worth watching. Python MAF pauses with a two-call
`execute()`/resume-via-`responses={...}`; .NET MAF has no equivalent and needs a dedicated
`RequestPort` node with the run held open across the pause; LangGraph uses `interrupt()` plus a
checkpointer. Three shapes for one requirement is a real asymmetry rather than a tie, and it is the
most useful thing this comparison can produce.

### LG3. Three-stack parity gate

Extend the parity spec and the dual-stack runner to three backends. **Needs B2 first.** The exit
criterion is the one already applied to .NET: `PARITY_GAPS.<stack>` empty *while every test in the
spec asserts presence*, because the earlier suite went green against a stack missing four whole
features.

### Open decisions

Three questions gate the back half of the sequence. A, B and B2 are correct under every answer, so
they are not waiting on these. Recorded here rather than left in conversation, because a plan that
depends on an unwritten assumption gets relitigated.

- [ ] **Azure spend ceiling.** Phases B–D cost money continuously; the LangGraph work costs only eval
      tokens. The .NET eval suite is currently deferred over roughly $1.50, which is the strongest
      available evidence that spend rather than time may be the binding constraint. If it is, LG1
      moves ahead of B.
- [ ] **Primary objective** — repository credibility as a worked Azure reference, or reach for the
      article series. "MAF vs LangGraph" is the higher-traffic reader question; "MAF on Container
      Apps" is the more useful artifact. This order optimises for the second and gets the first at LG1.
- [ ] **Does .NET stay at full parity?** A third backend plus a parity-locked .NET stack means three
      stacks in every gate, permanently. Freezing .NET at the v1.3 feature set is a defensible answer,
      and needs to become a recorded decision either way — including in
      [`docs/parity-matrix.md`](parity-matrix.md), which currently reads as an open commitment.

### Unsequenced backlog

Real work with no position claimed against the phases above.

**Two open items from the v1.3 close-out.** Both are known, both are small, and both undercut
something the repo already says.

- [ ] **The .NET eval suite.** Six of seven datasets are ported and the enabling work is merged. What
      remains needs a real key: record the fixtures, generate .NET baselines (**re-recorded, never
      copied from Python** — different mode set, so different absolute scores are legitimate), and
      add a CI job gating on *baseline regression* rather than an absolute floor, because the score
      is a property of the recording session. Deferred for budget, not difficulty.
- [ ] **The demo clip.** The spec is honest now — eight attempts found five defects that each let the
      run exit 0 while silently dropping the approval and resume beats, and it throws instead of
      logging. Still open: the return turn does not reach the approval gate, and the run hits its
      600s cap.

**Eval and gate completeness.** The dual-backend gate moved up to B2; what is left:

- [ ] **An eval gate for the MCP path** — run each dataset twice, native tools versus MCP, and fail
      CI if the MCP run scores below the native baseline. Today MCP is offered as an alternative
      data-access layer with nothing measuring whether it is as good.
- [ ] **A red-team evaluator.** `red_team.json` is scored by keyword matching, which means very
      little; it needs its own schema and judge.

**Retrieval and the tool surface.**

- [ ] **Typed filter DSL** — replace `search_products`' flat parameter list with a structured
      `ProductFilters` model. Text-to-SQL was considered and rejected ([ADR 0002](adr/0002-no-text-to-sql.md)):
      `user_email`/`user_role` scoping lives in ContextVars, and dynamic SQL would bypass that
      contract. A typed DSL gives the model flexibility at the boundary while keeping SQL generation
      server-side and auditable. **Cheaper before LG2 than after** — every tool signature ported is
      one more copy to change.
- [ ] **Publish the two MCP servers to PyPI** so any MCP-compatible client can run them against any
      PostgreSQL database without this codebase. That is the honest test of whether they are a real
      integration surface or just internal plumbing with a protocol on top.
- [ ] **Prompt caching** — cache system prompts and tool schemas per agent. Measurable against the
      cost counter that shipped in v1.3, which is the reason it is worth doing now rather than
      guessing at.

**The two cross-framework options not taken.** The rungs above are the middle of three options. The
other two remain open and separable.

- [ ] **Claude and other providers as a third model backend.** One chat client per stack behind the
      existing `LLM_PROVIDER` switch — both backends keep their orchestration, only the model
      changes. Mostly answers "is this locked to OpenAI?". **Worth doing before LG2 finishes:** a
      three-framework comparison run on a single model family is confounded by nothing, and this is
      the switch that proves it.
- [ ] **Agentic workflows on the repository itself** — coding agents for eval recording,
      documentation-drift checks and review. Ships nothing in the product; improves the rate at which
      everything else here gets done.


### Blocked, waiting on upstream

- [ ] **MCP 2.x migration** — blocked on `agent-framework-core`. Listed so the blocker stays visible;
      an item that vanishes looks like a decision nobody made.
- [ ] **OAuth later phases** — Phases A–D ship and are live-verified on both stacks. What remains is
      key rotation, RFC 7591 dynamic client registration, and an audit matrix — real future work, not a gap.

### What is deliberately not planned

Recorded so these are not rediscovered as oversights:

- **The third backend is LangGraph, and Python only.** Not the Claude Agent SDK, and not a .NET
  equivalent. One language keeps the comparison to one variable.
- **The third backend ships as rungs, not as a release.** LG1 is a spike that has to produce a
  number and a cost note. LG2 does not start on momentum.
- **No .NET container images are published.** The demo path stays Python-only — a visitor is
  there for the features, not the backend language. `--dotnet` remains build-from-source.
- **No Langfuse sink on .NET.** OTel already carries GenAI spans to Aspire; a second exporter
  would be additive only.
- **Anonymous storefront conversations are not persisted** on either stack, so follow-ups there
  have no context at any tier. A product decision, not a bug.
- **Magentic orchestration exists in neither stack**, and on .NET it is unavailable rather than
  merely unbuilt: Magentic is Python-only in MAF v1. Chapter 16's .NET side is a status stub whose
  tests are a tripwire — they reflect over the shipped assembly and go red the day Microsoft ships
  it. Not a parity gap, because neither side has it.

---

## What has shipped

This is v1.3. Both backends are live and stable. Remaining work is consolidated in
[`.claude/plans/remaining-work.md`](https://github.com/nitin27may/e-commerce-agents/blob/main/.claude/plans/remaining-work.md) — including the gaps
this section does not claim to cover.

One pattern is worth stating, because it has shaped every release so far: **the reported problem has
been smaller than the actual one every single time**, and every time the difference was found by
running something rather than reading it. "Follow-ups occasionally lose context" was deterministic
and total. "`optimize_cart` divides by zero" was *no promotion had ever worked*. Several were found
only because a gate had just been switched on — which is why the gates come before the content work
in the sequence above. The full table, now eight rows, is at
[Reported vs actual](reported-vs-actual.md).

Legend: `- [x]` shipped · `- [ ]` planned or in progress.

### Shipped in v1

- [x] **Agent evaluators** — scripted eval sets (precision@k, recall@k, answer faithfulness, tool-call correctness) across all six specialists, run against the seeded catalog. `.github/workflows/evals.yml` runs two jobs. **`smoke` gates every pull request**: deterministic scorers only, driven by committed replay fixtures under `LLM_PROVIDER=replay`, so it needs no API key, costs nothing, and fails the PR when a suite regresses more than 5% against its committed baseline. **`full`** runs weekly on a schedule (and on demand) with a real key and the LLM judge. The harness drives the *production* path — `evals/harness.py` runs the same orchestration modes a real request does, so guardrails, sanitization and HITL gates are exercised rather than bypassed.
- [x] **Prompt injection prevention** — `shared/guardrails/` wired into the middleware stack for all agents. Enabled by default (`GUARDRAILS_ENABLED=true`); runs in observe-first mode (`GUARDRAILS_FAIL_OPEN=true`) — flags and logs injections. Set `GUARDRAILS_BLOCK_ON_INJECTION=true` to enable hard blocking once false-positive rates are measured in your environment.
- [x] **Session memory & context persistence** — `store_memory` / `recall_memories` tools in `shared/tools/memory_tools.py`, surfaced to the orchestrator via `shared/context_providers.py`. Per-user preferences, recent intents, and history make follow-ups feel continuous.
- [x] **Full .NET / C# backend** — the same orchestrator and five specialists plus an MCP host, the same A2A protocol and PostgreSQL schema, idiomatic .NET throughout. Eight test projects, 450 test methods (~500 cases counting `[Theory]` data). Reached parity on the shipped surface through a nine-PR effort covering the shared tool library, orchestration modes, normalized SSE events, server-side grounding, rate limiting, cost estimation and a HITL claim-before-execute fix — gated by a dual-backend Playwright suite rather than a checklist. See [`agents/dotnet/`](../agents/dotnet/) and [`docs/parity-matrix.md`](parity-matrix.md).
- [x] **Distributed tracing across every agent** — OpenTelemetry throughout (`shared/telemetry.py`), GenAI semantic conventions, a Langfuse sink, and `trace_id` correlated into `usage_logs` so a row in the admin usage table links back to its trace. Spans nest correctly across A2A hops, so one chat turn reads as a single tree in the [Aspire Dashboard](http://localhost:18888). The dashboard itself runs stock — this repo ships no pre-built views.
- [x] **MCP data-access layer (2 servers)** — `mcp-product` (:9000) and `mcp-inventory` (:9001) are standalone, independently publishable Python packages (`packages/mcp-product`, `packages/mcp-inventory`) in a uv workspace. They expose product and inventory data over the MCP streamable HTTP transport (FastMCP). Flag-gated via `MCP_ENABLED`; `product-discovery` and `inventory-fulfillment` swap their direct-asyncpg `@tool` set for `MCPStreamableHTTPTool` with no behavior change. Any MCP-compatible client — Claude Desktop, Cursor, LangGraph — can use them without this codebase. See [MCP Integration](mcp-integration.md).
- [x] **Self-hosted OAuth2 Authorization Server** — opt-in `AUTH_MODE=oauth` path with the token issuer living *inside* this repo (`agents/python/auth_server/`, built on `authlib`), so login and every service call are genuinely OAuth2-compliant with no external identity provider or cloud dependency. RS256 signing with an AS-generated keypair and a JWKS endpoint; user login via the resource-owner-password grant brokered by the orchestrator (the browser keeps its email/password form); client-credentials service tokens replacing the static A2A shared secret; and both MCP servers hardened into OAuth 2.1 resource servers (audience/scope validation, `.well-known/oauth-protected-resource`, `WWW-Authenticate` challenge) — Python and .NET parity throughout. Fully additive — `AUTH_MODE=local` (self-issued JWT + shared secret) stays the zero-config default, so the OpenAI-key-only quick-start is unaffected. Verified end to end against a live stack: real browser login and chat session on AS-issued tokens, role-gated routes, inter-agent and MCP calls authenticated purely on OAuth scopes (no shared secrets), and cross-scope/cross-resource token rejection — both stacks, including the .NET MCP host validated against the real running auth-server. See [`docs/security-guide.md`](security-guide.md).

- [x] **Server-side grounding** — the model's claims are checked against Postgres before the answer leaves. Product and order ids in card blocks are verified to exist and to carry the quoted price; a fact-check badge reports how many claims were verified. `GROUNDING_MODE` is `annotate` by default (`shared/grounding/`, `Shared/Grounding/`).
- [x] **Orchestration modes, live** — the same question can be answered by a tool router, a handoff mesh, two workflow graphs or a group-chat round table, selected per request from the composer. The graph animates node-by-node from real SSE events, and "compare modes" runs one prompt through several and reports latency side by side.
- [x] **Idempotency on money-moving actions** — an `idempotency_keys` table plus an `@idempotent` decorator on returns, refunds and checkout, so a resubmitted approval cannot double-execute. Approval writes fail *closed*.
- [x] **Resilience and rate limiting** — bounded retries with jittered backoff and a per-endpoint circuit breaker on every A2A call (`shared/http_resilience.py`, mirroring the .NET Polly pipeline that led here), and a Redis sliding-window limiter on both chat routes, keyed by user and by IP for anonymous traffic.
- [x] **Generative UI** — every agent response is rendered by the shape of its data: cards, tables, charts, badges. An unrecognized or malformed payload renders nothing rather than falling back to raw JSON.

### Shipped in v1.1

- [x] **Follow-up questions keep their context** — specialists received *no* conversation history on any browser-originated turn, on the Python stack, deterministically. The web client never sent `x-session-id`, so rehydration short-circuited before the database and without logging. It read as model nondeterminism for weeks because the orchestrator sometimes inlined context into the specialist message and sometimes didn't. Fixed on both stacks, with the rehydration query now scoped to the caller's own conversation.
- [x] **.NET runs appear in Aspire's GenAI view** — .NET emitted `agent.run`/`chat` where the convention Aspire selects on is `invoke_agent`, so the dashboard looked empty on that backend while working normally on Python. Npgsql instrumentation, a meter provider, a log bridge and session/conversation enrichment landed with it.
- [x] **The .NET tutorials have a CI gate** — no job had ever built any of the 31 tutorial `.csproj` files. Turning the gate on immediately found chapter 08 entirely broken.
- [x] **Semantic search actually works** — it was dead under `LLM_PROVIDER=replay` (so no CI run ever exercised pgvector), and underneath that sat a production bug: the IVFFlat index is created on an empty table, so it had no centroids and returned unrelated products at similarity 0.000 where an exact scan returned the right one at 0.420.
- [x] **Promotions apply** — `promotions.rules` is untyped JSONB and the seeder wrote different key names than the reader read, so bundles contributed £0 on every cart, buy-X-get-Y crashed, and flash sales silently never matched. No promotion had ever applied correctly.
- [x] **The docs site is indexable** — all 85 pages shared one meta description. Now per-page descriptions, keywords, `TechArticle` JSON-LD, `lastmod`, a social image, and an accessible title on every one of the 71 diagrams.

### Shipped in v1.2

- [x] **Three of the five orchestration modes were dead in every Docker image** — `workflow:pre-purchase`, `workflow:return-replace` and `group-chat` returned the same 82-character apology regardless of the prompt, in under 10ms. The Dockerfile copied `shared/`, `config/` and `${AGENT_NAME}/` and nothing else, so `workflows/` was simply absent. Every containerised deployment was affected. Nothing caught it: the eval harness runs in-process where those packages are on `sys.path` regardless of image contents, and the image smoke-test imports `<agent>.main` only — every one of the missing imports is lazy, inside the mode, so the module imports cleanly and fails at request time.
- [x] **Hybrid product search** — `search_products` split the query into words and ANDed `%word%` patterns, so a natural phrase matched nothing. Replaced with Postgres full-text search plus reciprocal-rank fusion against the vector index.
- [x] **A release pipeline, and images to publish with it** — container images for all ten services on GHCR gated on the test suite, `release.yml`, `scripts/bump_version.py`, and a weekly registry retention policy. Plus `docker-compose.demo.yml`, which pulls rather than builds.
- [x] **The site is legible to machines** — `llms.txt`, `llms-full.txt` and `robots.txt`, generated from the same page set the site is built from, so they cannot drift from it.
- [x] **An orchestration-mode benchmark harness** (`evals/benchmark_modes.py`) — drives the real HTTP API rather than calling modes in-process, so it exercises auth, guardrails, sanitization and grounding along the way.

### Shipped in v1.3

- [x] **Tutorial .NET coverage** ([#20](https://github.com/nitin27may/e-commerce-agents/issues/20)) — done. Every chapter that ships code now ships both languages, both tested in CI: 334 .NET tests across 31 projects (was 47 across 11). The one remaining gap is chapter 20b, and it is Microsoft's: `Microsoft.Agents.AI.DevUI` is prerelease-only. The status table in `tutorials/README.md` is now generated from disk by `scripts/check_tutorial_coverage.py` and gated in CI, so it cannot drift again.
- [x] **Composer UX** ([#4](https://github.com/nitin27may/e-commerce-agents/issues/4)) — done. The six always-visible mode chips took the composer's top third to expose a control most turns never touch; they collapse into one toolbar picker. Suggested prompts are now derived from the assistant's last message rather than being the same four canned prompts after every turn — keyed off the message's typed card payload first (a ```product fence means the user is looking at products, whatever the prose says) and its closing question second, with the old static list as the fallback. No LLM call: it is a pure function over text the client already has.
- [x] **In-chat approval card** — done. The pause-and-resume loop already worked on both stacks, but the only control that could release a pause lived on `/runs`, so the user who caused it had no way to act without knowing a separate page existed. The blocker was that no streaming client ever learned the run's id: it *is* the `usage_logs` row, created during persistence, after `event: metadata` has gone out. Both stacks now emit `event: run` (`{run_id, pending_approval}`) once persistence lands and before `[DONE]`, and the chat thread renders Approve/Reject inline. Fixing this also surfaced that .NET wrote `[DONE]` before persisting — the race Python fixed as #9 — so the two stacks disagreed on whether a finished turn was durable.
- [x] **Cost metrics as first-class counters** — done. `shared/telemetry.py` had exposed `get_meter()` since telemetry was wired up and nothing had ever called it, so every metric on the dashboard came from MAF's or FastAPI's instrumentation and the one number this application knows — what a run costs — existed only as a log line. Both stacks already price every turn to enforce a budget ceiling; that estimate is now emitted as `ecommerce.llm.cost.usd`, with tokens split by direction beside it, because cost is derived from tokens through a hand-edited price table and only the raw counts say which of the two moved. Same instrument and meter names on Python and .NET, so one dashboard covers both. Nothing user-scoped is attached to the attributes.
- [x] **Streaming tool calls end-to-end** — done. Both stacks batched their timeline steps until after the last text chunk, so the timeline snapped into place once the answer had finished writing — precisely when it stops being useful. In a MAF tool loop the tools resolve first and the prose narrating them comes second, so both specialist hosts now drain steps before each chunk and both orchestrators forward them live. Not done, deliberately: rendering cards from those payloads as well, since the answer text already carries ```product fences for the same data and a client drawing from both would show every card twice.

### In progress

- [ ] **.NET eval suite** — 6 of 7 datasets are ported and the enabling work is done (record-on-miss, and an embedding seam without which product-discovery could not start in replay mode at all). What remains is the recording run, the baselines, and the CI job. `red_team` needs its own evaluator and is tracked separately. Scoped in the [unsequenced backlog](#unsequenced-backlog).

---

### Search & Retrieval

`search_products` is now Postgres full-text search over a weighted `tsvector`, and `semantic_search` fuses that lexical arm with the pgvector cosine arm — see *Hybrid product search* under Shipped in v1.1. What is left here is the shape of the filter surface, not the retrieval itself:

The one planned change here — a **typed filter DSL** replacing the flat parameter list on
`search_products` — is tracked in the [unsequenced backlog](#unsequenced-backlog) rather than
repeated here.

> **Upgrading an existing database.** The `tsvector` column ships in `docker/postgres/init.sql`,
> which Postgres only runs on an empty data directory. Either `./scripts/dev.sh --clean` (drops all
> local data) or apply it in place — see [Troubleshooting](troubleshooting.md#products-search_vector-does-not-exist).

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

See [MCP Integration](mcp-integration.md) for the full setup guide, tool coverage table, external client examples (Claude Desktop, LangGraph), and publishing instructions.

Two planned changes — publishing both servers to PyPI, and an eval gate comparing the MCP path
against native tools — are tracked in the [unsequenced backlog](#unsequenced-backlog).

---


