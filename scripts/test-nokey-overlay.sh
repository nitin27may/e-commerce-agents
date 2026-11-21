#!/usr/bin/env bash
# Checks that docker-compose.nokey.yml wins over the caller's environment (plan N9).
# Renders the merged config with hostile exported variables and asserts the
# replay settings survive on every service that calls a model. Needs docker
# compose v2, no daemon, no network.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

fail() { echo "FAIL: $*" >&2; exit 1; }

check() {
    local label="$1"; shift
    local out
    out="$(LLM_PROVIDER=openai OPENAI_API_KEY=sk-test MCP_ENABLED=true HANDOFF_AUTONOMOUS_MODE=false \
        ORCHESTRATION_MODE=handoff REPLAY_FIXTURES_DIR=/elsewhere DEMO_TOUR_ENABLED=false \
        docker compose "$@" config)" || fail "$label: compose config failed"
    local want
    for want in 'LLM_PROVIDER: replay' 'MCP_ENABLED: "false"' 'HANDOFF_AUTONOMOUS_MODE: "true"' \
                'ORCHESTRATION_MODE: tool' 'REPLAY_FIXTURES_DIR: /app/demo/fixtures/replay' \
                'DEMO_TOUR_ENABLED: "true"'; do
        # orchestrator + 5 specialists + seeder
        [ "$(grep -cF "$want" <<<"$out")" -ge 7 ] || fail "$label: '$want' missing on some service"
    done
    if grep -q 'sk-test' <<<"$out"; then fail "$label: the exported key reached a container"; fi
    echo "ok: $label"
}

check demo -f docker-compose.demo.yml -f docker-compose.nokey.yml
check source -f docker-compose.yml -f docker-compose.nokey.yml --profile agents --profile seed --profile frontend
echo "nokey-overlay: all checks passed"
