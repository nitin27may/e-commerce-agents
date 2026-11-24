#!/usr/bin/env bash
#
# Keyless boot smoke for the feature flags that are off by default, all on at once.
#
# compose-smoke.sh proves the default stack. The flagged features (event autonomy, the return lifecycle, merchant
# proposals, HITL) each have unit and integration tests, but until this script they had never been booted together
# through the real routes and the outbox worker. It starts the Python stack under LLM_PROVIDER=replay with
#
#   AUTONOMY_ENABLED  AUTONOMY_SIMULATOR_ENABLED  RETURN_LIFECYCLE_ENABLED
#   MERCHANT_OPS_SCHEDULE_ENABLED  HITL_ENABLED
#
# on, logs in as an admin, a seller and a customer, and drives:
#
#   1. event autonomy   POST /api/admin/events/simulate for small, medium and large, then waits for the worker:
#                       small -> an executed, auto decision; medium -> executed, auto_notify; large -> an
#                       awaiting_approval decision and a pending `autonomous_action` approval in the staff inbox.
#   2. return lifecycle a customer return over the approval threshold pauses for approval; an admin approves it; the
#                       run waits for a carrier scan (a `carrier_scan` wait, no inbox row); the simulator posts the scan;
#                       the worker resumes it into inspection; staff accept it and the return is refunded.
#   3. merchant         an admin runs proposals for a seller; the seller sees an open batch of proposal lines.
#
# No model is called and no key is accepted (compose-smoke.sh refuses one). Nothing here is a secret: the passwords
# are the seeded demo accounts.
#
# Usage:
#   scripts/flag-smoke.sh               # boot, drive, tear down
#   scripts/flag-smoke.sh --dry-run     # print the flags, the compose project and the checks; start nothing
#   scripts/flag-smoke.sh --keep        # leave the stack up afterwards
#   scripts/flag-smoke.sh --attach      # a stack with the flags is already up: only drive it
#
# It runs in its own compose project (COMPOSE_PROJECT_NAME, default eca-flag-smoke), never the default one: the
# teardown is `down -v`, and the default project's pgdata is a developer's real data. The stack binds the usual
# ports (8080-8085, 5432, 6379), so serialize with other runs.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 1

DRY_RUN=false
KEEP=false
ATTACH=false
ORCH_URL="${ORCH_URL:-http://localhost:${ORCH_PORT:-8080}}"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-eca-flag-smoke}"

FLAGS=(AUTONOMY_ENABLED AUTONOMY_SIMULATOR_ENABLED RETURN_LIFECYCLE_ENABLED MERCHANT_OPS_SCHEDULE_ENABLED HITL_ENABLED)
CHECKS=(
  "autonomy: small simulated delay is executed under the auto tier"
  "autonomy: medium simulated delay is executed under the auto_notify tier"
  "autonomy: large simulated delay waits for approval and opens an autonomous_action inbox row"
  "returns: an over-threshold return pauses for approval before anything is created"
  "returns: an admin approving it leaves a carrier_scan wait and no new inbox row"
  "returns: the simulated scan is resumed by the outbox worker into an inspection wait"
  "returns: staff accepting the inspection refunds the return"
  "merchant: an admin run produces an open proposal batch the owning seller can read"
)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    --keep) KEEP=true; shift ;;
    --attach) ATTACH=true; shift ;;
    -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

fail() { echo "::error::$*" >&2; echo "FAIL: $*" >&2; exit 1; }

if $DRY_RUN; then
  echo "flag-smoke (dry run): nothing is started"
  echo "compose project: $COMPOSE_PROJECT_NAME"
  echo "provider: LLM_PROVIDER=replay"
  for f in "${FLAGS[@]}"; do echo "flag: $f=true"; done
  for c in "${CHECKS[@]}"; do echo "check: $c"; done
  exit 0
fi

export LLM_PROVIDER=replay
for f in "${FLAGS[@]}"; do export "$f=true"; done
# The seeded orders are old and the stock thresholds are the shipped ones: nothing else is changed.

teardown() {
  $ATTACH && return 0
  $KEEP && return 0
  docker compose --profile agents --profile seed down -v --remove-orphans >/dev/null 2>&1 || true
}
trap teardown EXIT

if ! $ATTACH; then
  # compose-smoke.sh boots, seeds, waits for /ready, logs in and runs its own return turn; it inherits the flags.
  "$REPO_ROOT/scripts/compose-smoke.sh" --keep || fail "the stack did not boot with the flags on"
fi

python3 - "$ORCH_URL" <<'PY' || fail "a flagged feature did not behave as documented (see above)"
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

base = sys.argv[1]
ADMIN = ("admin.demo@gmail.com", "admin123")
CUSTOMER_PASSWORD = "customer123"


class Step(Exception):
    pass


def call(method, path, token=None, body=None, ok=(200,)):
    req = urllib.request.Request(
        base + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            status, raw = res.status, res.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    if status not in ok:
        raise Step(f"{method} {path} answered {status}: {raw[:300]!r}")
    return json.loads(raw) if raw else {}


def login(email, password):
    return call("POST", "/api/auth/login", body={"email": email, "password": password})["access_token"]


def wait_for(what, probe, seconds=90):
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        last = probe()
        if last:
            return last
        time.sleep(2)
    raise Step(f"timed out after {seconds}s waiting for {what}")


def sql(statement):
    out = subprocess.run(
        ["docker", "compose", "exec", "-T", "db", "psql", "-U", "ecommerce", "-d", "ecommerce_agents", "-At", "-c", statement],
        capture_output=True, text=True, timeout=60, check=False,
    )
    if out.returncode != 0:
        raise Step(f"psql failed: {out.stderr.strip()[:300]}")
    return out.stdout.strip()


def ok(line):
    print(f"flags  ok  {line}", flush=True)


admin = login(*ADMIN)

# ── 1. event autonomy ─────────────────────────────────────────────────────────────────────────────────────────
sim = {}
for scenario in ("small", "medium", "large"):
    sim[scenario] = call("POST", "/api/admin/events/simulate", admin, {"scenario": scenario})
    if not sim[scenario].get("enqueued"):
        raise Step(f"simulate {scenario} enqueued nothing: {sim[scenario]}")


def decision_for(order_id):
    rows = call("GET", "/api/admin/decisions?limit=200", admin)["decisions"]
    return next((d for d in rows if str(d.get("order_id")) == order_id and d.get("state") != "pending"), None)


results = {}
for scenario in ("small", "medium", "large"):
    order_id = sim[scenario]["order_id"]
    results[scenario] = wait_for(f"a decision for the {scenario} event", lambda o=order_id: decision_for(o))

small, medium, large = results["small"], results["medium"], results["large"]
if small.get("tier") != "auto" or small.get("state") != "executed":
    raise Step(f"small should be an executed auto decision: {small}")
ok("autonomy small -> executed, tier auto")
if medium.get("tier") != "auto_notify" or medium.get("state") != "executed":
    raise Step(f"medium should be an executed auto_notify decision: {medium}")
ok("autonomy medium -> executed, tier auto_notify")
if large.get("state") != "awaiting_approval":
    raise Step(f"large should be awaiting approval: {large}")
pending = call("GET", "/api/approvals?limit=200", admin)["approvals"]
if not any(a.get("kind") == "autonomous_action" for a in pending):
    raise Step(f"no autonomous_action approval in the staff inbox: {[a.get('kind') for a in pending]}")
ok("autonomy large -> awaiting_approval, autonomous_action in the inbox")

# ── 2. return lifecycle ───────────────────────────────────────────────────────────────────────────────────────
# A customer whose newest order is delivered and over the approval threshold, so the policy asks a person.
CUSTOMERS = [
    "alice.johnson", "bob.smith", "carol.davis", "dave.wilson", "emma.brown", "frank.miller", "grace.lee",
    "henry.taylor", "iris.chen", "jack.anderson", "kate.martinez", "leo.garcia", "mia.robinson",
]
customer = None
for name in CUSTOMERS:
    email = f"{name}@gmail.com"
    token = login(email, CUSTOMER_PASSWORD)
    newest = (call("GET", "/api/orders?limit=1", token).get("orders") or [None])[0]
    if newest and newest.get("status") == "delivered" and float(newest.get("total") or 0) > 500:
        customer = (email, token, newest)
        break
if customer is None:
    raise Step("no seeded customer has a delivered newest order over 500")
email, token, order = customer

known = {a["id"] for a in call("GET", "/api/approvals?limit=200", admin)["approvals"]}
turn = call("POST", "/api/chat", token, {"message": "I want to return my most recent order, it arrived damaged", "mode": "workflow:return-replace"})
if "needs approval" not in (turn.get("response") or ""):
    raise Step(f"the return did not pause for approval: {turn.get('response')!r}")


def return_approval():
    rows = call("GET", "/api/approvals?limit=200", admin)["approvals"]
    return next((a for a in rows if a.get("kind") == "return_approval" and a["id"] not in known), None)


approval = wait_for("the return approval in the inbox", return_approval)
order_detail = call("GET", f"/api/orders/{order['id']}", token)
if order_detail.get("return"):
    raise Step(f"a return row exists before anyone approved it: {order_detail['return']}")
ok(f"returns over-threshold return paused for approval, nothing created ({email})")

inbox_before = {a["id"] for a in call("GET", "/api/approvals?limit=200", admin)["approvals"]}
call("POST", f"/api/approvals/{approval['id']}/decide", admin, {"decision": "approve", "note": "flag-smoke"})
inbox_after = {a["id"] for a in call("GET", "/api/approvals?limit=200", admin)["approvals"]}
if inbox_after - inbox_before:
    raise Step("approving the return opened a new inbox row; a carrier wait is not an approval")


def created_return():
    ret = call("GET", f"/api/orders/{order['id']}", token).get("return")
    return ret if ret and ret.get("id") else None


ret = wait_for("the approved return to be created", created_return)
return_id = ret["id"]
ok("returns approved: return created, carrier wait is not an inbox row")

call("POST", f"/api/admin/returns/{return_id}/simulate-scan", admin, {"carrier": "UPS"})


def inspection():
    rows = call("GET", "/api/staff/returns/inspections", admin)["inspections"]
    return next((r for r in rows if r["return_id"] == return_id), None)


wait_for("the worker to resume the scanned return into inspection", inspection)
ok("returns simulated scan resumed by the worker into inspection")

call("POST", f"/api/staff/returns/{return_id}/inspect", admin, {"outcome": "accept", "resolution": "refund", "note": "flag-smoke"})


def refunded():
    ret = call("GET", f"/api/orders/{order['id']}", token).get("return") or {}
    return ret if ret.get("status") == "refunded" else None


wait_for("the accepted inspection to refund the return", refunded)
ok("returns inspection accepted -> refunded")

# ── 3. merchant proposals ─────────────────────────────────────────────────────────────────────────────────────
seller = "seller.demo@gmail.com"
run = call("POST", "/api/seller/proposals/run", admin, {"seller_email": seller})
seller_token = login(seller, "seller123")
listing = call("GET", "/api/seller/proposals", seller_token)
batches = listing.get("proposals") or []
if not batches:
    raise Step(f"the seller sees no proposal batch after a run: run={run} listing={listing}")
ok(f"merchant {len(batches)} proposal batch(es) visible to the seller")
first = call("GET", f"/api/seller/proposals/{batches[0]['id']}", seller_token)
if not (first.get("lines") or first.get("proposal", {}).get("lines")):
    raise Step(f"the proposal batch has no lines: {json.dumps(first)[:300]}")
ok("merchant the batch carries lines")
booked = sql("SELECT count(*) FROM outbox WHERE topic = 'merchant.daily_run_due' AND processed_at IS NULL")
if booked == "0":
    raise Step("MERCHANT_OPS_SCHEDULE_ENABLED is on but the worker booked no merchant.daily_run_due timer")
ok("merchant the worker booked the daily run timer")

# ── the worker parked nothing ─────────────────────────────────────────────────────────────────────────────────
broken = sql("SELECT topic || ': ' || left(last_error, 120) FROM outbox WHERE last_error IS NOT NULL LIMIT 5")
if broken:
    raise Step(f"outbox rows failed in the worker:\n{broken}")
ok("outbox no row has an error")
print("PASS: the flagged features ran end to end through the real routes and the outbox worker")
PY

echo "PASS: flag-smoke ($COMPOSE_PROJECT_NAME)"
