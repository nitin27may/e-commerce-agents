# Remaining Work — Master Plan

> Repo-committed index, per the "Working Artifacts Location" rule in the project `CLAUDE.md`.
>
> This folds the long-running teaching-asset plan down to **what is actually left**. The
> historical record of Phases 0–14 — the audit that started it, and the reasoning behind
> decisions that are now shipped — is not repeated here; where a past decision still constrains
> future work, it is restated inline under [Constraints that still bite](#constraints-that-still-bite).

## Where this stands (2026-08-21)

Everything through **Phase 14 (.NET parity)** and **Phase 12 (documentation site)** is merged to
`main`. Two things are worth stating precisely, because both were overstated at some point and
the correction is the useful part:

**The documentation site is live** at <https://nitinksingh.com/e-commerce-agents/> — 84 pages, 71
Mermaid diagrams, generated from the repo by `scripts/build_docs_site.py` and deployed by
`.github/workflows/jekyll-gh-pages.yml`. Verified against the deployed site, not just the build:
sibling-chapter links and GitHub source links resolve, the Mermaid 10.9.1 ESM loader is injected
against `.language-mermaid` blocks, all seven nav sections render, the search index builds, and no
front matter leaked into rendered output.

**.NET is at parity across the surface the gate covers — which is not the whole surface.**
`web/e2e/parity-gaps.ts` is empty and all seven parity tests pass against both backends, and the
full 130-test suite run against both found no .NET capability gap (Python 91/41, .NET 90/39, 38
failures shared and backend-independent). But the gate has a known hole, found while rebuilding the
matrix rather than by the gate itself — see [1. Workflow resume](#1-workflow-resume-on-net) below.
The defensible claim is *"parity minus workflow resume, with a documented backlog"*, not *"at
parity"*.

## Remaining work, in order

### 1. Workflow resume on .NET

**The top item, because it is a silent failure in shipped code.**

`POST /api/orchestration/{run_id}/resume` exists in Python (`orchestrator/routes/orchestration.py:174`)
and does not exist on .NET. So on the .NET backend `/runs` lists checkpoints and shows the
pending-approval badge, and clicking **Approve/Reject 404s**. A paused `workflow:return-replace`
run cannot be resumed.

`OrchestrationRoutes.cs:22` explains the omission as *"it drives `ReturnAndReplaceWorkflow`, which
has no runnable tool implementation on .NET yet."* **That reason is stale** —
`Shared/Workflows/ReturnReplaceTools.cs` exists and `ReturnReplaceMode` is registered. What is
missing is a `Resume` method on the mode and the route itself.

The gate does not catch this: `orchestration-parity.spec.ts`'s checkpoint test deliberately asserts
only that the endpoint is *served*, to avoid coupling itself to the mode registry. That decoupling
was reasonable and it left resume untested on both stacks.

- [ ] `ReturnReplaceMode.Resume(runId, approved)` — rebuild the workflow from `checkpoint_id` +
      `SendResponseAsync`, mirroring Python's fresh-graph-from-checkpoint contract. The .NET
      workflow already caches a long-lived `StreamingRun` across the pause (see
      [Constraints](#constraints-that-still-bite)), so this is wiring, not a port.
- [ ] `POST /api/orchestration/{run_id}/resume` in `OrchestrationRoutes.cs`, and delete the stale
      remark at `:22`.
- [ ] Extend the parity spec to **click Approve and assert the run resumes**, then confirm it fails
      against .NET before the fix and passes after. A test that only passes after is not evidence.

### 2. Rebuild `docs/parity-matrix.md` (#11)

Urgent because `README.md` now points readers at this file as the source of truth, and it is wrong
in ways a reader can check in minutes. Every correction below is verified against the tree, not
inferred:

| Row | Says | Actually |
|---|---|---|
| 8 | "Neither stack's version of either workflow is wired to a live route (both are test-only)" | Both stacks have live workflow modes; .NET registers `PrePurchaseMode` and `ReturnReplaceMode` in `ModeRegistry` |
| 10 | "No `Shared/Tools/` directory … none shared", status `Partial`, P3 | `Shared/Tools/` has six modules (`ProductLookupTools`, `UserProfileTools`, `StockLookupTools`, `PriceHistoryTools`, `LoyaltyTools`, `ReturnTools`) |
| 13 | Cites `orchestrator/modes/magentic_mode.py` | **Exists in neither stack.** Python's `orchestrator/modes/` holds `base`, `tool_router`, `handoff_mode`, `workflow_mode`, `group_chat_mode` |
| 16 | "All 32 chapters have Python tests" | 34 chapters |

Rows the matrix has **no entry for at all**, each of which now has a real answer:

- [ ] **Grounding** — `Shared/Grounding/` (`ClaimExtractor`, `GroundingVerifier`), `GROUNDING_MODE`
      `off`/`observe`/`annotate`. Python's `enforce` is refused at startup. Ledger tier absent:
      Python resolves prose figures against per-request tool results recorded *inside the
      specialist processes*, so an orchestrator-side port needs those facts carried over A2A.
- [ ] **Idempotency** — Python has `shared/idempotency.py` + `idempotency_keys`. **.NET has none**
      (verified: zero matches for `Idempot` under `agents/dotnet/src/`). The database constraint is
      currently the only thing between a double click and two refunds on .NET.
- [ ] **Rate limiting** — `Shared/RateLimiting/SlidingWindowRateLimiter.cs`, Python's Lua script
      ported byte-for-byte, applied to both chat routes.
- [ ] **Cost** — `Shared/Cost/CostEstimator.cs` plus a budget ceiling in `SpecialistPipeline`.
- [ ] **Telemetry depth** — 214 lines on .NET against Python's 441. Missing: Langfuse sink, log
      bridge, httpx/asyncpg instrumentation.
- [ ] **Orchestration modes and routes** — .NET has 3 modes to Python's 5, and 3 of the 4
      `/api/orchestration/*` routes (see item 1).
- [ ] **MCP client consumption** — .NET has an MCP *server* but no specialist wires an MCP
      *client*; there is no analogue of Python's `MCPStreamableHTTPTool` path.
- [ ] **Session/checkpoint wiring** — registered in `Orchestrator/Program.cs`, so
      `MAF_CHECKPOINT_BACKEND` is no longer a silent no-op.

`agents/dotnet/README.md` is stale in the same direction ("no shared tool library", "418 tests" —
now 500 passing) and must be fixed in the same change, or the contradiction just moves.

### 3. .NET evals harness (#19)

Harness, scorers, baselines, datasets, replay fixtures. Deliberately last of the .NET work: running
an eval suite against an agent that was missing 22 tools would only have measured a gap already
written down. That reason has now expired — the tools exist — so this is simply the largest
remaining piece.

The Python harness's own lesson is the thing to copy: `evals/evaluator.py` originally hand-rolled a
chat-completions loop and called raw undecorated functions, bypassing every guardrail it claimed to
measure. `evals/harness.py::ProductionRunner` drives the real orchestration modes instead. A .NET
harness that does not go through `ModeRegistry` would repeat the exact mistake.

### 4. Test-suite cleanup — 15 pre-existing failures

Backend-independent, none caused by recent work, all found by running the full suite against both
stacks. With these gone the suite is a real gate rather than 15 permanent reds:

- [ ] `ui-features.spec.ts` asserts `img[src*="picsum.photos"]`; seed data moved to
      `images.unsplash.com` long ago (`scripts/seed.py:84`). **Related trap:** `next.config.ts`
      still whitelists only `picsum.photos` in `remotePatterns`. Harmless today because product
      images use plain `<img>`, but it breaks the moment anyone switches to `next/image`.
- [ ] Five `chat-all-users` tests assert response length > 20 and receive 6.
- [ ] `ui-features` admin marketplace TypeError check.
- [ ] `all-roles` auth/navigation `waitForURL` timeouts.

### 5. Attached to the umbrella, smaller

- [ ] Handoff and group-chat modes on .NET (#19)
- [ ] Memory **write** tools on .NET — `ProfileRoutes` serves `GET`/`DELETE /api/user/memories`, but
      no agent-callable tool writes one, so a .NET agent can read memories the Python stack wrote
      and never add one. The Profile "AI Memory" card's instruction to "chat to build your profile"
      is untrue on .NET.
- [ ] Telemetry depth (#19)
- [ ] Tutorial .NET coverage for ch22–32 (#20) — eleven Python-only chapters
- [ ] **Not a parity item:** magentic. It exists in neither stack; the matrix asserting otherwise is
      simply wrong.

### 6. Open issues not owned by any of the above

**Corrected 2026-08-21:** an earlier version of this list said `#5`, `#6`, `#7`, `#8`, `#10` and
`#22` were open. **All six are closed.** Genuinely open: `#4` (composer UX), `#9` (follow-up
questions lose context), `#11` (parity matrix — item 2 above), `#18` (closeable with item 2, since
`Shared/Tools/` now exists), `#19`, `#20`, `#33` (umbrella).

### 7. Bugs and findings logged while working

Each was found by running something rather than reading it. Open unless marked.

- [ ] **Checkout is not idempotent on .NET.** Three of Python's four `@idempotent` sites are ported
      (`InitiateReturn`, `ProcessRefund`, the HITL approval executor); checkout is not. Python
      guards it at `orchestrator/routes/legacy.py:2151`; the .NET twin is
      `Orchestrator/Routes/CheckoutRoutes.cs`. A double-submitted checkout still places two orders.
- [ ] **`StreamAsync_EmitsAGroundingFrame` failed once and has not reproduced.** A
      `NullReferenceException` in one full-solution run, then green alone, in its class, in a
      three-class run and in two subsequent full runs. Nothing was changed to "fix" it, so it is
      unexplained rather than resolved — possibly contention now that a concurrent test shares
      Postgres across projects. Do not treat the green runs as evidence it is gone.
- [ ] **`test_record_against_real_provider_then_replay_offline` is order-dependent.** Passes alone,
      fails inside a larger run, independent of any recent change. `_available_provider()` is
      evaluated once in `@pytest.mark.skipif` at collection time and again in the body, and the
      environment has changed by then, so `assert provider is not None` fires. The fix is
      `pytest.skip()` in the body rather than an assert. Found by another session.
- [ ] **`docs/concepts/**` `file:line` pointers have drifted.** `01-what-is-an-agent.md` cites
      `orchestrator/agent.py:147-162` for `create_orchestrator_agent`, which is at 156. Some
      pointers are still correct, which is worse than all of them being wrong — a reader cannot tell
      which to trust. Symbols or permalinked SHAs instead of bare line numbers. Owned by another
      session, spans a directory, announced before it starts.
- [ ] **Twelve stale feature branches.** Not to be pruned unilaterally.
- [x] **The documented free-tier path pointed at a retired service.** GitHub Models was retired at
      the end of July 2026 and its endpoint 404s. It was written into `docs/quick-start.md` and
      `README.md` as *the* no-API-key option and shipped to the live docs site — the endpoint was
      copied out of `tutorials/00-setup` without being curled once. Fixed in #46, along with a
      second finding worth keeping: `qwen3.5:9b` is the *smallest* of the three suggested local
      models and the **slowest by 6.5x**, because it is a reasoning model whose thinking trace eats
      the output budget — under a 1024-token cap it returns `finish_reason: length` with empty
      content. Smaller is not faster.
- [x] **`WorkflowState` could not survive JSON.** Get-only `List<T>` on a type with a parameterized
      constructor: System.Text.Json returns the object with those collections **empty and no error**,
      so a resumed workflow would have forgotten every step it ran and re-opened a return it had
      already opened. Fixed with settable collections; `JsonObjectCreationHandling.Populate` is the
      tidier fix and throws `NotSupportedException` for exactly that constructor reason.
- [x] **Dapper `dynamic` silently makes downstream calls dynamic.** In the resume route, inferring
      `sessionId` from `claimed.payload` made `mode.ResumeAsync` dynamically dispatched; its result
      came back as `object` and failed at runtime on `.AgentsInvolved`. It compiled cleanly and
      500'd. Type anything derived from a Dapper row explicitly.

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
