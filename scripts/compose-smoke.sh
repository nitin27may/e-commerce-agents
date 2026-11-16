#!/usr/bin/env bash
#
# Keyless boot smoke for a compose stack (A-9, #85).
#
# Boots the stack with LLM_PROVIDER=replay, waits for /ready on the orchestrator and every
# specialist, logs in as a seeded customer and lists products. It needs no model key, makes no
# model call, and refuses to run if one is present in the environment: nothing that costs money
# may sit behind an automatic trigger (see evals.yml).
#
# What it proves: every image builds and starts, the migrated schema is visible to every
# service (/ready), the seeder's data is there, and login + a read route work end to end. What
# it does not prove: a chat turn (that needs fixtures for the replay path) or the frontend.
#
# Usage:
#   scripts/compose-smoke.sh                                   # Python stack
#   scripts/compose-smoke.sh --compose-file docker-compose.dotnet.yml
#   scripts/compose-smoke.sh --attach                          # services already running: only
#                                                              # wait, log in, list products
#   scripts/compose-smoke.sh --keep                            # leave the stack up afterwards
#
# Environment: READY_TIMEOUT (seconds per service, default 240); ORCH_PORT (8080),
# SPECIALIST_PORT_BASE (8081) and SPECIALIST_HOST (localhost) for --attach.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

COMPOSE_FILE="docker-compose.yml"
ATTACH=false
KEEP=false
READY_TIMEOUT="${READY_TIMEOUT:-240}"
# Ports are overridable so --attach can target a stack that is not on the compose defaults.
ORCH_PORT="${ORCH_PORT:-8080}"
SPECIALIST_PORT_BASE="${SPECIALIST_PORT_BASE:-8081}"   # product-discovery; the other four follow
SPECIALIST_HOST="${SPECIALIST_HOST:-localhost}"
ORCH_URL="${ORCH_URL:-http://$SPECIALIST_HOST:$ORCH_PORT}"
SMOKE_EMAIL="${SMOKE_EMAIL:-alice.johnson@gmail.com}"
SMOKE_PASSWORD="${SMOKE_PASSWORD:-customer123}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --compose-file) COMPOSE_FILE="$2"; shift 2 ;;
    --attach) ATTACH=true; shift ;;
    --keep) KEEP=true; shift ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

fail() { echo "::error::$*" >&2; echo "FAIL: $*" >&2; exit 1; }

# ── No key may be present ────────────────────────────────────
# Any *_API_KEY (and Azure's key) with a value means a real provider could be reached.
leaked="$(env | grep -E '^[A-Za-z0-9_]*(_API_KEY|AZURE_OPENAI_KEY)=.+' | cut -d= -f1 | tr '\n' ' ')"
[[ -z "$leaked" ]] || fail "refusing to run with credentials in the environment: $leaked"

# Secrets: generated per run, never read from a file or a CI secret.
gen() { if command -v openssl >/dev/null 2>&1; then openssl rand -hex 32; else python3 -c 'import secrets;print(secrets.token_hex(32))'; fi; }
export LLM_PROVIDER=replay
export JWT_SECRET="${JWT_SECRET:-$(gen)}"
export AGENT_SHARED_SECRET="${AGENT_SHARED_SECRET:-$(gen)}"
export OAUTH_SEED_KEY="${OAUTH_SEED_KEY:-$(gen)}"
export ENVIRONMENT="${ENVIRONMENT:-development}"

COMPOSE=(docker compose -f "$COMPOSE_FILE")

dump_logs() {
  $ATTACH && return 0
  echo "::group::compose ps"; "${COMPOSE[@]}" --profile agents --profile seed ps -a || true; echo "::endgroup::"
  echo "::group::compose logs (tail)"; "${COMPOSE[@]}" --profile agents --profile seed logs --no-color --tail=60 || true; echo "::endgroup::"
}
teardown() {
  $ATTACH && return 0
  $KEEP && return 0
  "${COMPOSE[@]}" --profile agents --profile seed down -v --remove-orphans >/dev/null 2>&1 || true
}
trap teardown EXIT

if ! $ATTACH; then
  echo "--- boot: $COMPOSE_FILE (LLM_PROVIDER=replay)"
  # Same order as scripts/dev.sh. The seeder (migrate, then seed) must finish before the
  # agents start: the orchestrator's depends_on waits for every specialist's /ready, which
  # needs schema_migrations, so seeding after `up` would deadlock.
  # aspire has no healthcheck (its image has no shell to run one), so it is
  # started without --wait; db and redis are the infrastructure that gates.
  "${COMPOSE[@]}" up -d aspire || { dump_logs; fail "aspire did not start"; }
  "${COMPOSE[@]}" up -d --build --wait db redis || { dump_logs; fail "infrastructure did not become healthy"; }
  "${COMPOSE[@]}" --profile seed run --rm --build seeder || { dump_logs; fail "migrate/seed failed"; }
  "${COMPOSE[@]}" --profile agents up -d --build --wait || { dump_logs; fail "agents did not become healthy"; }
fi

# ── /ready everywhere ────────────────────────────────────────
declare -A TARGETS=(
  [orchestrator]="$ORCH_URL/ready"
  [product-discovery]="http://$SPECIALIST_HOST:$((SPECIALIST_PORT_BASE + 0))/ready"
  [order-management]="http://$SPECIALIST_HOST:$((SPECIALIST_PORT_BASE + 1))/ready"
  [pricing-promotions]="http://$SPECIALIST_HOST:$((SPECIALIST_PORT_BASE + 2))/ready"
  [review-sentiment]="http://$SPECIALIST_HOST:$((SPECIALIST_PORT_BASE + 3))/ready"
  [inventory-fulfillment]="http://$SPECIALIST_HOST:$((SPECIALIST_PORT_BASE + 4))/ready"
)
for name in orchestrator product-discovery order-management pricing-promotions review-sentiment inventory-fulfillment; do
  url="${TARGETS[$name]}"
  deadline=$((SECONDS + READY_TIMEOUT))
  until body="$(curl -sf -m 5 "$url")"; do
    if (( SECONDS >= deadline )); then
      dump_logs
      fail "$name never became ready ($url): $(curl -s -m 5 "$url" || echo 'no response')"
    fi
    sleep 2
  done
  # Same JSON shape on both stacks: status, service, checks.{database,migrations,redis}.
  python3 - "$name" "$body" <<'PY' || fail "$name /ready body is malformed: $body"
import json, sys
name, raw = sys.argv[1], sys.argv[2]
body = json.loads(raw)
assert body["status"] == "ready", body
assert {"database", "migrations", "redis"} <= set(body["checks"]), body
assert body["checks"]["database"]["ok"] and body["checks"]["migrations"]["ok"], body
print(f"ready  {name:<22} redis={'ok' if body['checks']['redis']['ok'] else 'down (non-fatal)'}")
PY
done

# ── Log in and read ──────────────────────────────────────────
login="$(curl -sf -m 20 -X POST "$ORCH_URL/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$SMOKE_EMAIL\",\"password\":\"$SMOKE_PASSWORD\"}")" \
  || { dump_logs; fail "login as $SMOKE_EMAIL failed (is the stack seeded?)"; }
token="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])' <<<"$login")" \
  || fail "login response had no access_token"
echo "login  ok ($SMOKE_EMAIL)"

products="$(curl -sf -m 20 "$ORCH_URL/api/products?limit=5" -H "Authorization: Bearer $token")" \
  || { dump_logs; fail "GET /api/products failed"; }
python3 - "$products" <<'PY' || fail "GET /api/products returned no products: $products"
import json, sys
body = json.loads(sys.argv[1])
assert len(body["products"]) > 0 and body["total"] > 0, body
print(f"products ok ({len(body['products'])} returned, {body['total']} total)")
PY

echo "PASS: $COMPOSE_FILE boots keyless and serves /ready, login and products"
