#!/usr/bin/env bash
#
# Dual-stack parity gate, keyless (B2): boot each stack under LLM_PROVIDER=replay, run one dataset through the
# `http` eval target against it, then fail when the stacks' records diverge (evals/stack_compare.py).
#
# This is what .github/workflows/stack-parity.yml runs. It is the first automated check that the .NET stack
# *answers the same way* as the Python one, not just that it boots (that is compose-smoke.sh) or that it builds.
#
#   scripts/stack-parity.sh                                  # python vs dotnet, stack_parity dataset
#   scripts/stack-parity.sh --stacks "python py-net net-py"  # any of python dotnet py-net net-py; the first is the reference
#   scripts/stack-parity.sh --dataset evals/datasets/stack_parity.json --score-tolerance 0.05
#   scripts/stack-parity.sh --keep                           # leave the last stack up
#
# Demonstrate that it can go red (B2 acceptance), with a one-line change to the .NET stack: in
# agents/dotnet/src/ECommerceAgents.Shared/Workflows/PrePurchaseWorkflow.cs change the id of the reviews executor,
# `Executor<ResearchState>("reviews")` (line 106), to `("reviews-removed")`. The workflow still runs, but the
# `workflow nodes differ: python [..., 'reviews', ...], dotnet [..., 'reviews-removed', ...]` line appears, the
# comparison exits 1 and the job is red; revert the line and it is green again. The same check as a unit test:
# agents/python/tests/test_eval_stack_compare.py::test_removing_a_capability_from_one_stack_fails_the_comparison.
#
# What it can judge keyless. Under replay a turn that calls a model is a replay miss on every stack (the committed
# fixtures are Python request hashes recorded for an identity with no profile; a logged-in user's profile changes the
# request), so the dataset is `stack_parity.json`: orchestration modes that call no model (`workflow:pre-purchase`)
# and still run the real route, auth, mode registry, workflow graph and database tools on each stack. Tool-calling
# turns need a recorded, per-stack run (docs/agent-quality.md).
#
# Isolation: the stacks run in their own compose project (COMPOSE_PROJECT_NAME, default eca-stack-parity), never the
# default one, because `down -v` removes the project's volumes and the default project's pgdata is a developer's
# real data. The stacks bind the same host ports, so they run one after the other. Serialize with other eval runs:
#   flock /tmp/eca-evals.lock scripts/stack-parity.sh
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 1

STACKS="python dotnet"
DATASET="evals/datasets/stack_parity.json"
AGENT="orchestrator"
TOLERANCE="0.05"
KEEP=false
OUT_DIR="${PARITY_OUT:-$REPO_ROOT/stack-parity-out}"
ORCH_URL="${ORCH_URL:-http://localhost:8080}"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-eca-stack-parity}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stacks) STACKS="$2"; shift 2 ;;
    --dataset) DATASET="$2"; shift 2 ;;
    --agent) AGENT="$2"; shift 2 ;;
    --score-tolerance) TOLERANCE="$2"; shift 2 ;;
    --out) OUT_DIR="$2"; shift 2 ;;
    --keep) KEEP=true; shift ;;
    -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

fail() { echo "::error::$*" >&2; echo "FAIL: $*" >&2; exit 1; }

[[ "$COMPOSE_PROJECT_NAME" != "e-commerce-agents" ]] \
  || fail "refusing the default compose project: its pgdata volume is a developer's real data (set COMPOSE_PROJECT_NAME)"

leaked="$(env | grep -E '^[A-Za-z0-9_]*(_API_KEY|AZURE_OPENAI_KEY)=.+' | cut -d= -f1 | tr '\n' ' ')"
[[ -z "$leaked" ]] || fail "refusing to run with credentials in the environment: $leaked"

export LLM_PROVIDER=replay
export ENVIRONMENT="${ENVIRONMENT:-test}"
mkdir -p "$OUT_DIR"

# The compose invocation for one stack (a plain stack, or a mixed variant layered on its orchestrator's base file).
compose_args() {
  local stack="$1"
  SMOKE_ARGS=()
  COMPOSE=()
  case "$stack" in
    python) SMOKE_ARGS=(--compose-file docker-compose.yml); COMPOSE=(docker compose -f docker-compose.yml) ;;
    dotnet) SMOKE_ARGS=(--compose-file docker-compose.dotnet.yml); COMPOSE=(docker compose -f docker-compose.dotnet.yml) ;;
    py-net|net-py)
      # shellcheck source=lib/mixed-env.sh
      source "$REPO_ROOT/scripts/lib/mixed-env.sh"
      mixed_env "$stack" || return 2
      SMOKE_ARGS=(--mixed "$stack")
      COMPOSE=(docker compose -f "$MIXED_BASE_FILE" -f docker-compose.mixed.yml)
      ;;
    *) return 2 ;;
  esac
}

down() {
  local stack="$1"
  compose_args "$stack" || return 0
  "${COMPOSE[@]}" --profile agents --profile seed down -v --remove-orphans > /dev/null 2>&1 || true
}

CURRENT=""
cleanup() { $KEEP || { [[ -z "$CURRENT" ]] || down "$CURRENT"; }; }
trap cleanup EXIT

RECORDS=()
for stack in $STACKS; do
  compose_args "$stack" || fail "unknown stack '$stack' (python, dotnet, py-net or net-py)"
  CURRENT="$stack"
  echo "::group::boot $stack (replay, keyless)"
  # compose-smoke boots, seeds, waits for /ready everywhere, logs in and runs one keyless turn; --keep leaves it up.
  scripts/compose-smoke.sh "${SMOKE_ARGS[@]}" --keep || fail "$stack did not boot and pass its smoke"
  echo "::endgroup::"

  echo "::group::eval $DATASET over http against $stack"
  (
    cd agents/python || exit 1
    uv run python -m evals.run_evals --target http --base-url "$ORCH_URL" --stack "$stack" \
      --agent "$AGENT" --dataset "$DATASET" --pass-threshold 0 \
      --records-out "$OUT_DIR/$stack.jsonl" --output-json "$OUT_DIR/$stack.json"
  )
  rc=$?
  echo "::endgroup::"
  if [[ $rc -ne 0 ]]; then
    "${COMPOSE[@]}" --profile agents logs --no-color --tail=80 orchestrator || true
    fail "the http eval against $stack exited $rc"
  fi
  [[ -s "$OUT_DIR/$stack.jsonl" ]] || fail "the http eval against $stack wrote no records"
  RECORDS+=("$OUT_DIR/$stack.jsonl")
  $KEEP && [[ "$stack" == "${STACKS##* }" ]] || down "$stack"
  CURRENT=""
done

# Compare every stack with the first (the reference).
status=0
read -r -a NAMES <<< "$STACKS"
for ((i = 1; i < ${#NAMES[@]}; i++)); do
  echo "::group::compare ${NAMES[0]} vs ${NAMES[$i]}"
  (
    cd agents/python || exit 1
    uv run python -m evals.stack_compare "${RECORDS[0]}" "${RECORDS[$i]}" \
      --label-a "${NAMES[0]}" --label-b "${NAMES[$i]}" --score-tolerance "$TOLERANCE"
  ) || status=1
  echo "::endgroup::"
done

if [[ $status -eq 0 ]]; then
  echo "PASS: ${STACKS} agree on $DATASET (records in $OUT_DIR)"
else
  echo "::error::the stacks diverge on $DATASET; see the comparison above (records in $OUT_DIR)" >&2
  echo "FAIL: stack parity" >&2
fi
exit $status
