# Plan 17 — Tutorial .NET coverage

**Status:** proposed · **Date:** 2026-08-26 · **Issue:** [#20](https://github.com/nitin27may/e-commerce-agents/issues/20)
**Effort:** 3–5 weeks part-time · **Target:** incremental, no single release

The largest single piece of work left in the repo, and the one with the worst effort-to-adoption
ratio. That is not an argument against doing it — it is an argument for doing it in a shape that
delivers value continuously rather than in one multi-week block that blocks everything else.

---

## Ground truth

Derived from what is on disk today, not from the issue text (which predates several chapters):

| Category | Count | Chapters |
|---|---|---|
| **.NET code, no test project** | 9 | 12, 13, 14, 15, 16, 17, 18, 19, 20 |
| **No .NET at all** | 12 | 20b, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32 |
| No .NET *by design* | 2 | 00 (guide only), 21 (capstone, not started) |
| Python code, no tests | 1 | 22 |

Two corrections to the issue and to `remaining-work.md`, which both say "ch12–19 (7 chapters)" and
"ch22–32 (11 chapters)":

- It is **9** chapters lacking .NET tests, not 7 — ch20 belongs in the list, and 20b is a separate
  case (it has `dotnet/README.md` and no code, so it counts as "not ported", not "tests pending").
- It is **12** chapters with no .NET, not 11 — 20b was missed.

Chapters **16 and 20** ship .NET code as documented stubs and are deliberately excluded from the
test work: magentic is a genuine SDK gap, per `remaining-work.md`. They still need to *build*, which
CI already enforces.

**Also worth fixing while here:** ch22 has `python/main.py` and no tests — the only Python chapter
in that state. It is a one-chapter gap that the generated status table now makes visible.

---

## What already exists

- **CI builds every tutorial `.csproj`** and tests only chapters that have a test project
  (`tutorials.yml`, `tutorials-dotnet-build` / `tutorials-dotnet-test`). Adding a test project is
  therefore enough to opt a chapter in — no workflow change needed.
- **A working template**, `tutorials/11-agents-in-workflows/dotnet/`:
  ```
  Program.cs
  AgentsInWorkflows.csproj
  tests/AgentsInWorkflows.Tests.csproj
  tests/AgentsInWorkflowsTests.cs
  ```
- **`scripts/check_tutorial_readmes.py`** enforces the chapter contract in CI.
- **The status table is generated** from disk (v1.2.0), so progress becomes visible automatically
  with no bookkeeping.

That last point matters for sequencing: every chapter finished shows up immediately, so this can be
shipped one chapter per PR without the index drifting.

---

## Approach

**One chapter per pull request.** Not batched. The reasons are practical rather than stylistic:

- A 12-chapter PR is unreviewable, and the failure mode is a rubber stamp.
- CI opts each chapter in the moment its test project lands, so value is continuous.
- It can be interleaved with other work indefinitely without a long-lived branch rotting.

**Do the 9 test-only chapters first.** The code already exists and builds; only the test project is
missing. That is the cheaper half and it closes the more embarrassing gap — a chapter that claims
.NET parity and has no test is worse than one honestly marked "not ported".

### Phase A — tests for existing .NET code (7 chapters)

12, 13, 14, 15, 17, 18, 19. Excludes 16 and 20 (documented stubs).

Per chapter: add `dotnet/tests/<Name>.Tests.csproj` and one test class mirroring the Python test's
assertions. **Mirror the Python test's intent, not its structure** — the point is that both
languages demonstrate the same concept working, not that the test files look alike.

### Phase B — port the 12 unported chapters

20b, 22–32, in ascending order. Each needs `dotnet/Program.cs`, a `.csproj`, and a test project.

Two are worth flagging before starting:

- **20b (DevUI)** — currently a `dotnet/README.md` with no code. Check whether a .NET DevUI
  equivalent exists at all before scheduling it; if it does not, convert it to a documented stub
  like 16 and 20 and remove it from this list.
- **26 (Evals)** — overlaps the .NET eval suite in `remaining-work.md` item 1. Sequence them
  together or the chapter will demonstrate a harness that does not exist yet.

### Phase C — the one Python gap

Add `python/tests/` to ch22, so no chapter claims runnable code without a test.

---

## Definition of done

- [ ] `tutorials-dotnet-test` covers every chapter with .NET code except 16 and 20
- [ ] The generated status table shows no "tests pending" rows outside 16 and 20
- [ ] "Not ported" appears only where a deliberate decision is recorded (and 20b is resolved either
      way)
- [ ] `check_tutorial_readmes.py` passes on every touched chapter
- [ ] `tutorials/README.md`'s intro no longer says ".NET through chapter 20" if that changes

---

## Honest scheduling note

Nothing in this plan moves adoption. It is the integrity of the parity claim, which matters because
that claim is load-bearing for the project's credibility — but no visitor is currently blocked on
it, and the [adoption audit](../audit-2026-08-25-adoption-and-azure.md) explicitly deprioritised it
against the Azure work and the demo path.

**Recommendation: run it as background work, one chapter at a time, and never let it block a wave.**
