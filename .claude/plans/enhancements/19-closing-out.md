# Plan 19 — Closing out v1.2.x: the five remaining workstreams

**Status:** proposed · **Date:** 2026-08-26 · **Target:** v1.2.1 (items 1–2), v1.3.0 (items 3–5)
**Parent:** [`../audit-2026-08-25-adoption-and-azure.md`](../audit-2026-08-25-adoption-and-azure.md)
**Index:** [`../remaining-work.md`](../remaining-work.md)

Plans 16 and 18 already cover two of these five in detail. This plan does not repeat them — it
**sequences all five**, and adds the missing plan for the one workstream that has none: the two
orchestration-mode defects found by running v1.2.0.

---

## The five, and why this order

| # | Workstream | Has a plan? | Blocks |
|---|---|---|---|
| 1 | **Production .NET — plan 16 F1–F5** | [16](16-dotnet-local-parity.md), merged | Any release claiming two working backends |
| 2 | **Two orchestration-mode defects** | **none — §2 below** | Item 3 |
| 3 | **Publish the orchestration benchmark** | harness ships, no result | The audit's highest-value adoption item |
| 4 | **Record the demo clip** | spec ships, no recording | README still opens with a static PNG |
| 5 | **Composer UX (#4)** | [18](18-composer-ux.md), merged | — |

**The one reordering worth arguing about:** item 2 moves ahead of item 3, which is not the order
the index implies. The benchmark measures all five modes against the same prompt. Two of those
modes are currently misbehaving in ways that would dominate the table — `handoff` by an order of
magnitude on both axes, `workflow:pre-purchase` by silently answering from half its inputs.
Publishing that table first means either publishing numbers we know are wrong, or re-running the
whole benchmark (~$1, ~25 min, real money) after fixing them. Fix first, measure once.

Items 4 and 5 are independent of everything else and of each other.

---

## 1. Production .NET — plan 16

Plan 16 is merged and detailed; nothing to re-plan. What matters here is that **it is a plan with
no code**, and it holds the only P0 in the repo.

- **F1 (P0):** `CallSpecialistAgent` never receives `agentName`, so no specialist is reachable and
  `agents_involved` is `['orchestrator']` on every turn. The stack builds, twelve containers
  report healthy, the UI serves, login works, and every question fails.
- **F2 (P1):** a stale volume drops `search_vector`, and search fails with a friendly lie.
- **F3–F5 (P2/P3):** .NET Dockerfiles skip `Directory.Build.props`; both compose files bind the
  same ports; the .NET stack builds `seeder` and `auth-server` from the *Python* Dockerfile.

**Do not skip plan 16's own instruction:** capture the raw tool-call payload before changing code.
The mechanism is not proven, and "add `agentName` to the signature" is a guess until the payload
shows what MAF is actually sending.

**Exit:** `./scripts/dev.sh --dotnet` answers a routed question with `agents_involved` naming a
real specialist, and `scripts/e2e-both-stacks.sh` passes against both stacks.

---

## 2. The two orchestration-mode defects — NEW

Both were measured against a live v1.2.0 stack. Neither has an owner. I read the source for this
plan; the diagnoses below are **grounded in code but not yet reproduced under a debugger**, and
each says explicitly what would confirm or refute it.

### 2a. `handoff` returns 19–25k characters in 100–200 s

Against ~11 s and ~1,000 characters for `tool` on the same prompt. An order of magnitude on both
axes.

**Where to look:** `agents/python/orchestrator/modes/handoff_mode.py`, the `turns` accumulator.

```python
if current_speaker != executor_id:
    current_speaker = executor_id
    turns.append((executor_id, []))
update = getattr(event, "data", None)
text = getattr(update, "text", None) if update is not None else None
if text:
    turns[-1][1].append(text)
...
assembled = [(eid, "".join(parts).strip()) for eid, parts in turns if any(parts)]
final_text = assembled[-1][1] if assembled else ""
```

**Hypothesis (untested):** `AgentResponseUpdate.text` is being treated as an incremental delta,
and on this event stream it is **cumulative**. Joining cumulative snapshots gives
`c1 + (c1+c2) + (c1+c2+c3) + …` — quadratic growth that turns a ~1,000-character answer into
~20,000 without any single component looking wrong. The module docstring says this extraction
"mirrors `tutorials/14-handoff-orchestration/python/main.py::ask()`", so if the hypothesis holds,
**the tutorial has the same bug** and both fix together.

**Cheapest confirmation, before touching anything:** log `len(text)` per update for one turn. Deltas
give a flat-ish sequence; cumulative snapshots give a monotonically increasing one. That single
log line decides it.

**If refuted,** the next candidate is the mesh genuinely looping — many alternations, each a real
LLM call — which would explain the latency but *not* a 20k single turn, since `final_text` is only
the last speaker's turn. Latency and length may therefore have two different causes; do not assume
one fix addresses both.

**Exit:** `handoff` on the benchmark prompt returns a response length within the same order of
magnitude as `tool`, or the plan records why it legitimately does not.

### 2b. `workflow:pre-purchase` answers from half its inputs, silently

Four executors run — `reviews`, `stock`, `price_history`, `shipping` — and the reply is
48 characters: `"Stock: 348 units available | Price trend: stable"`.

**Correction to [`remaining-work.md`](../remaining-work.md):** it records this as "the synthesis
throws away reviews and shipping entirely." **That is wrong, and I wrote it.** `_build_recommendation`
does not discard anything — every line is guard-claused on data being present:

```python
if state.reviews.get("sentiment"):   parts.append(f"Reviews: …")
if state.shipping.get("options"):    parts.append(f"Shipping: …")
```

So the observed output means `reviews.sentiment` and `shipping.options` were **both empty**. The
fan-out is real and the synthesis is faithful; the *inputs* are missing. That is a materially
different bug, in a different file, and chasing the synthesis would have wasted the day.

**The actual defect is that every failure path here is silent.** Three of them:

1. **A missing tool is a no-op.** Every executor is `fn = self._tools.get(...)` followed by
   `if fn:` with no `else`. A tool absent from the registry produces no error, no log, no entry in
   `completed_steps` — indistinguishable from a tool that ran and found nothing.
2. **`state.errors` is collected and never read.** Executors append to it on exception;
   `_build_recommendation` never looks at it. A workflow can fail two of four probes and still
   return a confident sentence.
3. **`_merge_states` does not merge `shipping`** — which is *correct*, because shipping runs
   after the barrier in `_MergeAndShipExecutor`. Worth noting only so the next reader does not
   "fix" it: the docstring says "three partial states" and means it.

Shipping additionally sits behind `if merged.stock.get("in_stock")`. The observed run reported
348 units, so stock was in, so shipping *should* have run — meaning `estimate_shipping` was either
absent from `tools` or returned no `options`.

**Work:**

- Reproduce against a live stack and capture `state.errors` and `completed_steps` alongside the
  recommendation. That distinguishes all three causes in one run.
- Make the silent paths loud: log a warning when a named tool is absent, and surface `errors` /
  `completed_steps` on the workflow result so a partial answer is *visibly* partial.
- Only then decide whether the recommendation string itself should say what it could not check.
  A 48-character answer that admits two probes failed is honest; one that quietly omits them is
  not, and that is the actual user-facing harm.

**Exit:** a pre-purchase run with a dead probe reports it, and the four-executor fan-out is
either producing four contributions or explaining which it could not.

---

## 3. Publish the orchestration benchmark

`agents/python/evals/benchmark_modes.py` ships in v1.2.0 and is verified working. It drives
`POST /api/chat` rather than calling modes in-process, so it exercises auth, guardrails,
sanitization, grounding and usage logging — the real path.

**Nothing has been published.** The first full run measured a broken build through a tripped rate
limiter and was discarded rather than published, which was the right call and left a gap.

This is the audit's **highest value-per-day** item: an LLM asked "which orchestration pattern
should I use?" has nothing from this repo to cite, and `chatgpt.com` is already the #1 referrer.

**Work:**
1. Land items 2a and 2b first (see the reordering argument above).
2. Re-run against v1.2.0 images with pacing: `--reps 3`, default `--delay 8` (~60 calls, ~$1,
   ~25 min). Real money, cannot run under `LLM_PROVIDER=replay` — replay returns instantly, which
   makes latency meaningless.
3. Write `docs/orchestration-benchmark.md`: latency, tokens, cost and response length per mode,
   the prompt set, the date, the model, and the commit. A benchmark without its conditions is an
   anecdote.
4. Register it in `scripts/build_docs_site.py`'s `SECTIONS` so it reaches the site and `llms.txt`.
5. Report "not captured" where a mode logs no usage rows — the harness already distinguishes this
   from zero, and the write-up must preserve the distinction.

**Exit:** the page is live, and the README's orchestration section links to it.

---

## 4. Record the demo clip

`web/e2e/demo-recording.spec.ts` ships and typechecks. Nothing has been recorded, so the README
still opens with a static PNG.

**Re-check the prompts before recording.** The spec's header warns that `search_products` did
ILIKE `'%<whole phrase>%'`, so it hard-codes "Allbirds" — a literal substring of a seeded product.
**v1.2.0 replaced that with Postgres FTS**, so the constraint the prompt was written around no
longer applies, and the prompts should be re-verified against the current catalogue rather than
assumed still-good. A natural-sounding prompt that returns "I couldn't find any" is the worst
possible first impression, and it is now more likely, not less.

**Work:** run the stack with a live key, execute the spec, convert
(`ffmpeg -i video.webm -c:v libx264 -crf 24 -pix_fmt yuv420p demo.mp4`), and swap the README's PNG.
Keep the clip in the repo so it can be re-recorded after UI changes rather than decaying.

**Exit:** the README opens with the clip; the six beats in the spec's header are all visible.

---

## 5. Composer UX (#4) — plan 18

Plan 18 is merged and detailed. One thing from it is worth repeating here because getting it wrong
is silent and expensive:

**There are two mode surfaces.** `AGENT_MODES` in `web/src/components/ui/ai-prompt-box.tsx` and the
orchestration `mode-switcher.tsx` from Phase 1.6a. Issue #4 asks to "collapse the mode selector by
default" — collapsing the *orchestration* one hides the headline feature this repo is built around,
and the one the demo clip in item 4 is scripted to show. Confirm which the issue means before
touching either.

Plan 18 also argues against an LLM call for contextual suggestions: it would add latency and cost
to every turn and need an endpoint the frontend-only constraint forbids. The frontend already
classifies message shape to render cards — reuse that.

---

## Sequencing

```
now ──► #70 merges (tutorial .NET, done)
         │
         ├── 1. plan 16 F1 ──────────────► v1.2.1   [P0, blocks the parity claim]
         │
         ├── 2. mode defects 2a + 2b ──┐
         │                             ├──► 3. benchmark ──► v1.3.0
         │                             │
         ├── 4. demo clip ─────────────┘  (independent)
         │
         └── 5. composer UX (#4) ──────────► v1.3.0  (independent)
```

Items 1, 4 and 5 are mutually independent and can run in any order or in parallel. Only 2 → 3 is a
hard dependency.

## Not in scope, and why

Carried from [`remaining-work.md`](../remaining-work.md) so they are not rediscovered as gaps:

- **Chapter 21, Capstone Tour** — two `.gitkeep` files. The bridge from 34 tutorials to the running
  app, and a genuine missing rung. Deferred because it is a writing task with no defect behind it.
- **`docs/adr/`** — five decisions already argued in prose and recorded nowhere a reader would look.
- **Promoting the "reported vs actual" table** onto the docs site.
- **No .NET images** — accepted, deliberate. The demo path stays Python-only; `--dotnet` remains a
  build-from-source path.
