# PLAN — the one plan file

> Repo-committed working artifact, per the "Working Artifacts Location" rule in the project
> `CLAUDE.md`. **This is the only plan file.** It used to be twenty, then nine; the rest are in
> git history, which is where finished plans belong.
>
> The published, reader-facing version of the forward work is
> [`docs/roadmap.md`](../../docs/roadmap.md). This file is the working detail behind it: blockers,
> acceptance criteria, and the constraints that have already cost us time.

**Status: 2026-11-15.** **The LangGraph backend (LG1–LG3) is parked** by maintainer decision; the
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
      **Update 2026-11-15: no longer blocked.** Core 1.23+ requires `mcp>=2.2`, so the MAF upgrade
      forces this migration; it is PR B-6 in Part IV.

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
| **R0 — the tour** | After wave 1 and B-3 | OpenAI or Azure OpenAI (the existing one) | The ~10-prompt demo tour (C-6) and the catalog embeddings export (A-6b) | C-7 → C-11, C-18 | $1–3 |
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
| **4 — second upgrade, real A2A, flows** | | B-6 (core 1.26 + MCP 2) → B-7, B-8, B-12 → B-13 → B-14, B-15, B-18, B-19, D-5n, D-6, D-9a → D-9b, M-3 → M-4, V-1 → V-2, B-17, P-3, P-5, P-8, C-20. **Built but held on their branches until R1:** D-5b, B-16, M-2's dataset, E-3b, E-4, E-6, E-7, E-8 — each invalidates or needs fixtures, so it merges with its recording, not before | none for what merges |
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

---

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
| **B-11** Mixed-stack compose profile | #88 | `docker-compose.mixed.yml` overrides rebuilding the five specialists from the other stack's Dockerfile; `MIXED=py-net\|net-py`; `AGENT_REGISTRY` unchanged. `scripts/e2e-both-stacks.sh --mixed`. Run `e2e/orchestration-parity.spec.ts` against both mixed variants and both pure stacks. **Lands before the protocol change, as its regression net.** Needs B-9(d). The compose profile and script are key-free; **the Playwright run needs a live key** — C-6's Python recordings cannot serve it, because the .NET replay client uses different request hashes and response formats and the parity spec signs in as Alice, not the tour user. A keyless version needs its own recordings per stack combination (R1). | M | **yes**, to run |
| **B-12** Conformant A2A 1.0, Python | #88 | **12a** — `a2a-sdk[http-server]` 1.x mounted at `/a2a/v1` (legacy endpoints stay at root). JSON-RPC interface. A custom `AgentExecutor` calling the existing `_run_agent_native_stream` path — **not** `agent-framework-a2a`'s `A2AExecutor`, which builds a MAF session from `contextId` and so changes the messages the specialist model sees and every fixture hash. `contextId` carries the session id, replacing `x-session-id`. Lifecycle submitted → working → completed/failed; `input-required` reserved for the approval work. Text deltas as `TaskArtifactUpdateEvent` with `append=true`. Steps as `TaskStatusUpdateEvent.metadata["https://<repo>/a2a/ext/steps/v1"]`, declared in `capabilities.extensions`, switched on by the `A2A-Extensions` header. Card at `/.well-known/agent-card.json` with `supportedInterfaces` and skills built from each `FunctionTool`'s name and description. **12b** — the client in `orchestrator/agent.py` and `shared/remote_agent.py` uses the SDK client behind `A2A_TRANSPORT=legacy\|a2a` (default `legacy`). `tests/test_a2a_conformance.py`: the official client over `ASGITransport` against a specialist app on replay — card schema, SendMessage, SendStreamingMessage, task states, extension metadata. Accept: REPLAY passes with `A2A_TRANSPORT=a2a`, zero misses. | L (two PRs) | no |
| **B-13** Conformant A2A 1.0, .NET | #88 | `A2A.AspNetCore` preview4 or `Microsoft.Agents.AI.Hosting.A2A.AspNetCore` (`MapA2A`), pinned exactly. Same executor rule, extension URI, card and flag. Contract test: replay the Python conformance cases against .NET with a JSON golden file under `tests/contracts/a2a/`. Then B-11 with `A2A_TRANSPORT=a2a`. Remove the legacy endpoints one release after both stacks default to `a2a`. | L | no |
| **B-14** Identity as a signed token | #88 | `auth-server` (shared by both stacks): RFC 8693 token exchange returning a JWT with `sub` = user email, `role`, `sid`, `act.client_id` = orchestrator, `aud` = the specialist, 5-minute TTL. The orchestrator exchanges once per turn per specialist and sends it as `Authorization`. `AgentAuthMiddleware` on both stacks validates against JWKS and sets ContextVars / `RequestContext`. Flag `A2A_IDENTITY=headers\|token\|both`, default `both` for one release then `token`. Tests: a forged `X-User-Email` with a valid token for another user is rejected; an expired token is rejected. | M | no |
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
panelist stances after B-16. **Magentic — not built**: Python-only, so a permanent parity gap; B-9
removes the advertising.

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
| B-d9 | Magentic and the declarative mode | Do not build Magentic; delete the loader (B-10) |

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
