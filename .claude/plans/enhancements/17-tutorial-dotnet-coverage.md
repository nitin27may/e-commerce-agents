# Plan 17 — Tutorial .NET coverage

**Status:** DONE · **Date:** 2026-08-26 · **Issue:** [#20](https://github.com/nitin27may/e-commerce-agents/issues/20)

Executed in a single pass rather than the incremental shape planned below. Final state: every
chapter that ships code ships both languages, and both are gated in CI — **334 .NET tests across
31 projects**, up from 47 across 11, plus 15 new Python tests for chapter 22.

The plan's estimate was 3–5 weeks part-time, on the assumption that porting was the expensive
part. It was not. Writing the tests was, because writing them is what surfaced the bugs — and
the bugs were the actual value of the exercise.

### What the tests found

Three defects that had been sitting in the series unnoticed, all invisible to a `dotnet build`
gate, which was the only .NET gate that existed:

- **Chapter 12 never ran.** It handed a bare string to a workflow whose input type is
  `List<ChatMessage>`, never sent the `TurnToken` the wrapped agents wait on, and matched on an
  `AgentResponseEvent` that `BuildSequential` does not emit. Three independent silent failures
  stacked: `dotnet run` printed the topic, called no model, exited 0.
- **Chapter 15 could loop forever.** `PromptDrivenManager` overrode `ShouldTerminateAsync`, which
  is where the base class enforces `MaximumIterationCount`, and did not chain to it — so the cap
  the comment called "the safety net" did not exist. A selector that never picked the Editor ran
  unbounded, one provider call per turn.
- **Chapter 20 was a stub for no reason.** `WorkflowVisualizer` has shipped since 1.1.0, so unlike
  chapter 16 there was no SDK gap. Worse, the usage its stub *printed* did not compile —
  `ToMermaidString` is a static method, not an extension. Printed sample code is never compiled.

Two SDK behaviours are pinned as tests rather than fixed, because they are Microsoft's and not
ours: MAF names synthesised handoff tools positionally (`handoff_to_1`), so an agent's name never
reaches the model and only `description:` distinguishes targets; and
`FileSystemJsonCheckpointStore` takes an exclusive directory lock it never releases.

### Deviations from the plan below

- **Chapters 16 and 20 were not excluded.** Ch20 became a real runnable example (see above).
  Ch16 stayed a stub — correctly, Magentic really is Python-only — but gained *tripwire* tests
  that reflect over the shipped assembly and fail the day `MagenticBuilder` appears. A stub
  claiming "not supported yet" is only honest while it stays true, and left alone it decays
  silently.
- **20b stayed unported, for a corrected reason.** Its README said no `Microsoft.Agents.AI.DevUI`
  package ships. One does now — 46 versions on NuGet — but all prerelease, and this repo pins
  `Microsoft.Agents.AI` 1.1.0 stable.
- **Chapter 22's asymmetry was accepted and documented** rather than resolved. Python imports the
  production `workflows/group_chat.py`; .NET has no equivalent standalone type, so it reimplements
  the shape self-contained. One chapter is a tour, the other a faithful model.
- **A shared test double was added**, `tutorials/_shared/dotnet/ScriptedChatClient.cs`, the .NET
  counterpart to `replay_client.py`. It records what actually reaches the model — instructions
  arrive on `ChatOptions.Instructions`, not as a system message — and timestamps calls, which is
  how ch13 proves its agents genuinely overlap and ch12 proves its genuinely do not.
- **The status table is now generated**, by `scripts/check_tutorial_coverage.py`, and gated in CI.
  The README had claimed it was generated for a long time while being maintained by hand, which is
  how it came to describe chapters as ported that were not.

Everything below is the original plan, kept for the reasoning rather than the schedule.

---

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
