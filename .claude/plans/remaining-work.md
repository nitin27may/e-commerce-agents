# PLAN — the one plan file

> Repo-committed working artifact, per the "Working Artifacts Location" rule in the project
> `CLAUDE.md`. **This is the only plan file.** It used to be twenty, then nine; finished plans are
> deleted, not archived, and live in git history.
>
> The published, reader-facing version of the forward work is
> [`docs/roadmap.md`](../../docs/roadmap.md). This file is the working detail behind it: blockers,
> acceptance criteria, and the constraints that have already cost us time.

**Status: 2026-11-24.** v1.4.0 is published; v1.5.0 is staged (the `[Unreleased]` section of `CHANGELOG.md` is written as its
release notes). Everything that needs no Azure subscription is built and merged, or is listed below with its reason. The Azure
deployment is the last step and has not run. The live block is the next section; the
[Execution plan, 2026-11-21](#execution-plan-any-provider-evals-azure-foundry-and-the-ai-gateway-2026-11-21) keeps the
Azure, Foundry and gateway milestones (M0, M4 to M8); Part II is the unsequenced backlog and Part III the constraints that
still bite. The LangGraph backend (LG1 to LG3) stays parked by maintainer decision.

## Live status and order, 2026-11-24

Issues and pull requests are the source of truth; this block maps them to the order of work.

**Closed, with evidence on `main`:** #85 (CI and supply-chain hygiene, minus the two items in #303), #86 (no-key demo and tour in CI),
#87 and #182 (README GIF and stills), #90 (Aspire AppHost), #92 (.NET approval-timeout worker, parity row 36), #93 (merchant proposals),
#94 (event autonomy), #96 (model scorecard on the providers that have a key), #98 (ADR 0007), #99 (frontend resilience and the static
preview), #298 (merchant-ops specialist, review triage, PO-value threshold), #301 (hosted preview), #302 and #307 (customer
confirmation, Python then .NET), #314 (`draft_seller_response` ownership) and #320 (MCP tools match the native ones).

**Open, and why**

| Issue | What remains | Why it is open |
|---|---|---|
| #89, #91, #100 (deploy chapter) | The first real Azure deployment; Foundry features that need an Azure credential (`--log-to-project`, the Foundry safety evaluator, hosted agents); the deployment chapter of the course | They close together after the keyless Azure run below passes. PR #263 (free-trial profile, `sanity.sh`, `verify-clean.sh`, Deploy to Azure button) is held until then |
| #100 (other chapters) | Durable workflows, Foundry evals and AG-UI chapters; chapters 33 to 36 are done | Foundry evals needs the Azure credential; the others are unwritten |
| #97 | The .NET live baselines (about 1.50 USD of tokens), argument-level tool-call scoring, and whatever else the issue checklist still shows unticked | Remainders, not blockers. `azure_content_safety` and `COST_BUDGET_MODE` default are recorded decisions (no code). Close it or split the rest when the checklist is reconciled |
| #299 | A real carrier feed | External system. Everything else in the issue (five event types, monthly budgets, the responder) is built, parity row 40 |
| #303 | Dependency review workflow | Needs the dependency graph and Dependabot alerts enabled in the repository settings (owner action). The MCP-versus-native gate is built |
| #300 | Anthropic and Gemini rows of the model scorecard | The repository has no key for either; Azure-hosted Claude answers on the Anthropic Messages route. Anthropic stays stub-tested by maintainer decision |
| #101 | MCP Apps cards, agentic-checkout adapter | Exploratory, parked |
| #318 | Narrated walkthrough video and social preview upload | Human task (a recording and a settings upload) |
| #103 | Tracker | Closes last |

Judge-calibration labels: the maintainer reviews `agents/python/evals/judge_calibration/labels.jsonl`, then
`uv run python -m evals.judge_calibration.run --rescore` (no model call) reports whether the judge decision holds.

**The Azure step (last, free trial, capped at 10 USD).** Order: `scripts/azure-up.sh --dry-run`, a `what-if` and a cost estimate,
deploy with the free-trial profile (public GHCR images, Entra bearer-token model access, no model key stored), `scripts/azure/sanity.sh`,
a measured cost from Cost Management, teardown, `scripts/azure/verify-clean.sh`. #89, #91 and the deployment chapter of #100 close together
when **four conditions** hold: (1) the stack comes up and the sanity script passes with bearer-token model access; (2) no model key
exists in the deployment; (3) teardown leaves nothing (`verify-clean.sh`); (4) spend stays under 10 USD with the measured figure
recorded. The Postgres admin password remains, because Entra authentication for the database needs code on both stacks; the closing
comment says so. The first-deploy checklist in `docs/azure-deployment.md` is the runbook. M4 below keeps the longer list of checks
to run once the step passes (deployed evals, `azure.yml` end to end).

**Pending, separate from the Azure step: the .NET 11 move.** The .NET stack targets `net10.0` (`agents/dotnet/Directory.Build.props`) and
the Aspire 17 AppHost requires .NET 10. Moving to .NET 11 is its own change (target framework, Dockerfile base images, the SDK in CI,
AppHost packages, then every .NET test and the two mixed-stack boots) and is not started. It needs no key and no Azure, and it
must not ride along with the Azure step.

**Rules for the order:** small and keyless first; keyed work uses the local Azure OpenAI key under a per-lane spend cap; an issue
closes only with evidence on `main`; Dependabot is monthly for images, compose files and Actions and weekly for the three package
ecosystems, and the Serilog.AspNetCore major is ignored until migrated. Each lane opens one pull request and the open count stays small.

## Execution plan: any-provider evals, Azure, Foundry and the AI gateway (2026-11-21)

Checked against the code on branch `claude/new-session-ytrmq5` (after `29a45dc`). **This block supersedes Part I
phases C, D and E, the red-team bullet in §12, and the CI half of B2.** Phase B's I5 to I7 are folded in below as
M4 and M8. Work it top to bottom: each milestone says what it needs, the steps in order, how to know it is done,
and what it costs. Steps marked **[machine]** need a human at a workstation with Docker, `az`/`azd` and keys;
everything else can be done in a cloud session.

### Goal

**Any developer runs the repo with any LLM provider and gets an eval result they can trust for that provider;
Azure developers can also use Container Apps, Foundry (evals, tracing, hosted agents) and an AI gateway.**

| Tier | Needs | Gets |
|---|---|---|
| 0 Keyless | Docker | the app on replay, replay regression, detector recall |
| 1 Any provider, local | a profile + key or a local model | the app on that model and a live eval of it: capability check, cost, comparison to the reference and to the developer's own last run |
| 2 Azure | a subscription | tier 1 plus Container Apps, Foundry project (evals, tracing), AI gateway, Foundry-hosted agents |

Rule: every eval feature lands provider-neutral first and gets its Azure variant second. A tier-2 change that alters
a tier-1 code path is a design error.

### Decisions (settled 2026-11-21 unless marked open)

| ID | Decision |
|---|---|
| D-1 | **Azure spend is approved as needed.** Nothing in this plan is parked for cost. Guard rails stay: the budget alert in `infra/modules/budget.bicep`, `azure-down.sh` after every test deploy, and no scheduled job that spends (rule in `evals.yml`). Record actual spend per milestone in the progress log. |
| D-3 | .NET stays at parity (unchanged). Python goes to Azure first because it is what Bicep deploys today; .NET follows in M8. |
| D-6 | **All six agents move to Foundry hosted agents.** The route is staged (M7): design note, one agent, the rest. If M5's reachability spike fails, the decision returns to the maintainer with the reason. |
| D-7 | **One fixed judge for every reference run**, any provider, pinned and recorded; the model is chosen in M3 by calibration. Overrides are allowed and make a run "not comparable". |
| D-8 | **A dated reference pricing file ships**; no price lives in code (done, see state table). |
| Providers | **Supported:** OpenAI, Azure OpenAI, Gemini (OpenAI-compatible), Anthropic, Ollama, any OpenAI-compatible server (OpenRouter, llama.cpp `llama-server`, vLLM, LM Studio, LiteLLM). **Reference baselines:** Azure gpt-5.1 and OpenAI direct; a third family when community results show demand. |
| D-9 *(open)* | MCP ingress for Foundry-hosted agents: external ingress defended by the existing OAuth 2.1 tokens, or a VNet-integrated Foundry setup. Decided at the end of M5's spike, with evidence. |

### M0. Prerequisites **[machine]** (half a day, once)

Nothing here is code. Do it before M3 and M4.

1. Azure: subscription with Owner (or Contributor + User Access Administrator) on it; `az login`, `azd auth login`.
2. Quota: gpt-5.1 (Global Standard) and `text-embedding-3-small` in the chosen `openAiLocation`; check with
   `az cognitiveservices usage list -l <region>`. Pick the region from where gpt-5.1 is offered.
3. GitHub: an Entra app registration with a federated credential for `repo:nitin27may/e-commerce-agents:environment:azure-deploy`;
   the `azure-deploy` environment with a required reviewer; repo variables `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
   `AZURE_SUBSCRIPTION_ID` (names from `azure.yml`).
4. Images: approve the pending v1.4.0 `release` run so the images publish; make the GHCR packages public
   (`azure-deployment.md` says why).
5. Keys for M3: Azure OpenAI (existing), OpenAI, and any of Gemini/Anthropic/OpenRouter to be tried; Ollama
   installed with one tool-capable model.
6. Tools: Docker, `uv`, `pnpm`, .NET 10 SDK, `azd`, Bicep CLI.

*Done when* `scripts/azure/prereqs.sh` passes and `scripts/azure-up.sh --env t1 --location <r> --dry-run` prints a plan.

### M4. First real Azure deployment (I5) **[machine]** (2 days plus wall clock)

*The Azure step in the live block supersedes this list for the first deployment (free-trial profile, key-free, 10 USD cap); the checks below are what to run once it passes.*

1. `scripts/azure-up.sh --env <e> --location <r> --dry-run`, then `azure.yml` mode `what-if`.
2. Real deploy: `scripts/azure-up.sh --env <e> --location <r>` (demo profile, `openAiAuth=key` first).
3. `scripts/azure/smoke.sh` (full run), then Playwright with `E2E_BASE_URL=$FRONTEND_URL`.
4. Measure and fix forward the five open behaviours listed above (readiness, proxy hops, gpt-5.1 API version, MCP
   host headers, managed OTel). Each fix is its own PR with the measured value in the description.
5. Flip `openAiAuth=identity`, redeploy, re-run smoke. Then make identity the default (release blocker for publishing).
6. **EV-7, deployed evals.** Run the http target against the deployed orchestrator, k=3, gpt-5.1; compare with the
   local gpt-5.1 reference. Differences are deployment effects (timeouts, ingress, proxy) and each gets explained.
7. `azure.yml` mode `deploy-and-test` end to end from GitHub, including the always-run teardown and the
   leftover-resource assertion.
8. Record a day's cost for the demo profile from Cost Management; replace the estimate in `azure-deployment.md`.
9. `scripts/azure-down.sh`; confirm nothing is left (Key Vault and AIServices purged).

*Done when* deploy, smoke, deployed eval and teardown all run from `azure.yml` with no manual step.

### M5. Foundry foundation (3 days)

1. **AZ-2, Foundry project in Bicep** behind `deployFoundryProject`: `Microsoft.CognitiveServices/accounts/projects`
   under the existing AIServices account, the App Insights connection, role assignments for the app identity.
   Offline tests in `tests/test_infra_bicep.py` first.
2. **AZ-3, Foundry as model provider.** Document that the `azure` provider pointed at the AIServices account *is*
   Foundry for OpenAI models; verify with a deployed turn. Add `LLM_PROVIDER=foundry` only if a non-OpenAI catalogue
   model or the project-scoped client is actually needed, and note which in the PR.
3. **Tracing in Foundry.** Confirm Container Apps traces appear in the project's tracing view through the connected
   App Insights; note any GenAI semantic-convention fields missing on either stack.
4. **EV-8, Foundry evaluators.** Optional extra `foundry-evals` (`azure-ai-evaluation`): map run records onto the
   Foundry evaluators (groundedness, intent resolution, tool-call accuracy, task adherence, indirect attack); run
   locally; optional sink logs the run to the project. The core eval path imports no Azure package (a test enforces it).
5. **AZ-1, reachability spike [machine], half a day, decides D-6's route and D-9.** From a minimal Foundry-hosted
   agent: call a Container Apps specialist over A2A, call an MCP server with an OAuth token, reach Postgres. Today
   the MCP servers have internal ingress, so expect that call to fail; test both D-9 options. Also record which
   hosted-agent packages exist for Python and .NET at the pinned MAF versions, and whether a hosted agent can serve
   A2A itself or needs an adapter. Write the findings into this plan.

*Done when* a deployed run's traces and a Foundry evaluation of the same records are visible in one project, and the
spike's findings are written down.

### M6. AI gateway (3 days)

1. **AZ-4, gateway headers (keyless; can land any time after M1).** `get_chat_client` and the .NET factory send
   `x-agent-name`, `x-run-id`, `x-eval-run-id` on every model call; replay keys unaffected (`fixture_drift` 0, zero
   misses).
2. **Verify before writing [machine]:** which API Management tiers support the token-limit and token-metric policies
   for Azure OpenAI, provisioning time and price. Record in this plan.
3. **AZ-5, APIM module** behind `deployGateway`: API in front of the AIServices account, managed-identity auth to the
   backend, per-agent token limits, token metrics to App Insights. Apps point `AZURE_OPENAI_ENDPOINT` at the gateway
   when the flag is on: no provider change.
4. **Per-app identities** in the reference profile, so per-agent attribution is trusted rather than header-asserted.
5. **Reconcile:** gateway token metrics vs `usage_logs` tokens vs Cost Management for one day; explain any gap.
6. **EV-7 gateway variant:** deployed eval with the gateway on; record latency overhead and 429 behaviour.

*Done when* one deployed day shows per-agent tokens from the gateway matching `usage_logs` within an explained margin.

### M7. Foundry hosted agents, all six (D-6) (1.5 to 2 weeks)

1. **Design note first** (`docs/adr/` new ADR), from M5's findings: how the orchestrator's checkpoints and HITL
   resume (`PostgresCheckpointStorage`, `hitl_requests`), the outbox worker, identity propagation
   (`X-User-Context`, ContextVars) and the agent registry work when Foundry runs the agents; what stays on Container
   Apps (frontend, worker, MCP, auth server, database); D-9's choice.
2. **Packaging:** one specialist (`review-sentiment`: fewest tools, no money moves, no HITL) packaged for Foundry,
   registered through `AGENT_REGISTRY`, same prompt YAML and database.
3. **Prove it:** a live turn routed from the Container Apps orchestrator to the Foundry-hosted specialist; the
   frontend unchanged; deployed eval for that agent within its reference spread.
4. **The other four specialists**, one PR each, each with its deployed eval.
5. **The orchestrator last** (it owns modes, checkpoints, HITL): behind the design note; the frontend talks to it
   unchanged; approvals and resume pass the smoke test.
6. **EV-7 Foundry variant:** full deployed eval against the all-Foundry topology.

*Done when* all six run on Foundry, the smoke test and deployed eval pass, and paused returns resume.

### M8. Reference profile, .NET on Azure, injection comparison, publish (2 weeks)

1. **I6, reference profile:** VNet with private endpoints for Postgres, Key Vault and AIServices; Azure Managed
   Redis; ACR; `A2A_IDENTITY=token` with a distinct key; identity auth default.
2. **I7, build from source** with `remoteBuild` into ACR.
3. **.NET on Azure:** publish .NET images in `build-images.yml`; a `stack=dotnet` Bicep parameter; smoke and
   deployed eval on the .NET stack; parity-matrix row updated.
4. **EV-9, Prompt Shields:** a second detector behind the guardrail seam; `guardrail_confusion.py` over the M3
   adversarial set for both detectors; precision and recall published.
5. **AZ-7, publish:** `docs/azure-deployment.md` with measured costs for each topology (compose, Container Apps,
   + gateway, + Foundry project, Foundry-hosted), the "which host, when" guide, and one article per milestone that ran.

### Order and parallelism

```
M1 ──► M2 ──────────────► (profiles used by M3)
 │
 ├──► M3 [machine, keys]
 │
M0 ──► M4 [machine] ──► M5 ──► M7
                    └──► M6 (AZ-4 may land with M1)
M4 + M7 ──► M8
```

M1 and M2 need no key and no Azure, so they start now. M0 can run in parallel at any time. M3 needs M1's runner and
M2's profiles. M4 needs M0 only; it does not wait for M3. M5 needs M4's deployment; M6 needs M4; M7 needs M5's spike;
M8 needs M4, and its .NET and publish steps need M7.

### Unknowns to settle at the machine (write the answer here when found)

- Hosted-agent packages for MAF Python and .NET at the pinned versions, and whether a hosted agent can serve A2A (M5.5).
- Foundry-hosted agent egress: public endpoints only, or VNet integration (M5.5, decides D-9).
- APIM tiers that support the Azure OpenAI token policies; provisioning time; price (M6.2).
- gpt-5.1 regional availability and quota for the chosen region (M0.2).
- The five Phase B behaviours (M4.4).

### Rejected, so it is not proposed again

- A second plan file under `docs/` (CLAUDE.md: one plan).
- "Define four seams" as new work: the model client, guard and telemetry seams exist; the eval seam is M1.
- Nightly or scheduled live or Foundry evals (no automatic trigger may spend a key).
- A monthly cost estimate in the README: measured numbers in `azure-deployment.md` instead (M4.8, M8.5).
- `LLM_PROVIDER=foundry` as a duplicate of `azure` for OpenAI models.
- A Prompt Shields comparison on the current 8 red-team cases: noise, so it waits for M3's adversarial set.
- A fallback price for unknown models.

---

# Part II — Unsequenced backlog

Real work with no position claimed in the Execution plan.

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
- [ ] **A red-team evaluator** (now M3 step 1 in the 2026-11-21 execution plan). `red_team.json` is scored by keyword matching, which means very
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
- [x] **Audit matrix** covering which routes accept which token type: `docs/auth-route-matrix.md` and `tests/test_auth_route_matrix.py`.

## 16. Blocked, waiting on upstream

- [x] **MCP 2.x migration** — done: core 1.23+ required `mcp>=2.2`; the notes are in `CLAUDE.md` (MCP 2.x notes).

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

# History

Parts of this file that were plans or build logs and are finished or superseded. They were deleted from the working copy so that
"what is left?" is not a research question; the full text is in git (`git show 4ca4601:.claude/plans/remaining-work.md`, or
`git log -p -- .claude/plans/remaining-work.md`). What each one decided, so nothing is lost that still governs work:

- **Part I, the 2026-09 sequence (phases A to E, B2, LG1 to LG3).** A (Azure unblock: frontend proxy, agent registry) is done. B
  (Container Apps, Bicep, teardown, managed identity), C (Foundry as model provider), D (Foundry Hosted Agents) and E (publish the
  Azure reference) were replaced by milestones M0 to M8 of the Execution plan; B2 (the parity gate) is built (`stack-parity.yml`,
  parity row 53). LG1 to LG3 are parked. The three standing decisions it asked for: **D-1** Azure spend is approved but bounded and
  the deployment is last; **D-2** the objective is a worked reference; **D-3** .NET stays at full parity (recorded in
  `.claude/HANDOVER.md`, section 4).
- **The 2026-11-20 to 2026-11-23 consolidated plan and its NA-1 to NA-15 items** (live evals, datasets, judge calibration, provider
  checks, the manual live job, baselines, web fixes, dependency hygiene, the presentation pass, Codespaces, the docs audit) are built
  and merged; what they shipped is in the `CHANGELOG.md` `[Unreleased]` section, and what is left is in the live block above.
- **Part IV, the November 2026 audit build log and workstreams A to E (#80 to #104).** Built wave by wave and recorded in git; the
  text was deleted from this file once every item merged or moved to a numbered issue. The corrections the build log found are in `CLAUDE.md` (Architecture, A2A 1.0,
  Approvals, Model pricing, Testing & Evals) and in the docs they describe.

