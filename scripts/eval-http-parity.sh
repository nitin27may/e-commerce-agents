#!/usr/bin/env bash
#
# Keyless check that the `http` eval target and the in-process target score alike (EV-2).
#
# Against a RUNNING stack under LLM_PROVIDER=replay, this runs one dataset twice, in this process (the agents
# called directly, against the stack's database) and over HTTP (POST /api/chat/stream, as a seeded user), then
# compares the two runs with `evals.stack_compare` at zero score tolerance. Any difference is a bug in one of the
# two targets: the same case must produce the same tool calls, routes, workflow nodes, flags and scores.
#
#   scripts/eval-http-parity.sh                        # stack_parity dataset against http://localhost:8080
#   scripts/eval-http-parity.sh --base-url http://localhost:8080 --dataset evals/datasets/stack_parity.json
#   scripts/eval-http-parity.sh --stack dotnet         # label for the record (the in-process side is always Python)
#
# Start the stack first, keyless and in an ISOLATED compose project (never the default one: its pgdata volume is
# your real data):
#
#   COMPOSE_PROJECT_NAME=eca-parity scripts/compose-smoke.sh --keep      # boots, seeds, smokes, leaves it up
#   scripts/eval-http-parity.sh
#   COMPOSE_PROJECT_NAME=eca-parity docker compose --profile agents --profile seed down -v
#
# Which datasets this can judge. A turn that calls no model (the workflow modes: `stack_parity.json`) gives the same
# answer on both sides. A turn that calls a model does not, and that is a property of replay, not a bug: a logged-in
# user's profile and recent orders are part of the model request, the committed fixtures were recorded for an
# identity with none, so the http side (a real user) misses every fixture the in-process side (no profile) hits.
# Judge a model-calling dataset only after recording it through the http target (docs/agent-quality.md).
#
# Needs: Postgres reachable at DATABASE_URL (the stack's, published on localhost:5432 by compose), the stack on
# --base-url, `uv`. No key: the script refuses to run with one in the environment. Serialize with other eval runs:
#   flock /tmp/eca-evals.lock scripts/eval-http-parity.sh
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY_DIR="$REPO_ROOT/agents/python"

BASE_URL="${ORCH_URL:-http://localhost:8080}"
DATASET="evals/datasets/stack_parity.json"
AGENT="orchestrator"
STACK="${EVAL_STACK:-python}"
OUT_DIR="${PARITY_OUT:-$(mktemp -d "${TMPDIR:-/tmp}/eval-http-parity.XXXXXX")}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-url) BASE_URL="$2"; shift 2 ;;
    --dataset) DATASET="$2"; shift 2 ;;
    --agent) AGENT="$2"; shift 2 ;;
    --stack) STACK="$2"; shift 2 ;;
    --out) OUT_DIR="$2"; shift 2 ;;
    -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

fail() { echo "FAIL: $*" >&2; exit 1; }

leaked="$(env | grep -E '^[A-Za-z0-9_]*(_API_KEY|AZURE_OPENAI_KEY)=.+' | cut -d= -f1 | tr '\n' ' ')"
[[ -z "$leaked" ]] || fail "refusing to run with credentials in the environment: $leaked"

curl -sf -m 10 "$BASE_URL/health" > /dev/null || fail "no orchestrator answering at $BASE_URL/health (start the stack first)"
mkdir -p "$OUT_DIR"

export LLM_PROVIDER=replay
export REPLAY_FIXTURES_DIR="${REPLAY_FIXTURES_DIR:-evals/fixtures/replay}"
export DATABASE_URL="${DATABASE_URL:-postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents}"
export REDIS_URL="${REDIS_URL:-redis://localhost:6379}"
export ENVIRONMENT="${ENVIRONMENT:-test}"
export JWT_SECRET="${JWT_SECRET:-ci-eval-jwt-secret-please-rotate-32bytes}"
export AGENT_SHARED_SECRET="${AGENT_SHARED_SECRET:-ci-eval-agent-secret-please-rotate-32b}"
# The in-process side calls specialists over A2A for the orchestrator dataset.
DEFAULT_REGISTRY='{"product-discovery":"http://localhost:8081","order-management":"http://localhost:8082","pricing-promotions":"http://localhost:8083","review-sentiment":"http://localhost:8084","inventory-fulfillment":"http://localhost:8085"}'
export AGENT_REGISTRY="${AGENT_REGISTRY:-$DEFAULT_REGISTRY}"

cd "$PY_DIR" || fail "cannot enter $PY_DIR"

echo "--- in-process run: $DATASET"
uv run python -m evals.run_evals --agent "$AGENT" --dataset "$DATASET" --pass-threshold 0 \
  --records-out "$OUT_DIR/inproc.jsonl" --output-json "$OUT_DIR/inproc.json" || fail "the in-process run failed"

echo "--- http run: $DATASET against $BASE_URL (stack label: $STACK)"
uv run python -m evals.run_evals --target http --base-url "$BASE_URL" --stack "$STACK" \
  --agent "$AGENT" --dataset "$DATASET" --pass-threshold 0 \
  --records-out "$OUT_DIR/http.jsonl" --output-json "$OUT_DIR/http.json" || fail "the http run failed"

echo "--- compare (zero score tolerance)"
if uv run python -m evals.stack_compare "$OUT_DIR/inproc.jsonl" "$OUT_DIR/http.jsonl" \
     --label-a inproc --label-b http --score-tolerance 0; then
  echo "PASS: the http target reproduces the in-process run for $DATASET (records in $OUT_DIR)"
else
  echo "FAIL: the targets disagree; one of them has a bug (records in $OUT_DIR)" >&2
  exit 1
fi
