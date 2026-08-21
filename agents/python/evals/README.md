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
  the smoke suite's mode), or `--use-llm-judge` for a real judge call scoring
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

## Baselines and regression detection

```bash
# Compare this run against a stored baseline; fail if any tracked score drops > --max-regression
uv run python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json \
  --baseline evals/baselines/product_discovery.json --max-regression 0.05

# After a deliberate, reviewed change — overwrite the stored baseline
uv run python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json \
  --update-baseline evals/baselines/product_discovery.json
```

## Requirements

- Running PostgreSQL instance with seeded data (use `./scripts/dev.sh --infra-only` then `./scripts/dev.sh --seed-only`)
- For a real-LLM run: valid `OPENAI_API_KEY` or Azure OpenAI credentials in `.env`.
  For a replay run: none — see above.
- Python dependencies installed via `uv sync`

## Adding New Datasets

1. Create a new JSON file in `evals/datasets/`
2. Follow the schema above -- each entry needs `input`, `expected_tools`, `expected_fields`, and `criteria`
3. Run with `--agent <agent-name>` matching the agent's factory function name
4. Record replay fixtures for the new cases (`evals/fixtures/README.md`) so the smoke suite covers them too

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

## CI/CD Integration

Two jobs in `.github/workflows/evals.yml`:

- **`smoke`** — runs on every PR. Every dataset, including
  `orchestrator_routing.json` and `red_team.json`, all against
  `LLM_PROVIDER=replay` with the deterministic scorers only (no
  `--use-llm-judge`), compared against `evals/baselines/`. The job boots the
  five specialist services as plain replay-mode servers (no key, `RECORD`
  unset) before running the orchestrator/safety suites — `call_specialist_agent`
  makes a real HTTP call independent of `LLM_PROVIDER`, so those two suites
  need the services *up*, but not making any real LLM calls themselves.
  Verified end to end (`evals/fixtures/README.md`): total latency drops from
  tens of seconds to under one, proving zero network calls anywhere in the
  request graph. Zero cost, zero credentials, gates merges.
- **`full`** — `workflow_dispatch` + a weekly `schedule:`. Every suite again,
  this time with `--use-llm-judge` and a real key, so completeness is judged
  rather than keyword-matched. Uploads per-suite JSON results as an artifact.

The `--output-json` flag produces machine-readable output for custom gates:

```bash
uv run python -m evals.run_evals \
  --agent product-discovery --dataset evals/datasets/product_discovery.json \
  --output-json eval-results.json
python -c "import json; r=json.load(open('eval-results.json')); exit(0 if r['overall_score'] >= 0.8 else 1)"
```
