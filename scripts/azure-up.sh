#!/usr/bin/env bash
# One command to an Azure deployment: prerequisites, an azd environment, `azd up`.
#
#   scripts/azure-up.sh --env demo1 --location eastus2 [--image-tag v1.4.0] [--profile demo|reference]
#                       [--openai-location eastus2] [--public-open] [--allowed-ips 198.51.100.0/24,...]
#                       [--auth-server] [--mcp] [--stack python|dotnet] [--image-source ghcr|acr] [--build-from-source] [--dry-run]
#
# Defaults: demo profile, frontend reachable only from this machine's public IP, gpt-5.1, pinned image tag
# (the latest vX.Y.Z git tag when --image-tag is not given). It costs real money while it is up
# (demo: roughly 4 to 12 USD a day): scripts/azure-down.sh removes everything. Not verified against a real
# subscription yet; see docs/azure-deployment.md.
#
# --image-source acr creates a Container Registry and pulls from it: the published GHCR images are imported (the reference
# profile's default), or with --build-from-source built from this working tree in ACR Tasks (az acr build, no local Docker).
# An acr deployment is two provisions: the first creates everything but the apps and fills the registry (postprovision),
# the second starts the apps. --stack dotnet deploys the .NET orchestrator, specialists and inventory MCP host.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=scripts/azure/lib/common.sh
source scripts/azure/lib/common.sh

ENV_NAME="" LOCATION="" OPENAI_LOCATION="" PROFILE="demo" IMAGE_TAG_ARG="" PUBLIC_ACCESS_ARG="restricted"
ALLOWED_IPS_ARG="" AUTH_SERVER=false MCP=false STACK=python IMAGE_SOURCE_ARG=auto BUILD_FROM_SOURCE=false
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
        --dry-run) AZURE_DRY_RUN=1; shift ;;
        -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done
export AZURE_DRY_RUN
[ -n "$ENV_NAME" ] || die "--env is required (it names the resource group rg-<env>)"
[ -n "$LOCATION" ] || die "--location is required (for example eastus2)"
[[ "$ENV_NAME" =~ ^[a-z0-9][a-z0-9-]{0,30}$ ]] || die "--env must be lowercase letters, digits and dashes (max 31)"

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
    image_tag="$(git describe --tags --abbrev=0 --match 'v[0-9]*' 2>/dev/null || true)"
    [ -n "$image_tag" ] || die "no --image-tag given and no vX.Y.Z git tag found. Pass --image-tag <vX.Y.Z or sha-xxxxxxx>."
    log "image tag: $image_tag (latest git tag; override with --image-tag)"
fi
case "$image_tag" in latest|main) die "--image-tag $image_tag floats; use vX.Y.Z or sha-<7>" ;; esac

prereq_args=(--location "$LOCATION" --profile "$PROFILE" --env "$ENV_NAME")
[ -z "$OPENAI_LOCATION" ] || prereq_args+=(--openai-location "$OPENAI_LOCATION")
[ "$AZURE_DRY_RUN" != "1" ] || prereq_args+=(--dry-run)
scripts/azure/prereqs.sh "${prereq_args[@]}"

settings=("AZURE_LOCATION=$LOCATION" "DEPLOY_PROFILE=$PROFILE" "IMAGE_TAG=$image_tag" "PUBLIC_ACCESS=$PUBLIC_ACCESS_ARG"
    "ALLOWED_IPS=$ALLOWED_IPS_ARG" "DEPLOY_AUTH_SERVER=$AUTH_SERVER" "DEPLOY_MCP=$MCP" "AZURE_OPENAI_LOCATION=${OPENAI_LOCATION:-$LOCATION}"
    "DEPLOY_STACK=$STACK" "IMAGE_SOURCE=$IMAGE_SOURCE_ARG" "BUILD_FROM_SOURCE=$BUILD_FROM_SOURCE")

if [ "$AZURE_DRY_RUN" = "1" ]; then
    run azd env new "$ENV_NAME" --location "$LOCATION" --no-prompt
    for kv in "${settings[@]}"; do run azd env set "${kv%%=*}" "${kv#*=}"; done
    if [ "$image_source" = acr ]; then
        # preprovision sets DEPLOY_APPS=false until the registry holds this tag; postprovision then fills it.
        run azd provision --environment "$ENV_NAME" --no-prompt
        # What postprovision runs between the two provisions (printed, not executed).
        mode=import; [ "$BUILD_FROM_SOURCE" != true ] || mode=build
        scripts/azure/acr-images.sh "$mode" --registry acr-from-AZURE_CONTAINER_REGISTRY_NAME --tag "$image_tag" --stack "$STACK" --dry-run
        run azd provision --environment "$ENV_NAME" --no-prompt
    else
        run azd up --environment "$ENV_NAME" --no-prompt
    fi
    exit 0
fi

if ! azd env list --output json 2>/dev/null | jq -e --arg n "$ENV_NAME" '.[] | select(.Name == $n)' >/dev/null; then
    azd env new "$ENV_NAME" --location "$LOCATION" --no-prompt
fi
azd env select "$ENV_NAME"
azd env set AZURE_LOCATION "$LOCATION"
for kv in "${settings[@]}"; do azd env set "${kv%%=*}" "${kv#*=}"; done

# Key Vault role assignments take a minute to propagate; the first provision can fail on a secret
# reference the identity cannot read yet. One retry covers it. Anything else fails twice and stops.
provision_with_retry() {
    if ! "$@"; then
        warn "$* failed; retrying provision once (Key Vault RBAC propagation is the usual cause)"
        sleep 60
        azd provision --no-prompt
    fi
}

# acr: two provisions. The first (DEPLOY_APPS=false, set by preprovision while the registry lacks this tag) creates
# the infrastructure; its postprovision fills the registry. The second starts the apps and bootstraps the database.
if [ "$image_source" = acr ]; then
    provision_with_retry azd provision --no-prompt
    if [ "$(env_get DEPLOY_APPS)" = false ]; then
        log "registry filled; provisioning again to start the apps"
        provision_with_retry azd provision --no-prompt
    fi
else
    provision_with_retry azd up --no-prompt
fi
