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
# service (/ready), the seeder's data is there, login + a read route work end to end, and one
# real chat turn runs through the HTTP path, the mode registry and the database-backed tools.
# That turn uses the return workflow, which makes no model call, so it needs no recorded
# fixture. What it does not prove: a turn that needs a model (that needs fixtures for the
# replay path) or the frontend.
#
# Usage:
#   scripts/compose-smoke.sh                                   # Python stack
#   scripts/compose-smoke.sh --compose-file docker-compose.dotnet.yml
#   scripts/compose-smoke.sh --mixed py-net                    # Python orchestrator, .NET specialists
#   scripts/compose-smoke.sh --mixed net-py                    # .NET orchestrator, Python specialists
#   A2A_TRANSPORT=a2a A2A_IDENTITY=token scripts/compose-smoke.sh --mixed py-net
#                                                              # (a distinct A2A_IDENTITY_KEY is generated)
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
MIXED_VARIANT=""
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
    --mixed) MIXED_VARIANT="$2"; shift 2 ;;
    --attach) ATTACH=true; shift ;;
    --keep) KEEP=true; shift ;;
    -h|--help) sed -n '2,28p' "$0"; exit 0 ;;
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

# A mixed variant layers docker-compose.mixed.yml over the base stack of its orchestrator
# (scripts/lib/mixed-env.sh exports the build variables that overlay interpolates).
if [[ -n "$MIXED_VARIANT" ]]; then
  # shellcheck source=lib/mixed-env.sh
  source "$REPO_ROOT/scripts/lib/mixed-env.sh"
  mixed_env "$MIXED_VARIANT" || fail "unknown mixed variant: $MIXED_VARIANT"
  COMPOSE_FILE="$MIXED_BASE_FILE + docker-compose.mixed.yml"
  COMPOSE=(docker compose -f "$MIXED_BASE_FILE" -f docker-compose.mixed.yml)
else
  COMPOSE=(docker compose -f "$COMPOSE_FILE")
fi

# Token identity needs a key of its own: under the default the key falls back to AGENT_SHARED_SECRET, which
# every caller already holds, so the token would prove nothing (docs/a2a.md). Generated per run, never stored.
if [[ "${A2A_IDENTITY:-both}" == "token" ]]; then
  export A2A_IDENTITY_KEY="${A2A_IDENTITY_KEY:-$(gen)}"
  [[ "$A2A_IDENTITY_KEY" != "$AGENT_SHARED_SECRET" ]] || fail "A2A_IDENTITY_KEY must differ from AGENT_SHARED_SECRET"
fi
echo "--- wire: A2A_TRANSPORT=${A2A_TRANSPORT:-legacy} A2A_IDENTITY=${A2A_IDENTITY:-both}"

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
# /ready proves the service answers, not that its first request-path call is warm: a cold .NET
# orchestrator pays JIT, a first bcrypt verify and its first pooled connection on the first login, and
# one such call timed out once in CI (net-py) and passed on re-run. Each read below gets a bounded,
# logged retry (3 attempts, 3 s apart), so a transient cold-start failure does not fail the job while a
# real failure still does after the last attempt.
retry3() {
  local what="$1" attempt out; shift
  for attempt in 1 2 3; do
    if out="$("$@")"; then printf '%s' "$out"; return 0; fi
    echo "retry  $what failed (attempt $attempt/3)" >&2
    (( attempt < 3 )) && sleep 3
  done
  return 1
}

login="$(retry3 login curl -sf -m 20 -X POST "$ORCH_URL/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$SMOKE_EMAIL\",\"password\":\"$SMOKE_PASSWORD\"}")" \
  || { dump_logs; fail "login as $SMOKE_EMAIL failed (is the stack seeded?)"; }
token="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])' <<<"$login")" \
  || fail "login response had no access_token"
echo "login  ok ($SMOKE_EMAIL)"

products="$(retry3 products curl -sf -m 20 "$ORCH_URL/api/products?limit=5" -H "Authorization: Bearer $token")" \
  || { dump_logs; fail "GET /api/products failed"; }
python3 - "$products" <<'PY' || fail "GET /api/products returned no products: $products"
import json, sys
body = json.loads(sys.argv[1])
assert len(body["products"]) > 0 and body["total"] > 0, body
print(f"products ok ({len(body['products'])} returned, {body['total']} total)")
PY

# ── One real chat turn, no model ─────────────────────────────
# The return workflow (mode workflow:return-replace) calls no model: it checks eligibility, asks the
# approval policy and pauses or proceeds. A turn through /api/chat therefore exercises the route, auth,
# the mode registry, the workflow engine and the database tools for real, keyless. It would have caught
# a mode that is dead in every image. The seeded customer whose latest order is delivered is found by
# asking the API, so the check does not depend on one fixed user.
# >>> chat-turn
python3 - "$ORCH_URL" "$SMOKE_PASSWORD" <<'PY' || { dump_logs; fail "the keyless chat turn (workflow:return-replace) failed"; }
import json, sys, urllib.error, urllib.request

base, password = sys.argv[1], sys.argv[2]
# The seeder's customers (scripts/seed.py USERS); all share one password.
CUSTOMERS = [
    "alice.johnson", "bob.smith", "carol.davis", "dave.wilson", "emma.brown", "frank.miller", "grace.lee",
    "henry.taylor", "iris.chen", "jack.anderson", "kate.martinez", "leo.garcia", "mia.robinson",
]
REFUSALS = ("Order must be in", "Return window expired", "A return already exists", "No orders found")


def call(method, path, token=None, body=None):
    req = urllib.request.Request(
        base + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})},
    )
    with urllib.request.urlopen(req, timeout=60) as res:
        return json.load(res)


for name in CUSTOMERS:
    email = f"{name}@gmail.com"
    try:
        token = call("POST", "/api/auth/login", body={"email": email, "password": password})["access_token"]
        orders = call("GET", "/api/orders?limit=1", token)
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        # One slow or reset call (cold orchestrator) must not abandon the whole check: try the next customer.
        print(f"chat   skip {email}: {type(exc).__name__}: {exc}", file=sys.stderr)
        continue
    newest = orders.get("orders", [])[:1]  # newest first, on both stacks
    if not newest or newest[0].get("status") != "delivered":
        continue
    request = {"message": "I want to return my most recent order, it arrived damaged", "mode": "workflow:return-replace"}
    try:
        turn = call("POST", "/api/chat", token, request)
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        # The turn creates nothing before the approval gate, so one logged retry is safe.
        print(f"chat   retrying after {type(exc).__name__}: {exc}", file=sys.stderr)
        turn = call("POST", "/api/chat", token, request)
    text = turn.get("response") or ""
    steps = turn.get("agents_involved") or []
    assert text, f"empty response: {turn}"
    assert not turn.get("replay_miss"), f"unexpected replay miss: {turn}"
    assert not any(r in text for r in REFUSALS), f"the workflow refused the return instead of running: {text!r}"
    assert len(steps) >= 2, f"the workflow ran fewer than two steps: {steps}"
    print(f"chat   ok ({email}, workflow:return-replace, steps={steps})")
    break
else:
    raise SystemExit("no seeded customer has a delivered latest order to return")
PY
# <<< chat-turn

echo "PASS: $COMPOSE_FILE boots keyless and serves /ready, login and products"
