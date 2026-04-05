# AgentBazaar Evaluation Framework

Automated evaluation pipeline for testing agent quality across tool calling, response correctness, and grounding.

## What It Tests

Each eval run scores agent responses on three dimensions:

- **Groundedness**: Did the agent call tools instead of hallucinating? Did it stay within its knowledge boundary?
- **Correctness**: Did it call the right tool(s) for the query? Were tool parameters reasonable?
- **Completeness**: Does the response contain all expected fields (e.g., price, name, status)?

## Dataset Format

Test cases live in `evals/datasets/` as JSON arrays. Each test case has:

```json
{
  "input": "User's natural language query",
  "expected_tools": ["tool_name_1", "tool_name_2"],
  "expected_fields": ["field_1", "field_2"],
  "criteria": {
    "grounded": true,
    "max_price_respected": true,
    "tool_called": true
  }
}
```

## Running Evals

From the `agents/` directory:

```bash
# Evaluate a single agent against its dataset
uv run python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json

# Evaluate order management agent
uv run python -m evals.run_evals --agent order-management --dataset evals/datasets/order_management.json

# Run with verbose output (shows per-case results)
uv run python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json --verbose

# Output results as JSON for CI/CD integration
uv run python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json --output-json results.json
```

## Requirements

- Running PostgreSQL instance with seeded data (use `./scripts/dev.sh --infra-only` then `./scripts/dev.sh --seed-only`)
- Valid `OPENAI_API_KEY` or Azure OpenAI credentials in `.env`
- Python dependencies installed via `uv sync`

## Adding New Datasets

1. Create a new JSON file in `evals/datasets/`
2. Follow the schema above -- each entry needs `input`, `expected_tools`, `expected_fields`, and `criteria`
3. Run with `--agent <agent-name>` matching the agent's factory function name

## CI/CD Integration

The `--output-json` flag produces machine-readable output for pipeline gates:

```yaml
# GitHub Actions example
- name: Run agent evals
  run: |
    cd agents
    uv run python -m evals.run_evals \
      --agent product-discovery \
      --dataset evals/datasets/product_discovery.json \
      --output-json eval-results.json
    # Fail if score < 0.8
    python -c "import json; r=json.load(open('eval-results.json')); exit(0 if r['overall_score'] >= 0.8 else 1)"
```
