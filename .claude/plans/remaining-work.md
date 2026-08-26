# Remaining Work — Master Plan

> Repo-committed index, per the "Working Artifacts Location" rule in the project `CLAUDE.md`.
>
> This folds the long-running teaching-asset plan down to **what is actually left**. The
> historical record of Phases 0–14 — the audit that started it, and the reasoning behind
> decisions that are now shipped — is not repeated here; where a past decision still constrains
> future work, it is restated inline under [Constraints that still bite](#constraints-that-still-bite).

## Where this stands (2026-08-26, after v1.2.0)

v1.2.0 shipped: hybrid FTS+vector search, the orchestration-mode fix, prebuilt images with a
one-command demo, and a release pipeline that gates publishing on the test suite. Ten images are
published for `linux/amd64` and `linux/arm64`; `./scripts/dev.sh --demo` reaches a working stack in
about a minute.

Plans **13–18** now carry most of the detail; this file is the index.

| Plan | Subject | Status |
|---|---|---|
| [13](enhancements/13-azure-deployment-and-foundry.md) | Azure Container Apps + Microsoft Foundry | proposed |
| [14](enhancements/14-pre-azure.md) | Adoption/conversion work before Azure | 6 of 8 done |
| [15](enhancements/15-build-and-release.md) | Build gating and release process | **shipped in v1.2.0** |
| [16](enhancements/16-dotnet-local-parity.md) | Make `--dotnet` actually work | proposed, **P0 inside** — PR #69 is the plan only, no code |
| [17](enhancements/17-tutorial-dotnet-coverage.md) | Tutorial .NET coverage (#20) | **done** — PR #70, unmerged |
| [18](enhancements/18-composer-ux.md) | Composer UX (#4) | proposed |

### Two different ".NET" workstreams — do not conflate them

- **Tutorial .NET (plan 17, #20) is done.** Every chapter that ships code ships both languages,
  both gated in CI: 334 .NET tests across 31 projects. This says nothing about the app.
- **Production .NET (plan 16) is broken.** `agents/dotnet` builds and starts and cannot answer a
  single question. Finishing the tutorials did not touch it.

### The one thing to read first

**Plan 16 F1: the .NET orchestrator cannot reach any specialist.** `CallSpecialistAgent` is rejected
with "arguments dictionary is missing a value for the required parameter 'agentName'", so
`agents_involved` is `['orchestrator']` on every turn and no specialist is ever invoked. The stack
builds, all 12 containers report healthy, the UI serves, login works — and every question fails.

While that stands, "two complete, working backends" is not true of `main`. Either fix it before the
next release repeating the claim, or qualify the claim.

### Found by running v1.2.0, not yet planned

Measured against a live stack during the v1.2.0 work. None of these has an owner yet.

- [ ] **`handoff` mode is an outlier on both cost and latency.** Measured 100–200 s per turn and
      19,000–25,000 characters of response, against ~11 s and ~1,000 characters for `tool` on the
      same prompt. An order of magnitude beyond every other mode; it looks like intermediate content
      is being flushed into the reply. Worth understanding before the mode benchmark is published,
      since it will dominate the table.
- [ ] **`workflow:pre-purchase` discards most of its own work.** Four executors run — `reviews`,
      `stock`, `price_history`, `shipping` — and the reply is 48 characters:
      `"Stock: 348 units available | Price trend: stable"`. The fan-out is real; the synthesis
      throws away reviews and shipping entirely.
- [ ] **The orchestration-mode benchmark has a harness but no published result.**
      `evals/benchmark_modes.py` ships in v1.2.0 and is verified working. The first full run
      measured a broken build through a tripped rate limiter and was discarded rather than
      published. Needs a re-run against v1.2.0 images (~60 calls, ~$1, ~25 min with pacing) and a
      `docs/orchestration-benchmark.md` page. **This is the highest value-per-day item in the
      [adoption audit](audit-2026-08-25-adoption-and-azure.md)** — an LLM answering "which
      orchestration pattern should I use?" has nothing to cite today.
- [ ] **The demo clip has a recording spec but no recording.**
      `web/e2e/demo-recording.spec.ts` ships and typechecks. Nothing has been recorded, so the
      README still opens with a static PNG. Re-check the prompts against the post-FTS catalogue
      before recording.
- [ ] **No .NET images are published** — accepted, deliberately. The demo path stays Python-only;
      a visitor is there for the features, not the backend language. `--dotnet` remains a
      build-from-source path. Recorded here so it is not rediscovered as a gap.

### Still open from the adoption audit

- [ ] **Chapter 21, Capstone Tour** — two `.gitkeep` files. The bridge from 34 tutorials to the
      running application, and the one missing rung on the ladder.
- [ ] **`docs/adr/`** — five decisions already argued in prose (A2A over direct calls, no
      text-to-SQL, YAML prompt composition, MAF-native execution, dual-stack parity) and recorded
      nowhere a reader would look.
- [ ] **Promote the "reported vs actual" table** below onto the docs site. It is the most credible
      artifact in the repository and it lives in `.claude/`.

---

## Where this stood (2026-08-21, after v1.1.0)

*Kept for the record; superseded by the section above.*

Everything through **Phase 14 (.NET parity)** and **Phase 12 (documentation site)** is merged, plus
a further round covering conversation context, telemetry, tutorial CI, SEO, and three defects that
turned out to be larger than their issue titles.

**The documentation site is live** at <https://nitinksingh.com/e-commerce-agents/> — 85 pages, 71
Mermaid diagrams, generated from the repo by `scripts/build_docs_site.py`. Every page now carries
its own meta description, keywords and `TechArticle` JSON-LD, and every diagram carries an
accessible title.

**Workflow resume works on both stacks**, so the pause → badge → Approve → resume loop is real on
.NET as well as Python, and `web/e2e/parity-gaps.ts` is empty.

### The pattern worth carrying forward

Five separate times in the most recent round, **the reported problem was smaller than the actual
one**, and in every case the difference was found by running something rather than reading it:

| Filed as | Actually |
|---|---|
| "follow-ups *occasionally* lose context" (#9) | Deterministic: specialists received **zero** history on every browser turn |
| "telemetry depth: no metrics provider" (#19) | Metrics were never the gap; .NET spans were **invisible in Aspire's GenAI view** |
| ".NET tests only for ch01-11" (#20) | **No CI job built any** of the 31 tutorial projects; ch08 was fully broken |
| "`semantic_search` dead under replay" (#52) | Also a **production** IVFFlat bug returning unrelated products |
| "`optimize_cart` divides by zero" (#51) | **No promotion had ever applied correctly**, in any environment |

Two of those were found only because a gate was switched on. That is the argument for finishing the
gates below before the content work.

## Remaining work, in order

### 1. Finish the .NET eval suite

**Groundwork is merged; the recording run is not.** `ECommerceAgents.Evals` now has 6 of 7 datasets,
record-on-miss (`RECORD=true` + `REPLAY_RECORD_PROVIDER`), and an `IEmbeddingProvider` seam without
which product-discovery could not even start in replay mode.

- [ ] Record fixtures for all 6 datasets against real Azure OpenAI
- [ ] Generate .NET baselines — **re-record, never copy Python's**; .NET has a different mode set,
      so its absolute scores legitimately differ
- [ ] Add the CI job, gating on baseline regression rather than an absolute floor (the score is a
      property of the recording session — measured spread on one suite was 8 points across four
      identical recordings)
- [ ] `red_team` / safety: needs its own schema and evaluator, so it is a separate piece

### 2. Tutorial .NET coverage (#20) — see [plan 17](enhancements/17-tutorial-dotnet-coverage.md)

The CI gate is in and immediately found chapter 08 completely broken. What it now protects is
incomplete:

- [ ] Tests for **ch12–19** (7 chapters; ch16 and ch20 stay documented stubs — magentic is a
      genuine SDK blocker, not a repo gap)
- [ ] `dotnet/` for **ch22–32** (11 chapters) — the largest single piece of work left in the repo

### 3. Composer UX (#4) — see [plan 18](enhancements/18-composer-ux.md)

- [ ] Collapse the always-visible `AGENT_MODES` chip row behind a control
- [ ] Derive suggested prompts from the reply on screen instead of a static
      `DEMO_SCENARIOS.slice(0, 4)`

**Frontend-only by constraint**: no new endpoint, no changed request shape, no fence-contract
change. Both backends must be byte-identically unaffected, verified by the dual-backend gate.

### 4. Smaller items, unscheduled

- [ ] **Cost counter instrument** — dollar estimation and a budget ceiling both ship; what is
      missing is a counter this repo owns, so an OTLP sink can alert on spend anomalies
- [ ] **In-chat approval card** — the resume loop is real on both stacks but the control renders
      only on `/runs`, not inside the chat thread
- [ ] **Streaming tool calls** — text deltas stream; raw tool-result payloads do not yet travel as
      their own SSE frames
- [x] **Search & retrieval** — shipped in v1.2.0: `search_products` is a weighted `tsvector` behind
      a GIN index, and `semantic_search` fuses lexical and vector arms via RRF. Only the typed
      filter DSL remains planned
- [ ] **Langfuse sink on .NET** — deliberately skipped as an additive second exporter
- [ ] **Anonymous multi-turn memory** — neither stack persists anonymous storefront conversations,
      so follow-ups there have no context at any tier. A product decision, not a bug, but currently
      undocumented as such

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
cd agents/dotnet && dotnet test ECommerceAgents.sln          # 500 passing

# Python
cd agents/python && uv run pytest && uv run ruff check .     # ~776 passing

# Web
cd web && npx vitest run && npx tsc --noEmit && npx eslint .

# Docs site
uv run python scripts/build_docs_site.py --check             # 84 pages, 0 broken links
uv run python scripts/check_tutorial_readmes.py --check      # 34/34 chapters

# Dual-backend gate — the definition of done for .NET parity
scripts/e2e-both-stacks.sh -- e2e/orchestration-parity.spec.ts
```

**The exit criterion for .NET parity is `PARITY_GAPS.dotnet` being empty *while every test in the
spec asserts presence*.** It is empty today, and item 1 is the proof that "empty" is only as strong
as the tests in the file — closing resume means adding a test first, watching it fail, then fixing
it.
