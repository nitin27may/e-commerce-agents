#!/usr/bin/env bash
# azd preprovision hook (azure.yaml). Runs before every `azd provision`, so it is idempotent:
#
#   1. generates the secrets this environment needs, only those that are unset, and never overwrites;
#   2. pins the image tag (IMAGE_TAG must be set: a deployment never floats on :latest or :main);
#   3. records the deployer's public IP for the frontend allow-list (PUBLIC_ACCESS=restricted);
#   4. checks Postgres SKU, model, quota and soft-deleted names in the target region;
#   5. for an acr image source, keeps DEPLOY_APPS=false until postprovision has put IMAGE_TAG into the registry
#      (an app cannot start from an image that is not there yet), then sets it true.
#
# Standalone and offline (no azd, no Azure) with a dotenv store:
#   AZURE_SECRET_STORE_FILE=/tmp/x.env scripts/azure/preprovision.sh --secrets-only
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
# shellcheck source=scripts/azure/lib/common.sh
source scripts/azure/lib/common.sh
# shellcheck source=scripts/azure/lib/secrets.sh
source scripts/azure/lib/secrets.sh
# shellcheck source=scripts/azure/lib/checks.sh
source scripts/azure/lib/checks.sh

SECRETS_ONLY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --secrets-only) SECRETS_ONLY=1; shift ;;
        --dry-run) AZURE_DRY_RUN=1; shift ;;
        -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

# ---- 1. secrets ------------------------------------------------------------------------------
generated="$(ensure_azure_secrets)" || die "could not generate secrets (need openssl or python3)"
if [ -n "$generated" ]; then
    log "generated for this environment: $(tr '\n' ' ' <<<"$generated")"
else
    log "secrets already present; none rewritten"
fi
[ "$SECRETS_ONLY" = "1" ] && exit 0

# ---- 2. image tag ----------------------------------------------------------------------------
image_tag="$(env_get IMAGE_TAG)"
if [ -z "$image_tag" ]; then
    die "IMAGE_TAG is not set. Pin a published tag: azd env set IMAGE_TAG v1.4.0   (or sha-<7>; see docs/releasing.md). scripts/azure-up.sh --image-tag does this for you."
fi
case "$image_tag" in latest|main) die "IMAGE_TAG=$image_tag floats. Use a release tag (vX.Y.Z) or sha-<7>." ;; esac

# ---- 3. deployer IP --------------------------------------------------------------------------
public_access="$(env_get PUBLIC_ACCESS)"
public_access="${public_access:-restricted}"
if [ "$public_access" = "open" ]; then
    warn "PUBLIC_ACCESS=open: anyone can reach the frontend and spend your model quota. Budget mode stays enforce."
else
    ip="$(env_get DEPLOYER_IP)"
    if [ -z "$ip" ] || [ "$(env_get DEPLOYER_IP_PINNED)" != "true" ]; then
        detected=""
        for url in https://api.ipify.org https://checkip.amazonaws.com; do
            detected="$(curl -fsS --max-time 8 "$url" 2>/dev/null | tr -d '[:space:]' || true)"
            [[ "$detected" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] && break
            detected=""
        done
        if [ -n "$detected" ]; then
            ip="$detected"
        elif [ -z "$ip" ]; then
            die "could not detect this machine's public IPv4 and DEPLOYER_IP is unset. Set it: azd env set DEPLOYER_IP <a.b.c.d>  (an unrestricted frontend needs PUBLIC_ACCESS=open, which is not recommended)."
        else
            warn "could not re-detect the public IP; keeping DEPLOYER_IP=$ip"
        fi
        env_set DEPLOYER_IP "$ip"
    fi
    log "frontend ingress allows $ip/32 and ALLOWED_IPS ($(env_get ALLOWED_IPS | sed 's/^$/none/'))"
fi

# ---- 4. region checks --------------------------------------------------------------------------
location="$(env_get AZURE_LOCATION)"
[ -n "$location" ] || die "AZURE_LOCATION is not set (azd env set AZURE_LOCATION eastus2)"
openai_location="$(env_get AZURE_OPENAI_LOCATION)"
openai_location="${openai_location:-$location}"
profile="$(env_get DEPLOY_PROFILE)"
profile="${profile:-demo}"
profile_file="infra/profiles/${profile}.json"
[ -f "$profile_file" ] || die "unknown DEPLOY_PROFILE=$profile"
stack="$(env_get DEPLOY_STACK)"
stack="${stack:-python}"
case "$stack" in python|dotnet) ;; *) die "DEPLOY_STACK=$stack is not python or dotnet" ;; esac
image_source="$(env_get IMAGE_SOURCE)"
image_source="${image_source:-auto}"
if [ "$image_source" = auto ]; then
    image_source="$(sed -n 's/.*"imageSource": *"\([a-z]*\)".*/\1/p' "$profile_file" | head -n 1)"
fi
case "$image_source" in ghcr|acr) ;; *) die "IMAGE_SOURCE must be ghcr or acr (or auto); the $profile profile resolves to '$image_source'" ;; esac
if [ "$image_source" = acr ] && [ "$(env_get ACR_IMAGES_TAG)" != "$image_tag" ]; then
    env_set DEPLOY_APPS false
    log "image source acr: provisioning infrastructure first; postprovision fills the registry with $image_tag, then provision again to start the apps"
else
    env_set DEPLOY_APPS true
fi
if [ "$stack" = dotnet ] && [ "$image_source" = ghcr ]; then
    warn "stack dotnet pulls <service>-dotnet images from GHCR at $image_tag; they exist only for releases built after the .NET images were added to build-images.yml"
fi
chat_model="$(env_get AZURE_CHAT_MODEL)"
chat_version="$(env_get AZURE_CHAT_MODEL_VERSION)"

if [ "$AZURE_DRY_RUN" != "1" ] && ! have jq; then die "jq is required for the region checks"; fi
check_postgres_sku "$location" "$(jq -r '.postgres.skuName' "$profile_file")"
check_model "$openai_location" "${chat_model:-gpt-5.1}" "${chat_version:-2025-11-13}" "$(jq -r '.openAi.chatCapacity' "$profile_file")" \
    "$(jq -r '.openAi.embeddingCapacity' "$profile_file")"
env_name="$(env_get AZURE_ENV_NAME)"
if [ -n "$env_name" ]; then
    deleted=0
    check_soft_deleted "$env_name" || deleted=$?
    case "$deleted" in
        0) ;;
        2) die "a purge-protected Key Vault from an earlier '$env_name' deployment still holds its name. Use a different --env." ;;
        *) die "soft-deleted resources from an earlier '$env_name' deployment still hold their names. Purge them: scripts/azure-down.sh --env $env_name --purge-only" ;;
    esac
fi
log "preprovision ok"
