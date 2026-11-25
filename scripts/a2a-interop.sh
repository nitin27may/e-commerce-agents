#!/usr/bin/env bash
# Cross-process A2A 1.0 interop between the two stacks — keyless, no database, no Docker.
#
#   scripts/a2a-interop.sh              # both directions
#   scripts/a2a-interop.sh --dotnet-host   # only start the .NET host(s) and wait (prints their URLs)
#
# Direction 1 (Python client -> .NET specialist): starts real .NET specialists on real sockets
#   (agents/dotnet/tests/ECommerceAgents.A2AInteropHost: the real AgentHost, auth middleware, step recorder and
#   A2A 1.0 surface, around a scripted model) and runs agents/python/tests/test_a2a_dotnet_interop.py, which drives
#   them with the official a2a-sdk client and with this repo's own Python client (shared/a2a_client.py).
# Direction 2 (.NET client -> Python specialist): starts a real Python specialist on a real socket
#   (agents/python/tests/a2a_interop_server.py: the production specialist app around a scripted model) and runs
#   agents/dotnet/tests/ECommerceAgents.Shared.Tests/A2AInteropTests.cs, which drives it with the official A2A
#   .NET SDK client and with the orchestrator's A2AClient under A2A_TRANSPORT=a2a.
#
# Needs `uv` and the .NET SDK. Ports can be moved with A2A_INTEROP_BASE_PORT (default 18080; uses 10 ports).
# Only the processes this script started are stopped, by PID.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY_DIR="$REPO_ROOT/agents/python"
NET_DIR="$REPO_ROOT/agents/dotnet"
BASE="${A2A_INTEROP_BASE_PORT:-18080}"
LOG_DIR="${A2A_INTEROP_LOG_DIR:-${TMPDIR:-/tmp}/a2a-interop}"
mkdir -p "$LOG_DIR"

PIDS=()
cleanup() {
  # Only the processes this script started, by PID: ask politely, then insist after three seconds.
  for pid in "${PIDS[@]:-}"; do
    [[ -n "$pid" ]] && kill "$pid" 2>/dev/null
  done
  for _ in 1 2 3 4 5 6; do
    alive=0
    for pid in "${PIDS[@]:-}"; do
      [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null && alive=1
    done
    [[ $alive -eq 0 ]] && break
    sleep 0.5
  done
  for pid in "${PIDS[@]:-}"; do
    [[ -n "$pid" ]] && kill -9 "$pid" 2>/dev/null
  done
  wait 2>/dev/null
}
trap cleanup EXIT INT TERM

wait_for() { # url, label, pid
  local url="$1" label="$2" pid="$3"
  for _ in $(seq 1 120); do
    if curl -fsS -o /dev/null "$url" 2>/dev/null; then return 0; fi
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "a2a-interop: $label exited early; see $LOG_DIR" >&2
      return 1
    fi
    sleep 0.5
  done
  echo "a2a-interop: $label did not come up at $url; see $LOG_DIR" >&2
  return 1
}

start_dotnet() { # port, mode [identity]
  local port="$1" mode="$2" identity="${3:-both}"
  A2A_IDENTITY="$identity" dotnet "$HOST_DLL" "http://127.0.0.1:$port" "$mode" >"$LOG_DIR/dotnet-$mode-$identity.log" 2>&1 &
  PIDS+=("$!")
  wait_for "http://127.0.0.1:$port/health" ".NET specialist ($mode)" "$!"
}

start_python() { # port, mode [identity]
  local port="$1" mode="$2" identity="${3:-both}"
  (cd "$PY_DIR" && exec uv run python -m tests.a2a_interop_server --port "$port" --mode "$mode" --identity "$identity") \
    >"$LOG_DIR/python-$mode-$identity.log" 2>&1 &
  PIDS+=("$!")
  wait_for "http://127.0.0.1:$port/health" "Python specialist ($mode)" "$!"
}

# No model key, ever.
export LLM_PROVIDER=openai OPENAI_API_KEY=
unset ANTHROPIC_API_KEY AZURE_OPENAI_KEY AZURE_OPENAI_API_KEY
export ENVIRONMENT=test
# Nothing listens here: a request that reached for the database fails at once instead of waiting.
export DATABASE_URL="postgresql://nobody:nobody@127.0.0.1:1/none"

echo "a2a-interop: building the .NET host and tests"
(cd "$NET_DIR" && dotnet build tests/ECommerceAgents.A2AInteropHost -v q -nologo) || exit 1
(cd "$NET_DIR" && dotnet build tests/ECommerceAgents.Shared.Tests -v q -nologo) || exit 1
HOST_DLL="$(ls "$NET_DIR"/tests/ECommerceAgents.A2AInteropHost/bin/Debug/net11.0/ECommerceAgents.A2AInteropHost.dll)"

STATUS=0

echo "a2a-interop: starting .NET specialists (ok, boom, miss, weird, token-mode)"
start_dotnet "$((BASE + 1))" ok || exit 1
start_dotnet "$((BASE + 2))" boom || exit 1
start_dotnet "$((BASE + 3))" miss || exit 1
start_dotnet "$((BASE + 4))" weird || exit 1
# B-14: a specialist that takes identity from the signed token alone (A2A_IDENTITY=token).
start_dotnet "$((BASE + 8))" ok token || exit 1
export DOTNET_SPECIALIST_URL_TOKEN="http://127.0.0.1:$((BASE + 8))"
export DOTNET_SPECIALIST_URL="http://127.0.0.1:$((BASE + 1))"
export DOTNET_SPECIALIST_URL_FAIL="http://127.0.0.1:$((BASE + 2))"
export DOTNET_SPECIALIST_URL_MISS="http://127.0.0.1:$((BASE + 3))"
export DOTNET_SPECIALIST_URL_WEIRD="http://127.0.0.1:$((BASE + 4))"

if [[ "${1:-}" == "--dotnet-host" ]]; then
  echo "DOTNET_SPECIALIST_URL=$DOTNET_SPECIALIST_URL"
  echo "a2a-interop: hosts running; Ctrl-C to stop"
  wait
  exit 0
fi

echo "a2a-interop: Python client -> .NET specialist"
(cd "$PY_DIR" && uv run pytest tests/test_a2a_dotnet_interop.py -v -p no:cacheprovider) || STATUS=1

echo "a2a-interop: starting Python specialists (ok, boom, miss, token-mode)"
start_python "$((BASE + 5))" ok || exit 1
start_python "$((BASE + 6))" boom || exit 1
start_python "$((BASE + 7))" miss || exit 1
start_python "$((BASE + 9))" ok token || exit 1
export PYTHON_SPECIALIST_URL_TOKEN="http://127.0.0.1:$((BASE + 9))"
export PYTHON_SPECIALIST_URL="http://127.0.0.1:$((BASE + 5))"
export PYTHON_SPECIALIST_URL_FAIL="http://127.0.0.1:$((BASE + 6))"
export PYTHON_SPECIALIST_URL_MISS="http://127.0.0.1:$((BASE + 7))"

echo ".NET client -> Python specialist"
(cd "$NET_DIR" && dotnet test tests/ECommerceAgents.Shared.Tests --no-build --filter "FullyQualifiedName~A2AInteropTests" -nologo) || STATUS=1

if [[ $STATUS -eq 0 ]]; then
  echo "a2a-interop: PASS (both directions)"
else
  echo "a2a-interop: FAIL; logs in $LOG_DIR" >&2
fi
exit $STATUS
