# PLAN — the one plan file

> Repo-committed working artifact, per the "Working Artifacts Location" rule in the project
> `CLAUDE.md`. **This is the only plan file.** It used to be twenty, then nine; the rest are in
> git history, which is where finished plans belong.
>
> The published, reader-facing version of the forward work is
> [`docs/roadmap.md`](../../docs/roadmap.md). This file is the working detail behind it: blockers,
> acceptance criteria, and the constraints that have already cost us time.

**Status: 2026-08-27.** Plan 20 (close-out) is complete — the .NET stack works, both broken
orchestration modes are fixed, the benchmark is published, the ADRs exist, four features shipped,
and every document has been reconciled against what the code actually does. **v1.3.0 is cut** —
the forward roadmap it was held for landed first, so the release ships with a published,
checkbox-tracked statement of what comes next rather than pointing at an empty page.

---

## 1. Next objective — Azure and Microsoft Foundry

**The largest gap in the repository.** No Bicep, no `azure.yaml`, no Terraform, no Kubernetes
manifest, no Foundry integration. `docs/deployment.md` is 428 lines of local Docker Compose, and
most readers arrive asking how they would run this at work.

The goal is not "this app runs on Azure". It is a **reference for taking a multi-agent MAF system
to Azure**, with this app as the worked example and the trade-offs written down. That means three
topologies, because *the difference between them is the content*: one deployment is a tutorial;
the same six agents in three topologies, with what each costs you, is a reference.

| Topology | Who owns the runtime | Who owns the model |
|---|---|---|
| 0 — Docker Compose | you | you | 
| 1 — Azure Container Apps | you | you |
| 2 — Foundry as model provider | you | Foundry (model + hosted tools) |
| 3 — Foundry Hosted Agents | Foundry | Foundry |

### 1.1 Blockers found in the code before writing any `az` command

Each of these will stop a deployment. Finding them by reading was the point of planning first.

- [ ] **B1 — `NEXT_PUBLIC_API_URL` is inlined at build time**, and the ACA FQDN does not exist
      until provisioning. Three ways out: two-phase deploy (works, makes `azd up` a lie), a custom
      domain pinned up front (adds DNS and certificates to a quick start), or **proxy `/api/*`
      through the Next.js server to the orchestrator's internal FQDN**. Take the third: the browser
      only ever talks to its own origin, the variable becomes a relative path, the orchestrator
      needs no external ingress at all, and CORS disappears. Smallest change, and it *removes* a
      public surface rather than adding one.
- [ ] **B2 — no managed-identity path to Azure OpenAI.** Both stacks authenticate with a key.
      `AZURE_OPENAI_AUTH=key|identity` on both.
- [ ] **B3 — `AGENT_REGISTRY` is hardcoded host:port.** Must be assembled from Bicep outputs, and
      the A2A client verified against `https` with no port.
- [ ] **B4/B5 — database ordering.** pgvector must be enabled before `init.sql` runs, and the
      seeder job must be ordered before index creation.
- [ ] **B6 — telemetry has no Azure sink documented.** Application Insights section in the
      deployment doc.

### 1.2 Phases and acceptance

Acceptance is written as something that either happened or did not. "Deployed successfully" is not
acceptance; a signed-in user completing a chat turn is.

- [ ] **Phase A — unblock (~2 d).** B1, B2, B3. *Acceptance:* the existing Playwright suite passes
      unchanged through the proxy, **and SSE streaming and the live mode graph are verified through
      it rather than assumed**; a live chat turn against Azure OpenAI with no key in the environment.
- [ ] **Phase B — Azure Container Apps (~4 d).** `infra/` Bicep, `azure.yaml` and `azure-down.sh`
      written and tested **first**. *Acceptance:* from a clean subscription, `azure-up.sh` produces
      a working public URL where a signed-in user completes a chat turn with product cards and one
      approval gate; Playwright passes against that URL via `E2E_BASE_URL`; `azure-down.sh` leaves
      zero resources **and no soft-deleted vault**.
- [ ] **Phase C — Foundry as model provider (~2 d).** `LLM_PROVIDER=foundry` on both stacks,
      embeddings endpoint resolved, one hosted web-search tool on `review-sentiment` as the
      demonstration. *Acceptance:* the eval smoke suite passes under the Foundry provider, and a
      live turn shows the hosted tool called in the Application Insights trace.
- [ ] **Phase D — Foundry Hosted Agents (~3–5 d).** Orchestrator packaged for Invocations,
      specialists reached via hosted MCP, a Responses variant built alongside with the graph loss
      documented. *Acceptance:* both variants deployed and callable; the Invocations variant drives
      the existing frontend unchanged; one A2A call to an ACA specialist either proven to work or
      **explicitly recorded as not working, with the reason**.
- [ ] **Phase E — publish (~2 d).** `docs/azure-deployment.md` on the site with verified cost
      numbers, not estimates.

**Cost and teardown are part of the deliverable, not an afterthought.** A reader who cannot cheaply
undo it will not try it, so `azure-down.sh` is written in Phase B alongside the provisioning, not
after it.

---

## 2. Open items from plan 20

- [ ] **The .NET eval suite.** 6 of 7 datasets ported; record-on-miss and the `IEmbeddingProvider`
      seam are merged. Needs a live key: record fixtures for all six, generate .NET baselines —
      **re-recorded, never copied from Python**, since .NET has a different mode set and its
      absolute scores legitimately differ — and add a CI job gating on *baseline regression* rather
      than an absolute floor, because the score is a property of the recording session (measured
      spread: 8 points across four identical recordings). `red_team` needs its own schema and
      evaluator and stays separate. **Deferred for budget (~$1.50), not difficulty.**
- [ ] **The demo clip.** Eight attempts found five defects that each let the run exit 0 while
      silently dropping the approval and resume beats: wrong mode for the gate, a regex that cannot
      span `" & "`, locators keyed off button text that changes after the first switch, four
      simultaneous data constraints, and a return that can only be initiated once per order. All
      five are fixed and the spec now **throws** instead of logging. *Still open:* the return turn
      does not reach the HITL gate and the run hits its 600 s cap with no `hitl_requests` row.
      The prompts are fine — FTS ranks `Allbirds Wool Runners` at 0.67.

---

## 3. Eval and gate completeness

- [ ] **Get the dual-backend Playwright gate into CI.** It is the definition of done for parity and
      it runs only locally, because it needs both stacks up against a seeded database.
      [ADR 0005](../../docs/adr/0005-dual-stack-parity.md) records this as its own honest weakness.
      Every parity claim in the repo currently rests on a gate nobody runs automatically.
- [ ] **An eval gate for the MCP path** — run each dataset twice, native tools versus MCP, and fail
      CI if the MCP run scores below the native baseline. MCP is offered as an alternative
      data-access layer with nothing measuring whether it is as good.
- [ ] **A red-team evaluator.** `red_team.json` is scored by keyword matching, which means very
      little; it needs its own schema and judge.

---

## 4. Retrieval and the tool surface

- [ ] **Typed filter DSL** — replace `search_products`' flat parameter list with a structured
      `ProductFilters` model. Text-to-SQL was considered and rejected
      ([ADR 0002](../../docs/adr/0002-no-text-to-sql.md)): `user_email`/`user_role` scoping lives in
      ContextVars and dynamic SQL would bypass that contract. A typed DSL gives the model
      flexibility at the boundary while keeping SQL generation server-side and auditable.
- [ ] **Publish the two MCP servers to PyPI**, so any MCP client can run them against any
      PostgreSQL database without this codebase. That is the honest test of whether they are a real
      integration surface or internal plumbing with a protocol on top.
- [ ] **Prompt caching** — cache system prompts and tool schemas per agent. Worth doing *now*
      rather than earlier because the cost counter that shipped in v1.3 can measure it.

---

## 5. Cross-framework comparison — needs a decision before any code

The repo already runs the same six agents, the same database and the same prompt corpus through two
implementations of one framework. The question a reader asks next is how that compares to the
alternatives they are actually choosing between. Three separable options, increasing in scope:

- [ ] **Claude and other providers as a third model backend.** One chat client per stack behind the
      existing `LLM_PROVIDER` switch. Both backends keep their orchestration; only the model
      changes. Mostly answers "is this locked to OpenAI?".
- [ ] **A third backend on a different agent SDK** — Claude Agent SDK or LangGraph — serving the
      same frontend, database and prompts. Turns a two-way comparison into a three-way one and
      produces something genuinely hard to find: the same non-trivial system built three ways, with
      the differences attributable to the framework rather than to the problem.
- [ ] **Agentic workflows on the repository itself** — coding agents for eval recording,
      documentation-drift checks, review. Ships nothing in the product; improves the rate at which
      everything else gets done.

**Sequencing:** the middle option does not start until Azure lands. A third backend multiplies the
deployment matrix, and building it before there is *one* good deployment story would produce three
mediocre ones.

---

## 6. OAuth — later phases

Phases A–D shipped and are live-verified: the authorization server, user login brokered by the
orchestrator, client-credentials inter-agent auth, and the MCP servers as OAuth 2.1 resource
servers — all on both stacks. The design and per-phase notes for what shipped are documented in
[`docs/security-guide.md`](../../docs/security-guide.md).

- [ ] **Key rotation.** A single active signing key per `kid`, with no automatic rotation, is the
      known gap. `AUTH_SIGNING_KEY_ENCRYPTION_KEY` and per-service `OAUTH_CLIENT_SECRET` must come
      from a secret store in any real deployment; the `OAUTH_SEED_KEY` dev default must never ship.
- [ ] **RFC 7591 dynamic client registration** — scoped and gated, not open registration.
- [ ] **Audit matrix** covering which routes accept which token type.

---

## 7. Blocked, waiting on upstream

- [ ] **MCP 2.x migration** — blocked on `agent-framework-core`. Listed rather than deleted, because
      an item that vanishes looks like a decision nobody made.

---

## 8. Known debt

- [ ] **Frontend type/lint debt.** Two ESLint rules are downgraded to warnings in
      `web/eslint.config.mjs` so the gate stays meaningful; the suppressions come off by fixing the
      root causes, not by re-raising the rules. **Type the API layer** — replace `any` in
      `web/src/lib/api.ts` and its consumers with real interfaces (consider extending the Zod types
      in `web/src/lib/chat-schemas.ts`), then restore `@typescript-eslint/no-explicit-any` to
      `error`. **Auth/cart store refactor** — move `lib/auth-context.tsx` and `lib/cart-context.tsx`
      off mount-effect `setState` to a `useSyncExternalStore`-backed store, then restore
      `react-hooks/set-state-in-effect` to `error`. Also clear the remaining `next/no-img-element`
      warnings where `next/image` is practical.
- [ ] **The chat page hard-crashes on an unexpected API shape.** Found during plan 20's UI
      verification; unowned.
- [ ] **`embeddings=0` in the default local stack**, so `semantic_search` is lexical-only until
      `scripts/generate_embeddings` has run. Correct behaviour, surprising default.

---

## 9. Recorded decisions — not pending work

Listed so they are not rediscovered as gaps.

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
- **Magentic orchestration exists in neither stack.** MAF .NET ships `MagenticWorkflowBuilder`, so
  it is unbuilt rather than unavailable — and not a parity gap, because neither side has it.
- **Text-to-SQL was considered and rejected.** See ADR 0002.

---

## 10. The pattern worth carrying forward

**The reported problem has been smaller than the actual one every single time**, and every time the
difference was found by running something rather than reading it. That table is now a published
page — [`docs/reported-vs-actual.md`](../../docs/reported-vs-actual.md) — with eight rows. It is the
most useful thing in this repo for anyone deciding how much to trust an issue title.

---

## Constraints that still bite

Carried forward because each was learned the hard way and still governs future work.

**Live runs catch what tests cannot.** The .NET orchestrator could never route — `EcommerceContextProvider`
returned a fresh `AIContext`, discarding the caller's messages and clearing every tool — and 418
tests passed while it was broken. It was found by pointing `AZURE_OPENAI_ENDPOINT` at a logging proxy
and reading what actually reached the model. Every item above must be exercised against a running
stack, not just unit-tested.

**Two ways a dual-backend run can lie, both observed.** The Playwright base URL override is
`E2E_BASE_URL`; a run setting anything else silently drives whichever frontend is on `:3000`. And
`NEXT_PUBLIC_*` is inlined at build time, so a second `next dev` started while the first one's build
directory is warm boots in milliseconds off that cache and serves the first one's API URL. Either
produces a green ".NET" run that never reached .NET. `assertFrontendTalksToOrchUrl` now fails at
login when the frontend's token is rejected by `ORCH_URL`, and `NEXT_DIST_DIR` gives a side-by-side
dev frontend its own build directory.

**A shared failure is not a parity gap.** Diffing the two backends' failure sets is what makes a
dual-stack run interpretable. Three assertions were "fixed" in the wrong direction before this was
applied — the inventory test's badge assertion depends on which of two equally-valid tools the model
picked, and its heading assertion (`product_name || "Stock & Fulfillment"`) failed on a *different*
backend each time.

**MAF .NET has no `ctx.request_info` equivalent.** Pausing from inside an arbitrary executor is not
possible; it requires a dedicated `RequestPort` node. Python's two-call `execute()`/resume-via-
`responses={...}` maps to one long-lived `StreamingRun` cached across the pause — verified
empirically: break out of the event stream on `RequestInfoEvent` *without disposing the run*, then
`SendResponseAsync` and open a fresh `WatchStreamAsync()`. Directly relevant to item 1.

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
cd agents/dotnet && dotnet test ECommerceAgents.sln          # 585 passing

# Python
cd agents/python && uv run pytest                            # 833 passing

# Web
cd web && npx vitest run && npx tsc --noEmit && npx eslint .

# Docs site
uv run python scripts/build_docs_site.py --check             # 97 pages, 0 broken links
uv run python scripts/check_tutorial_readmes.py --check      # 34/34 chapters

# Dual-backend gate — the definition of done for .NET parity
scripts/e2e-both-stacks.sh -- e2e/orchestration-parity.spec.ts
```

**The exit criterion for .NET parity is `PARITY_GAPS.dotnet` being empty *while every test in the
spec asserts presence*.** It is empty today, and item 1 is the proof that "empty" is only as strong
as the tests in the file — closing resume means adding a test first, watching it fail, then fixing
it.
