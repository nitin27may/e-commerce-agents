#!/usr/bin/env bash
# MCP path versus native tools, keyless: the same specialist cases and the same tool calls both ways.
#
#   scripts/mcp-compare.sh            # DB already migrated and seeded (as for scripts/replay-evals.sh)
#
# Starts the two MCP servers (product, inventory) on $MCP_PRODUCT_PORT / $MCP_INVENTORY_PORT (default 9000 / 9001),
# runs `python -m evals.mcp_compare --run` against the replay fixtures (the MCP path has its own recorded fixtures,
# because its tool schemas make a different request key) and stops the servers it started. No model key, no cost.
# Exit status is non-zero on a value difference in a tool result, a drop in tool-call accuracy, or any difference not
# listed in agents/python/evals/mcp_known_differences.json. See agents/python/evals/mcp_compare.py.
#
# Needs Postgres (pgvector) and Redis reachable at $DATABASE_URL / $REDIS_URL, seeded.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY_DIR="$REPO_ROOT/agents/python"
LOG_DIR="${MCP_LOG_DIR:-${TMPDIR:-/tmp}}"
PRODUCT_PORT="${MCP_PRODUCT_PORT:-9000}"
INVENTORY_PORT="${MCP_INVENTORY_PORT:-9001}"

export LLM_PROVIDER=replay
export REPLAY_FIXTURES_DIR="${REPLAY_FIXTURES_DIR:-evals/fixtures/replay}"
export DATABASE_URL="${DATABASE_URL:-postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents}"
export REDIS_URL="${REDIS_URL:-redis://localhost:6379}"
export ENVIRONMENT="${ENVIRONMENT:-test}"
export JWT_SECRET="${JWT_SECRET:-ci-eval-jwt-secret-please-rotate-32bytes}"
export AGENT_SHARED_SECRET="${AGENT_SHARED_SECRET:-ci-eval-agent-secret-please-rotate-32b}"
export MCP_PRODUCT_SERVER_URL="http://127.0.0.1:$PRODUCT_PORT/mcp"
export MCP_INVENTORY_SERVER_URL="http://127.0.0.1:$INVENTORY_PORT/mcp"

PIDS=()

run_in_new_session() {
  # `exec` so $! is the process that owns the new session: stop_servers signals its group.
  if command -v setsid > /dev/null 2>&1; then
    exec setsid "$@"
  else
    exec python3 -c 'import os, sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' "$@"
  fi
}

start_servers() {
  cd "$PY_DIR" || return 1
  run_in_new_session nohup uv run uvicorn ecommerce_mcp_product.server:app --host 127.0.0.1 --port "$PRODUCT_PORT" \
    > "$LOG_DIR/mcp-product.log" 2>&1 &
  PIDS+=("$!")
  run_in_new_session nohup uv run uvicorn ecommerce_mcp_inventory.server:app --host 127.0.0.1 --port "$INVENTORY_PORT" \
    > "$LOG_DIR/mcp-inventory.log" 2>&1 &
  PIDS+=("$!")
  local port code
  for port in "$PRODUCT_PORT" "$INVENTORY_PORT"; do
    code=000
    for _ in $(seq 1 40); do
      # Any HTTP answer (the bare POST gets a 4xx) means the server is listening.
      code="$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'content-type: application/json' -d '{}' \
        "http://127.0.0.1:$port/mcp" || true)"
      [[ "$code" != "000" ]] && break
      sleep 1
    done
    if [[ "$code" == "000" ]]; then
      echo "::error::MCP server on port $port never started"
      cat "$LOG_DIR"/mcp-*.log
      return 1
    fi
  done
}

stop_servers() {
  local pid
  for pid in "${PIDS[@]:-}"; do [[ -n "$pid" ]] && kill -- "-$pid" 2> /dev/null; done
  sleep 2
  for pid in "${PIDS[@]:-}"; do [[ -n "$pid" ]] && kill -9 -- "-$pid" 2> /dev/null; done
  return 0
}

trap stop_servers EXIT
start_servers || exit 1
cd "$PY_DIR" || exit 1
uv run python -m evals.mcp_compare --run
exit $?
