#!/usr/bin/env bash
#
# Run the same Playwright suite against both backends, in turn.
#
# There is one frontend and each compose file points it at its own
# orchestrator, so "does the UI work on .NET?" is only answerable by actually
# driving it. This is the gate for the .NET parity work (issue #33): every item
# there closes by deleting a line from `web/e2e/parity-gaps.ts`, and when the
# `dotnet` list is empty, parity is done.
#
# The stacks run serially, not in parallel: docker-compose.dotnet.yml binds the
# same ports as docker-compose.yml (3000, 5432, 6379, 8080-8085, 8090, 9001),
# so both cannot be up at once.
#
# This used to require a frontend rebuild per stack, because the API URL was a
# NEXT_PUBLIC_* variable inlined into the JS chunks at build time — starting an
# existing build with a different value changed nothing, and the mismatch
# surfaced as a confusing 401 when a token from one backend was presented to
# the other. That is no longer true: the frontend proxies /api/* server-side
# and reads `ORCHESTRATOR_URL` per request, so the same image serves either
# backend and only the environment variable differs. `up --build` below is now
# only for the backends.
#
# The failure it caused has not gone away, it moved. A frontend started with
# the wrong `ORCHESTRATOR_URL` still logs in against backend A while every
# API-level assertion queries backend B, and the base-URL override is
# `E2E_BASE_URL` — a run that sets some other variable silently drives
# whichever frontend is on :3000. Both are caught at login by
# `assertFrontendTalksToOrchUrl` in the parity spec.
#
# Usage:
#   scripts/e2e-both-stacks.sh                  # both backends, whole suite
#   scripts/e2e-both-stacks.sh --only dotnet    # one backend
#   scripts/e2e-both-stacks.sh -- e2e/orchestration-parity.spec.ts
#
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

STACKS=("python" "dotnet")
PLAYWRIGHT_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --only) STACKS=("$2"); shift 2 ;;
    --) shift; PLAYWRIGHT_ARGS=("$@"); break ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

compose_file_for() {
  [[ "$1" == "dotnet" ]] && echo "docker-compose.dotnet.yml" || echo "docker-compose.yml"
}

declare -A RESULTS=()

teardown() {
  local file="$1"
  echo "--- tearing down ($file)"
  docker compose -f "$file" --profile seed --profile agents --profile mcp --profile frontend down --remove-orphans >/dev/null 2>&1 || true
}

for stack in "${STACKS[@]}"; do
  file="$(compose_file_for "$stack")"

  echo "======================================================================"
  echo "  $stack  ($file)"
  echo "======================================================================"

  # Make sure the *other* stack isn't holding the ports.
  teardown "$(compose_file_for python)"
  teardown "$(compose_file_for dotnet)"

  echo "--- starting stack"
  if ! docker compose -f "$file" --profile seed --profile agents --profile mcp --profile frontend up -d --build; then
    echo "!!! $stack failed to start" >&2
    RESULTS[$stack]="STACK FAILED TO START"
    continue
  fi

  # Port 3000 is not guaranteed to be the frontend — another service can
  # already own it, in which case compose brings the frontend up with no
  # published port and every spec fails at login for unrelated reasons.
  # E2E_BASE_URL lets a developer point at wherever it really is.
  BASE_URL="${E2E_BASE_URL:-http://localhost:3000}"

  echo "--- waiting for the frontend ($BASE_URL) and orchestrator"
  ready=0
  for _ in $(seq 1 90); do
    if curl -sf -m2 "$BASE_URL" >/dev/null 2>&1 \
       && curl -sf -m2 http://localhost:8080/health >/dev/null 2>&1; then
      ready=1; break
    fi
    sleep 2
  done

  if [[ "$ready" -ne 1 ]]; then
    echo "!!! $stack never became ready" >&2
    docker compose -f "$file" ps
    RESULTS[$stack]="NEVER BECAME READY"
    teardown "$file"
    continue
  fi

  echo "--- running Playwright against $stack"
  if (cd web && BACKEND_STACK="$stack" E2E_BASE_URL="$BASE_URL" npx playwright test "${PLAYWRIGHT_ARGS[@]}"); then
    RESULTS[$stack]="PASSED"
  else
    RESULTS[$stack]="FAILED"
  fi

  teardown "$file"
done

echo
echo "======================================================================"
echo "  summary"
echo "======================================================================"
exit_code=0
for stack in "${STACKS[@]}"; do
  printf '  %-8s %s\n' "$stack" "${RESULTS[$stack]:-NOT RUN}"
  [[ "${RESULTS[$stack]:-}" == "PASSED" ]] || exit_code=1
done

echo
echo "Skipped tests on .NET are recorded gaps, not silence — see web/e2e/parity-gaps.ts."
echo "When that file's dotnet list is empty, .NET parity is done."
exit "$exit_code"
