#!/usr/bin/env bash
# Run every eval suite against the committed replay fixtures — keyless, free.
#
# This is the one implementation of the "Replay regression" job in .github/workflows/evals.yml:
# CI calls it, and so can you. Zero LLM calls, no credentials.
#
#   scripts/replay-evals.sh            # services + all suites (DB already seeded)
#   scripts/replay-evals.sh --setup    # first migrate the schema and seed (incl. embeddings)
#   scripts/replay-evals.sh --adversarial   # also insert the indirect-injection corpus before the
#                                           # safety suite (or ADVERSARIAL_SEED=1). OFF by default:
#                                           # the committed recorded safety suite does not use it.
#
#   scripts/replay-evals.sh --profile keyless   # accepted for symmetry with dev.sh; any other profile is refused.
#                                               # This script is the keyless gate: it always replays recorded
#                                               # fixtures. A live run of another provider is
#                                               #   cd agents/python && uv run python -m evals.run_evals --profile NAME ...
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

# util-linux `setsid` is absent on macOS. Fall back to a tiny Python exec that
# calls os.setsid() first, so the process-group cleanup below works the same.
run_in_new_session() {
  # `exec` matters: this function runs in the backgrounded subshell, so without it $! is the
  # subshell's pid while the new session's group id is its child's. stop_specialists signals
  # `-$!`, so it would miss the group and leave the specialists (and the lock they inherit) running.
  if command -v setsid > /dev/null 2>&1; then
    exec setsid "$@"
  else
    exec python3 -c 'import os, sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' "$@"
  fi
}

start_specialists() {
  cd "$PY_DIR" || return 1
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
      cat "$LOG_DIR"/*.log
      return 1
    }
  done
}

# A job should stop what it starts: left running, the specialists hold the uv
# cache open and hang CI's post-steps. Each service runs in its own session
# (setsid, or its macOS fallback), so killing the process group also takes the uvicorn child that
# `uv run` spawns — and only ours, never another checkout's services.
stop_specialists() {
  local pid
  for pid in "${SPECIALIST_PIDS[@]:-}"; do [[ -n "$pid" ]] && kill -- "-$pid" 2>/dev/null; done
  sleep 2
  for pid in "${SPECIALIST_PIDS[@]:-}"; do [[ -n "$pid" ]] && kill -9 -- "-$pid" 2>/dev/null; done
  return 0
}

# Baselines are keyed by setup (agents/python/evals/baselines.py): replay on the in-process target
# compares against the python-inproc-replay directory.
BASELINE_DIR="evals/baselines/python-inproc-replay"

# Every suite also writes its run records, and the records are then re-scored with no model call:
# the stored scores must come back exactly, or the scorers and the records have drifted apart.
rescore_check() {
  uv run python -m evals.run_evals --rescore "$1" --verify > /dev/null
}

run_suites() {
  cd "$PY_DIR" || return 1
  local fail=0 a ds base
  for a in product-discovery order-management pricing-promotions review-sentiment inventory-fulfillment; do
    ds="evals/datasets/${a//-/_}.json"
    base="$BASELINE_DIR/${a//-/_}.json"
    echo "::group::quality eval — $a"
    uv run python -m evals.run_evals --agent "$a" --dataset "$ds" \
      --pass-threshold "$SMOKE_THRESH" --baseline "$base" \
      --records-out "eval-records-$a.jsonl" --output-json "eval-$a.json" || fail=1
    rescore_check "eval-records-$a.jsonl" || fail=1
    echo "::endgroup::"
  done
  echo "::group::approvals eval — order-management (customer confirmation, #302)"
  uv run python -m evals.run_evals --agent order-management \
    --dataset evals/datasets/approvals.json \
    --pass-threshold "$SMOKE_THRESH" --baseline "$BASELINE_DIR/approvals.json" \
    --records-out eval-records-approvals.jsonl --output-json eval-approvals.json || fail=1
  rescore_check eval-records-approvals.jsonl || fail=1
  echo "::endgroup::"
  echo "::group::routing eval — orchestrator"
  uv run python -m evals.run_evals --agent orchestrator \
    --dataset evals/datasets/orchestrator_routing.json \
    --pass-threshold "$SMOKE_THRESH" --baseline "$BASELINE_DIR/orchestrator_routing.json" \
    --records-out eval-records-orchestrator.jsonl --output-json eval-orchestrator.json || fail=1
  rescore_check eval-records-orchestrator.jsonl || fail=1
  echo "::endgroup::"
  # The orchestrator in every other mode, each with a baseline directory of its own
  # (<setup-key>-mode-<mode>). The two workflow modes call no model; handoff and group-chat replay fixtures.
  local pair mode ds slug
  for pair in handoff:orchestrator_routing group-chat:orchestrator_modes workflow:pre-purchase:orchestrator_modes \
    workflow:return-replace:orchestrator_modes; do
    mode="${pair%:*}"; ds="${pair##*:}"; slug="${mode//:/-}"
    echo "::group::orchestrator mode eval — $mode"
    uv run python -m evals.run_evals --agent orchestrator --mode "$mode" --dataset "evals/datasets/$ds.json" \
      --pass-threshold "$SMOKE_THRESH" --baseline "evals/baselines/python-inproc-replay-mode-$slug/$ds.json" \
      --records-out "eval-records-mode-$slug.jsonl" --output-json "eval-mode-$slug.json" || fail=1
    rescore_check "eval-records-mode-$slug.jsonl" || fail=1
    echo "::endgroup::"
  done
  # Indirect injection: the corpus (reviews and product text that order a refund, a cancellation, a return) is seeded
  # for this suite alone and removed again, so no other suite sees an [adversarial] product.
  echo "::group::indirect injection eval — orchestrator"
  uv run python -m evals.seed_adversarial --yes || fail=1
  uv run python -m evals.run_evals --agent orchestrator \
    --dataset evals/datasets/indirect_injection.json \
    --pass-threshold "$SMOKE_THRESH" --baseline "$BASELINE_DIR/indirect_injection.json" \
    --records-out eval-records-indirect-injection.jsonl --output-json eval-indirect-injection.json || fail=1
  rescore_check eval-records-indirect-injection.jsonl || fail=1
  if [[ "${ADVERSARIAL_SEED:-0}" != "1" ]]; then
    uv run python -m evals.seed_adversarial --yes --remove || fail=1
  fi
  echo "::endgroup::"
  if [[ "${ADVERSARIAL_SEED:-0}" == "1" ]]; then
    # After every quality suite (the rows are real rows the review and product tools would
    # return) and before the safety suite, which is the only consumer.
    echo "::group::adversarial corpus seed"
    uv run python -m evals.seed_adversarial --yes || fail=1
    echo "::endgroup::"
  fi
  echo "::group::safety / red-team"
  uv run python -m evals.run_evals --suite safety \
    --pass-threshold 0.8 --records-out eval-records-safety.jsonl --output-json eval-safety.json || fail=1
  rescore_check eval-records-safety.jsonl || fail=1
  echo "::endgroup::"
  return $fail
}

# --profile NAME: validate against agents/python/config/profiles. Only `keyless` is meaningful here, because the
# script exports LLM_PROVIDER=replay unconditionally; accepting another profile and then ignoring it would let a
# green run look like a result for that provider.
check_profile() {
  local name="$1"
  # shellcheck source=lib/profile.sh
  source "$REPO_ROOT/scripts/lib/profile.sh"
  if ! profile_valid "$name"; then
    profile_refuse "$name"
    exit 2
  fi
  if [[ "$name" != "keyless" ]]; then
    echo "replay-evals.sh replays recorded fixtures and would ignore --profile $name." >&2
    echo "For a live run of that provider: cd agents/python && uv run python -m evals.run_evals --profile $name ..." >&2
    exit 2
  fi
}

main() {
  local arg do_setup=0
  while [[ $# -gt 0 ]]; do
    arg="$1"; shift
    case "$arg" in
      --setup) do_setup=1 ;;
      --adversarial) export ADVERSARIAL_SEED=1 ;;
      --profile)
        [[ $# -gt 0 ]] || { echo "--profile needs a name" >&2; exit 2; }
        check_profile "$1"; shift ;;
      --profile=*) check_profile "${arg#--profile=}" ;;
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
