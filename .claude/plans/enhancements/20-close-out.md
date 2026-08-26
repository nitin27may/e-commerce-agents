# Plan 20 — Close out everything, then Azure

**On execution, copy into the repo as** `.claude/plans/enhancements/20-close-out.md`
(the repo `CLAUDE.md` requires working artifacts in repo-local `.claude/plans/`).

**Supersedes:** plan 19 (which sequenced five of these). **Folds in:** plans 16, 18, 19, the
adoption audit's open findings, and every unchecked item in `remaining-work.md`.
**Explicitly not in this plan:** plan 13 (Azure Container Apps + Microsoft Foundry). That is the
*next* objective and starts once this one is closed.

---

## Context

The repo has accumulated a long tail of open items across several plans, three of them carrying
real defects rather than missing features. They have been outstanding for a while and are now
spread across `remaining-work.md`, the adoption audit, and plans 16/18/19 — which makes "what is
actually left?" a research question every time it is asked.

The objective is to close **all** of it, refresh every document that has drifted, and cut a release
from a state where nothing is flagged. Only then move to the Azure/Foundry work, so that effort
starts from a clean base rather than inheriting this tail.

The one thing that makes this urgent rather than tidy: **the .NET stack cannot answer a single
question today**, and the README describes "two complete, working backends". That claim is false on
`main` right now.

---

## Scope — the complete pending inventory

Everything below is currently open. Nothing else is.

| # | Item | Source | Kind | Live key? |
|---|---|---|---|---|
| 1 | .NET orchestrator reaches no specialist (**P0**) | plan 16 F1 | defect | verify only |
| 2 | Stale volume silently breaks search | plan 16 F2 | defect | no |
| 3 | .NET Docker builds laxer than CI | plan 16 F3 | defect | no |
| 4 | Two stacks collide on ports | plan 16 F4 | UX | no |
| 5 | .NET stack uses Python seeder/auth-server | plan 16 F5 | document it | no |
| 6 | `handoff` returns 19–25k chars in 100–200 s | plan 19 §2a | defect | yes |
| 7 | `workflow:pre-purchase` fails silently | plan 19 §2b | defect | yes |
| 8 | Orchestration benchmark has no published result | audit F5 | content | **yes, paid** |
| 9 | Demo clip has a spec, no recording | audit F3 | content | **yes, paid** |
| 10 | Composer UX (#4) | plan 18 | feature | no |
| 11 | Chapter 21 Capstone Tour is two `.gitkeep` files | audit F9 | writing | no |
| 12 | No ADR trail | audit F10 | writing | no |
| 13 | "Reported vs actual" table invisible in `.claude/` | audit F12 | content | no |
| 14 | .NET eval suite: no fixtures, baselines or CI job | remaining-work §1 | testing | **yes, paid** |
| 15 | Cost counter instrument | remaining-work §4 | feature | no |
| 16 | In-chat approval card | remaining-work §4 | feature | no |
| 17 | Streaming tool-call frames | remaining-work §4 | feature | no |
| 18 | Anonymous multi-turn memory undocumented | remaining-work §4 | document it | no |
| 19 | Langfuse sink on .NET | remaining-work §4 | **decision, not work** | no |
| 20 | Docs refresh + release | this plan | docs | no |

Items 5, 18 and 19 are closed by *writing something down*, not by building. Saying so up front stops
them being re-litigated as engineering tasks.

**Honest total: 3–4 weeks of focused work.** The waves below are ordered so it can be stopped after
any one of them and still leave the repo in a coherent, releasable state.

### Named exclusions — considered, deliberately out

Four unchecked `docs/roadmap.md` items are **not** in this plan. They are forward-looking features
that were never started, not loose ends from work already in flight, and folding them in would turn
a 3–4 week close-out into a multi-month one. Listed so the boundary is a decision rather than an
oversight — say the word and any can be pulled in:

- **Typed filter DSL** — replace `search_products`' flat parameter list with a structured
  `ProductFilters` model
- **External integration surface** — publish the two MCP packages to PyPI
- **Eval gate: native vs MCP** — run each dataset twice and fail CI if the MCP path scores lower
- **Prompt caching** — cache system prompts and tool schemas per agent

Plan 13 (Azure + Foundry) is excluded on your instruction: it is the next objective, after this.

---

## Wave 1 — Make the .NET stack work (items 1–5)

The P0. Nothing else on this page matters while the orchestrator cannot reach a specialist.

### 1a. Fix F1 — the shared-prompt / tool-contract mismatch

**Already diagnosed** (PR #71, open). Two of plan 16's three candidates are refuted; the mechanism
is confirmed. Recreating the tool registration against the pinned `Microsoft.Agents.AI` 1.18.0:

```
schema advertises   "agentName"      binder expects "agentName"   → they agree
BIND 'agent_name' → "The arguments dictionary is missing a value for
                     the required parameter 'agentName'."          → the production error, exactly
```

The .NET Dockerfiles ship the **Python** prompt corpus verbatim
(`COPY agents/python/config ./agents/python/config`), and `config/prompts/orchestrator.yaml` tells
the orchestrator its tool is `call_specialist_agent` — Python's name, with Python's `agent_name`
parameter. The registered tool is `CallSpecialistAgent(agentName, message)`.

| | Python | .NET | Shared prompt says |
|---|---|---|---|
| Tool | `call_specialist_agent` | `CallSpecialistAgent` | `call_specialist_agent` |
| Parameter | `agent_name` | `agentName` | — |

**Files:** `agents/dotnet/src/ECommerceAgents.Orchestrator/Agent/OrchestratorTools.cs`

Register as `call_specialist_agent` and expose the parameter as `agent_name`, conforming .NET to
the shared corpus. Do **not** rename the prompt — that breaks the Python stack, which works.

- Confirm the live model emits `agent_name` (one log line; the three-way investigation is resolved)
- Add a test that exercises **schema generation and argument binding**, not the C# method directly —
  the existing tests are green precisely because they call the method
- **Grep the whole corpus for other Python-idiom symbol names.** If one shared prompt name can
  silently break .NET, the others are candidates. Fix the class, not the instance

### 1b. F2 — stale volume probe

`scripts/dev.sh` / `dev.ps1` already probe for a stale volume; extend to assert **schema**, not just
auth — a one-line `information_schema` query for a column current `init.sql` guarantees. On
mismatch, refuse to start and print the two options. Same check on `docker-compose.demo.yml`'s path.
Hardcode the column: derived is clever and will drift.

### 1c. F3 — `Directory.Build.props` in seven Dockerfiles

All seven copy `Directory.Packages.props` and not `Directory.Build.props`, so Docker builds silently
lose `TreatWarningsAsErrors`, `LangVersion`, `InvariantGlobalization` and `VersionPrefix` — images
ship **unversioned** and compile under laxer rules than CI. Add the `COPY`. Removing the now-redundant
per-`.csproj` properties is a **separate, second** change: that duplication is what keeps the build
alive today.

### 1d. F4 / F5 — switch UX and the shared-services record

Detect the other stack at startup and fail with a plain message plus the exact command, not a raw
Docker port-binding error. Add `--switch` to do it properly (down, drop volumes, up clean) — which
also sidesteps F2. Document the one-at-a-time constraint in `docs/configuration.md`. Add the parity
matrix row explaining that `seeder` and `auth-server` are shared Python images, and why.

**Exit:** `./scripts/dev.sh --dotnet` answers a routed question naming a real specialist; all five
modes return real answers; `scripts/e2e-both-stacks.sh` passes against both stacks.

---

## Wave 2 — Orchestration-mode correctness (items 6–7)

Must precede Wave 3: these two modes would dominate a benchmark table measured today.

### Narrowed by Wave 1's live run — both defects are Python-only

Verifying the .NET fix incidentally measured the same two modes on the .NET stack, and **neither
misbehaves there**:

| Mode | Python (measured v1.2.0) | .NET (measured after Wave 1) |
|---|---|---|
| `handoff` | 19,000–25,000 chars, 100–200 s | **1,417 chars**, normal latency |
| `workflow:pre-purchase` | 48 chars, 2 of 4 contributions | **113 chars, all 4**: `Reviews: positive (8 reviews) \| Stock: 317 units available \| Price trend: stable \| Shipping: from $5.99, 5-7 days` |

Two consequences:

1. **Both are Python-side defects**, not framework or design problems. The cumulative-vs-delta
   hypothesis for `handoff` is now much stronger: .NET reads the same MAF event stream through
   different accumulation code and does not blow up.
2. **.NET is the reference implementation for both.** Rather than reasoning from first principles,
   diff the Python accumulation against `HandoffMode.cs`, and the Python pre-purchase tool wiring
   against the .NET specialists' — which return all four contributions, so the tools exist and work.

The stack that could not answer a question at the start of this plan now behaves better than the
one that could, on both of these. Worth stating plainly in the changelog.


### 2a. `handoff` — 19–25k characters in 100–200 s

Against ~11 s / ~1,000 chars for `tool` on the same prompt.

**File:** `agents/python/orchestrator/modes/handoff_mode.py`, the `turns` accumulator.

**Hypothesis:** `AgentResponseUpdate.text` is treated as an incremental delta but is **cumulative**
on this stream, so `"".join(parts)` grows quadratically — `c1 + (c1+c2) + (c1+c2+c3) + …` turns a
1,000-char answer into ~20,000 with no single component looking wrong.

**Confirm before changing anything:** log `len(text)` per update for one turn. Flat-ish → deltas;
monotonically increasing → cumulative. One log line decides it.

The module docstring says this extraction mirrors
`tutorials/14-handoff-orchestration/python/main.py::ask()` — **if the hypothesis holds, the tutorial
has the same bug** and both fix together. If refuted, the next candidate is genuine mesh looping,
which explains latency but *not* a 20k single turn, since `final_text` is only the last speaker's
turn. Do not assume one fix covers both axes.

### 2b. `workflow:pre-purchase` — answers from half its inputs, silently

Four executors run; the reply is 48 chars: `"Stock: 348 units available | Price trend: stable"`.

**File:** `agents/python/workflows/pre_purchase.py`

The synthesis is **not** at fault — `_build_recommendation` guard-clauses every line on data being
present, so the fan-out is real and the synthesis faithful. The *inputs* are missing. The actual
defect is that every failure path is silent:

1. `fn = self._tools.get(...)` then `if fn:` with **no `else`** — a missing tool produces no error,
   no log, no `completed_steps` entry, indistinguishable from a tool that ran and found nothing
2. `state.errors` is appended to on exception and **never read** by `_build_recommendation`
3. `_merge_states` does not merge `shipping` — which is *correct* (shipping runs after the barrier
   in `_MergeAndShipExecutor`). Noted only so the next reader does not "fix" it

Reproduce and capture `state.errors` + `completed_steps` alongside the recommendation — that
distinguishes all three causes in one run. Then make the silent paths loud, and surface partial-ness
on the result so a partial answer is *visibly* partial.

**Exit:** both modes return responses within the same order of magnitude as `tool`, or the plan
records why one legitimately does not.

---

## Waves 1–2 — DONE, with three corrections to this plan

**Wave 1 (items 1–5):** all five plan-16 findings closed and live-verified.
**Wave 2 (items 6–7):** both mode defects closed on both stacks.

Three things this plan predicted turned out wrong, and the corrections matter more than the
completions:

| Predicted | Actual |
|---|---|
| F1 is one bad parameter | **39 of 46 tools** were registered under a name the shared prompt corpus does not use. The orchestrator's was fatal; the other 38 degraded silently. |
| F4 is a port collision | Ports were the symptom. No compose file set `name:`, so all three shared **one project** — `down` on one orphaned the other's containers. |
| `handoff`'s 23k output is quadratic accumulation | Refuted by measurement: genuine deltas, max 16 chars. The start agent was the tool-router orchestrator, so it **never handed off**, and autonomous mode looped it to MAF's 50-turn default. |

Plus one correction to a claim I had written into `remaining-work.md`: `workflow:pre-purchase`'s
synthesis does **not** discard reviews and shipping. Every line is guard-claused on data being
present — the fan-out was real and the synthesis faithful, and the *inputs* were missing. Chasing
the synthesis would have wasted a day.

### The one that only a browser could find

After F1 was fixed and verified — five modes answering, `agents_involved` correct, 570 tests green —
the Playwright suite failed two inventory tests. The cause:

```
tool.invoked name=call_specialist_agent
  error=ArgumentException: The arguments dictionary is missing a value
  for the required parameter 'agent_name'.
```

The model **intermittently** sends `agentName` even when the schema says `agent_name`. Every API
spot-check had hit the lucky casing; running the same two-turn conversation by hand worked. Only a
suite that exercises the path repeatedly surfaced it.

`NamingTolerantAIFunction` now normalises inbound argument keys to whatever the schema declares, in
both directions, for every tool — while still throwing on a genuinely absent argument, because
tolerating *that* is how a misbehaving model becomes a silent empty string.

**Lesson for the rest of this plan: an intermittent failure cannot be closed by running something
once.** Where a wave's exit criterion is "it works", the evidence has to be a suite, not a spot-check.

---

## Wave 3 — The paid measurements (items 8–9, 14)

Needs the live Azure OpenAI key already configured in `.env`.

**Run against the fixed code, not the v1.2.0 images.** Plan 19 said v1.2.0; that is now wrong —
Waves 1 and 2 change what is being measured, and measuring the old build would publish numbers we
already know are stale.

### Budget — $5 hard cap for everything

Not per-run: **$5 total across every live call in this plan**, including verification and testing.
That is tight enough that it has to be spent in priority order, with a named drop list rather than
discovering the ceiling mid-run.

| Priority | Run | Calls | Est. | If budget runs short |
|---|---|---|---|---|
| 1 | Wave 1 verification — .NET routing, five modes | ~20 | ~$0.30 | **never drop** — proves the P0 |
| 2 | Wave 2 verification — handoff + pre-purchase | ~25 | ~$0.40 | **never drop** — proves the fixes |
| 3 | Benchmark, Python stack, `--reps 3` | ~60 | ~$1.00 | drop to `--reps 2` (~$0.70) |
| 4 | Demo clip recording | ~6 turns | ~$0.10 | **never drop** — cheapest, highest visibility |
| 5 | .NET eval fixtures, 6 datasets | ~60–120 | ~$1.00–2.00 | record 3 datasets, land the rest later |
| 6 | Benchmark against the .NET stack | ~60 | ~$1.00 | **drop first** — nice to have, not required |

Worst case with everything: ~$4.80. Too close to the ceiling to be comfortable, so **item 6 is
dropped by default** and only run if items 1–5 come in under ~$3.

**I will report running spend after each paid step**, and stop and ask rather than crossing $5.
Every non-paid gate (unit tests, replay-driven suites, lint, contracts, docs build) stays free and
keyless — that is unchanged and is why most of this plan costs nothing.

### 3a. Publish the orchestration benchmark (audit F5)

`agents/python/evals/benchmark_modes.py` ships and is verified. It drives `POST /api/chat`, so it
exercises auth, guardrails, sanitization, grounding and usage logging — the real path. Cannot run
under `LLM_PROVIDER=replay`: fixtures return instantly, which makes latency meaningless.

Write `docs/orchestration-benchmark.md` with latency, tokens, cost and response length per mode,
**plus the prompt set, date, model and commit** — a benchmark without its conditions is an anecdote.
Preserve the harness's "not captured" vs zero distinction. Register in `scripts/build_docs_site.py`
`SECTIONS` under **Reference** so it reaches the site and `llms.txt`.

This is the audit's highest value-per-day item: `chatgpt.com` is the #1 referrer and an LLM asked
"which orchestration pattern should I use?" has nothing here to cite.

### 3b. Record the demo clip (audit F3)

`web/e2e/demo-recording.spec.ts` ships and typechecks; nothing has been recorded.

**Re-verify the prompts first.** The spec hard-codes "Allbirds" because `search_products` used
ILIKE `'%<whole phrase>%'`. **v1.2.0 replaced that with Postgres FTS**, so the constraint the prompts
were written around no longer holds and they must be re-checked against the current catalogue. A
natural-sounding prompt returning "I couldn't find any" is the worst possible first impression.

Convert (`ffmpeg -i video.webm -c:v libx264 -crf 24 -pix_fmt yuv420p demo.mp4`), swap the README's
static PNG, keep the spec so it can be re-recorded rather than decaying.

### 3c. Finish the .NET eval suite (remaining-work §1)

6 of 7 datasets are ported; record-on-miss and the `IEmbeddingProvider` seam are merged.

- Record fixtures for all 6 against real Azure OpenAI
- Generate .NET baselines — **re-record, never copy Python's**. .NET has a different mode set, so
  its absolute scores legitimately differ
- Add the CI job gating on **baseline regression, not an absolute floor**: the score is a property
  of the recording session, and measured spread was 8 points across four identical recordings
- `red_team` needs its own schema and evaluator — keep it separate and say so

---

## Wave 4 — The writing (items 11–13, 18–19)

Independent of Waves 1–3; can interleave.

- **Chapter 21, Capstone Tour** (audit F9) — the bridge from 34 tutorials to the running app, and
  the one missing rung on the ladder. Must satisfy `scripts/check_tutorial_readmes.py`, which
  already grants it a reduced check set (`CHAPTER_OVERRIDES`). Regenerate the coverage table after.
- **`docs/adr/`** (audit F10) — five records mined from existing prose: A2A over direct calls, no
  text-to-SQL, YAML prompt composition, MAF-native execution, dual-stack parity. Register under
  **Reference**.
- **Promote the "reported vs actual" table** (audit F12) — five rows in `remaining-work.md` showing
  the reported problem was smaller than the actual one every time, found by running rather than
  reading. The most credible artifact in the repo, currently invisible in `.claude/`. Its own page.
- **Anonymous multi-turn memory** — neither stack persists anonymous storefront conversations. A
  product decision, not a bug. Record it as one in the parity matrix.
- **Langfuse sink on .NET** — already a decision (deliberately skipped as an additive second
  exporter). Record and close; it is not pending work.

---

## Wave 5 — Features (items 10, 15–17)

- **Composer UX (#4)** — plan 18, 2–3 days, frontend-only by constraint. **Part B first**
  (collapse the mode selector, 0.5–1 d), then Part A (contextual suggestions, 1–1.5 d).
  **There are two mode surfaces**: `AGENT_MODES` in `web/src/components/ui/ai-prompt-box.tsx` and
  the orchestration `mode-switcher.tsx`. Collapsing the *orchestration* one hides the headline
  feature the demo clip is scripted around — confirm which #4 means before touching either.
  Plan 18 argues against an LLM call for suggestions: latency and cost on every turn, plus an
  endpoint the frontend-only constraint forbids. Reuse the existing message-shape classification.
  `ai-prompt-box.tsx` has **no test today**; plan 18 §5 specifies the suite.
- **Cost counter instrument** — dollar estimation and the budget ceiling ship; add a counter this
  repo owns so an OTLP sink can alert on spend anomalies.
- **In-chat approval card** — the pause/resume loop is real on both stacks; the control renders only
  on `/runs`. Render it inside the chat thread.
- **Streaming tool-call frames** — text deltas stream; propagate raw tool-result payloads as their
  own SSE frames so cards appear before the text completes.

---

## Wave 6 — Document refresh and release (item 20)

Do this **last**, once nothing above is open, so it is written once against a settled state.

- `CHANGELOG.md` — `[Unreleased]` is empty despite the tutorial .NET work having merged. Populate
  it, in the register the existing entries use (they say plainly when a thing never worked, and for
  how long — keep that).
- `docs/parity-matrix.md`, `docs/roadmap.md`, `README.md`, `docs/deployment.md`,
  `docs/configuration.md` — reconcile against reality.
- `.claude/plans/remaining-work.md` — every item in the table above ticked or moved to a decision.
- Register the new pages in `scripts/build_docs_site.py` `SECTIONS`; rebuild and confirm the page
  count and `llms.txt` grew.
- Version bump via `scripts/bump_version.py`, then tag. **v1.3.0**, not v1.2.1 — this ships new
  content (benchmark page, Chapter 21, ADRs) alongside the fixes, which is a minor, not a patch.
- Release via `.github/workflows/release.yml`: gate → version-check → approve → publish → release.

### 6b. Prune the plans directory

`.claude/plans/enhancements/` has grown to twenty files, several of which are finished, superseded,
or describe an era of the repo that no longer exists. "What is actually left?" should be answerable
by reading one file, not by triaging twenty.

Git retains everything, so **deleting is safe** — the reasoning is never lost, only moved out of the
way. What must not be lost is anything still load-bearing, so each file gets a verdict rather than a
bulk `rm`:

| File | Status on disk | Verdict |
|---|---|---|
| `00-master.md` … `09-public-storefront.md` | "not started" / "in progress" | **Delete.** These are the original UI/teaching-asset plans; the repo shipped past them long ago and their status lines have been stale for months. `00-master.md` indexes only these nine. |
| `10-oauth-authorization.md` (827 lines) | Phase A done | **Keep, mark done.** OAuth ships and is live-verified; the remaining phases are real future work. |
| `11-hardening-gaps.md` | ✅ Shipped | **Delete.** Fully shipped and live-verified. |
| `12-mcp-2x-migration.md` | Blocked upstream | **Keep.** Still blocked on `agent-framework-core`; deleting would lose the blocker record. |
| `13-azure-deployment-and-foundry.md` | proposed | **Keep.** The next objective. |
| `14-pre-azure.md` | proposed (6 of 8 done) | **Keep, mark done.** Its two open items (clip, benchmark) move into this plan; mark the file done and point at plan 20. |
| `15-build-and-release.md` | "in progress" | **Keep, mark shipped.** Shipped in v1.2.0; the status line is simply stale. |
| `16-dotnet-local-parity.md` | proposed | **Keep, mark done** once Wave 1 lands. |
| `17-tutorial-dotnet-coverage.md` | DONE | **Keep.** Already annotated with its outcome; it is the record of what the tests found. |
| `18-composer-ux.md` | proposed | **Keep, mark done** once Wave 5 lands. |
| `19-closing-out.md` | proposed | **Delete.** Wholly superseded by this plan, which folds in all five of its workstreams plus everything else. |
| `20-close-out.md` | this file | Keep. |

Net: twenty files down to nine, and every survivor has an accurate status line.

`remaining-work.md` stays as the index and is rewritten to point at the survivors. The
`audit-2026-08-25-adoption-and-azure.md` stays untouched — it is the reasoning behind most of this
work and is referenced from the docs site.

**Do this in the same PR as the rest of Wave 6**, so the deletions land next to the document
refresh that makes them true.

---

## Pull requests — three, not fifteen

Grouped by wave boundary, because each boundary is a point where the repo is coherent and
releasable. One PR for the whole thing would be unreviewable; one per item would be fifteen.

| PR | Waves | Contents |
|---|---|---|
| **1** | 1 + 2 | The defects. .NET stack works, both orchestration modes behave. **Folds in PR #71** (currently open, docs-only — its F1 diagnosis becomes the commit message context for the fix) |
| **2** | 3 + 4 | Measurements and writing: benchmark page, demo clip, .NET eval suite, Chapter 21, ADRs, the reported-vs-actual page |
| **3** | 5 + 6 | Composer UX and the three smaller features, then the document refresh and version bump |

PR #71 gets **closed in favour of PR 1** rather than merged separately — the diagnosis belongs
next to the fix it produced.

Each PR is a stacked branch off the previous one, so review can start on PR 1 while PR 2 is being
built. If a wave turns out larger than expected, it splits at the wave boundary rather than
accumulating.

---

## Verification

Run at the end of each wave, not just at the finish.

| Gate | Command |
|---|---|
| Python | `uv run --project agents/python pytest` |
| Tutorials (Python) | `PYTHONHASHSEED=0 pytest tutorials/ -m "not integration"` |
| Tutorials (.NET) | `dotnet test` across all 31 `*.Tests.csproj` |
| App (.NET) | `dotnet test agents/dotnet/ECommerceAgents.sln` |
| Web | `pnpm typecheck && pnpm lint && pnpm test && pnpm build` |
| Lint | `ruff check` |
| Contracts | `check_tutorial_readmes.py --check` · `check_tutorial_coverage.py --check` |
| Docs site | `python scripts/build_docs_site.py` |
| **Both stacks, live** | `scripts/e2e-both-stacks.sh` |

The last one is the gate that matters. Container health checks would not have caught F1 — every
container was healthy the entire time the stack was broken.

**Definition of done for `--dotnet`** (plan 16): login succeeds; a product question routes to
`product-discovery` with real catalogue data; an order question routes to `order-management` with a
real order; all five orchestration modes return real answers; `/`, `/login`, `/shop` all return 200;
a stale-volume start fails fast; the dual-backend Playwright suite passes.

---

## Exit criterion

`remaining-work.md` has **no unchecked boxes**, the adoption audit has no open findings outside
plan 13, and `README.md`'s "two complete, working backends" is true of `main` and demonstrated by a
passing dual-stack run.

At that point — and not before — start plan 13: Azure Container Apps and Microsoft Foundry.
