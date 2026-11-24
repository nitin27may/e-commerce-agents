#!/usr/bin/env bash
# Run the live eval datasets (agents/python/evals/datasets/live/) against a real model.
#
# This is the one implementation of the `live` job in .github/workflows/evals.yml, which is
# workflow_dispatch only. It SPENDS MONEY. Nothing may call it from an automatic trigger
# (agents/python/tests/test_workflow_secret_triggers.py enforces that for the workflow files).
#
# Configuration is environment variables, so the workflow and a local run are the same thing:
#
#   LIVE_PROVIDER        openai | azure | anthropic | openai-compatible   (required)
#   LIVE_MODEL           model name; for azure the deployment name        (required)
#   LIVE_REPEATS         case repeats (default 1)
#   LIVE_SUITE           all (default) or one of: product-discovery order-management pricing-promotions
#                        review-sentiment inventory-fulfillment merchant-ops orchestrator approvals
#   LIVE_PASS_THRESHOLD  minimum overall score per suite (default 0.7)
#   LIVE_USE_JUDGE       true to score completeness with an LLM judge (self-judged unless JUDGE_* is set)
#   COST_BUDGET_TOKENS_PER_RUN   per-run ceiling, enforced (default 100000)
#   LIVE_MAX_TOTAL_TOKENS        ceiling for the whole script, checked between suites (default 2000000)
#
# Keys, read by name and never printed:
#   openai             OPENAI_API_KEY
#   azure              AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY
#   anthropic          ANTHROPIC_API_KEY
#   openai-compatible  LLM_BASE_URL, LLM_API_KEY
#
#   scripts/live-evals.sh --check-env   # validate the configuration and exit; calls nothing
#   scripts/live-evals.sh --setup       # migrate + seed a fresh database first, then run
#
# Needs Postgres (pgvector) and Redis at $DATABASE_URL / $REDIS_URL, as replay-evals.sh does.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY_DIR="$REPO_ROOT/agents/python"
LOG_DIR="${LIVE_LOG_DIR:-${TMPDIR:-/tmp}}"

# `approvals` is a suite, not an agent: order-management against evals/datasets/live/approvals.live.json (#302).
AGENTS=(product-discovery order-management pricing-promotions review-sentiment inventory-fulfillment merchant-ops orchestrator approvals)

fail_config() {
  echo "::error::$1" >&2
  exit 2
}

require_secret() {
  # $1 = variable name, $2 = provider it belongs to
  local name="$1" provider="$2"
  if [[ -z "${!name:-}" ]]; then
    fail_config "Provider '$provider' needs the repository secret $name, and it is not set. Add it under Settings, Secrets and variables, Actions (or export it for a local run); no model was called."
  fi
}

configure_provider() {
  LIVE_PROVIDER="${LIVE_PROVIDER:-}"
  LIVE_MODEL="${LIVE_MODEL:-}"
  [[ -n "$LIVE_PROVIDER" ]] || fail_config "LIVE_PROVIDER is not set (openai, azure, anthropic or openai-compatible)."
  [[ -n "$LIVE_MODEL" ]] || fail_config "LIVE_MODEL is not set (the model name; for azure, the deployment name)."

  case "$LIVE_PROVIDER" in
    openai)
      require_secret OPENAI_API_KEY openai
      export LLM_PROVIDER=openai LLM_MODEL="$LIVE_MODEL"
      ;;
    azure)
      require_secret AZURE_OPENAI_ENDPOINT azure
      require_secret AZURE_OPENAI_KEY azure
      export LLM_PROVIDER=azure AZURE_OPENAI_DEPLOYMENT="$LIVE_MODEL" LLM_MODEL="$LIVE_MODEL"
      ;;
    anthropic)
      require_secret ANTHROPIC_API_KEY anthropic
      export LLM_PROVIDER=anthropic ANTHROPIC_MODEL="$LIVE_MODEL"
      # Claude has no embeddings API: semantic search falls back to its full-text arm.
      ;;
    openai-compatible)
      require_secret LLM_BASE_URL openai-compatible
      require_secret LLM_API_KEY openai-compatible
      # The openai provider reads OPENAI_API_KEY; the secret is named LLM_API_KEY so it cannot be
      # mistaken for a real OpenAI key. Copied here, in this process only, never written to a file.
      export LLM_PROVIDER=openai LLM_MODEL="$LIVE_MODEL" OPENAI_API_KEY="$LLM_API_KEY"
      export LLM_API="${LLM_API:-chat_completions}"
      ;;
    *)
      fail_config "LIVE_PROVIDER='$LIVE_PROVIDER' is not one of openai, azure, anthropic, openai-compatible."
      ;;
  esac
}

configure_run() {
  LIVE_REPEATS="${LIVE_REPEATS:-1}"
  LIVE_SUITE="${LIVE_SUITE:-all}"
  LIVE_PASS_THRESHOLD="${LIVE_PASS_THRESHOLD:-0.7}"
  LIVE_MAX_TOTAL_TOKENS="${LIVE_MAX_TOTAL_TOKENS:-2000000}"
  [[ "$LIVE_REPEATS" =~ ^[1-9][0-9]*$ ]] || fail_config "LIVE_REPEATS='$LIVE_REPEATS' must be a positive integer."
  [[ "$LIVE_MAX_TOTAL_TOKENS" =~ ^[1-9][0-9]*$ ]] || fail_config "LIVE_MAX_TOTAL_TOKENS must be a positive integer."
  [[ "$LIVE_PASS_THRESHOLD" =~ ^(0|1)(\.[0-9]+)?$ ]] || fail_config "LIVE_PASS_THRESHOLD='$LIVE_PASS_THRESHOLD' must be between 0 and 1."

  # A live run is never unbounded: the per-run token ceiling is enforced, and fails closed when a turn
  # reports no usage (shared/config.py, COST_BUDGET_TOKENS_PER_RUN).
  export COST_BUDGET_MODE=enforce
  export COST_BUDGET_TOKENS_PER_RUN="${COST_BUDGET_TOKENS_PER_RUN:-100000}"
  [[ "$COST_BUDGET_TOKENS_PER_RUN" =~ ^[1-9][0-9]*$ ]] || fail_config "COST_BUDGET_TOKENS_PER_RUN must be a positive integer."

  SELECTED=()
  if [[ "$LIVE_SUITE" == "all" ]]; then
    SELECTED=("${AGENTS[@]}")
  else
    local a
    for a in "${AGENTS[@]}"; do [[ "$a" == "$LIVE_SUITE" ]] && SELECTED=("$a"); done
    [[ ${#SELECTED[@]} -gt 0 ]] || fail_config "LIVE_SUITE='$LIVE_SUITE' is not 'all' or one of: ${AGENTS[*]}."
  fi
}

export_runtime_defaults() {
  # Same local defaults as replay-evals.sh; the workflow sets its own.
  export DATABASE_URL="${DATABASE_URL:-postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents}"
  export REDIS_URL="${REDIS_URL:-redis://localhost:6379}"
  export ENVIRONMENT="${ENVIRONMENT:-test}"
  export JWT_SECRET="${JWT_SECRET:-ci-eval-jwt-secret-please-rotate-32bytes}"
  export AGENT_SHARED_SECRET="${AGENT_SHARED_SECRET:-ci-eval-agent-secret-please-rotate-32b}"
  local default_registry='{"product-discovery":"http://localhost:8081","order-management":"http://localhost:8082","pricing-promotions":"http://localhost:8083","review-sentiment":"http://localhost:8084","inventory-fulfillment":"http://localhost:8085"}'
  export AGENT_REGISTRY="${AGENT_REGISTRY:-$default_registry}"
}

setup_database() {
  (cd "$REPO_ROOT" && uv run --project agents/python python -m scripts.migrate) || return 1
  # The seeder fills product_embeddings itself (it loads the committed vectors for a keyed provider);
  # there is deliberately no separate generate_embeddings step, which would spend embedding tokens.
  (cd "$REPO_ROOT" && uv run --project agents/python python -m scripts.seed) || return 1
}

SPECIALIST_PIDS=()

run_in_new_session() {
  if command -v setsid > /dev/null 2>&1; then
    exec setsid "$@"
  else
    exec python3 -c 'import os, sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' "$@"
  fi
}

start_specialists() {
  cd "$PY_DIR" || return 1
  local svc name port
  for svc in product_discovery:8081 order_management:8082 pricing_promotions:8083 review_sentiment:8084 inventory_fulfillment:8085; do
    name="${svc%%:*}"; port="${svc##*:}"
    AGENT_NAME="$name" AGENT_PORT="$port" \
      run_in_new_session nohup uv run --no-project uvicorn "${name}.main:app" --host 0.0.0.0 --port "$port" \
      > "$LOG_DIR/${name}.log" 2>&1 &
    SPECIALIST_PIDS+=("$!")
  done
  for port in 8081 8082 8083 8084 8085; do
    for _ in $(seq 1 30); do
      curl -sf "http://localhost:$port/health" > /dev/null && break
      sleep 1
    done
    curl -sf "http://localhost:$port/health" > /dev/null || {
      echo "::error::specialist on port $port never became healthy"
      return 1
    }
  done
}

stop_specialists() {
  local pid
  for pid in "${SPECIALIST_PIDS[@]:-}"; do [[ -n "$pid" ]] && kill -- "-$pid" 2>/dev/null; done
  sleep 2
  for pid in "${SPECIALIST_PIDS[@]:-}"; do [[ -n "$pid" ]] && kill -9 -- "-$pid" 2>/dev/null; done
  return 0
}

# Tokens (in + out) already spent by the suites that wrote an eval-*.json, from their own reports.
tokens_spent() {
  python3 - "$PY_DIR" <<'EOF'
import glob
import json
import os
import sys

total = 0
for path in glob.glob(os.path.join(sys.argv[1], "eval-live-*.json")):
    with open(path) as f:
        data = json.load(f)
    total += int(data.get("total_tokens_in") or 0) + int(data.get("total_tokens_out") or 0)
print(total)
EOF
}

run_suites() {
  cd "$PY_DIR" || return 1
  local fail=0 a stem spent ds twin judge_args=()
  [[ "${LIVE_USE_JUDGE:-false}" == "true" ]] && judge_args=(--use-llm-judge --allow-self-judge)
  rm -f eval-live-*.json eval-records-live-*.jsonl

  for a in "${SELECTED[@]}"; do
    stem="${a//-/_}"
    spent="$(tokens_spent)"
    if (( spent >= LIVE_MAX_TOTAL_TOKENS )); then
      echo "::error::Stopping before '$a': $spent tokens spent, ceiling LIVE_MAX_TOTAL_TOKENS=$LIVE_MAX_TOTAL_TOKENS."
      fail=1
      break
    fi
    if [[ "$a" == "orchestrator" ]]; then stem="orchestrator_routing"; fi
    ds="evals/datasets/live/${stem}.live.json"
    twin="evals/datasets/${stem}.json"
    target="$a"
    if [[ "$a" == "approvals" ]]; then target="order-management"; fi
    echo "::group::live eval: $a"
    # What the replay twin would cost: informational, calls no model.
    uv run python -m evals.run_evals --estimate "$LIVE_MODEL" --agent "$target" \
      --dataset "$twin" --repeats "$LIVE_REPEATS" 2>&1 | tail -5 || true
    # Live cases act on real rows (initiate_return writes), and cancel_order parks an approval: the
    # database is a throwaway one per run. Baselines are keyed by setup and none exists for a live
    # model, so there is no --baseline: the threshold is the gate.
    uv run python -m evals.run_evals --agent "$target" --dataset "$ds" \
      --pass-threshold "$LIVE_PASS_THRESHOLD" --repeats "$LIVE_REPEATS" "${judge_args[@]}" \
      --records-out "eval-records-live-$a.jsonl" --output-json "eval-live-$a.json" || fail=1
    echo "::endgroup::"
  done
  return $fail
}

main() {
  local arg do_setup=0 check_only=0
  for arg in "$@"; do
    case "$arg" in
      --setup) do_setup=1 ;;
      --check-env) check_only=1 ;;
      *) echo "Unknown argument: $arg" >&2; exit 2 ;;
    esac
  done

  configure_provider
  configure_run
  echo "Live eval: provider=$LIVE_PROVIDER model=$LIVE_MODEL suites=${SELECTED[*]} repeats=$LIVE_REPEATS" \
    "threshold=$LIVE_PASS_THRESHOLD per-run-token-ceiling=$COST_BUDGET_TOKENS_PER_RUN total-token-ceiling=$LIVE_MAX_TOTAL_TOKENS"
  [[ $check_only -eq 1 ]] && exit 0

  export_runtime_defaults
  if [[ $do_setup -eq 1 ]]; then
    setup_database || { echo "::error::database setup failed" >&2; exit 1; }
  fi

  local needs_specialists=0 a
  for a in "${SELECTED[@]}"; do [[ "$a" == "orchestrator" ]] && needs_specialists=1; done
  if [[ $needs_specialists -eq 1 ]]; then
    trap stop_specialists EXIT
    start_specialists || exit 1
  fi

  run_suites
  exit $?
}

main "$@"
