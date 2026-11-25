#!/usr/bin/env bash
# Remove an Azure deployment completely and prove nothing is left. Run it only after you have reviewed the sanity check:
# nothing else ever calls it.
#
#   scripts/azure-down.sh --env demo1 [--dry-run] [--purge-only] [--keep-local]
#
# What a resource group delete does NOT remove, and what this script does about each (docs/azure-deployment-sequence.md):
#   1. the Log Analytics workspace: soft-deleted for 14 days, which blocks the same name   -> force-deleted first
#   2. the Key Vault: soft-deleted, holds its name (purge protection, reference profile, cannot be purged) -> purged
#   3. the Foundry / AIServices account, with its project and connection: soft-deleted      -> purged
#   4. API Management, if deployGateway was on: soft-deleted                                -> purged
#   5. subscription-scope deployment records (Microsoft.Resources/deployments)              -> deleted
#   6. the budget: a Microsoft.Consumption/budgets resource. The template puts it IN the resource group, so it goes with
#      the group; a subscription-scope copy (a hand-made one with the same name) is deleted too
#   7. role assignments: the template creates none outside the group; orphans are only reported (verify-clean.sh)
#   8. local azd state, .azure/<env> (it holds the generated secrets and the staff password) -> removed last, and only when
#      everything above is verified clean. --keep-local keeps it.
# Then scripts/azure/verify-clean.sh proves every class is gone, read only, and its exit code is this script's.
# Provider registrations stay (free; `az provider unregister` if you want them gone).
# --dry-run prints every command it would run and deletes nothing. --purge-only skips the resource group delete (use it to
# clear leftovers from a group deleted by hand).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=scripts/azure/lib/common.sh
source scripts/azure/lib/common.sh
# shellcheck source=scripts/azure/lib/checks.sh
source scripts/azure/lib/checks.sh

ENV_NAME="${AZURE_ENV_NAME:-}" PURGE_ONLY=0 KEEP_LOCAL=0
while [ $# -gt 0 ]; do
    case "$1" in
        --env) ENV_NAME="$2"; shift 2 ;;
        --purge-only) PURGE_ONLY=1; shift ;;
        --keep-local) KEEP_LOCAL=1; shift ;;
        --dry-run) AZURE_DRY_RUN=1; shift ;;
        -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done
export AZURE_DRY_RUN
[ -n "$ENV_NAME" ] || die "--env is required"
[[ "$ENV_NAME" =~ ^[a-z0-9][a-z0-9-]{0,30}$ ]] || die "--env must be lowercase letters, digits and dashes (max 31)"
resource_group="rg-$ENV_NAME"
local_state=".azure/$ENV_NAME"

if [ "$AZURE_DRY_RUN" = "1" ]; then
    log "dry run for '$ENV_NAME': nothing below is executed. What this script deletes, in order:"
    {
        echo "  1. Log Analytics workspace(s) in $resource_group     (permanent delete, skips the 14 day soft delete)"
        echo "  2. the resource group $resource_group and everything in it (azd down --purge --force):"
        echo "       Container Apps, Postgres, Key Vault, the AIServices account with its Foundry project and connection,"
        echo "       Application Insights, identities and their role assignments, and the budget (it lives in the group)"
        echo "  3. soft-deleted Key Vault, Cognitive Services / AIServices account, API Management  (purged)"
        echo "  4. subscription-scope deployment records for $ENV_NAME  (az deployment sub delete)"
        echo "  5. a subscription-scope budget named budget-$ENV_NAME, if one exists"
        echo "  6. role assignments under $resource_group: none are created outside it; orphans are reported only"
        echo "  7. local azd state $local_state (unless --keep-local), only after verify-clean passes"
        echo "  then: scripts/azure/verify-clean.sh --env $ENV_NAME"
    } >&2
fi

if [ "$PURGE_ONLY" != "1" ]; then
    if [ "$AZURE_DRY_RUN" != "1" ] && ! azd env list --output json 2>/dev/null | jq -e --arg n "$ENV_NAME" '.[] | select(.Name == $n)' >/dev/null; then
        die "no azd environment named '$ENV_NAME' on this machine (azd keeps them under .azure/). If the deployment was made elsewhere, delete it by hand: az group delete --name $resource_group, then re-run with --purge-only to clear the soft-deleted names."
    fi
    # 1. A deleted workspace stays recoverable (and keeps its name) for 14 days; --force makes it permanent.
    if [ "$AZURE_DRY_RUN" = "1" ]; then
        run az monitor log-analytics workspace delete --resource-group "$resource_group" --workspace-name '<each workspace>' --force true --yes
    else
        for ws in $(az monitor log-analytics workspace list --resource-group "$resource_group" --query '[].name' -o tsv 2>/dev/null || true); do
            log "deleting Log Analytics workspace $ws permanently"
            az monitor log-analytics workspace delete --resource-group "$resource_group" --workspace-name "$ws" --force true --yes \
                || warn "could not force-delete workspace $ws; it will be soft-deleted with the group"
        done
    fi
    # 2. The group and everything in it. A purge-protected vault makes azd's own purge step complain; verify-clean decides.
    log "azd down --purge --force ($ENV_NAME)"
    run azd down --environment "$ENV_NAME" --purge --force --no-prompt \
        || warn "azd down exited non-zero; checking for leftovers anyway"
fi

status=0
# 3. check_soft_deleted: 0 clean, 1 purgeable leftovers, 2 only purge-protected Key Vaults (they cannot be purged early).
deleted=0
check_soft_deleted "$ENV_NAME" || deleted=$?
if [ "$deleted" -eq 1 ]; then
    log "leftovers found; purging"
    check_soft_deleted "$ENV_NAME" purge || true
    deleted=0
    check_soft_deleted "$ENV_NAME" || deleted=$?
    if [ "$deleted" -eq 1 ]; then
        warn "soft-deleted resources remain after the purge; purge them by hand (az keyvault purge, az cognitiveservices account purge, az apim deletedservice purge)"
        status=1
    fi
fi
if [ "$deleted" -eq 2 ]; then
    warn "a purge-protected Key Vault stays soft-deleted until its scheduled purge date (reference profile). Nothing is billed for it, but do not reuse the name '$ENV_NAME' before then."
fi

# 4. Subscription-scope deployment records (they cost nothing, but they list the environment's outputs and block nothing).
if [ "$AZURE_DRY_RUN" = "1" ]; then
    run az deployment sub delete --name '<each deployment of this environment>'
else
    for dep in $(deployments_of_env "$ENV_NAME"); do
        log "deleting subscription deployment record $dep"
        az deployment sub delete --name "$dep" >/dev/null || { warn "could not delete deployment record $dep"; status=1; }
    done
fi

# 5. A subscription-scope budget with this environment's name (the template's own budget lives in the group).
if [ "$AZURE_DRY_RUN" = "1" ]; then
    run az rest --method delete --url "https://management.azure.com/subscriptions/<subscription-id>/providers/Microsoft.Consumption/budgets/budget-$ENV_NAME?api-version=2023-05-01"
else
    if subscription_budget_exists "$ENV_NAME"; then
        log "deleting subscription-scope budget budget-$ENV_NAME"
        az rest --method delete --url "https://management.azure.com/subscriptions/$(az account show --query id -o tsv)/providers/Microsoft.Consumption/budgets/budget-$ENV_NAME?api-version=2023-05-01" >/dev/null \
            || { warn "could not delete the subscription budget"; status=1; }
    fi
fi

# 6 and 7. Prove it, read only; then the local state.
if [ "$AZURE_DRY_RUN" = "1" ]; then
    run scripts/azure/verify-clean.sh --env "$ENV_NAME" --skip-local
    [ "$KEEP_LOCAL" = "1" ] || log "would remove $local_state after a clean verification"
    log "dry run complete"
    exit 0
fi
verify=0
scripts/azure/verify-clean.sh --env "$ENV_NAME" --skip-local || verify=$?
if [ "$verify" -ne 0 ] || [ "$status" -ne 0 ]; then
    die "teardown incomplete (see above). Re-run: scripts/azure-down.sh --env $ENV_NAME --purge-only. Local state $local_state was kept."
fi
if [ "$KEEP_LOCAL" = "1" ]; then
    log "kept local azd state $local_state (--keep-local); it holds the generated secrets in clear text"
elif [ -d "$local_state" ]; then
    rm -rf -- "${local_state:?}"
    log "removed local azd state $local_state"
fi
log "clean: $ENV_NAME is gone, nothing is billing and no name is held"
