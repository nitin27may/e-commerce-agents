# Remaining Work — Master Plan

> Repo-committed index, per the "Working Artifacts Location" rule in the project `CLAUDE.md`.
>
> This folds the long-running teaching-asset plan down to **what is actually left**. The
> historical record of Phases 0–14 — the audit that started it, and the reasoning behind
> decisions that are now shipped — is not repeated here; where a past decision still constrains
> future work, it is restated inline under [Constraints that still bite](#constraints-that-still-bite).

## Where this stands (2026-08-21, after v1.1.0)

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

### 2. Tutorial .NET coverage (#20)

The CI gate is in and immediately found chapter 08 completely broken. What it now protects is
incomplete:

- [ ] Tests for **ch12–19** (7 chapters; ch16 and ch20 stay documented stubs — magentic is a
      genuine SDK blocker, not a repo gap)
- [ ] `dotnet/` for **ch22–32** (11 chapters) — the largest single piece of work left in the repo

### 3. Composer UX (#4)

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
- [ ] **Search & retrieval** — `search_products` is still `ILIKE` with no lexical index; full-text
      search, hybrid retrieval and a typed filter DSL are all planned
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
