#!/usr/bin/env bash
# One command to an Azure deployment: prerequisites, an azd environment, `azd up`.
#
#   scripts/azure-up.sh --env demo1 --location eastus2 [--image-tag v1.4.0] [--profile demo|reference]
#                       [--openai-location eastus2] [--public-open] [--allowed-ips 198.51.100.0/24,...]
#                       [--auth-server] [--mcp] [--dry-run]
#
# Defaults: demo profile, frontend reachable only from this machine's public IP, gpt-5.1, pinned image tag
# (the latest vX.Y.Z git tag when --image-tag is not given). It costs real money while it is up
# (demo: roughly 4 to 12 USD a day): scripts/azure-down.sh removes everything. Not verified against a real
# subscription yet; see docs/azure-deployment.md.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=scripts/azure/lib/common.sh
source scripts/azure/lib/common.sh

ENV_NAME="" LOCATION="" OPENAI_LOCATION="" PROFILE="demo" IMAGE_TAG_ARG="" PUBLIC_ACCESS_ARG="restricted"
ALLOWED_IPS_ARG="" AUTH_SERVER=false MCP=false
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
        --dry-run) AZURE_DRY_RUN=1; shift ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done
export AZURE_DRY_RUN
[ -n "$ENV_NAME" ] || die "--env is required (it names the resource group rg-<env>)"
[ -n "$LOCATION" ] || die "--location is required (for example eastus2)"
[[ "$ENV_NAME" =~ ^[a-z0-9][a-z0-9-]{0,30}$ ]] || die "--env must be lowercase letters, digits and dashes (max 31)"

# A floating image would make a re-deploy change what runs.
image_tag="$IMAGE_TAG_ARG"
if [ -z "$image_tag" ]; then
    image_tag="$(git describe --tags --abbrev=0 --match 'v[0-9]*' 2>/dev/null || true)"
    [ -n "$image_tag" ] || die "no --image-tag given and no vX.Y.Z git tag found. Pass --image-tag <vX.Y.Z or sha-xxxxxxx>."
    log "image tag: $image_tag (latest git tag; override with --image-tag)"
fi
case "$image_tag" in latest|main) die "--image-tag $image_tag floats; use vX.Y.Z or sha-<7>" ;; esac

prereq_args=(--location "$LOCATION" --profile "$PROFILE" --env "$ENV_NAME")
[ -z "$OPENAI_LOCATION" ] || prereq_args+=(--openai-location "$OPENAI_LOCATION")
[ "$AZURE_DRY_RUN" != "1" ] || prereq_args+=(--dry-run)
scripts/azure/prereqs.sh "${prereq_args[@]}"

if [ "$AZURE_DRY_RUN" = "1" ]; then
    run azd env new "$ENV_NAME" --location "$LOCATION" --no-prompt
    for kv in "AZURE_LOCATION=$LOCATION" "DEPLOY_PROFILE=$PROFILE" "IMAGE_TAG=$image_tag" "PUBLIC_ACCESS=$PUBLIC_ACCESS_ARG" \
        "ALLOWED_IPS=$ALLOWED_IPS_ARG" "DEPLOY_AUTH_SERVER=$AUTH_SERVER" "DEPLOY_MCP=$MCP" "AZURE_OPENAI_LOCATION=${OPENAI_LOCATION:-$LOCATION}"; do
        run azd env set "${kv%%=*}" "${kv#*=}"
    done
    run azd up --environment "$ENV_NAME" --no-prompt
    exit 0
fi

if ! azd env list --output json 2>/dev/null | jq -e --arg n "$ENV_NAME" '.[] | select(.Name == $n)' >/dev/null; then
    azd env new "$ENV_NAME" --location "$LOCATION" --no-prompt
fi
azd env select "$ENV_NAME"
azd env set AZURE_LOCATION "$LOCATION"
azd env set DEPLOY_PROFILE "$PROFILE"
azd env set IMAGE_TAG "$image_tag"
azd env set PUBLIC_ACCESS "$PUBLIC_ACCESS_ARG"
azd env set ALLOWED_IPS "$ALLOWED_IPS_ARG"
azd env set DEPLOY_AUTH_SERVER "$AUTH_SERVER"
azd env set DEPLOY_MCP "$MCP"
azd env set AZURE_OPENAI_LOCATION "${OPENAI_LOCATION:-$LOCATION}"

# Key Vault role assignments take a minute to propagate; the first provision can fail on a secret
# reference the identity cannot read yet. One retry covers it. Anything else fails twice and stops.
if ! azd up --no-prompt; then
    warn "azd up failed; retrying provision once (Key Vault RBAC propagation is the usual cause)"
    sleep 60
    azd provision --no-prompt
fi
