# Remaining Work — Master Plan

> Repo-committed index, per the "Working Artifacts Location" rule in the project `CLAUDE.md`.
>
> This is the index. Where a past decision still constrains future work it is restated inline
> under [Constraints that still bite](#constraints-that-still-bite); the rest of the historical
> record lives in git, not here.

## Where this stands (2026-08-27, after plan 20)

**Plan 20 is complete.** Six waves: the .NET stack made to work at all, both broken orchestration
modes fixed, the paid measurements taken, the writing done, four features shipped, and the
documents reconciled against reality. Nothing from the adoption audit is open outside plan 13.

The claim that most needed to become true has: **`--dotnet` answers questions.** It did not when
plan 20 was written — 39 of 46 tools were registered under names the shared prompt corpus never
uses, and all twelve containers reported healthy the entire time. README's "two complete, working
backends" is now true of `main`.

The plans directory went from twenty files to nine. The deleted ones were the original
UI/teaching-asset plans (00–09), a fully-shipped hardening plan (11), and plan 19, which plan 20
wholly supersedes. Git keeps them; they are only out of the way.

| Plan | Subject | Status |
|---|---|---|
| [10](enhancements/10-oauth-authorization.md) | OAuth authorization | Phase A shipped and live-verified; later phases are real future work |
| [12](enhancements/12-mcp-2x-migration.md) | MCP 2.x migration | **blocked upstream** on `agent-framework-core` |
| [13](enhancements/13-azure-deployment-and-foundry.md) | Azure Container Apps + Microsoft Foundry | **the next objective** |
| [14](enhancements/14-pre-azure.md) | Adoption/conversion work before Azure | **done** — its last two items closed in plan 20 |
| [15](enhancements/15-build-and-release.md) | Build gating and release process | **shipped in v1.2.0** |
| [16](enhancements/16-dotnet-local-parity.md) | Make `--dotnet` actually work | **done** — plan 20 Wave 1 |
| [17](enhancements/17-tutorial-dotnet-coverage.md) | Tutorial .NET coverage (#20) | **done** — PR #70 |
| [18](enhancements/18-composer-ux.md) | Composer UX (#4) | **done** — plan 20 Wave 5 |
| [20](enhancements/20-close-out.md) | Close out everything, then Azure | **done** |

## What is actually left

Two items, and one of them is a decision rather than work.

- [ ] **The .NET eval suite.** 6 of 7 datasets are ported and the enabling work is merged
      (record-on-miss, and an `IEmbeddingProvider` seam without which product-discovery could not
      start in replay mode at all). What remains needs a real key: record fixtures for all six,
      generate .NET baselines — **re-record, never copy Python's**, since .NET has a different mode
      set and its absolute scores legitimately differ — and add a CI job gating on *baseline
      regression*, not an absolute floor, because the score is a property of the recording session
      (measured spread was 8 points across four identical recordings). `red_team` needs its own
      schema and evaluator and stays separate. Deferred out of plan 20 for budget, not difficulty.
- [ ] **The demo clip.** The spec is honest now and the artifact is not produced. Eight recording
      attempts found five separate defects that each let the run exit 0 while silently dropping the
      clip's approval and resume beats: the wrong mode for the gate, a regex that cannot span
      `" & "`, locators keyed off button text that changes after the first switch, four
      simultaneous data constraints, and a return that can only be initiated once per order. All
      five are fixed and the spec now **throws** rather than logging. Still open: with all of them
      fixed and a qualifying order present, the return turn does not reach the HITL gate and the run
      hits its 600s cap with no `hitl_requests` row written. The prompts themselves are fine — FTS
      ranks `Allbirds Wool Runners` at 0.67, so the post-FTS concern plan 20 raised is closed.

### Recorded decisions — not pending work

Listed so they are not rediscovered as gaps.

- **No .NET images are published.** Deliberate. The demo path stays Python-only; a visitor is there
  for the features, not the backend language. `--dotnet` remains a build-from-source path.
- **No Langfuse sink on .NET.** Deliberately skipped as an additive second exporter.
- **Anonymous storefront conversations are not persisted**, on either stack, so follow-ups there
  have no context at any tier. A product decision, not a bug.
- **The dual-backend Playwright gate is not in CI.** It needs both stacks running against a seeded
  database, which is minutes rather than seconds. Run it locally; it is the definition of done for
  parity. ADR 0005 records this as its own honest weakness.
- **`.NET` seeds and authenticates with Python images.** `seeder` and `auth-server` are shared
  rather than duplicated — recorded in the parity matrix so it is a stated choice, not an omission.

### Known debt

- [ ] **Frontend type/lint debt.** Two ESLint rules are downgraded to warnings in
      `web/eslint.config.mjs` so the gate stays meaningful; the suppressions come off by fixing the
      root causes, not by re-raising the rules. **Type the API layer** — replace `any` in
      `web/src/lib/api.ts` and its consumers with real interfaces (consider extending the Zod types
      in `web/src/lib/chat-schemas.ts`), then restore `@typescript-eslint/no-explicit-any` to
      `error`. **Auth/cart store refactor** — move `lib/auth-context.tsx` and `lib/cart-context.tsx`
      off mount-effect `setState` to a `useSyncExternalStore`-backed store so localStorage
      hydration is rule-clean, then restore `react-hooks/set-state-in-effect` to `error`. Also
      clear the remaining `next/no-img-element` warnings where `next/image` is practical.
- [ ] **The chat page hard-crashes on an unexpected API shape.** Recorded during plan 20's UI
      verification; not yet owned.
- [ ] **`embeddings=0` in the default local stack**, so `semantic_search` is lexical-only until
      `scripts/generate_embeddings` has run. Correct behaviour, surprising default.

### The pattern worth carrying forward

**The reported problem has been smaller than the actual one every single time**, and every time the
difference was found by running something rather than reading it. That table is now a published
page — [`docs/reported-vs-actual.md`](../../docs/reported-vs-actual.md) — with eight rows. It is
the most useful thing in this repo for anyone deciding how much to trust an issue title.

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
