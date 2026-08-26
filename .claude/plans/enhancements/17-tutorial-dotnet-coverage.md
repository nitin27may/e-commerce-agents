# Plan 17 — .NET Tutorial Coverage (#20)

**Status:** DONE · **Date:** 2026-08-26 · **Issue:** [#20](https://github.com/nitin27may/e-commerce-agents/issues/20)
**Parent:** [`../audit-2026-08-25-adoption-and-azure.md`](../audit-2026-08-25-adoption-and-azure.md) (finding: deprioritised for adoption, kept for parity integrity)
**Sibling:** [`18-composer-ux.md`](18-composer-ux.md) — independent, no shared code, separate PR stream.

Closes #20. The issue text is out of date in two places and is corrected below.

---

## 0. Outcome — executed 2026-08-26

Done in one pass rather than the five phases below. Final state: **every chapter that ships code
ships both languages, both gated in CI** — 334 .NET tests across 31 projects (was 47 across 11),
plus 15 new Python tests for chapter 22.

The estimate below (§10) was 12–15 days, on the assumption that porting was the expensive part.
It was not. Writing the tests was — and writing them is what surfaced the defects, which turned
out to be the actual return on the exercise.

### What the tests found

Three defects, all invisible to `dotnet build`, which was the only .NET gate at the time:

- **Chapter 12 never ran.** It handed a bare string to a workflow whose input type is
  `List<ChatMessage>`, never sent the `TurnToken` the wrapped agents wait on, and matched on an
  `AgentResponseEvent` that `BuildSequential` does not emit. Three independent silent failures
  stacked: `dotnet run` printed the topic, called no model, exited 0.
- **Chapter 15 could loop forever.** `PromptDrivenManager` overrode `ShouldTerminateAsync` —
  where the base class enforces `MaximumIterationCount` — without chaining to it, so the cap its
  own comment called "the safety net" did not exist. Its regression test asserts against a clock,
  because the failure mode is a hang rather than a wrong value.
- **Chapter 20 was a stub for no reason.** `WorkflowVisualizer` has shipped since 1.1.0, so unlike
  chapter 16 there was no SDK gap. The usage its stub *printed* did not compile either —
  `ToMermaidString` is a static method, not an extension — and printed sample code is never
  compiled.

Two SDK behaviours are pinned as tests rather than fixed, because they are Microsoft's: MAF names
synthesised handoff tools **positionally** (`handoff_to_1`), so an agent's name never reaches the
model and only `description:` distinguishes targets; and `FileSystemJsonCheckpointStore` takes an
exclusive directory lock it never releases.

### Where the execution diverged from the phases below

| Plan said | What happened | Why |
|---|---|---|
| Phase 3: ch16 and ch20 are "re-verify blocker" | ch20 became a **real runnable example**; ch16 stayed a stub | There was never a blocker for ch20 — `WorkflowVisualizer` shipped in 1.1.0. Magentic genuinely is Python-only. |
| Phase 3: stubs get no tests | ch16 got **tripwire tests** | A stub saying "not supported yet" is only honest while it stays true. Its tests reflect over the shipped assembly and fail the day `MagenticBuilder` appears, with a control test so a failed assembly load cannot masquerade as "still missing". |
| §7 non-goal: 20b stays unported | Still unported, **corrected reason** | Its README said no `Microsoft.Agents.AI.DevUI` package ships. One does now — 46 versions on NuGet — but all prerelease, and this repo pins `Microsoft.Agents.AI` 1.1.0 stable. |
| Phase 4: port ch22 like the rest | Ported, with the **asymmetry documented** | Python imports the production `workflows/group_chat.py` and tours real shipped code; .NET has no equivalent standalone type, so it reimplements the shape self-contained. One chapter is a tour, the other a faithful model. |
| Phase 1: `MafV1.TestSupport` shared project | Shipped as `tutorials/_shared/dotnet/ScriptedChatClient.cs`, **linked** into each test project | Matches the existing `_shared/replay_client.py` precedent rather than introducing a second convention. Same effect: one copy, fixed once. |
| Not planned | Chapter 22's **Python** tests | It was the only Python chapter with code and no tests. The recorded reasoning was that the production module's tests covered it — those cover the *module*, not the chapter's own panelists, synthesizer, or its central claim. |
| Not planned | `scripts/check_tutorial_coverage.py` | `tutorials/README.md` claimed its status table was generated from disk. It was maintained by hand and had drifted. Now generated, and CI fails when it is stale. |

### Verification at the point of completion

| Gate | Result |
|---|---|
| `dotnet build` (62 projects) | pass |
| `dotnet test` (31 projects) | **334 passed** |
| `pytest -m "not integration"` | **207 passed**, 49 deselected |
| `ruff check tutorials/` | pass |
| `check_tutorial_readmes.py --check` | pass |
| `check_tutorial_coverage.py --check` | pass |

No test needs an API key or a network call.

Everything below is the plan as written before execution. Kept for the reasoning — particularly
§2 (the three gate defects) and §3 (why the .NET test pattern beats Python's) — not for the
schedule.


---

## 1. Measured state, 2026-08-26 (before execution)

Generated from disk, not from the README. `tests` counts `*.Tests.csproj` under the chapter.

| # | Chapter | Python | .NET code | .NET tests | Verdict |
|---|---|---|---|---|---|
| 00 | setup | guide | guide | — | out of scope, guide only |
| 01–11 | first-agent → agents-in-workflows | tested | yes | **yes** | complete, 11 chapters |
| 12 | sequential-orchestration | tested | yes | **no** | **Phase 2** |
| 13 | concurrent-orchestration | tested | yes | **no** | **Phase 2** |
| 14 | handoff-orchestration | tested | yes | **no** | **Phase 2** |
| 15 | group-chat-orchestration | tested | yes | **no** | **Phase 2** |
| 16 | magentic-orchestration | tested | stub | no | **Phase 3** — re-verify blocker |
| 17 | human-in-the-loop | tested | yes | **no** | **Phase 2** |
| 18 | state-and-checkpoints | tested | yes | **no** | **Phase 2** |
| 19 | declarative-workflows | tested | yes | **no** | **Phase 2** |
| 20 | visualization | tested | stub | no | **Phase 3** — re-verify blocker |
| 20b | devui | tested | README only | — | **non-goal**, see §7 |
| 21 | capstone-tour | planned | `.gitkeep` | — | **out of scope** — audit F9 owns it |
| 22 | group-chat-debate | *tests pending* | **none** | — | **Phase 4** |
| 23–32 | a2a → cost-control | tested | **none** | — | **Phase 4**, 10 chapters |

**Totals:** 7 chapters need tests only. 11 chapters need a `dotnet/` port plus tests. 2 stubs need a
decision. That is the whole of #20.

### Corrections to the issue text

- #20 says "Chapter 22 has no .NET version at all". True, but so do **23–32**. The real gap is 11
  chapters, not one.
- #20 says tests exist for "01–11". Confirmed accurate.
- `tutorials/README.md`'s status table is already correct and already uses the right vocabulary
  (audit F7 is done). It does not need rewriting — only extending as chapters land.

---

## 2. Three defects found while assessing this — fix before adding anything

These are the reason this plan starts with a Phase 0 rather than with chapter work.

### D1 — The .NET tutorial test gate is silently green (P1)

`tutorials.yml` runs every `*.Tests.csproj` with **no filter**:

```yaml
for proj in $(find tutorials -iname '*.Tests.csproj' | sort); do
  dotnet test "$proj" --nologo -v minimal -p:NuGetAudit=false || fail=1
```

Ten of the eleven chapters ship an integration test marked `[Trait("Category", "Integration")]` that
hits a real LLM. CI has no key. The tests do **not** skip — they do this:

```csharp
if (!LlmCredentialsPresent())
{
    Console.WriteLine("[skip] no LLM credentials in .env");
    return;            // <-- test PASSES
}
```

So on every PR, ten integration tests report **passed** having asserted nothing. The Python job gets
this right (`pytest -m "not integration"`); the .NET job never got the equivalent.

This is the same failure shape `remaining-work.md` documents under *"Two ways a dual-backend run can
lie"*: a green run that never reached the thing it claims to test.

**Fix:** add `--filter "Category!=Integration"` to the `tutorials-dotnet-test` job, and add a second
opt-in job (`workflow_dispatch` + weekly schedule) that runs `Category=Integration` with a real key,
mirroring `evals.yml`'s smoke/full split.

### D2 — The `Skip` shim is dead code that would fail if used (P2)

`01-first-agent/dotnet/tests/FirstAgentTests.cs` defines:

```csharp
internal static class Skip { public static void IfNot(bool c, string reason) { if (!c) throw new SkipException(reason); } }
internal sealed class SkipException : Exception { ... }
```

Nothing calls it. If anything did, xunit would record a **failure**, not a skip — xunit v2 has no
exception-based skip. The comment above it ("we use this skipper inside tests that need to bail")
describes behaviour that does not exist.

**Fix:** delete the shim. Once D1 lands, conditional skipping is unnecessary — the filter decides.
If a genuine conditional skip is ever wanted, the supported route is `Xunit.SkippableFact`, which is
a package decision, not a hand-rolled shim.

### D3 — `StubChatClient` is copy-pasted into every chapter (P2)

Each of the 11 test files carries its own private fake `IChatClient` (`StubChatClient`,
`ScriptedChatClient`, and variants). There is no `tutorials/_shared/dotnet/`, though Python has
`tutorials/_shared/` with `replay_client.py` and `maf_bootstrap.py`.

Adding 18 more chapters means 18 more copies. **Fix in Phase 1.**

**Judgement call to make explicitly:** duplication in *teaching* code is sometimes correct — a reader
opening chapter 14 should see everything that chapter needs. The proposal below keeps that property:
the shared project is **test-only support**, never referenced by a chapter's `Program.cs`, so the
thing a reader studies stays self-contained.

---

## 3. What the .NET test pattern is, and why it is better than Python's

Worth stating because it changes the effort estimate and is publishable material in its own right.

Python tutorials assert against **recorded replay fixtures** (`_shared/replay_client.py`,
`LLM_PROVIDER=replay`). Fixtures are brittle — see the memory note on eval fixture invalidation, and
`tutorials.yml`'s `PYTHONHASHSEED: "0"` pin, which exists solely because chapter 14's fixture hash is
sensitive to Python's hash randomisation.

.NET fakes one seam higher, at `Microsoft.Extensions.AI.IChatClient`:

```csharp
var stub = new StubChatClient("Paris.");
var agent = new ChatClientAgent(stub, instructions: Program.Instructions, name: "first-agent");
var answer = await Program.Ask(agent, "What is the capital of France?");
answer.Should().Be("Paris.");
stub.CallCount.Should().Be(1);
```

No fixtures, no recording step, no hash pin, and it asserts on *wiring* — which executors ran, in
what order, what reached the model — which is what a tutorial should teach. Chapter 11's
`invoked.Should().ContainInOrder("input-adapter", "en-to-fr", "fr-to-es", "output-adapter")` is a
better test than any replay cassette.

**Consequence for estimating:** Phase 2 and Phase 4 need **no recording run and no API key**. That is
the single biggest reason this work is cheaper than the ".NET eval suite" item it is often grouped
with.

**Consequence for content:** "why our C# tests need no cassettes and our Python tests do" is a real
post, and it argues for the typed-seam design that enterprise .NET readers already believe in.

---

## 4. Phase 0 — fix the gate (½ day, do first)

Not optional and not batchable with chapter work. Landing chapter tests behind a lying gate means
the new tests inherit the lie.

- [ ] `tutorials.yml` → `tutorials-dotnet-test`: add `--filter "Category!=Integration"`.
- [ ] New job `tutorials-dotnet-integration`: `workflow_dispatch` + weekly `schedule`, runs
      `--filter "Category=Integration"` with `OPENAI_API_KEY` from secrets. Non-blocking on PRs.
- [ ] Delete the `Skip`/`SkipException` shim from `01-first-agent`.
- [ ] Replace the ten `Console.WriteLine("[skip]"); return;` bodies with a real assertion — once the
      filter excludes them from PR runs, they no longer need to self-neuter.
- [ ] **Prove the gate now bites:** break one wiring assertion on purpose, confirm CI goes red,
      revert. Per `remaining-work.md`, a gate that has never failed is not known to work.

**Done when:** a deliberately broken wiring assertion fails CI, and the PR run reports 10 fewer
"passed" tests than before (the integration tests are now excluded, not fake-passing).

---

## 5. Phase 1 — shared .NET test support (1 day)

New project `tutorials/_shared/dotnet/MafV1.TestSupport/MafV1.TestSupport.csproj`, referenced only by
`*.Tests.csproj`.

Contents, all extracted from what already exists rather than invented:

| Type | Extracted from | Purpose |
|---|---|---|
| `StubChatClient` | ch01 | canned responses, records messages + `ChatOptions` |
| `ScriptedChatClient` | ch11 | ordered responses, `throwOnFirst` for failure paths |
| `ToolCallingChatClient` | ch02 | returns a `FunctionCallContent` then a final answer |
| `StreamingChatClient` | ch03 | drives `GetStreamingResponseAsync` deterministically |
| `RepoEnv.Load()` | ch01 `LoadRepoEnv` | walks up for `.env`; used only by integration tests |
| `WorkflowEventRecorder` | ch11 inline | collects `ExecutorInvokedEvent` etc. into an ordered list |

- [ ] Create the project; `IsPackable=false`.
- [ ] Migrate ch01–11 onto it, deleting the per-chapter copies. **One chapter per commit** so a
      regression is bisectable.
- [ ] Confirm the build job still finds every `.csproj` — it globs, so no workflow change is needed.

**Gotcha:** the build job builds *every* `.csproj` including this one. It must compile standalone
with no chapter reference, or the build job goes red for a reason unrelated to any chapter.

**Deliberate non-change:** chapter `Program.cs` files do not reference this project. A reader's
chapter stays readable end to end.

---

## 6. Phase 2 — tests for the 7 chapters that already have .NET code (2 days)

Chapters 12, 13, 14, 15, 17, 18, 19. Pattern per chapter: `dotnet/tests/<Name>.Tests.csproj` +
`<Name>Tests.cs`, `ProjectReference` to the chapter's own `.csproj`, xunit + FluentAssertions +
`MafV1.TestSupport`. No CI change — the glob picks it up.

Per-chapter assertions, chosen so the test proves *the thing the chapter teaches*:

| Ch | Teaches | Wiring assertions (no LLM) |
|---|---|---|
| 12 | Sequential orchestration | executors fire in declared order; each stage receives the previous stage's output; a mid-chain throw surfaces as a failure event, not a hang |
| 13 | Concurrent orchestration | all branches invoked; aggregation waits for every branch; result is order-independent (assert as a set, not a list — see risk R2) |
| 14 | Handoff orchestration | handoff target is chosen from the scripted reply; the receiving agent gets the conversation, not just the last turn; an unknown target fails loudly |
| 15 | Group chat | turn order matches the manager's selection; termination condition ends the chat; max-turns cap holds |
| 17 | Human-in-the-loop | workflow suspends at the `RequestPort` node; `SendResponseAsync` + a fresh `WatchStreamAsync()` resumes it; **do not dispose the run across the pause** (see §9) |
| 18 | State and checkpoints | a checkpoint is written at the expected boundary; resume from checkpoint reproduces state; resuming twice does not double-apply |
| 19 | Declarative workflows | the YAML/JSON definition parses to the same graph the fluent builder produces; an invalid definition fails at build time with a useful message |

- [ ] One PR per chapter, or one PR per pair. Not one PR for all seven — a single red assertion
      should not block six green chapters.
- [ ] Update `tutorials/README.md`: `Runnable · tests pending` → `Runnable · tested in CI`, per
      chapter, **in the same commit as its tests**. The table is described as the source of truth;
      drift there is worse than the gap it documents.

**Chapter 17 is the one to schedule carefully.** `remaining-work.md` records that MAF .NET has no
`ctx.request_info` equivalent, that pausing needs a dedicated `RequestPort` node, and that the
verified resume shape is: break out of the event stream on `RequestInfoEvent` *without disposing the
run*, then `SendResponseAsync`, then open a fresh `WatchStreamAsync()`. That was learned the hard way
in the production backend. The tutorial test must use the same shape or it will teach a hang.

---

## 7. Phase 3 — the two stubs (½ day to decide, up to 2 days if unblocked)

Both are marked `Runnable · stub` in the README with a footnote. Both footnotes were written months
ago against an SDK that ships roughly monthly.

- [ ] **Ch16 (magentic).** Its `bin/` contains only `Magentic.dll` and no `Microsoft.Agents.AI.*`,
      confirming the stub does not reference MAF at all. Re-verify against the current
      `Microsoft.Agents.AI.Workflows` package whether a magentic/manager orchestration primitive now
      exists in C#. If yes → port it (~1 d) and it becomes the most interesting new chapter in the
      set. If no → keep the stub, but **date the footnote** ("verified absent in <package version>,
      <date>") so the next reader knows how stale the claim is.
- [ ] **Ch20 (visualization).** The 26-line print stub. Python uses `WorkflowViz`. Check whether the
      .NET workflow object can emit a DOT/Mermaid graph. This repo already renders an orchestration
      graph from SSE events in the frontend, so worst case the chapter can teach graph extraction
      from `Workflow` metadata and hand off rendering — which is honest and still useful.

**Do not write tests for a stub.** A test asserting "this prints a not-supported message" is
ceremony. Either the chapter becomes real and gets real tests, or it stays a documented gap.

---

## 8. Phase 4 — port chapters 22–32 to .NET (8–10 days)

The largest piece. Split by where the code comes from, because the two halves have very different
costs.

### 4a — Extract from the production .NET backend (7 chapters, ~4 days)

For these, `agents/dotnet/` already contains a working, tested implementation. The chapter is a
minimal extraction, not a design exercise. Verified present today:

| Ch | Chapter | Production source in `agents/dotnet/src/` |
|---|---|---|
| 22 | group-chat-debate | `ECommerceAgents.Orchestrator/Modes/` (group-chat mode ships) |
| 23 | a2a-protocol | `ECommerceAgents.Shared/A2A/A2AClient.cs`, `Orchestrator/Agent/OrchestratorTools.cs` |
| 24 | rag-and-grounding | `Orchestrator/Routes/ChatRoutes.cs`, `Shared/Grounding/` |
| 25 | guardrails | `Shared/Guardrails/SanitizeToolsConfig.cs`, `OutputSanitizer.cs` |
| 26 | evals | `ECommerceAgents.Evals/` — `Scorers.cs`, `EvalTypes.cs` |
| 31 | retry-and-compensation | `Shared/A2A/A2AClient.cs` (Polly pipeline), `Routes/HitlActionExecutor.cs` |
| 32 | cost-control-and-budgets | `Shared/Cost/CostEstimator.cs`, `Shared/Agents/SpecialistPipeline.cs` |

**Rule for this half:** the chapter must not *reference* `agents/dotnet` — tutorials stay standalone.
It re-implements the pattern in ~100 lines. But the production code is the proof the pattern works in
C#, so no chapter here can turn out to be blocked.

**Chapter 26 caveat:** it teaches evals, and the .NET eval suite's own recording run is still
outstanding (`remaining-work.md` item 1). The chapter should teach **scorers and the harness shape**
against a fake client — deterministic, keyless — and must not depend on recorded fixtures. Keep the
two efforts uncoupled or this chapter inherits that blocker.

### 4b — Author fresh against the MAF SDK (4 chapters, ~4 days)

No production analogue exists — a grep for `Planner`, `SubWorkflow`, and agent-as-tool across
`agents/dotnet/src` returns nothing. These need real SDK work and each carries a genuine risk of
turning out to be Python-only:

| Ch | Chapter | Risk |
|---|---|---|
| 27 | agent-as-tool | Low — MAF exposes agents as `AIFunction`; verify the C# surface exists |
| 28 | reflection-and-critique | Low — a two-agent loop, buildable from primitives |
| 29 | planner-executor | Medium — depends on what planning primitives C# ships |
| 30 | subworkflows | **Medium-high** — verify `WorkflowBuilder` supports nesting in C# |

- [ ] **Spike each of the four for one hour before committing to the phase.** If a primitive is
      absent, mark it `Not portable` per #19's precedent and say why, with the package version and
      the date. A dated, reasoned gap is a better artifact than a fake chapter.

### 4c — Tests for all 11 (~2 days)

Same pattern as Phase 2, using `MafV1.TestSupport`. Written **in the same PR as the chapter**, not
after — the whole point of #20 is that .NET code shipped ahead of its tests once already.

---

## 9. Non-goals, stated so they are not re-litigated

- **Ch20b (DevUI).** `agent-framework-devui` is Python tooling. There is no .NET DevUI to port. The
  README's `Not ported` should become **`N/A — Python tooling`** so it stops reading as debt.
- **Ch21 (Capstone Tour).** Empty in both languages. Owned by audit finding F9, not by #20.
- **Ch00 (Setup).** Guide only, by design.
- **A .NET replay/cassette layer.** §3 explains why the `IChatClient` seam is better. Do not port
  Python's replay design across.
- **Touching `agents/dotnet/` production code.** If a port needs a production change, that is a
  separate issue and a separate PR.

---

## 10. Effort

| Phase | Work | Effort |
|---|---|---|
| 0 | Fix the lying gate | 0.5 d |
| 1 | Shared .NET test support | 1 d |
| 2 | Tests for ch12–15, 17–19 | 2 d |
| 3 | Ch16 / ch20 stub decision | 0.5–2 d |
| 4a | Port ch22–26, 31, 32 (extract) | 4 d |
| 4b | Port ch27–30 (author fresh) | 4 d |
| 4c | Tests for ch22–32 | 2 d |
| | **Total** | **14–15.5 focused days** |

Phases 0–2 (**3.5 days**) close the "tests pending" half of #20 and are worth shipping alone. Phase 4
is the multi-week half the audit deprioritised — ship it chapter by chapter, never as a blocker.

**Recommended stopping point for now:** land Phases 0–2, update the README table, and leave #20 open
with Phase 4 as a checklist. That converts #20 from "the largest single piece of work left" into a
tracked, incremental backlog, and it makes the parity claim honest for every chapter that currently
claims to be runnable.

---

## 11. Verification

```bash
# Per chapter, before pushing
dotnet build tutorials/<chapter>/dotnet/<Name>.csproj -p:NuGetAudit=false
dotnet test  tutorials/<chapter>/dotnet/tests/<Name>.Tests.csproj -p:NuGetAudit=false \
             --filter "Category!=Integration"

# What CI will do — run the same globs locally first
for p in $(find tutorials -name '*.csproj' | sort); do dotnet build "$p" -p:NuGetAudit=false || echo "FAIL $p"; done
for p in $(find tutorials -iname '*.Tests.csproj' | sort); do dotnet test "$p" -p:NuGetAudit=false --filter "Category!=Integration" || echo "FAIL $p"; done

# The chapter contract — merge-blocking, checks README/nav consistency
python3 scripts/check_tutorial_readmes.py --check

# Docs site — chapters render into it
uv run python scripts/build_docs_site.py --check
```

`remaining-work.md`'s rule applies: **verify locally, treat CI as confirmation.**

---

## 12. Risks and gotchas

**R1 — A green .NET tutorial run can still lie.** That is D1, and it is why Phase 0 comes first.
After fixing it, prove the gate bites by breaking an assertion on purpose.

**R2 — Concurrent orchestration (ch13) is order-nondeterministic.** Assert on the *set* of executors
invoked and on the aggregate result, never on arrival order. The production suite already learned
this: `remaining-work.md` records three assertions "fixed" in the wrong direction because a
model-chosen path differed per run.

**R3 — Chapter 17 will hang if resumed the obvious way.** Documented in `remaining-work.md`: do not
dispose the `StreamingRun` across the pause, and open a *fresh* `WatchStreamAsync()` after
`SendResponseAsync`. Python's equivalent failure is `RuntimeError: Workflow is already running`.

**R4 — `NuGetAudit` must stay off.** A pre-existing transitive advisory is escalated to an error by
default and is unrelated to chapter correctness. Every new `.csproj` inherits the same flag.

**R5 — The MAF C# surface moves.** Any "not supported in C#" claim must carry a package version and a
date. Undated claims are how ch16 and ch20 became stale.

**R6 — Concurrent sessions in this working tree.** See §13.

---

## 13. Handover notes

**Ownership.** `remaining-work.md` records that more than one Claude session works in this tree, and
that `tutorials/**` belongs to the *other* session. This plan is written **for** that session — it
does not itself touch `tutorials/`.

**The git hazard, restated because it has already cost work once.** A `git add -A` swept 35 files
belonging to another session into an unrelated commit; from that session's side the work had simply
vanished. **Stage explicit paths. Never `-A`. Commit or stash before switching branches** —
`git checkout` drags uncommitted work across, and that is the actual failure mode.

**State at time of writing.** Branch `fix/flaky-stream-test` is checked out; plan 16 (Docker Compose
dual-stack) is being written concurrently by another session and has **no overlap** with this one —
it does not mention `tutorials/` at all. This file and `18-composer-ux.md` are left **untracked**
deliberately; commit them with explicit paths.

**Suggested branches:** `fix/tutorial-dotnet-gate` (Phase 0), `chore/tutorial-dotnet-test-support`
(Phase 1), then `test/tutorial-dotnet-ch<NN>` per chapter.
