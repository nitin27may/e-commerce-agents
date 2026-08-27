# Orchestration mode benchmark

The same question, routed five ways, with numbers attached.

This repository's thesis is that one domain can be orchestrated five different ways and
that the choice has real consequences. That claim has been asserted here for a long time
and never measured. This page is the measurement.

## Conditions

Everything needed to reproduce or to distrust it:

| | |
|---|---|
| **Model** | `gpt-4.1` (Azure OpenAI) |
| **Stack** | Python backend, local Docker Compose |
| **Prompts** | 4 (product search, order status, pre-purchase advice, return request) |
| **Repetitions** | 2 per mode per prompt — 40 requests total |
| **Pacing** | 8 s between requests; the chat routes sit behind a Redis sliding-window limiter and firing back-to-back measures 429s instead of orchestration |
| **Measured** | 2026-08-27, commit `b53d20b` |
| **Harness** | [`agents/python/evals/benchmark_modes.py`](https://github.com/nitin27may/e-commerce-agents/blob/main/agents/python/evals/benchmark_modes.py) |

The harness drives `POST /api/chat` rather than calling modes in-process, so every run
passes through auth, guardrails, sanitization, grounding and usage logging — the real
path, not a copy of it. It cannot run under `LLM_PROVIDER=replay`: fixtures return
instantly, which makes latency meaningless.

## Results

| Mode | p50 | p95 | Response | LLM calls | What ran |
|---|---|---|---|---|---|
| `tool` | 10.6 s | 21.4 s | 878 chars | orchestrator + specialist | orchestrator, order-management, product-discovery |
| `handoff` | 5.1 s | 7.2 s | 970 chars | triage + specialist | order-management, product-discovery |
| `group-chat` | 3.2 s | 10.8 s | 110 chars | 2 panelists + moderator | value, quality, moderator |
| `workflow:pre-purchase` | 0.26 s | 0.28 s | 127 chars | **none** | reviews, stock, price_history, shipping |
| `workflow:return-replace` | 0.10 s | 0.11 s | 82 chars | **none** | check_eligibility |

## Read the last two rows carefully

**The workflow modes make no LLM call at all.** They are deterministic graphs over tool
calls, and their "recommendation" is a formatted string, not generated prose. Comparing
0.26 s against `tool`'s 10.6 s and concluding that workflows are forty times faster would
be wrong — they are doing different work. What the number honestly says is: *when the
answer can be assembled from tool output without a model, it costs milliseconds.*

That is a genuinely useful result. It is not a latency win over `tool` mode; it is an
argument for noticing when you did not need a model.

**`workflow:return-replace` only reached `check_eligibility`.** The seeded order used by
the return prompt is in `shipped` status, and returns require `delivered` — so the
workflow correctly refused and stopped at its first gate. That 0.10 s measures a
rejection, not a return flow. The number is real; it is not representative.

## Tokens and cost are mostly absent, deliberately

Only `tool` mode logged usage rows: **7,106 tokens and $0.0167 per run at gpt-4.1 rates**.
Every other mode reports *not captured*, which the harness distinguishes from zero because
they are very different claims.

The gap is real: modes that stream through MAF workflow events do not currently write
`usage_logs` rows the way the tool router does. Reporting them as `$0.00` would have made
this table look complete and been a lie. Closing it is tracked as its own piece of work.

## What actually changed while measuring this

Two of these five modes were broken when the benchmark was first attempted, and the
attempt is what proved it.

**`handoff` was 100–200 s and 19,000–25,000 characters per turn.** It is now 5.1 s and 970
characters — the fastest LLM-backed mode here. The cause was not performance: the mesh's
start agent was the *tool-router* orchestrator, carrying `call_specialist_agent` and a
prompt naming it, so it never called a handoff tool. MAF's autonomous mode then fed it a
continuation prompt and re-ran it, up to a 50-turn default. Measured before the fix: 5,403
streamed updates, no specialist ever invoked.

**`workflow:pre-purchase` returned 48 characters** from a four-executor fan-out:
`Stock: 348 units available | Price trend: stable`. The fan-out was real; the synthesis
read field names its own tools never returned (`sentiment` for `overall_sentiment`,
`options` for `shipping_options`). Every line was guard-claused, so two of four
contributions vanished with no error, on every run since the workflow was written. It now
returns all four:

```
Reviews: very_positive (4.7/5 avg) | Stock: 203 units available |
Price trend: stable | Shipping: from $5.99, 5-7 business days
```

Publishing the first run's numbers would have shipped a table describing two broken modes
as if they were design characteristics.

## Choosing a mode

- **`tool`** — the default, and the right one when the orchestrator should compose the
  answer. Most expensive per turn because the orchestrator round-trips.
- **`handoff`** — when a specialist should own the answer outright. Cheaper and faster than
  `tool` precisely because nobody re-writes the specialist's reply.
- **`group-chat`** — when several fixed perspectives must react to each other before a
  verdict. Cost scales with panelists.
- **`workflow:*`** — when the shape of the work is known in advance. No model, no
  variance, milliseconds — and no ability to handle anything the graph did not anticipate.

## Reproduce it

```bash
./scripts/dev.sh                         # Python stack, real LLM key in .env
cd agents/python
uv run python -m evals.benchmark_modes --reps 2 --delay 8
```

Results land in `agents/python/evals/results/` as timestamped JSON, with the commit
recorded. Costs real money — roughly $0.50 for the run above.

## Related

- [Orchestration patterns](concepts/06-orchestration-patterns.md) — what each mode *is*
- [Reported vs actual](reported-vs-actual.md) — how both defects above were found
- [Chapter 21 — Capstone Tour](https://nitinksingh.com/e-commerce-agents/tutorials/21-capstone-tour/) — where each mode lives in the code
