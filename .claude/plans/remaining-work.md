# PLAN — the one plan file

> Repo-committed working artifact, per the "Working Artifacts Location" rule in the project
> `CLAUDE.md`. **This is the only plan file.** It used to be twenty, then nine; the rest are in
> git history, which is where finished plans belong.
>
> The published, reader-facing version of the forward work is
> [`docs/roadmap.md`](../../docs/roadmap.md). This file is the working detail behind it: blockers,
> acceptance criteria, and the constraints that have already cost us time.

**Status: 2026-09-01. Phase A is done** — see below. v1.3.0 is cut and both backends are live.
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
- [ ] **RFC 7591 dynamic client registration** — scoped and gated, not open registration.
- [ ] **Audit matrix** covering which routes accept which token type.

## 16. Blocked, waiting on upstream

- [ ] **MCP 2.x migration** — blocked on `agent-framework-core`. Listed rather than deleted, because
      an item that vanishes looks like a decision nobody made.

## 17. Known debt

- [ ] **Frontend type/lint debt.** Two ESLint rules are downgraded to warnings in
      `web/eslint.config.mjs` so the gate stays meaningful; the suppressions come off by fixing the
      root causes, not by re-raising the rules. **Type the API layer** — replace `any` in
      `web/src/lib/api.ts` and its consumers with real interfaces (consider extending the Zod types
      in `web/src/lib/chat-schemas.ts`), then restore `@typescript-eslint/no-explicit-any` to
      `error`. **Auth/cart store refactor** — move `lib/auth-context.tsx` and `lib/cart-context.tsx`
      off mount-effect `setState` to a `useSyncExternalStore`-backed store, then restore
      `react-hooks/set-state-in-effect` to `error`. Also clear the remaining `next/no-img-element`
      warnings where `next/image` is practical. **Phase A touches this file set** — the `/api/*`
      proxy lands in the same layer, so typing the API layer is cheapest done alongside it.
- [ ] **The chat page hard-crashes on an unexpected API shape.** Found during plan 20's UI
      verification; unowned. **Blocks nothing until LG1**, at which point a second framework starts
      producing payloads and an unexpected shape stops being hypothetical.
- [ ] **`embeddings=0` in the default local stack**, so `semantic_search` is lexical-only until
      `scripts/generate_embeddings` has run. Correct behaviour, surprising default.

## 18. Recorded decisions — not pending work

Listed so they are not rediscovered as gaps.

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
