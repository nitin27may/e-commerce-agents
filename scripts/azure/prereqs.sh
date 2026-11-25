#!/usr/bin/env bash
# Azure deployment prerequisites. Read-mostly: it installs the `containerapp` CLI extension and
# registers missing resource providers (both idempotent), nothing else.
#
#   scripts/azure/prereqs.sh [--location eastus2] [--openai-location eastus2] [--profile demo|reference|free-trial]
#                            [--no-openai] [--dry-run]
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
    Microsoft.DBforPostgreSQL Microsoft.KeyVault Microsoft.ManagedIdentity Microsoft.Consumption Microsoft.Authorization
    Microsoft.ContainerRegistry Microsoft.Network)
# A brand-new subscription has most of these unregistered, and registration takes one to several minutes each (they run
# in parallel here). Seconds to wait for all of them before giving up.
PROVIDER_WAIT_SECONDS="${PROVIDER_WAIT_SECONDS:-1200}"

LOCATION="${AZURE_LOCATION:-}"
OPENAI_LOCATION="${AZURE_OPENAI_LOCATION:-}"
PROFILE="${DEPLOY_PROFILE:-demo}"
ENV_NAME="${AZURE_ENV_NAME:-}"
# false (--no-openai, or DEPLOY_OPENAI in the azd environment): no Azure OpenAI account, so no model, quota or provider check for it.
DEPLOY_OPENAI="${DEPLOY_OPENAI:-$(env_get DEPLOY_OPENAI)}"
DEPLOY_OPENAI="${DEPLOY_OPENAI:-true}"
while [ $# -gt 0 ]; do
    case "$1" in
        --location) LOCATION="$2"; shift 2 ;;
        --openai-location) OPENAI_LOCATION="$2"; shift 2 ;;
        --profile) PROFILE="$2"; shift 2 ;;
        --env) ENV_NAME="$2"; shift 2 ;;
        --dry-run) AZURE_DRY_RUN=1; shift ;;
        --no-openai) DEPLOY_OPENAI=false; shift ;;
        -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
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
    az account show >/dev/null 2>&1 || die "not logged in: run 'az login'"
    # azd keeps its own credential cache; `az login` does not sign it in.
    if have azd && ! azd auth login --check-status >/dev/null 2>&1; then
        die "azd is not signed in: run 'azd auth login' (it is separate from 'az login')"
    fi
    log "subscription: $(az account show --query '[name, id]' -o tsv | tr '\t' ' ') (change it with: az account set --subscription <id>)"
fi
if [ "$AZURE_DRY_RUN" != "1" ] && ! az extension show --name containerapp >/dev/null 2>&1; then
    log "installing the az containerapp extension"
fi
run az extension add --name containerapp --upgrade --only-show-errors

# ---- providers -------------------------------------------------------------------------------
providers=("${PROVIDERS[@]}")
case "$PROFILE" in reference) providers+=(Microsoft.Cache) ;; esac
[ "$(env_get DEPLOY_GATEWAY)" != true ] || providers+=(Microsoft.ApiManagement)
# The free-trial profile has no registry and no VNet (and, without Azure OpenAI, no Cognitive Services): register only what it uses.
if [ "$PROFILE" = free-trial ]; then
    drop=" Microsoft.ContainerRegistry Microsoft.Network "
    [ "$DEPLOY_OPENAI" != false ] || drop="$drop Microsoft.CognitiveServices "
    kept=()
    for p in "${providers[@]}"; do
        case "$drop" in *" $p "*) ;; *) kept+=("$p") ;; esac
    done
    providers=("${kept[@]}")
fi
pending=()
for p in "${providers[@]}"; do
    if [ "$AZURE_DRY_RUN" = "1" ]; then
        run az provider register --namespace "$p"
        continue
    fi
    state="$(az provider show --namespace "$p" --query registrationState -o tsv 2>/dev/null || echo NotRegistered)"
    if [ "$state" != "Registered" ]; then
        log "registering $p (was $state)"
        # No --wait: start every registration, then poll them together below.
        az provider register --namespace "$p" --only-show-errors >/dev/null \
            || die "could not start registering $p; registering a provider needs Contributor or Owner on the subscription"
        pending+=("$p")
    fi
done
if [ "${#pending[@]}" -gt 0 ]; then
    log "waiting for ${#pending[@]} provider registration(s); on a new subscription this can take several minutes"
    deadline=$((SECONDS + PROVIDER_WAIT_SECONDS))
    while [ "${#pending[@]}" -gt 0 ]; do
        still=()
        for p in "${pending[@]}"; do
            state="$(az provider show --namespace "$p" --query registrationState -o tsv 2>/dev/null || echo Unknown)"
            [ "$state" = "Registered" ] || still+=("$p")
        done
        pending=("${still[@]+"${still[@]}"}")
        [ "${#pending[@]}" -gt 0 ] || break
        [ "$SECONDS" -lt "$deadline" ] || die "still registering after ${PROVIDER_WAIT_SECONDS}s: ${pending[*]}. Re-run this script; registration continues in the background (az provider show --namespace <ns> --query registrationState)."
        sleep 10
    done
fi
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
        # --include-groups also finds a role held through a group; it needs Graph read access, so fall back without it.
        roles="$(az role assignment list --assignee "$principal" --include-inherited --include-groups \
            --scope "/subscriptions/$subscription_id" --query '[].roleDefinitionName' -o tsv 2>/dev/null || true)"
        [ -n "$roles" ] || roles="$(az role assignment list --assignee "$principal" --include-inherited \
            --scope "/subscriptions/$subscription_id" --query '[].roleDefinitionName' -o tsv 2>/dev/null || true)"
        if [ -z "$roles" ]; then
            warn "could not read any role assignment for the signed-in user on the subscription (no Graph or RBAC read access?). Continuing; the deployment fails fast if you cannot create role assignments."
        elif grep -qx 'Owner' <<<"$roles" \
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
embedding_capacity="$(jq -r '.openAi.embeddingCapacity' "$profile_file")"
check_postgres_sku "$LOCATION" "$sku"
if [ "$DEPLOY_OPENAI" = false ]; then
    log "DEPLOY_OPENAI=false: no Azure OpenAI account, skipping model and quota checks"
else
    # The profile names its own default model (free-trial: gpt-4.1-mini, which has quota on a new subscription); an explicit AZURE_CHAT_MODEL wins.
    default_model="$(jq -r '.openAi.chatModelName // "gpt-5.1"' "$profile_file")"
    default_version="$(jq -r '.openAi.chatModelVersion // "2025-11-13"' "$profile_file")"
    check_model "$OPENAI_LOCATION" "${AZURE_CHAT_MODEL:-$default_model}" "${AZURE_CHAT_MODEL_VERSION:-$default_version}" "$capacity" "$embedding_capacity"
fi
if [ -n "$ENV_NAME" ]; then
    deleted=0
    check_soft_deleted "$ENV_NAME" || deleted=$?
    case "$deleted" in
        0) ;;
        2) die "a purge-protected Key Vault from an earlier '$ENV_NAME' deployment still holds its name. Use a different --env." ;;
        *) die "soft-deleted resources from a previous '$ENV_NAME' deployment hold their names. Purge them: scripts/azure-down.sh --env $ENV_NAME --purge-only" ;;
    esac
fi
log "prerequisites ok"
