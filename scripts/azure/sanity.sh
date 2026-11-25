#!/usr/bin/env bash
# Sanity check of a DEPLOYED environment. Run it last, after azure-up.sh (or a portal deployment plus the db-init command).
#
#   scripts/azure/sanity.sh [--env <azd env>] [--resource-group rg-<env>] [--only db|model|turn|eval]
#                           [--eval-cases 3] [--eval] [--dry-run]
#
# Four checks, each printed as PASS, FAIL or SKIP:
#   db     database connectivity and content, read only, through the deployed path: the frontend proxy, the orchestrator and
#          its asyncpg pool. Counts products (admin) and lists the seeded customer's orders, and reads the approvals inbox
#          (a table added by a later migration). No firewall rule is opened, no `az containerapp exec`, no SQL client:
#          the database stays reachable only from the apps. A count over the API is what is possible without exposing it.
#   model  one direct chat completion against the deployed Foundry (Azure OpenAI) endpoint with a bearer token for the signed-in user
#          (`az account get-access-token`, role Cognitive Services OpenAI User; no key, none is read or stored), on the deployed low-cost model, max 200 completion tokens.
#          SKIP when the environment has no model account (deployOpenAi=false).
#   turn   one full application turn as the seeded customer through the orchestrator: "show me my recent orders". It must
#          route to a specialist, the specialist must call a tool (which reads the database), and text must come back.
#   eval   the eval harness over http (--target http) against the deployed orchestrator, on the first N cases of
#          evals/datasets/orchestrator_routing.json (default 3, at most 5; a few thousand tokens each, about 0.01 USD at
#          the small model's price, well inside a 0.10 USD cap). PASS when no case errored and every case routed to a
#          specialist; the average groundedness score is printed and recorded, not graded.
#
# This script creates and deletes NOTHING in Azure and never tears anything down. What it does write is rows in the
# deployed database (chat conversations and run records for the seeded customer and the eval users); they go with the
# environment when you run `scripts/azure-down.sh --env <env>`, which is a separate step for after you have reviewed the
# results. It handles no key at all: the model check uses your own Entra token, held in one process's environment for the single call.
#
# Without azd (a portal deployment) pass --resource-group and set E2E_STAFF_PASSWORD to the seedStaffPassword you chose;
# the frontend URL, Key Vault, account and deployment are discovered from the resource group.
#
# Exit 0 only when no check FAILed. Scale to zero makes the first calls slow: each call waits and retries for up to
# SANITY_WAIT_SECONDS (default 300). Not run against a real subscription yet.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1
# shellcheck source=scripts/azure/lib/common.sh
source scripts/azure/lib/common.sh

ENV_NAME="" RG="" ONLY="" EVAL_CASES=3 RUN_EVAL=0
while [ $# -gt 0 ]; do
    case "$1" in
        --env) ENV_NAME="$2"; shift 2 ;;
        --resource-group) RG="$2"; shift 2 ;;
        --only) ONLY="$2"; shift 2 ;;
        --eval-cases) EVAL_CASES="$2"; shift 2 ;;
        --eval) RUN_EVAL=1; shift ;;
        --no-eval) RUN_EVAL=0; shift ;;
        --dry-run) AZURE_DRY_RUN=1; shift ;;
        -h|--help) sed -n '2,36p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done
case "$ONLY" in ""|db|model|turn|eval) ;; *) die "--only must be db, model, turn or eval" ;; esac
[[ "$EVAL_CASES" =~ ^[1-5]$ ]] || die "--eval-cases must be 1 to 5 (the cost cap is about 0.10 USD)"
export AZURE_DRY_RUN

PASSES=0 FAILS=0 SKIPS=0
WROTE=()
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
pass() { PASSES=$((PASSES + 1)); printf 'PASS  %s\n' "$*"; }
fail() { FAILS=$((FAILS + 1)); printf 'FAIL  %s\n' "$*" >&2; }
skip() { SKIPS=$((SKIPS + 1)); printf 'SKIP  %s\n' "$*"; }
wanted() { [ -z "$ONLY" ] || [ "$ONLY" = "$1" ]; }
# env_get with azd's "no environment" error text (it goes to stdout) turned into an empty value.
envv() {
    local v
    v="$(env_get "$1")"
    case "$v" in ERROR*|*"environment not specified"*|*"no default environment"*) v="" ;; esac
    printf '%s' "$v"
}

# ---- resolve the environment -------------------------------------------------------------------
if [ -n "$ENV_NAME" ] && [ "$AZURE_DRY_RUN" != "1" ] && have azd; then azd env select "$ENV_NAME" >/dev/null 2>&1 || true; fi
RG="${RG:-$(envv AZURE_RESOURCE_GROUP)}"
[ -n "$RG" ] || RG="${ENV_NAME:+rg-$ENV_NAME}"
FRONTEND_URL="${FRONTEND_URL:-$(envv FRONTEND_URL)}"
if [ "$(envv SEED_DEMO_PASSWORDS)" = "true" ]; then
    STAFF_PASSWORD="${E2E_STAFF_PASSWORD:-admin123}"   # --demo-passwords: the documented demo password
else
    STAFF_PASSWORD="${E2E_STAFF_PASSWORD:-$(envv SEED_STAFF_PASSWORD)}"
fi
if [ "$AZURE_DRY_RUN" = "1" ]; then
    RG="${RG:-rg-example}"; FRONTEND_URL="${FRONTEND_URL:-https://ca-frontend.example}"; STAFF_PASSWORD="${STAFF_PASSWORD:-example}"
    log "dry run: the checks below would run against $FRONTEND_URL in $RG"
    for c in db model turn eval; do wanted "$c" && printf '[dry-run] check %s\n' "$c" >&2; done
    exit 0
fi
[ -n "$RG" ] || die "no resource group: pass --env or --resource-group (or run from an azd environment)"
for tool in python3 jq; do have "$tool" || die "$tool is required"; done
# az is needed to discover the frontend and for the model check; the db and turn checks need only the URL.
need_az() {
    have az || die "az is required"
    az account show >/dev/null 2>&1 || die "not logged in: az login"
}
if [ -z "$FRONTEND_URL" ]; then
    need_az
    fqdn="$(az containerapp show --name ca-frontend --resource-group "$RG" --query properties.configuration.ingress.fqdn -o tsv 2>/dev/null || true)"
    [ -z "$fqdn" ] || FRONTEND_URL="https://$fqdn"
fi
[ -n "$FRONTEND_URL" ] || die "cannot find the frontend URL in $RG (set FRONTEND_URL)"
[ -n "$STAFF_PASSWORD" ] || die "no staff password: azd env get-value SEED_STAFF_PASSWORD, or set E2E_STAFF_PASSWORD to the one you chose"
FRONTEND_URL="${FRONTEND_URL%/}"
export FRONTEND_URL ADMIN_PASSWORD="$STAFF_PASSWORD" CUSTOMER_PASSWORD="${SMOKE_CUSTOMER_PASSWORD:-customer123}"
export SANITY_WAIT_SECONDS="${SANITY_WAIT_SECONDS:-300}"
log "environment: $RG, $FRONTEND_URL"

PYLIB='
import json, os, sys, time, urllib.error, urllib.request

BASE = os.environ["FRONTEND_URL"]
WAIT = float(os.environ.get("SANITY_WAIT_SECONDS", "300"))


def say(message):
    print("      " + message)


def die(message):
    print("      " + message, file=sys.stderr)
    sys.exit(1)


def call(method, path, token=None, body=None, timeout=120):
    """One JSON call; scale to zero means the first ones can be slow or answer 502-504, so retry within WAIT."""
    deadline = time.monotonic() + WAIT
    while True:
        req = urllib.request.Request(
            BASE + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return json.load(res)
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:200]
            if exc.code in (502, 503, 504) and time.monotonic() < deadline:
                time.sleep(10)
                continue
            die("%s %s -> %s %r" % (method, path, exc.code, detail))
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if time.monotonic() < deadline:
                time.sleep(10)
                continue
            die("%s %s unreachable: %s (restricted ingress? this machine IP must be allowed)" % (method, path, exc))


def login(email, password):
    token = call("POST", "/api/auth/login", body={"email": email, "password": password}).get("access_token")
    if not token:
        die("login as %s returned no access_token" % email)
    return token
'

# ---- a: database through the deployed path -----------------------------------------------------
if wanted db; then
    log "check db: read-only counts through the frontend, the orchestrator and its connection pool"
    if python3 -I - <<PY
$PYLIB
admin = login("admin.demo@gmail.com", os.environ["ADMIN_PASSWORD"])
alice = login("alice.johnson@gmail.com", os.environ["CUSTOMER_PASSWORD"])
products = call("GET", "/api/products?limit=1", alice)
total = products.get("total", 0)
if total < 1:
    die("the products table is empty or unreadable: %r" % (products,))
say("products: %d rows" % total)
orders = call("GET", "/api/orders?limit=50", alice).get("orders") or []
if not orders:
    die("the seeded customer has no orders: the seed did not run or a join table is empty")
say("orders for the seeded customer: %d" % len(orders))
inbox = call("GET", "/api/approvals?status=all&limit=1", admin)
if "approvals" not in inbox:
    die("the approvals inbox did not answer: migration 0006 may not have run")
say("approvals inbox answers (late migration present)")
PY
    then pass "db: connected and seeded (read through the deployed path; the database was not exposed)"
    else fail "db: see above. Run the bootstrap again if the tables are empty: az containerapp job start --name job-db-init --resource-group $RG"; fi
fi

# ---- b: direct model call ----------------------------------------------------------------------
if wanted model; then
    log "check model: one direct completion on the deployed Foundry endpoint"
    need_az
    endpoint="$(envv AZURE_OPENAI_ENDPOINT)"
    deployment="$(envv AZURE_OPENAI_CHAT_DEPLOYMENT)"
    [ -n "$endpoint" ] || endpoint="$(az cognitiveservices account list --resource-group "$RG" --query "[?kind=='AIServices'] | [0].properties.endpoint" -o tsv 2>/dev/null || true)"
    account="$(az cognitiveservices account list --resource-group "$RG" --query "[?kind=='AIServices'] | [0].name" -o tsv 2>/dev/null || true)"
    deployment="${deployment:-chat}"
    if [ -z "$endpoint" ] || [ -z "$account" ]; then
        skip "model: no Azure OpenAI account in $RG (deployOpenAi=false: the apps use replay or an external endpoint)"
    else
        # Keyless: the caller's own Entra token for the Cognitive Services audience. The template grants the deployer
        # Cognitive Services OpenAI User on the account (azd passes the principal id; in the portal tick grantDeployerAccess).
        token="$(az account get-access-token --resource https://cognitiveservices.azure.com --query accessToken -o tsv 2>/dev/null || true)"
        if [ -z "$token" ]; then
            fail "model: could not get an Entra token for https://cognitiveservices.azure.com (az login again)"
        else
            if SANITY_TOKEN="$token" SANITY_ENDPOINT="$endpoint" SANITY_DEPLOYMENT="$deployment" \
                SANITY_API_VERSION="$(envv AZURE_OPENAI_API_VERSION)" python3 -I - <<'PY'
import json, os, sys, urllib.error, urllib.request

endpoint = os.environ["SANITY_ENDPOINT"].rstrip("/")
deployment = os.environ["SANITY_DEPLOYMENT"]
version = os.environ.get("SANITY_API_VERSION") or "2025-04-01-preview"
url = "%s/openai/deployments/%s/chat/completions?api-version=%s" % (endpoint, deployment, version)
body = {"messages": [{"role": "user", "content": "Reply with the single word: pong"}], "max_completion_tokens": 200}
req = urllib.request.Request(
    url, method="POST", data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["SANITY_TOKEN"]}
)
# A brand-new account answers badly for a few minutes: the endpoint's TLS is not ready (connection reset), then the caller's
# role assignment has not propagated (401 or 403), and the deployment can lag (404). Retry those for up to four minutes; a
# failure that persists is then reported as it is.
import time

deadline = time.monotonic() + 240
data = None
while data is None:
    problem = None
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            data = json.load(res)
    except urllib.error.HTTPError as exc:
        problem = "HTTP %s from the model endpoint: %r" % (exc.code, exc.read()[:300])
        if exc.code not in (401, 403, 404, 408, 429, 500, 502, 503, 504):
            print("      " + problem, file=sys.stderr)
            sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        problem = "model endpoint unreachable: %s" % exc
    if data is None:
        if time.monotonic() >= deadline:
            print("      " + problem, file=sys.stderr)
            sys.exit(1)
        print("      not ready yet (%s); retrying in 20 s" % problem[:110], file=sys.stderr)
        time.sleep(20)
text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
usage = data.get("usage") or {}
print("      model %s answered %d characters; tokens in %s, out %s" % (data.get("model", "?"), len(text), usage.get("prompt_tokens"), usage.get("completion_tokens")))
if not text.strip():
    print("      empty completion (a reasoning model can spend all 200 tokens thinking)", file=sys.stderr)
    sys.exit(1)
PY
            then pass "model: $deployment on $account answered a one-line completion"
            else fail "model: the direct call failed. HTTP 401 or 403: the deployer lacks Cognitive Services OpenAI User on the account (a new role assignment takes a few minutes; in the portal tick grantDeployerAccess). HTTP 400 or 404: the model name or API version"; fi
        fi
        token=""
    fi
fi

# ---- c: a full application turn ----------------------------------------------------------------
if wanted turn; then
    log "check turn: 'show me my recent orders' as the seeded customer through the orchestrator"
    WROTE+=("one chat conversation and run record for the seeded customer (turn)")
    if python3 -I - <<PY
$PYLIB
alice = login("alice.johnson@gmail.com", os.environ["CUSTOMER_PASSWORD"])
req = urllib.request.Request(
    BASE + "/api/chat/stream", method="POST",
    data=json.dumps({"message": "Show me my 3 most recent orders with their status."}).encode(),
    headers={"Content-Type": "application/json", "Authorization": "Bearer " + alice},
)
text, steps, other = [], [], []
deadline = time.monotonic() + WAIT
while True:
    try:
        buffer = ""
        with urllib.request.urlopen(req, timeout=240) as res:
            while True:
                chunk = res.read1(65536) if hasattr(res, "read1") else res.read(4096)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", "replace")
        break
    except urllib.error.HTTPError as exc:
        if exc.code in (502, 503, 504) and time.monotonic() < deadline:
            time.sleep(10)
            continue
        die("POST /api/chat/stream -> %s %r" % (exc.code, exc.read()[:200]))
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        if time.monotonic() < deadline:
            time.sleep(10)
            continue
        die("the chat stream failed: %s" % exc)
for raw in buffer.split("\n\n"):
    lines = raw.split("\n")
    kind = next((ln[7:].strip() for ln in lines if ln.startswith("event: ")), "")
    data = "\n".join(ln[6:] for ln in lines if ln.startswith("data: "))
    if data == "[DONE]" or not data and not kind:
        continue
    if kind == "":
        text.append(data)
    elif kind == "step":
        try:
            steps.append(json.loads(data))
        except ValueError:
            pass
    else:
        other.append(kind)
answer = "".join(text).strip()
routes = [
    (s.get("tool_input") or {}).get("agent_name") for s in steps
    if s.get("tool_name") == "call_specialist_agent" and isinstance(s.get("tool_input"), dict)
]
tools = [s.get("tool_name") for s in steps if s.get("tool_name") and s.get("tool_name") != "call_specialist_agent"]
if "replay_miss" in other:
    die("replay miss: this environment runs LLM_PROVIDER=replay, which answers no free-form chat. Skip this check or deploy with a model.")
if "error" in other:
    die("the stream carried an error frame")
# The orchestrator answers an order question with its own order tools on some turns and routes it to a specialist on
# others; either way what this check proves is that a tool read the database and an answer came back.
if not routes and not tools:
    die("no specialist was called and no tool ran (steps: %s)" % [s.get("tool_name") for s in steps])
say("routed to: %s" % (", ".join(str(r) for r in routes) if routes else "no specialist (the orchestrator used its own tools)"))
if not tools:
    die("no tool ran, so nothing read the database")
say("tools: %s" % ", ".join(sorted(set(str(t) for t in tools))))
if len(answer) < 20:
    die("the answer is empty or too short: %r" % answer)
say("answer: %d characters" % len(answer))
PY
    then pass "turn: a tool read the database and an answer came back"
    else fail "turn: see above. Scale to zero can make a first turn exceed the A2A timeout: run it again once (troubleshooting row 40)"; fi
fi

# ---- d: a few eval cases over http -------------------------------------------------------------
if wanted eval; then
    if [ "$RUN_EVAL" != "1" ]; then
        skip "eval: opt-in (--eval); run the quality evals locally against the same model, see docs/agent-quality.md"
    elif ! have uv; then
        skip "eval: uv is not installed (the harness is a Python project)"
    else
        log "check eval: first $EVAL_CASES cases of orchestrator_routing.json over the http target"
        jq --argjson n "$EVAL_CASES" '.[0:$n]' agents/python/evals/datasets/orchestrator_routing.json >"$TMP/cases.json"
        WROTE+=("up to $EVAL_CASES evaluation conversations and run records for the seeded users (eval)")
        model="$(envv AZURE_OPENAI_CHAT_DEPLOYMENT)"
        if (cd agents/python && EVAL_STAFF_PASSWORD="$STAFF_PASSWORD" LLM_PROVIDER=replay uv run python -m evals.run_evals --target http \
            --base-url "$FRONTEND_URL" --stack python --chat-provider azure --chat-model "${model:-chat}" --agent orchestrator \
            --dataset "$TMP/cases.json" --repeats 1 --pass-threshold 0 --output-json "$TMP/out.json" >"$TMP/eval.log" 2>&1); then
            if python3 -I - "$TMP/out.json" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1]))
results = data.get("results") or []
if not results:
    print("      the run produced no results", file=sys.stderr)
    sys.exit(1)
errors = [r for r in results if r.get("error")]
unrouted = [r for r in results if "call_specialist_agent" not in (r.get("tools_called") or [])]
print("      cases: %d, errors: %d, without a specialist call: %d" % (len(results), len(errors), len(unrouted)))
print("      average groundedness: %s (recorded, not graded); tokens in/out: %s/%s; estimated cost: %s"
      % (data.get("avg_groundedness"), data.get("total_tokens_in"), data.get("total_tokens_out"), data.get("estimated_cost_usd")))
for r in results:
    print("      - %-60.60s routes=%s tools=%s grounded=%s" % (r["input"], r.get("route_called"), r.get("tools_called"), r.get("groundedness_score")))
cost = data.get("estimated_cost_usd")
if isinstance(cost, (int, float)) and cost > 0.10:
    print("      WARNING: estimated cost %s is above the 0.10 USD cap" % cost)
sys.exit(1 if errors or unrouted else 0)
PY
            then pass "eval: $EVAL_CASES case(s) over http, every case routed and none errored"
            else fail "eval: see above; full log kept until exit: $TMP/eval.log (tail printed below)"; tail -n 15 "$TMP/eval.log" >&2; fi
        else
            fail "eval: the harness exited non-zero (a replay deployment answers model cases with replay misses)"
            tail -n 15 "$TMP/eval.log" >&2
        fi
    fi
fi

# ---- summary -----------------------------------------------------------------------------------
echo
printf 'sanity: %d passed, %d failed, %d skipped\n' "$PASSES" "$FAILS" "$SKIPS"
echo "Created in Azure by this script: nothing. Deleted: nothing. Nothing was torn down."
if [ "${#WROTE[@]}" -gt 0 ]; then
    echo "Rows written to the deployed database (removed with the environment):"
    printf '  - %s\n' "${WROTE[@]}"
fi
if [ "$FAILS" -gt 0 ]; then
    echo "Re-run one check: scripts/azure/sanity.sh --resource-group $RG --only db|model|turn|eval"
    echo "The environment is still up and still billing. Stop spending after you have looked: scripts/azure-down.sh --env ${ENV_NAME:-<env>}"
    exit 1
fi
echo "The environment is still up and still billing. Review it, then tear it down yourself: scripts/azure-down.sh --env ${ENV_NAME:-<env>}"
exit 0
