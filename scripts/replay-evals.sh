#!/usr/bin/env bash
# Run every eval suite against the committed replay fixtures — keyless, free.
#
# This is the one implementation of the "smoke" job in .github/workflows/evals.yml:
# CI calls it, and so can you. Zero LLM calls, no credentials.
#
#   scripts/replay-evals.sh            # services + all suites (DB already seeded)
#   scripts/replay-evals.sh --setup    # first migrate the schema and seed (incl. embeddings)
#   scripts/replay-evals.sh --adversarial   # also insert the indirect-injection corpus before the
#                                           # safety suite (or ADVERSARIAL_SEED=1). OFF by default:
#                                           # the committed recorded safety suite does not use it.
#
# Needs Postgres (pgvector) and Redis reachable at $DATABASE_URL / $REDIS_URL.
# Defaults match docker-compose.yml, so `docker compose up -d db redis` is enough.
#
# Exit status is non-zero if any suite fails, regresses past its baseline, or
# hits a missing fixture.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY_DIR="$REPO_ROOT/agents/python"

# Replay mode, no key.
export LLM_PROVIDER=replay
export REPLAY_FIXTURES_DIR="${REPLAY_FIXTURES_DIR:-evals/fixtures/replay}"
export DATABASE_URL="${DATABASE_URL:-postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents}"
export REDIS_URL="${REDIS_URL:-redis://localhost:6379}"
export ENVIRONMENT="${ENVIRONMENT:-test}"
export JWT_SECRET="${JWT_SECRET:-ci-eval-jwt-secret-please-rotate-32bytes}"
export AGENT_SHARED_SECRET="${AGENT_SHARED_SECRET:-ci-eval-agent-secret-please-rotate-32b}"
DEFAULT_REGISTRY='{"product-discovery":"http://localhost:8081","order-management":"http://localhost:8082","pricing-promotions":"http://localhost:8083","review-sentiment":"http://localhost:8084","inventory-fulfillment":"http://localhost:8085"}'
export AGENT_REGISTRY="${AGENT_REGISTRY:-$DEFAULT_REGISTRY}"
# Replay scores are a property of the recording session; what a change can move
# is measured by --baseline/--max-regression and the missing-fixture check.
SMOKE_THRESH="${SMOKE_THRESH:-0}"
LOG_DIR="${REPLAY_LOG_DIR:-${TMPDIR:-/tmp}}"

# ── SCHEMA LOADING ─────────────────────────────────────────────────────────
# The single place this script touches the database schema. Only used with
# --setup (CI loads the schema in its own earlier step).
load_schema() {
  # The frozen baseline plus every numbered migration, in order — the same
  # runner the seeder, dev.sh and CI use (scripts/migrate.py).
  (cd "$REPO_ROOT" && uv run --project agents/python python -m scripts.migrate)
}
# ───────────────────────────────────────────────────────────────────────────

setup_database() {
  load_schema || return 1
  # scripts.seed lives at the repo root, not in
  # agents/python/, so run from the root and point uv at the project.
  # Under LLM_PROVIDER=replay the seeder populates product_embeddings itself.
  (cd "$REPO_ROOT" && uv run --project agents/python python -m scripts.seed) || return 1
}

SPECIALIST_PIDS=()

start_specialists() {
  cd "$PY_DIR" || return 1
  for svc in product_discovery:8081 order_management:8082 pricing_promotions:8083 review_sentiment:8084 inventory_fulfillment:8085; do
    name="${svc%%:*}"; port="${svc##*:}"
    AGENT_NAME="$name" AGENT_PORT="$port" \
      setsid nohup uv run --no-project uvicorn "${name}.main:app" --host 0.0.0.0 --port "$port" \
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
      cat "$LOG_DIR"/*.log
      return 1
    }
  done
}

# A job should stop what it starts: left running, the specialists hold the uv
# cache open and hang CI's post-steps. Each service runs in its own session
# (setsid), so killing the process group also takes the uvicorn child that
# `uv run` spawns — and only ours, never another checkout's services.
stop_specialists() {
  local pid
  for pid in "${SPECIALIST_PIDS[@]:-}"; do [[ -n "$pid" ]] && kill -- "-$pid" 2>/dev/null; done
  sleep 2
  for pid in "${SPECIALIST_PIDS[@]:-}"; do [[ -n "$pid" ]] && kill -9 -- "-$pid" 2>/dev/null; done
  return 0
}

run_suites() {
  cd "$PY_DIR" || return 1
  local fail=0 a ds base
  for a in product-discovery order-management pricing-promotions review-sentiment inventory-fulfillment; do
    ds="evals/datasets/${a//-/_}.json"
    base="evals/baselines/${a//-/_}.json"
    echo "::group::quality eval — $a"
    uv run python -m evals.run_evals --agent "$a" --dataset "$ds" \
      --pass-threshold "$SMOKE_THRESH" --baseline "$base" --max-regression 0.05 \
      --output-json "eval-$a.json" || fail=1
    echo "::endgroup::"
  done
  echo "::group::routing eval — orchestrator"
  uv run python -m evals.run_evals --agent orchestrator \
    --dataset evals/datasets/orchestrator_routing.json \
    --pass-threshold "$SMOKE_THRESH" --baseline evals/baselines/orchestrator_routing.json --max-regression 0.05 \
    --output-json eval-orchestrator.json || fail=1
  echo "::endgroup::"
  if [[ "${ADVERSARIAL_SEED:-0}" == "1" ]]; then
    # After every quality suite (the rows are real rows the review and product tools would
    # return) and before the safety suite, which is the only consumer.
    echo "::group::adversarial corpus seed"
    uv run python -m evals.seed_adversarial || fail=1
    echo "::endgroup::"
  fi
  echo "::group::safety / red-team"
  uv run python -m evals.run_evals --suite safety \
    --pass-threshold 0.8 --output-json eval-safety.json || fail=1
  echo "::endgroup::"
  return $fail
}

main() {
  local arg do_setup=0
  for arg in "$@"; do
    case "$arg" in
      --setup) do_setup=1 ;;
      --adversarial) export ADVERSARIAL_SEED=1 ;;
      *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
  done
  if [[ "$do_setup" == "1" ]]; then
    setup_database || exit 1
  fi
  trap stop_specialists EXIT
  start_specialists || exit 1
  run_suites
  exit $?
}

main "$@"
