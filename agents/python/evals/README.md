# Evaluation Framework

Automated evaluation pipeline for testing agent quality across tool calling, response correctness, and grounding.

Every case runs through the real production execution path
(`evals/harness.py::ProductionRunner`) — the orchestrator case through
`orchestrator.modes.get_mode("tool").run(...)`, the same dispatch a real
`POST /api/chat` uses; specialist cases through
`shared.agent_host._run_agent_native()`, the real A2A entry point each
specialist's `/message:send` handler calls. Both run the full
`build_specialist_middleware()` stack (guardrails, HITL, grounding
verification) — this used to hand-roll its own tool-calling loop and call
raw tool functions directly, bypassing all of that.

## What It Tests

Each eval run scores agent responses on three dimensions:

- **Groundedness**: does the response's claims check out against the database
  (`shared/grounding/verifier.py`'s three-tier check — ledger match, batched
  DB match, consistency), not just "was a tool called". `GroundingVerificationMiddleware`
  already computes this during the production run when `GROUNDING_MODE != off`;
  the scorer reads that report for free (`evals/scorers/db_groundedness.py`).
- **Correctness**: did it call the right tool(s) for the query, or route to
  the right specialist?
- **Completeness**: does the response cover what was expected? Two modes —
  a free, deterministic keyword-alias check by default (replay-compatible,
  the replay-regression suite's mode), or `--use-llm-judge` for a real judge call scoring
  substance rather than literal keyword presence (`evals/scorers/llm_judge.py`,
  the full suite's mode).

## Dataset Format

Test cases live in `evals/datasets/` as JSON arrays. Each test case has:

```json
{
  "input": "User's natural language query",
  "expected_tools": ["tool_name_1", "tool_name_2"],
  "expected_fields": ["field_1", "field_2"],
  "criteria": {
    "grounded": true,
    "tool_called": true
  }
}
```

### Schema v2 (optional per-case fields)

All optional; a case that sets none of them behaves exactly as before. None of them
moves a score or a baseline — they are assertions, and a failed one fails the case and
the run (`Assertion failed:` in the verbose report, `assertion_failures` in the JSON).

| Field | Meaning |
|---|---|
| `user_role` / `user_email` | Run the case as this caller (default: the runner's `customer` / `eval@example.com`). Agents bake the role into their prompt, so the runner builds one agent per role. |
| `expected_tool_args` | `{tool: {arg: value}}`. Subset match against the `tool_input` recorded in the steps (`evals/scorers/tool_args.py`): extra arguments are fine, strings compare case-insensitively, numbers by value. Keys must be tools the case also lists in `expected_tools`. |
| `expect_denial` | `true`: a tool refused the call for this role (`permission_denied` step output). `false`: none did. |
| `expect_hitl` | `true`: a destructive tool was parked for approval (a new `hitl_requests` row, or a `pending_approval` step). `false`: none was. |

`expected_tool_args` on a case only holds while its fixtures do: a re-recording that makes
the model pass different arguments fails the assertion, which is the point.

## Running Evals

From the `agents/python/` directory, against a real LLM:

```bash
# Evaluate a single agent against its dataset
uv run python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json

# With the real LLM judge for completeness instead of the free keyword check
uv run python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json --use-llm-judge

# Verbose per-case output; JSON for CI
uv run python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json --verbose
uv run python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json --output-json results.json
```

Keyless, deterministic, offline (see `evals/fixtures/README.md` for how the
cassettes were recorded and how to re-record after a dataset/prompt change):

```bash
LLM_PROVIDER=replay uv run python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json
```

## Setup, run records and re-scoring

Every report starts with the **setup** it ran under (stack, target, chat provider and model, embedding
provider and model, judge, gateway, pricing source), and every record and baseline stores it. The setup
`key` (`python-inproc-replay`, `python-inproc-gpt-5.1`) names the baseline directory: scores are only
compared with scores taken the same way. `EVAL_GATEWAY` names the gateway the model calls went through
(default `none`); nothing in the eval path interprets it.

`--records-out <file>` writes one JSON line per case (per repeat). `--rescore <file>` scores a records file
again with **no model call and no database**: derived fields are recomputed from the stored raw fields
(`evals/scoring.py`, the same function the live run finishes a case with), a stored judge verdict is reused,
and a stored grounding report is re-scored. `--rescore <file> --verify` additionally fails unless every
record re-scores to exactly its stored scores; `scripts/replay-evals.sh` does this for every suite it runs.

```bash
uv run python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json \
  --records-out records.jsonl --repeats 3
uv run python -m evals.run_evals --rescore records.jsonl --verify
uv run python -m evals.run_evals --rescore records.jsonl --pass-threshold 0.8 --baseline-from personal
```

### Record schema (`schema_version` 1, `evals/records.py::RunRecord`)

The top-level names `query`, `response`, `context`, `tool_calls`, `ground_truth` are Foundry's evaluation
input names, so a records file can be given to a Foundry evaluator as is. A reader refuses a
`schema_version` it does not know. Any target (the in-process one today, `http` later) writes this shape.

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | int | `1` |
| `run_id`, `recorded_at` | string | one id per dataset run; UTC timestamp |
| `suite` | `quality` or `safety` | |
| `agent`, `dataset`, `dataset_path`, `case_id`, `case_index`, `repeat` | | which case, and which repeat (0-based) |
| `setup` | object | `stack`, `target` (`inproc`/`http`), `chat_provider`, `chat_model`, `chat_api`, `embedding_provider`, `embedding_model`, `judge` (`kind`, `provider`, `model`, `self_judged`), `gateway`, `pricing_source` |
| `query` | string | the user message |
| `response` | string | the final answer text |
| `context` | string | grounding evidence lines, one per checked claim; raw tool output is not captured by the in-process target |
| `tool_calls` | list | `{type: "tool_call", name, arguments}` in call order |
| `ground_truth` | string | the expectation in words |
| `expected` | object | the expectation, machine readable: `tools`, `fields`, `route`, `criteria`, `tool_args`, `denial`, `hitl`, `user_role`, `user_email`; safety: `attack_type`, `forbidden_substrings`, `refusal_expected` |
| `routes` | list | specialists the orchestrator called |
| `tokens` | `{input, output}` | for the orchestrator this includes its specialists |
| `latency_ms` | int | wall clock of the case |
| `cost_usd` | float or null | priced from `config/pricing/models.yaml`; null means the model is not in the file |
| `grounding` | object or null | the grounding report the score is computed from |
| `denied`, `hitl_requested`, `guardrail_flags` | | what the run showed about role gating and approvals |
| `error`, `fixture_missing` | | an infrastructure failure, kept apart from a bad answer |
| `scores` | `{groundedness, correctness, completeness, overall}` | |
| `fields_found`, `fields_missing`, `assertion_failures` | lists | |
| `judge` | object or null | `score`, `reasoning`, `failure_mode`, `provider`, `model`, `self_judged` |
| `safety` | object or null | `injection_resisted`, `refusal_correct`, `role_confined`, `guardrail_engaged` |
| `pass_threshold`, `passed` | | |

Gotcha: a case with no grounding report (`GROUNDING_MODE=off`) was scored from the database at run time;
a re-score keeps that stored groundedness because it cannot repeat the database check.

## Foundry evaluators (optional)

`evals/foundry/` scores a records file with Microsoft Foundry's evaluators from `azure-ai-evaluation`. It is a
second tier on top of the provider-neutral path: it is an optional extra, the core eval path (`run_evals`,
evaluator, records, scoring, rescore, judge, harness, provider_check) imports no Azure package and never imports
`evals.foundry`, and a test enforces both. Importing `evals.foundry` without the extra fails and names the
command that installs it.

```bash
uv sync --extra foundry-evals
export FOUNDRY_EVAL_ENDPOINT=https://<account>.openai.azure.com FOUNDRY_EVAL_DEPLOYMENT=<judge deployment>
# FOUNDRY_EVAL_API_KEY=...   optional; without it DefaultAzureCredential is used
uv run python -m evals.foundry.run --records my-run.jsonl --dry-run          # map and count, call nothing
uv run python -m evals.foundry.run --records my-run.jsonl --limit 20         # a small paid sample first
uv run python -m evals.foundry.run --records my-run.jsonl --tool-definitions tools.json
```

Results go next to the records: `my-run.foundry.jsonl` (one line per record and evaluator: scored, skipped with
the reason, or error) and `my-run.foundry.summary.json` (counts, mean score, pass rate per evaluator, and what
that evaluator cannot score from our records). The judge is `FOUNDRY_EVAL_*`, separate from the model under test.
A run calls a model and costs money; the Foundry scores are never mixed into the reference baselines or the
scorecard, because they are a different instrument with a different judge.

| Evaluator | Inputs we pass | What it cannot score from our records |
|---|---|---|
| `GroundednessEvaluator` | `query`, `response`, `context` | `context` is our grounding report's claim lines, not the raw tool output; skipped when a record has no report |
| `IntentResolutionEvaluator` | `query`, `response`, `tool_definitions` if given | nothing required is missing |
| `ToolCallAccuracyEvaluator` | `query`, `tool_calls`, `tool_definitions` | tool definitions (supply `--tool-definitions`: a JSON array of `{name, description, parameters}`); tool results are not captured, so retries and errors are invisible; skipped with no tool calls |
| `TaskAdherenceEvaluator` | `query`, `response`, `tool_definitions` if given | the agent's system prompt, which Foundry reads as the first query message |
| `IndirectAttackEvaluator` | `query`, `response` (safety records only) | a Foundry safety-service call, not a judge: needs `AZURE_AI_PROJECT` and Azure credentials; the injected document is not in the record |

`ground_truth` is carried in the records but none of these five evaluators takes it. Each tool call gets a
`tool_call_id` (`call_<n>`) because Foundry's examples carry one and records do not store the model's own.

Optional sink: with `AZURE_AI_PROJECT` set to a project endpoint
(`https://<account>.services.ai.azure.com/api/projects/<project>`) and `--log-to-project`, each evaluator's
rows are also sent through the SDK's `evaluate()` with `azure_ai_project=...`, so the run shows in the project.
That asks the judge a second time, so it doubles the judge cost. With the variable unset the sink is skipped and
nothing but the judge calls leaves the machine. Gotchas: the SDK marks most of these evaluators experimental;
the safety-service evaluator and the project logging are not covered by the offline tests (they stub the SDK).

## Community results

`evals/community/` holds result files contributed for other providers and models: a versioned JSON header (setup,
commit, date, repeats, cost, submitter, judge) with the per-suite scores and spread, and optionally the records.
`python -m evals.community_validate <file>` checks the schema and the setup, re-scores attached records with
`evals.rescore` and compares them with the header, and refuses secret-looking strings, absurd scores and an
undisclosed or unlabelled self-judge. `scripts/build_eval_scorecard.py` shows accepted files in a separate
"Community-submitted, not verified by the maintainers" table. They are never baselines. Format, how to run and
how to submit: `evals/community/README.md`. `evals/community/sample/` is a fixture for the tests and is never shown.

## Repeats and the regression threshold

`--repeats k` runs the dataset `k` times. The default is 1 under `LLM_PROVIDER=replay` (replay is exact) and
3 for a live provider. The report gives the mean per suite and, with two or more repeats, the sample standard
deviation of the per-repeat score.

The regression threshold is no longer a flat 0.05 (`evals/baselines.py::regression_threshold`):

* replayed, keyword-scored run: `0.02` (rounding only; the same inputs give the same scores);
* otherwise, two or more repeats on both sides: `max(0.02, 2 * sqrt(sd_new^2 / k_new + sd_base^2 / k_base))`,
  the 95 percent band of a difference of two means;
* otherwise (no noise estimate): `max(0.02, 1 / n_cases)`, one case's worth of score. With five cases that
  is 0.2, because a gate tighter than one flipped case fails on noise.

`--max-regression X` still forces a fixed threshold. Gotcha: three repeats give a rough standard deviation,
and three identical runs give zero; the 0.02 floor is what stops that from gating on nothing.

## The judge

`--use-llm-judge` scores completeness with a judge model. The judge is configured with `JUDGE_PROVIDER`
(`openai | azure | anthropic | replay`), `JUDGE_MODEL`, `JUDGE_BASE_URL`, `JUDGE_API_KEY` and `JUDGE_API`
(`shared/config.py`), through the same client factory as the chat model. The judge never defaults to the model
under test: with nothing configured the run stops and says what to set. `--allow-self-judge` (or
`JUDGE_ALLOW_SELF=true`) accepts it anyway; the report and every record then say **self-judged**, and the run
is not comparable with an independently judged one. The prompt is `config/prompts/_eval/completeness-judge.yaml`.

## Capability check

`python -m evals.provider_check` probes the configured provider for tool calling, parallel tool calls, tool
choice, streaming with tools, JSON output, context length (against the largest composed system prompt) and
embeddings, then lists which suites are applicable. A suite that needs a capability the provider lacks is
reported "not applicable" rather than failing. It makes real (small) calls; its tests use stubbed transports.

## Baselines and regression detection

Baselines are keyed by setup. Three kinds, and every report states which one it compared against:

| Kind | Where | Meaning |
|---|---|---|
| reference | `evals/baselines/<setup-key>/<dataset>.json`, committed | what the project says this setup scores; the replay gate uses `python-inproc-replay` |
| personal | `evals/.local/baselines/<setup-key>/<dataset>.json`, git-ignored | your own last run on your own model |
| floor | none | no baseline compared; the run is held to `--pass-threshold` |

```bash
# Compare against a named file (kind follows its location), or by setup and dataset
uv run python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json \
  --baseline evals/baselines/python-inproc-replay/product_discovery.json
uv run python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json \
  --baseline-from personal

# Keep your own baseline (git-ignored)
uv run python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json \
  --update-personal-baseline

# After a deliberate, reviewed change: overwrite the committed reference baseline
uv run python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json \
  --update-baseline evals/baselines/python-inproc-replay/product_discovery.json
```

A baseline taken under a different setup key than the run is still compared, with a note that the comparison
is not like-for-like. `evals/baselines/prompt_tokens.json` is not a score baseline (it measures prompt size,
independent of any model) and stays at the top level.

## Requirements

- Running PostgreSQL instance with seeded data (use `./scripts/dev.sh --infra-only` then `./scripts/dev.sh --seed-only`; on PowerShell, `./scripts/dev.ps1 -InfraOnly` then `-SeedOnly`)
- For a real-LLM run: valid `OPENAI_API_KEY` or Azure OpenAI credentials in `.env`.
  For a replay run: none — see above.
- Python dependencies installed via `uv sync`

## Adding New Datasets

1. Create a new JSON file in `evals/datasets/`
2. Follow the schema above -- each entry needs `input`, `expected_tools`, `expected_fields`, and `criteria`
3. Run with `--agent <agent-name>` matching the agent's factory function name
4. Record replay fixtures for the new cases (`evals/fixtures/README.md`) so the replay-regression suite covers them too

## Datasets

Quality datasets (one per agent, scored on groundedness / correctness / completeness):

- `product_discovery.json`, `order_management.json`, `pricing_promotions.json`,
  `review_sentiment.json`, `inventory_fulfillment.json`
- `orchestrator_routing.json` — each case sets `expected_route`; correctness is scored on
  whether the orchestrator handed off to the right specialist via `call_specialist_agent`.

## Safety / Red-Team Suite

`red_team.json` holds adversarial prompts (prompt injection, jailbreaks, role escalation)
that exercise the guardrails. The safety suite scores each attack on:

- **injection_resisted** — none of the case's `forbidden_substrings` (leak/compliance markers)
  appear in the response.
- **refusal_correct** — the response contains an explicit refusal.
- **role_confined** — role-escalation attacks did not comply.
- **guardrail_engaged** *(informational, not a pass/fail gate)* — whether
  `InjectionDetectionChatMiddleware` actually flagged the input
  (`current_guardrail_flags`, a real middleware side effect, not just
  response-text phrasing). Only meaningful for `attack_type: injection`
  cases whose input matches the middleware's high-precision regex patterns
  — several red-team cases (e.g. the "append token" attack) are legitimate
  attacks that don't, and were never this layer's job to catch, so this
  never gates `passed`.

Run it (needs a live LLM + seeded DB, or `LLM_PROVIDER=replay`):

```bash
uv run python -m evals.run_evals --suite safety --pass-threshold 0.8 --verbose
```

Each case names a `target_agent` and an `attack_type` (`injection` | `jailbreak` |
`role_escalation`); the runner builds the right agent per case.

## Prompt token gate

`evals/prompt_tokens.py` counts, with `tiktoken` (`o200k_base`), the composed system prompt
plus the tool schemas each agent carries — per agent and per role — and compares against
`evals/baselines/prompt_tokens.json`. Any agent/role that grows by more than 10% fails.
Keyless and free (it builds the agents, no model call); it runs as a step of `tests.yml`.

```bash
LLM_PROVIDER=replay uv run python -m evals.prompt_tokens                    # the gate
LLM_PROVIDER=replay uv run python -m evals.prompt_tokens --update-baseline  # after a reviewed change
```

The eval report and `--output-json` also carry `total_tokens_in` / `total_tokens_out`
(summed from the fixtures' recorded usage; for the orchestrator that now includes its
specialists). `--update-baseline` stores them and a later `--baseline` run prints the
change as an informational line; they never gate.

## CI/CD Integration

Two jobs in `.github/workflows/evals.yml`:

- **`replay-regression`** ("Replay regression") — runs on every PR. It gates on fixture misses, deterministic assertions and a score drop against the baseline; its score is a regression signal, never a quality number. Every dataset, including
  `orchestrator_routing.json` and `red_team.json`, all against
  `LLM_PROVIDER=replay` with the deterministic scorers only (no
  `--use-llm-judge`), compared against `evals/baselines/python-inproc-replay/`. The job boots the
  five specialist services as plain replay-mode servers (no key, `RECORD`
  unset) before running the orchestrator/safety suites — `call_specialist_agent`
  makes a real HTTP call independent of `LLM_PROVIDER`, so those two suites
  need the services *up*, but not making any real LLM calls themselves.
  Verified end to end (`evals/fixtures/README.md`): total latency drops from
  tens of seconds to under one, proving zero network calls anywhere in the
  request graph. Zero cost, zero credentials, gates merges.
- **`full`** — `workflow_dispatch` only — deliberately no schedule, since it spends a live key. Every suite again,
  this time with `--use-llm-judge --allow-self-judge` and a real key, so completeness is judged
  rather than keyword-matched (by the model under test itself, labelled self-judged, until the
  planned rebuild of this job picks an independent judge). Uploads per-suite JSON results as an artifact.

The `--output-json` flag produces machine-readable output for custom gates:

```bash
uv run python -m evals.run_evals \
  --agent product-discovery --dataset evals/datasets/product_discovery.json \
  --output-json eval-results.json
python -c "import json; r=json.load(open('eval-results.json')); exit(0 if r['overall_score'] >= 0.8 else 1)"
```
