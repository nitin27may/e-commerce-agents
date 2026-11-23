#!/usr/bin/env bash
# Azure deployment prerequisites. Read-mostly: it installs the `containerapp` CLI extension and
# registers missing resource providers (both idempotent), nothing else.
#
#   scripts/azure/prereqs.sh [--location eastus2] [--openai-location eastus2] [--profile demo] [--dry-run]
#
# Checks, in order: az / azd / bicep floor versions, jq and curl, az login, the containerapp
# extension, resource provider registrations, role-assignment write rights, and (when a location is
# known) Postgres SKU, model availability and quota, and soft-deleted names. Exit 1 on the first
# failure, with the fix in the message.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
# shellcheck source=scripts/azure/lib/common.sh
source scripts/azure/lib/common.sh
# shellcheck source=scripts/azure/lib/checks.sh
source scripts/azure/lib/checks.sh

MIN_AZ=2.67.0
MIN_AZD=1.18.0
MIN_BICEP=0.34.0
PROVIDERS=(Microsoft.App Microsoft.OperationalInsights Microsoft.Insights Microsoft.CognitiveServices
    Microsoft.DBforPostgreSQL Microsoft.KeyVault Microsoft.ManagedIdentity Microsoft.Consumption Microsoft.Authorization Microsoft.ContainerRegistry)

LOCATION="${AZURE_LOCATION:-}"
OPENAI_LOCATION="${AZURE_OPENAI_LOCATION:-}"
PROFILE="${DEPLOY_PROFILE:-demo}"
ENV_NAME="${AZURE_ENV_NAME:-}"
while [ $# -gt 0 ]; do
    case "$1" in
        --location) LOCATION="$2"; shift 2 ;;
        --openai-location) OPENAI_LOCATION="$2"; shift 2 ;;
        --profile) PROFILE="$2"; shift 2 ;;
        --env) ENV_NAME="$2"; shift 2 ;;
        --dry-run) AZURE_DRY_RUN=1; shift ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done
OPENAI_LOCATION="${OPENAI_LOCATION:-$LOCATION}"

# ---- tools -----------------------------------------------------------------------------------
# Under --dry-run a missing tool is a warning, so the whole script can be read offline.
for tool in az azd jq curl; do
    have "$tool" && continue
    if [ "$AZURE_DRY_RUN" = "1" ]; then warn "$tool is not installed (dry run, continuing)"; continue; fi
    die "$tool is not installed. az: https://aka.ms/installazurecli  azd: https://aka.ms/azd  jq, curl: your package manager."
done
if have az; then
    version_ge "$(first_version "$(az version --query '"azure-cli"' -o tsv 2>/dev/null || az --version | head -n 1)")" "$MIN_AZ" \
        || die "az $MIN_AZ or newer is required"
fi
if have azd; then
    version_ge "$(first_version "$(azd version 2>/dev/null | head -n 1)")" "$MIN_AZD" || die "azd $MIN_AZD or newer is required"
fi
bicep_version=none
if have bicep; then
    bicep_version="$(first_version "$(bicep --version)")"
elif [ -x "$HOME/.azure/bin/bicep" ]; then
    bicep_version="$(first_version "$("$HOME/.azure/bin/bicep" --version)")"
elif [ "$AZURE_DRY_RUN" != "1" ]; then
    die "the Bicep CLI is missing: run 'az bicep install'"
fi
if [ "$bicep_version" != none ]; then
    version_ge "$bicep_version" "$MIN_BICEP" || die "bicep $MIN_BICEP or newer is required (found $bicep_version): run 'az bicep upgrade'"
fi
log "tools ok (bicep $bicep_version)"

# ---- login and extension ---------------------------------------------------------------------
if [ "$AZURE_DRY_RUN" = "1" ]; then
    run az account show
else
    az account show >/dev/null 2>&1 || die "not logged in: run 'az login' (and 'azd auth login')"
fi
if [ "$AZURE_DRY_RUN" != "1" ] && ! az extension show --name containerapp >/dev/null 2>&1; then
    log "installing the az containerapp extension"
fi
run az extension add --name containerapp --upgrade --only-show-errors

# ---- providers -------------------------------------------------------------------------------
providers=("${PROVIDERS[@]}")
case "$PROFILE" in reference) providers+=(Microsoft.Cache Microsoft.Network) ;; esac
for p in "${providers[@]}"; do
    if [ "$AZURE_DRY_RUN" = "1" ]; then
        run az provider register --namespace "$p"
        continue
    fi
    state="$(az provider show --namespace "$p" --query registrationState -o tsv 2>/dev/null || echo NotRegistered)"
    if [ "$state" != "Registered" ]; then
        log "registering $p (was $state)"
        az provider register --namespace "$p" --wait --only-show-errors
    fi
done
log "resource providers registered"

# ---- role assignment rights --------------------------------------------------------------------
# The Bicep creates role assignments (Key Vault Secrets User, Cognitive Services OpenAI User), which
# needs Owner, or Contributor plus User Access Administrator, or RBAC Administrator, on the subscription.
if [ "${SKIP_RBAC_CHECK:-0}" = "1" ]; then
    warn "SKIP_RBAC_CHECK=1: not checking role-assignment rights"
elif [ "$AZURE_DRY_RUN" = "1" ]; then
    run az role assignment list --assignee '<signed-in principal>' --include-inherited
else
    subscription_id="$(az account show --query id -o tsv)"
    principal="$(az ad signed-in-user show --query id -o tsv 2>/dev/null || true)"
    if [ -z "$principal" ]; then
        warn "cannot resolve the signed-in user (service principal or no Graph access); set SKIP_RBAC_CHECK=1 once you have confirmed Owner or User Access Administrator"
    else
        roles="$(az role assignment list --assignee "$principal" --include-inherited \
            --scope "/subscriptions/$subscription_id" --query '[].roleDefinitionName' -o tsv 2>/dev/null || true)"
        if grep -qx 'Owner' <<<"$roles" \
            || { grep -qx 'Contributor' <<<"$roles" && grep -qx 'User Access Administrator' <<<"$roles"; } \
            || grep -qx 'Role Based Access Control Administrator' <<<"$roles"; then
            log "role-assignment rights ok"
        else
            die "the signed-in user cannot create role assignments on subscription $subscription_id (found: ${roles//$'\n'/, }). Needs Owner, or Contributor plus User Access Administrator."
        fi
    fi
fi

# ---- region checks ---------------------------------------------------------------------------
if [ -z "$LOCATION" ]; then
    log "no --location / AZURE_LOCATION: skipping SKU, model, quota checks (preprovision repeats them)"
    log "prerequisites ok"
    exit 0
fi
profile_file="infra/profiles/${PROFILE}.json"
[ -f "$profile_file" ] || die "unknown profile $PROFILE (no $profile_file)"
sku="$(jq -r '.postgres.skuName' "$profile_file")"
capacity="$(jq -r '.openAi.chatCapacity' "$profile_file")"
check_postgres_sku "$LOCATION" "$sku"
check_model "$OPENAI_LOCATION" "${AZURE_CHAT_MODEL:-gpt-5.1}" "${AZURE_CHAT_MODEL_VERSION:-2025-11-13}" "$capacity"
if [ -n "$ENV_NAME" ]; then
    check_soft_deleted "$ENV_NAME" || die "soft-deleted resources from a previous '$ENV_NAME' deployment hold their names. Purge them: scripts/azure-down.sh --env $ENV_NAME --purge-only"
fi
log "prerequisites ok"
