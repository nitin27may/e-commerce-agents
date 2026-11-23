#!/usr/bin/env bash
# Smoke test for a deployed stack, through the frontend proxy (the only public surface).
#
#   FRONTEND_URL=https://ca-frontend.<domain> E2E_STAFF_PASSWORD=... scripts/azure/smoke.sh [--quick]
#   scripts/azure/smoke.sh --local          # against a running compose stack: http://localhost:3000, demo passwords
#
# --quick (what postprovision runs, spends nothing):
#   1. every active revision is Healthy and job-db-init's latest execution Succeeded   (needs az + AZURE_RESOURCE_GROUP)
#   2. the frontend answers 200
#   3. admin and alice log in through the proxy
#   4. products are listed (the database was seeded)
# Full run adds:
#   5. one KEYED chat turn: a ```product card with a non-empty id, and SSE timing (first frame < 15 s, > 10 frames)
#   6. one approval with no model: a return in workflow:return-replace (over the 500 threshold when a seeded
#      order allows it) is paused, appears in the staff inbox, is decided by a reviewer of the right role, and the
#      run timeline shows it finished.
# Steps 5 and 6 need the stack to have a model (5) and are skipped by --quick.
#
# Environment: FRONTEND_URL, E2E_STAFF_PASSWORD (admin, support), SMOKE_CUSTOMER_PASSWORD (default customer123),
# AZURE_RESOURCE_GROUP, DB_INIT_JOB_NAME, SMOKE_FIRST_FRAME_SECONDS (15), SMOKE_MIN_FRAMES (10), SMOKE_WAIT_SECONDS (300).
# Not verified against a real subscription; the Azure-only steps (1) are the least certain.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1

QUICK=0 LOCAL=0
while [ $# -gt 0 ]; do
    case "$1" in
        --quick) QUICK=1; shift ;;
        --local) LOCAL=1; shift ;;
        -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "ok    $*"; }

if [ "$LOCAL" = "1" ]; then
    FRONTEND_URL="${FRONTEND_URL:-http://localhost:3000}"
    ADMIN_PASSWORD="${E2E_STAFF_PASSWORD:-admin123}"
    SUPPORT_PASSWORD="${E2E_STAFF_PASSWORD:-support123}"
else
    [ -n "${FRONTEND_URL:-}" ] || fail "FRONTEND_URL is not set"
    [ -n "${E2E_STAFF_PASSWORD:-}" ] || fail "E2E_STAFF_PASSWORD is not set (azd env get-value SEED_STAFF_PASSWORD)"
    ADMIN_PASSWORD="$E2E_STAFF_PASSWORD"
    SUPPORT_PASSWORD="$E2E_STAFF_PASSWORD"
fi
FRONTEND_URL="${FRONTEND_URL%/}"
# Seconds to wait for revisions and the frontend to come up before a check fails (0 = fail on the first look).
SMOKE_WAIT_SECONDS="${SMOKE_WAIT_SECONDS:-300}"
CUSTOMER_PASSWORD="${SMOKE_CUSTOMER_PASSWORD:-customer123}"
export FRONTEND_URL ADMIN_PASSWORD SUPPORT_PASSWORD CUSTOMER_PASSWORD
export SMOKE_FIRST_FRAME_SECONDS="${SMOKE_FIRST_FRAME_SECONDS:-15}" SMOKE_MIN_FRAMES="${SMOKE_MIN_FRAMES:-10}"
command -v python3 >/dev/null || fail "python3 is required"
command -v curl >/dev/null || fail "curl is required"

# ---- 1. revisions and job (Azure only) -------------------------------------------------------
if [ -n "${AZURE_RESOURCE_GROUP:-}" ] && command -v az >/dev/null 2>&1; then
    # Right after a provision, revisions are still starting (health 'None' or 'Unhealthy' for a minute or two): poll.
    wait_deadline=$((SECONDS + SMOKE_WAIT_SECONDS))
    while :; do
        unhealthy=""
        for app in $(az containerapp list --resource-group "$AZURE_RESOURCE_GROUP" --query '[].name' --output tsv); do
            health="$(az containerapp revision list --name "$app" --resource-group "$AZURE_RESOURCE_GROUP" \
                --query '[?properties.active] | [0].properties.healthState' --output tsv)"
            [ "$health" = "Healthy" ] || unhealthy="$unhealthy $app($health)"
        done
        [ -n "$unhealthy" ] || break
        [ "$SECONDS" -lt "$wait_deadline" ] || fail "revisions not Healthy after ${SMOKE_WAIT_SECONDS}s:$unhealthy"
        echo "wait  revisions not Healthy yet:$unhealthy" >&2
        sleep 10
    done
    pass "every active revision is Healthy"
    if [ -n "${DB_INIT_JOB_NAME:-}" ]; then
        status="$(az containerapp job execution list --name "$DB_INIT_JOB_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
            --query 'sort_by(@, &properties.startTime) | [-1].properties.status' --output tsv)"
        [ "$status" = "Succeeded" ] || fail "$DB_INIT_JOB_NAME latest execution is '$status'"
        pass "$DB_INIT_JOB_NAME succeeded"
    fi
else
    echo "skip  revision and job checks (no AZURE_RESOURCE_GROUP or az)"
fi

# ---- 2. frontend -----------------------------------------------------------------------------
wait_deadline=$((SECONDS + SMOKE_WAIT_SECONDS))
while :; do
    code="$(curl -s -o /dev/null -m 20 -L -w '%{http_code}' "$FRONTEND_URL/")" || code=000
    [ "$code" != "200" ] || break
    [ "$SECONDS" -lt "$wait_deadline" ] || break
    echo "wait  frontend answered $code; retrying" >&2
    sleep 10
done
[ "$code" = "200" ] || fail "frontend $FRONTEND_URL/ answered $code (restricted ingress? this machine's IP must be allowed)"
pass "frontend answers 200"

# ---- 3, 4, 5, 6: through the proxy ---------------------------------------------------------------
QUICK="$QUICK" python3 - <<'PY' || exit 1
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ["FRONTEND_URL"]
QUICK = os.environ["QUICK"] == "1"


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    sys.exit(1)


def call(method, path, token=None, body=None, timeout=60):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.load(res)
    except urllib.error.HTTPError as exc:
        fail(f"{method} {path} -> {exc.code} {exc.read()[:200]!r}")


def login(email, password):
    try:
        return call("POST", "/api/auth/login", body={"email": email, "password": password})["access_token"]
    except KeyError:
        fail(f"login as {email} returned no access_token")


admin = login("admin.demo@gmail.com", os.environ["ADMIN_PASSWORD"])
print("ok    admin login through the proxy")
alice = login("alice.johnson@gmail.com", os.environ["CUSTOMER_PASSWORD"])
print("ok    alice login through the proxy")
products = call("GET", "/api/products?limit=5", alice)
if not products.get("products") or products.get("total", 0) < 1:
    fail(f"no products listed: {products}")
print(f"ok    products listed ({products['total']} total)")
if QUICK:
    print("PASS: quick smoke")
    sys.exit(0)


def stream(token, body):
    """POST /api/chat/stream; returns (seconds to first frame, frames, text, other events)."""
    req = urllib.request.Request(
        BASE + "/api/chat/stream",
        method="POST",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    started = time.monotonic()
    first = None
    frames = 0
    text: list[str] = []
    events: list[tuple[str, str]] = []
    buffer = ""
    with urllib.request.urlopen(req, timeout=240) as res:
        while True:
            chunk = res.read1(65536) if hasattr(res, "read1") else res.read(4096)
            if not chunk:
                break
            buffer += chunk.decode("utf-8", "replace")
            *complete, buffer = buffer.split("\n\n")
            for raw in complete:
                lines = raw.split("\n")
                kind = next((ln[7:].strip() for ln in lines if ln.startswith("event: ")), "")
                data = "\n".join(ln[6:] for ln in lines if ln.startswith("data: "))
                if not any(ln.startswith(("data:", "event:")) for ln in lines):
                    continue
                if first is None:
                    first = time.monotonic() - started
                frames += 1
                if data == "[DONE]":
                    continue
                if kind == "":
                    text.append(data)
                else:
                    events.append((kind, data))
    return (first if first is not None else 1e9), frames, "".join(text), events


# ---- 5. keyed chat turn: a product card and SSE timing --------------------------------------------
first, frames, text, events = stream(alice, {"message": "Show me some laptops with prices"})
cards = re.findall(r"```products?\s*([\[{][\s\S]*?)```", text)
ids = []
for raw in cards:
    try:
        parsed = json.loads(raw)
    except ValueError:
        continue
    for item in parsed if isinstance(parsed, list) else [parsed]:
        ident = item.get("id") or item.get("product_id")
        if ident:
            ids.append(str(ident))
if not ids:
    fail(f"chat turn produced no product card with a non-empty id. text={text[:300]!r}")
print(f"ok    chat turn rendered {len(ids)} product card(s), first id {ids[0]}")
limit = float(os.environ["SMOKE_FIRST_FRAME_SECONDS"])
if first > limit:
    fail(f"first SSE frame after {first:.1f}s (limit {limit:.0f}s): the stream is buffered or the model is slow")
if frames <= int(os.environ["SMOKE_MIN_FRAMES"]):
    fail(f"only {frames} SSE frames (need more than {os.environ['SMOKE_MIN_FRAMES']}): the response was buffered")
print(f"ok    SSE timing: first frame {first:.1f}s, {frames} frames")

# ---- 6. one approval, no model -------------------------------------------------------------------
THRESHOLD = 500.0
CUSTOMERS = [
    "alice.johnson", "bob.smith", "carol.davis", "dave.wilson", "emma.brown", "frank.miller", "grace.lee",
    "henry.taylor", "iris.chen", "jack.anderson", "kate.martinez", "leo.garcia", "mia.robinson",
]
picked = None
fallback = None
for name in CUSTOMERS:
    email = f"{name}@gmail.com"
    token = login(email, os.environ["CUSTOMER_PASSWORD"])
    newest = (call("GET", "/api/orders?limit=1", token).get("orders") or [None])[0]
    if not newest or newest.get("status") != "delivered":
        continue
    candidate = (email, token, float(newest.get("total") or 0))
    if candidate[2] > THRESHOLD:
        picked = candidate
        break
    fallback = fallback or candidate
picked = picked or fallback
if picked is None:
    fail("no seeded customer has a delivered latest order to return")
email, token, total = picked
if total <= THRESHOLD:
    print(f"warn  no delivered latest order over {THRESHOLD:.0f}; using {email} at {total:.2f}")
_, _, _, events = stream(token, {"message": "I want to return my most recent order, it arrived damaged", "mode": "workflow:return-replace"})
run = next((json.loads(d) for k, d in events if k == "run"), None)
if not run or not run.get("run_id"):
    fail(f"the return turn emitted no run frame: {events[:5]}")
if not run.get("pending_approval"):
    fail(f"the return was not paused for approval: {run}")
run_id = run["run_id"]
print(f"ok    return paused for approval (run {run_id}, {email}, order total {total:.2f})")

# A manager route (over 500) needs admin; support decides what is routed to support. Pick by assigned_role.
pending = [
    a for a in call("GET", "/api/approvals?status=all&limit=100", admin).get("approvals", [])
    if a.get("requester_email") == email and a.get("status") in ("pending_review", "needs_info")
]
if not pending:
    fail(f"no pending approval for {email} in the staff inbox")
approval = pending[0]
decider = login("support.demo@gmail.com", os.environ["SUPPORT_PASSWORD"]) if approval.get("assigned_role") == "support" else admin
role = "support" if decider is not admin else "admin"
decided = call("POST", f"/api/approvals/{approval['id']}/decide", decider, {"decision": "approve", "note": "smoke test"})
print(f"ok    approval {approval['id']} (route {approval.get('route')}) decided by {role}: {decided.get('status', decided)}")

deadline = time.monotonic() + 60
titles: list[str] = []
while time.monotonic() < deadline:
    titles = [e.get("title", "") for e in call("GET", f"/api/runs/{run_id}/timeline", admin).get("entries", [])]
    if any("approv" in t.lower() for t in titles) and any("state saved" in t.lower() for t in titles):
        break
    time.sleep(3)
if not any("approv" in t.lower() for t in titles):
    fail(f"timeline shows no approval event: {titles}")
print(f"ok    run timeline: {', '.join(titles[-6:])}")
print("PASS: full smoke")
PY
