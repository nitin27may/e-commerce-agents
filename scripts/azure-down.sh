#!/usr/bin/env bash
# Remove an Azure deployment and prove nothing is left.
#
#   scripts/azure-down.sh --env demo1 [--dry-run] [--purge-only]
#
# 1. azd down --purge --force   (deletes rg-<env>, purges the Key Vault and the Azure OpenAI account)
# 2. lists soft-deleted Key Vaults and Cognitive Services accounts that belonged to rg-<env>, purges
#    any that remain, and lists again
# 3. az group exists            (must be false)
# Exit 1 if anything remains: a soft-deleted vault keeps its name and breaks the next deployment.
# --purge-only skips step 1 (use it to clear leftovers from a deployment that was deleted by hand).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=scripts/azure/lib/common.sh
source scripts/azure/lib/common.sh
# shellcheck source=scripts/azure/lib/checks.sh
source scripts/azure/lib/checks.sh

ENV_NAME="${AZURE_ENV_NAME:-}" PURGE_ONLY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --env) ENV_NAME="$2"; shift 2 ;;
        --purge-only) PURGE_ONLY=1; shift ;;
        --dry-run) AZURE_DRY_RUN=1; shift ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done
export AZURE_DRY_RUN
[ -n "$ENV_NAME" ] || die "--env is required"
resource_group="rg-$ENV_NAME"

if [ "$PURGE_ONLY" != "1" ]; then
    log "azd down --purge --force ($ENV_NAME)"
    run azd down --environment "$ENV_NAME" --purge --force --no-prompt
fi

status=0
if ! check_soft_deleted "$ENV_NAME"; then
    log "leftovers found; purging"
    check_soft_deleted "$ENV_NAME" purge || true
    if ! check_soft_deleted "$ENV_NAME"; then
        warn "soft-deleted resources remain after the purge; purge them by hand (az keyvault purge, az cognitiveservices account purge)"
        status=1
    fi
fi

if [ "$AZURE_DRY_RUN" = "1" ]; then
    run az group exists --name "$resource_group"
    log "dry run complete"
    exit 0
fi
if [ "$(az group exists --name "$resource_group")" = "true" ]; then
    warn "resource group $resource_group still exists"
    status=1
fi

if [ "$status" -eq 0 ]; then
    log "clean: no resource group, no soft-deleted Key Vault or Cognitive Services account for $ENV_NAME"
else
    die "teardown incomplete (see the warnings above)"
fi
