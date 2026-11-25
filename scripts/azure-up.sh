#!/usr/bin/env bash
# One command to an Azure deployment: prerequisites, an azd environment, `azd provision` (the hooks do the rest).
#
#   scripts/azure-up.sh                      # every value below has a default; the flags (or ECA_* variables) override them
#   scripts/azure-up.sh [--env demo1] [--location eastus2] [--image-tag sha-0123abc|v1.5.0] [--profile free-trial|demo|reference]
#                       [--chat-model gpt-4.1-mini] [--chat-model-version 2025-04-14] [--generated-passwords] [--no-verify]
#                       [--llm replay|openai [--llm-base-url https://host/v1] [--llm-model gpt-4.1-mini]]
#                       [--openai-location eastus2] [--public-open] [--allowed-ips 198.51.100.0/24,...] [--demo-passwords] [--verify]
#                       [--auth-server] [--mcp] [--stack python|dotnet] [--image-source ghcr|acr] [--build-from-source] [--dry-run]
#
# Defaults (flag > ECA_ENV, ECA_LOCATION, ECA_OPENAI_LOCATION, ECA_PROFILE, ECA_IMAGE_TAG, ECA_DEMO_PASSWORDS, ECA_VERIFY > default):
#   env          eca-<6 hex of the subscription id> (stable, so a re-run updates the same environment)
#   location     the first of centralus westus3 northcentralus canadacentral eastus2 eastus westus2 southcentralus where
#                PostgreSQL Standard_B1ms is offered to the subscription
#   profile      free-trial (gpt-4.1-mini, scale to zero; the demo and reference profiles are about 4 to 12 USD a day or more)
#   image tag    sha-<7> of the newest main commit whose images are published
#   passwords    the documented demo accounts (admin123 and so on); --generated-passwords generates one for the staff accounts.
#                Honoured only while the frontend is IP-restricted, which is the default
#   verify       on: runs scripts/azure/sanity.sh at the end; --no-verify skips it
#   frontend     reachable only from this machine's public IP
# It costs real money while it is up: scripts/azure-down.sh removes everything. Not verified against a real
# subscription yet; see docs/azure-deployment.md.
#
# --profile free-trial is the 5 USD budget profile (docs/azure-deployment-sequence.md): scale to zero, one replica at most, no
# registry, a 0.1 GB/day log cap. --llm replay|openai skips the Azure OpenAI account (a trial often has no quota for it):
# replay calls no model; openai uses an OpenAI-compatible endpoint whose key is read from the LLM_API_KEY environment
# variable (never an argument) and stored in Key Vault as llm-api-key.
#
# --preview runs the prerequisite checks, creates the local azd environment and its secrets, then `azd provision --preview`
# (a what-if: it creates nothing in Azure) and stops.
#
# --image-source acr creates a Container Registry and pulls from it: the published GHCR images are imported (the reference
# profile's default), or with --build-from-source built from this working tree in ACR Tasks (az acr build, no local Docker).
# An acr deployment is two provisions: the first creates everything but the apps and fills the registry (postprovision),
# the second starts the apps. --stack dotnet deploys the .NET orchestrator, specialists and inventory MCP host.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=scripts/azure/lib/common.sh
source scripts/azure/lib/common.sh

# Numbered stages with elapsed time, so a run shows where it is. Five stages: checks, secrets, provision, smoke, summary.
STAGE_TOTAL=5
STARTED_AT=$SECONDS
stage() {
    local n="$1"; shift
    printf '\n[azure] ---- [%s/%s] %s (elapsed %dm%02ds) ----\n' "$n" "$STAGE_TOTAL" "$*" $(((SECONDS - STARTED_AT) / 60)) $(((SECONDS - STARTED_AT) % 60)) >&2
}

# Every value has a default, so `scripts/azure-up.sh` alone works. Each is overridden by its flag, or by the ECA_* environment
# variable named here (a flag wins over the variable, the variable over the default).
ENV_NAME="${ECA_ENV:-}" LOCATION="${ECA_LOCATION:-}" OPENAI_LOCATION="${ECA_OPENAI_LOCATION:-}" PROFILE="${ECA_PROFILE:-free-trial}"
IMAGE_TAG_ARG="${ECA_IMAGE_TAG:-}" PUBLIC_ACCESS_ARG="restricted"
ALLOWED_IPS_ARG="" AUTH_SERVER=false MCP=false STACK=python IMAGE_SOURCE_ARG=auto BUILD_FROM_SOURCE=false
LLM="" LLM_BASE_URL="" LLM_MODEL="" PREVIEW=false DEMO_PASSWORDS="${ECA_DEMO_PASSWORDS:-true}" VERIFY="${ECA_VERIFY:-true}"
while [ $# -gt 0 ]; do
    case "$1" in
        --env) ENV_NAME="$2"; shift 2 ;;
        --location) LOCATION="$2"; shift 2 ;;
        --openai-location) OPENAI_LOCATION="$2"; shift 2 ;;
        --profile) PROFILE="$2"; shift 2 ;;
        --image-tag) IMAGE_TAG_ARG="$2"; shift 2 ;;
        --public-open) PUBLIC_ACCESS_ARG=open; shift ;;
        --allowed-ips) ALLOWED_IPS_ARG="$2"; shift 2 ;;
        --auth-server) AUTH_SERVER=true; shift ;;
        --mcp) MCP=true; shift ;;
        --stack) STACK="$2"; shift 2 ;;
        --image-source) IMAGE_SOURCE_ARG="$2"; shift 2 ;;
        --build-from-source) BUILD_FROM_SOURCE=true; shift ;;
        --preview) PREVIEW=true; shift ;;
        --demo-passwords) DEMO_PASSWORDS=true; shift ;;
        --verify) VERIFY=true; shift ;;
        --no-verify) VERIFY=false; shift ;;
        --generated-passwords) DEMO_PASSWORDS=false; shift ;;
        --chat-model) export AZURE_CHAT_MODEL="$2"; shift 2 ;;
        --chat-model-version) export AZURE_CHAT_MODEL_VERSION="$2"; shift 2 ;;
        --llm) LLM="$2"; shift 2 ;;
        --llm-base-url) LLM_BASE_URL="$2"; shift 2 ;;
        --llm-model) LLM_MODEL="$2"; shift 2 ;;
        --dry-run) AZURE_DRY_RUN=1; shift ;;
        -h|--help) sed -n '2,29p' "$0"; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done
export AZURE_DRY_RUN

# ---- defaults: what a run with no arguments uses ------------------------------------------------------------------------
# Environment name: stable for a subscription (so a re-run updates the same environment), unique across subscriptions.
default_env_name() {
    local id
    id="$(az account show --query id --output tsv)" || die "not signed in: az login"
    printf 'eca-%s' "$(printf '%s' "$id" | openssl dgst -sha256 | awk '{print $NF}' | cut -c1-6)"
}
# Region: the first one where PostgreSQL Flexible Server Standard_B1ms is offered to this subscription. Several are
# restricted for new subscriptions (eastus and eastus2 were), and the deployment cannot be created there.
REGION_CANDIDATES=(centralus westus3 northcentralus canadacentral eastus2 eastus westus2 southcentralus)
default_location() {
    local r skus
    for r in "${REGION_CANDIDATES[@]}"; do
        skus="$(az postgres flexible-server list-skus --location "$r" --output json 2>/dev/null || true)"
        # Here-strings, not `printf | grep -q`: with pipefail, grep -q exits at the first match, printf dies of SIGPIPE on a
        # large listing, and the pipeline reports failure for a match that was found.
        if grep -q 'Standard_B1ms' <<<"$skus" && ! grep -q '"reason": "[^"]*restricted' <<<"$skus"; then
            printf '%s' "$r"; return 0
        fi
    done
    return 1
}
# Image tag: the newest main commit whose images are in the registry (a commit publishes sha-<7> only after its tests pass).
default_image_tag() {
    local repo sha tok accept
    repo="$(git remote get-url origin 2>/dev/null | sed -E 's#^(git@github.com:|https://github.com/)##; s#\.git$##')"
    [ -n "$repo" ] || repo="nitin27may/e-commerce-agents"
    git fetch --quiet origin main 2>/dev/null || true
    tok="$(curl -fsS "https://ghcr.io/token?service=ghcr.io&scope=repository:${repo}/orchestrator:pull" 2>/dev/null | jq -r '.token // empty')" || true
    [ -n "$tok" ] || return 1
    accept="application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json"
    for sha in $(git rev-list --max-count=25 origin/main 2>/dev/null | cut -c1-7); do
        if curl -fsSI -o /dev/null -H "Authorization: Bearer $tok" -H "Accept: $accept" \
            "https://ghcr.io/v2/${repo}/orchestrator/manifests/sha-${sha}" 2>/dev/null; then
            printf 'sha-%s' "$sha"; return 0
        fi
    done
    return 1
}
DEFAULTED=()
if [ -z "$ENV_NAME" ]; then
    if [ "$AZURE_DRY_RUN" = "1" ]; then ENV_NAME="eca-dryrun"; else ENV_NAME="$(default_env_name)"; fi
    DEFAULTED+=("env=$ENV_NAME")
fi
if [ -z "$LOCATION" ]; then
    if [ "$AZURE_DRY_RUN" = "1" ]; then LOCATION="centralus"
    else
        log "no --location: looking for a region where PostgreSQL Standard_B1ms is offered to this subscription"
        LOCATION="$(default_location)" || die "no candidate region offers PostgreSQL Standard_B1ms to this subscription (tried: ${REGION_CANDIDATES[*]}). Pass --location, or ask Azure support to lift the regional restriction."
    fi
    DEFAULTED+=("location=$LOCATION")
fi
[[ "$ENV_NAME" =~ ^[a-z0-9][a-z0-9-]{0,30}$ ]] || die "--env must be lowercase letters, digits and dashes (max 31)"

case "$PROFILE" in demo|reference|free-trial) ;; *) die "--profile must be demo, reference or free-trial" ;; esac
case "$LLM" in
    "") ;;
    replay) ;;
    openai)
        [ -n "$LLM_BASE_URL" ] || die "--llm openai needs --llm-base-url (an OpenAI-compatible base URL, for example https://api.openai.com/v1)"
        [ -n "${LLM_API_KEY:-}" ] || die "--llm openai needs the key in the LLM_API_KEY environment variable (it is never taken as an argument)"
        ;;
    *) die "--llm must be replay or openai" ;;
esac
[ -z "$LLM" ] || [ "$STACK" = python ] || die "--llm is Python stack only"
[ "$PROFILE" != reference ] || [ -z "$LLM" ] || die "--llm needs no Azure OpenAI account, which the reference profile's private endpoints expect; use demo or free-trial"
case "$STACK" in python|dotnet) ;; *) die "--stack must be python or dotnet" ;; esac
case "$IMAGE_SOURCE_ARG" in auto|ghcr|acr) ;; *) die "--image-source must be ghcr or acr" ;; esac
# Building from source needs a registry to put the result in.
if [ "$BUILD_FROM_SOURCE" = true ]; then
    [ "$IMAGE_SOURCE_ARG" != ghcr ] || die "--build-from-source builds into ACR; it cannot be combined with --image-source ghcr"
    IMAGE_SOURCE_ARG=acr
fi
# The effective source: auto follows the profile (demo: ghcr, reference: acr).
image_source="$IMAGE_SOURCE_ARG"
if [ "$image_source" = auto ]; then
    case "$PROFILE" in reference) image_source=acr ;; *) image_source=ghcr ;; esac
fi

# A floating image would make a re-deploy change what runs.
image_tag="$IMAGE_TAG_ARG"
if [ -z "$image_tag" ] && [ "$BUILD_FROM_SOURCE" = true ]; then
    # A source build has no release to name: tag it with the commit, and make a dirty tree's build unique.
    image_tag="src-$(git rev-parse --short=7 HEAD 2>/dev/null || echo unknown)"
    if [ -n "$(git status --porcelain 2>/dev/null || true)" ]; then image_tag="${image_tag}-dirty$(date +%s)"; fi
    log "image tag: $image_tag (source build; override with --image-tag)"
fi
if [ -z "$image_tag" ]; then
    if [ "$AZURE_DRY_RUN" = "1" ]; then image_tag="sha-0000000"
    else
        image_tag="$(default_image_tag || true)"
        [ -z "$image_tag" ] || { log "no --image-tag: using $image_tag, the newest main commit whose images are published"; DEFAULTED+=("image-tag=$image_tag"); }
    fi
fi
if [ -z "$image_tag" ] && [ "$PROFILE" = free-trial ]; then
    # The newest release tag lags main; a first trial run must name exactly what it pulls.
    die "could not find a published image for any of the last 25 main commits (is ghcr.io reachable?). Pass --image-tag sha-<7> or vX.Y.Z"
fi
if [ -z "$image_tag" ]; then
    image_tag="$(git describe --tags --abbrev=0 --match 'v[0-9]*' 2>/dev/null || true)"
    [ -n "$image_tag" ] || die "no --image-tag given and no vX.Y.Z git tag found. Pass --image-tag <vX.Y.Z or sha-xxxxxxx>."
    log "image tag: $image_tag (latest git tag; override with --image-tag)"
fi
case "$image_tag" in
    v[0-9]*.[0-9]*.[0-9]*|sha-[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]|src-*) ;;
    *) die "--image-tag $image_tag is not an immutable tag; use vX.Y.Z or sha-<7 hex> (latest and main float)" ;;
esac

prereq_args=(--location "$LOCATION" --profile "$PROFILE" --env "$ENV_NAME")
[ -z "$OPENAI_LOCATION" ] || prereq_args+=(--openai-location "$OPENAI_LOCATION")
[ "$AZURE_DRY_RUN" != "1" ] || prereq_args+=(--dry-run)
[ -z "$LLM" ] || prereq_args+=(--no-openai)
log "using: env=$ENV_NAME location=$LOCATION openai-location=${OPENAI_LOCATION:-$LOCATION} profile=$PROFILE image=$image_tag stack=$STACK demo-passwords=$DEMO_PASSWORDS verify=$VERIFY"
[ "${#DEFAULTED[@]}" -eq 0 ] || log "defaulted (override with the flag or ECA_* variable): ${DEFAULTED[*]}"
stage 1 "Checking the subscription, providers, regional SKUs and model quota (reads only, nothing is created)"
scripts/azure/prereqs.sh "${prereq_args[@]}"

settings=("AZURE_LOCATION=$LOCATION" "DEPLOY_PROFILE=$PROFILE" "IMAGE_TAG=$image_tag" "PUBLIC_ACCESS=$PUBLIC_ACCESS_ARG"
    "ALLOWED_IPS=$ALLOWED_IPS_ARG" "DEPLOY_AUTH_SERVER=$AUTH_SERVER" "DEPLOY_MCP=$MCP" "AZURE_OPENAI_LOCATION=${OPENAI_LOCATION:-$LOCATION}"
    "DEPLOY_STACK=$STACK" "IMAGE_SOURCE=$IMAGE_SOURCE_ARG" "BUILD_FROM_SOURCE=$BUILD_FROM_SOURCE")
# Every run states these explicitly. A value left in the azd environment by an earlier run (for example DEPLOY_OPENAI=false
# from an --llm run) used to carry over and silently skip Foundry on the next run.
settings+=("SEED_DEMO_PASSWORDS=$DEMO_PASSWORDS")
if [ -z "$LLM" ]; then
    settings+=("DEPLOY_OPENAI=true" "EXTERNAL_LLM_PROVIDER=replay")
fi
if [ -n "$LLM" ]; then
    settings+=("DEPLOY_OPENAI=false" "EXTERNAL_LLM_PROVIDER=$LLM")
    if [ "$LLM" = openai ]; then
        settings+=("EXTERNAL_LLM_BASE_URL=$LLM_BASE_URL" "EXTERNAL_LLM_MODEL=${LLM_MODEL:-gpt-4.1-mini}")
        [ "$AZURE_DRY_RUN" = "1" ] || settings+=("EXTERNAL_LLM_API_KEY=$LLM_API_KEY")
    fi
fi
# A model other than the default (see check_model's message when gpt-5.1 has no quota): the shell value is persisted so
# preprovision and the template parameters see the same one the preflight just checked.
[ -z "${AZURE_CHAT_MODEL:-}" ] || settings+=("AZURE_CHAT_MODEL=$AZURE_CHAT_MODEL")
[ -z "${AZURE_CHAT_MODEL_VERSION:-}" ] || settings+=("AZURE_CHAT_MODEL_VERSION=$AZURE_CHAT_MODEL_VERSION")

# azd would otherwise prompt for a subscription (and `--no-prompt` makes that a failure): pin the one az is using.
subscription_id="<subscription-id>"
if [ "$AZURE_DRY_RUN" != "1" ]; then
    subscription_id="$(az account show --query id --output tsv)"
    [ -n "$subscription_id" ] || die "no active Azure subscription: az login, then az account set --subscription <id>"
fi
settings+=("AZURE_SUBSCRIPTION_ID=$subscription_id")

if [ "$AZURE_DRY_RUN" = "1" ]; then
    run azd env new "$ENV_NAME" --location "$LOCATION" --subscription "$subscription_id" --no-prompt
    for kv in "${settings[@]}"; do run azd env set "${kv%%=*}" "${kv#*=}"; done
    log "secrets are generated into the azd environment before provision (ensure_azure_secrets), never printed"
    if [ "$PREVIEW" = true ]; then
        run bash scripts/azure/preprovision.sh
        run azd provision --preview --environment "$ENV_NAME" --no-prompt
        exit 0
    fi
    if [ "$image_source" = acr ]; then
        # preprovision sets DEPLOY_APPS=false until the registry holds this tag; postprovision then fills it.
        run azd provision --environment "$ENV_NAME" --no-prompt
        # What postprovision runs between the two provisions (printed, not executed).
        mode=import; [ "$BUILD_FROM_SOURCE" != true ] || mode=build
        scripts/azure/acr-images.sh "$mode" --registry acr-from-AZURE_CONTAINER_REGISTRY_NAME --tag "$image_tag" --stack "$STACK" --dry-run
        run azd provision --environment "$ENV_NAME" --no-prompt
    else
        # azure.yaml declares no services, so there is nothing for the deploy half of `azd up` to do: provision is the whole job.
        run azd provision --environment "$ENV_NAME" --no-prompt
    fi
    exit 0
fi

if ! azd env list --output json 2>/dev/null | jq -e --arg n "$ENV_NAME" '.[] | select(.Name == $n)' >/dev/null; then
    azd env new "$ENV_NAME" --location "$LOCATION" --subscription "$subscription_id" --no-prompt
fi
azd env select "$ENV_NAME"
azd env set AZURE_LOCATION "$LOCATION"
for kv in "${settings[@]}"; do azd env set "${kv%%=*}" "${kv#*=}"; done

# Generate every secret BEFORE `azd provision`, with the same function the preprovision hook uses. azd resolves the template
# parameters before it runs hooks, so a hook-only generator had two faults: seedStaffPassword (a secure parameter with no
# default) stopped a first run with "missing required inputs", and the other secrets took the template's random defaults on
# the first provision and the hook's values on the next, so a re-provision rotated the database password away from the one
# Key Vault and the apps held (sign-in then failed with a password authentication error). Idempotent: existing values stay.
# shellcheck source=scripts/azure/lib/secrets.sh
source scripts/azure/lib/secrets.sh
stage 2 "Generating secrets into the local azd environment (never printed)"
ensure_azure_secrets >/dev/null || die "could not generate secrets (need openssl or python3)"

if [ "$PREVIEW" = true ]; then
    bash scripts/azure/preprovision.sh
    azd provision --preview --no-prompt
    log "preview only: nothing was created in Azure. Run again without --preview to deploy."
    exit 0
fi

# Key Vault role assignments take a minute to propagate; the first provision can fail on a secret
# reference the identity cannot read yet. One retry covers it. Anything else fails twice and stops.
provision_with_retry() {
    if ! "$@"; then
        warn "$* failed; retrying provision once (Key Vault RBAC propagation is the usual cause)"
        sleep 60
        azd provision --no-prompt
    fi
}

stage 3 "Provisioning in Azure (azd lists each resource as it finishes; about 5 to 8 minutes)"
# acr: two provisions. The first (DEPLOY_APPS=false, set by preprovision while the registry lacks this tag) creates
# the infrastructure; its postprovision fills the registry. The second starts the apps and bootstraps the database.
if [ "$image_source" = acr ]; then
    provision_with_retry azd provision --no-prompt
    if [ "$(env_get DEPLOY_APPS)" = false ]; then
        log "registry filled; provisioning again to start the apps"
        provision_with_retry azd provision --no-prompt
    fi
else
    provision_with_retry azd provision --no-prompt
fi
stage 5 "Done"
frontend_url="$(env_get FRONTEND_URL)"
elapsed=$((SECONDS - STARTED_AT))
{
    echo
    echo "=================================================================================================="
    echo "  DEPLOYED: $ENV_NAME in $LOCATION ($((elapsed / 60))m$((elapsed % 60))s)"
    echo "=================================================================================================="
    echo "  Open this:   ${frontend_url:-<no FRONTEND_URL in the azd environment>}"
    echo "               (it answers only the IP that deployed it: $(env_get DEPLOYER_IP); change with: azd env set ALLOWED_IPS ...)"
    echo
    if [ "$(env_get SEED_DEMO_PASSWORDS)" = "true" ]; then
        echo "  Sign in with the documented demo accounts:"
        echo "      customer   alice.johnson@gmail.com   customer123    (orders, a delivered order to return)"
        echo "      admin      admin.demo@gmail.com      admin123       (approvals inbox, /runs)"
        echo "      support    support.demo@gmail.com    support123"
        echo "      seller     seller.demo@gmail.com     seller123"
        echo "      power user power.demo@gmail.com      power123"
    else
        echo "  Sign in: customer alice.johnson@gmail.com / customer123;"
        echo "           staff (admin.demo, support.demo, seller.demo, seller2.demo @gmail.com) with the generated password:"
        echo "           azd env get-value SEED_STAFF_PASSWORD"
    fi
    echo
    echo "  Please test it. As Alice, ask:"
    echo "      Show me some wireless headphones with prices"
    echo "      I want to return my most recent order, it arrived damaged      (then approve it as the admin)"
    echo
    echo "  Verify from here (database, a keyless model call, a chat turn):"
    echo "      scripts/azure/sanity.sh --env $ENV_NAME"
    echo
    echo "  It is billing now (about 5 cents an hour idle, apps scale to zero). When you are done:"
    echo "      scripts/azure-down.sh --env $ENV_NAME"
    echo "=================================================================================================="
} >&2
if [ "$VERIFY" = true ]; then
    stage 5 "Verifying (database, a bearer-token model call, one chat turn)"
    scripts/azure/sanity.sh --env "$ENV_NAME" || warn "sanity reported a failure; the environment is still up"
fi
