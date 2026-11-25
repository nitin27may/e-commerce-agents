#!/usr/bin/env bash
# Prove an environment is completely gone. READ ONLY: it lists, never deletes (scripts/azure-down.sh deletes).
#
#   scripts/azure/verify-clean.sh --env <env> [--skip-local] [--dry-run]
#
# One line per class of thing that can survive a teardown, CLEAN or LEFT, and exit 1 if any is LEFT:
#   group         the resource group rg-<env>
#   budget        Microsoft.Consumption/budgets budget-<env>, in the group and at subscription scope
#   vault         a soft-deleted Key Vault of the group (a purge-protected one cannot be purged: reported, not billed)
#   account       a soft-deleted Cognitive Services / AIServices (Foundry) account of the group, which holds its project
#   apim          a soft-deleted API Management service of the group
#   workspace     a soft-deleted Log Analytics workspace of the group (it keeps its name for 14 days)
#   deployments   subscription-scope deployment records named <env>, <env>-<suffix>, or whose outputs name rg-<env>
#   roles         role assignments still scoped under rg-<env> (orphans of deleted principals)
#   local         the azd environment state .azure/<env> (--skip-local leaves it out; azure-down.sh removes it after this)
# Not checked, because they are not environment state: resource provider registrations (free) and your sign-in.
# shellcheck disable=SC2016 # the jq expressions are single-quoted on purpose
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1
# shellcheck source=scripts/azure/lib/common.sh
source scripts/azure/lib/common.sh
# shellcheck source=scripts/azure/lib/checks.sh
source scripts/azure/lib/checks.sh

ENV_NAME="${AZURE_ENV_NAME:-}" SKIP_LOCAL=0
while [ $# -gt 0 ]; do
    case "$1" in
        --env) ENV_NAME="$2"; shift 2 ;;
        --skip-local) SKIP_LOCAL=1; shift ;;
        --dry-run) AZURE_DRY_RUN=1; shift ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done
[ -n "$ENV_NAME" ] || die "--env is required"
[[ "$ENV_NAME" =~ ^[a-z0-9][a-z0-9-]{0,30}$ ]] || die "--env must be lowercase letters, digits and dashes (max 31)"
resource_group="rg-$ENV_NAME"
marker="/resourcegroups/$resource_group"

if [ "$AZURE_DRY_RUN" = "1" ]; then
    run az group exists --name "$resource_group"
    run az rest --method get --url "https://management.azure.com/subscriptions/<subscription-id>/resourceGroups/$resource_group/providers/Microsoft.Consumption/budgets/budget-$ENV_NAME?api-version=2023-05-01"
    run az rest --method get --url "https://management.azure.com/subscriptions/<subscription-id>/providers/Microsoft.Consumption/budgets/budget-$ENV_NAME?api-version=2023-05-01"
    run az keyvault list-deleted
    run az cognitiveservices account list-deleted
    run az apim deletedservice list
    run az monitor log-analytics workspace list-deleted-workspaces --resource-group "$resource_group"
    run az deployment sub list
    run az role assignment list --all
    [ "$SKIP_LOCAL" = "1" ] || printf '[dry-run] test -d .azure/%s\n' "$ENV_NAME" >&2
    exit 0
fi
for tool in az jq; do have "$tool" || die "$tool is required"; done
az account show >/dev/null 2>&1 || die "not logged in: az login"

LEFT=0
clean() { printf 'CLEAN  %-12s %s\n' "$1" "$2"; }
left() { LEFT=$((LEFT + 1)); printf 'LEFT   %-12s %s\n' "$1" "$2"; }

if [ "$(az group exists --name "$resource_group" 2>/dev/null)" = "true" ]; then
    left group "$resource_group still exists"
else
    clean group "$resource_group does not exist"
fi

if group_budget_exists "$ENV_NAME" || subscription_budget_exists "$ENV_NAME"; then
    left budget "budget-$ENV_NAME still exists"
else
    clean budget "budget-$ENV_NAME does not exist (group and subscription scope)"
fi

TMPERR="$(mktemp)"
trap 'rm -f "$TMPERR"' EXIT
# list_class <class> <clean text> <left text prefix> <jq expression over the JSON list, printing names> <az command...>
# A failed query is LEFT ("could not query"), never CLEAN, except when the provider is not registered: then nothing can exist.
list_class() {
    local class="$1" clean_text="$2" left_prefix="$3" expr="$4" out mine
    shift 4
    if ! out="$("$@" --output json 2>"$TMPERR")"; then
        if grep -qiE 'ProviderError|not registered|NoRegisteredProviderFound|MissingSubscriptionRegistration' "$TMPERR"; then
            clean "$class" "$clean_text (provider not registered: nothing can exist)"
        else
            left "$class" "could not query: $(head -c 140 "$TMPERR" | tr '\n' ' ')"
        fi
        return
    fi
    mine="$(jq -r --arg m "$marker/" "$expr" <<<"${out:-[]}" 2>/dev/null || true)"
    if [ -n "$mine" ]; then left "$class" "$left_prefix$mine"; else clean "$class" "$clean_text"; fi
}
list_class vault "no soft-deleted Key Vault" "soft-deleted: " \
    '[.[] | select(((.properties.vaultId // "") | ascii_downcase) | contains($m)) | .name + (if (.properties.purgeProtectionEnabled // false) then " (purge protected until " + (.properties.scheduledPurgeDate // "its scheduled date") + ", not billed, the name stays held)" else "" end)] | join(", ")' \
    az keyvault list-deleted
list_class account "no soft-deleted AIServices account" "soft-deleted (with its Foundry project): " \
    '[.[] | select(((.id // "") | ascii_downcase) | contains($m)) | .name] | join(", ")' \
    az cognitiveservices account list-deleted
list_class apim "no soft-deleted API Management service" "soft-deleted: " \
    '[.[] | select(((.serviceId // "") | ascii_downcase) | contains($m)) | .name] | join(", ")' \
    az apim deletedservice list
list_class workspace "no soft-deleted Log Analytics workspace" "soft-deleted (keeps its name for 14 days): " \
    '[.[].name] | join(", ")' \
    az monitor log-analytics workspace list-deleted-workspaces --resource-group "$resource_group"

deps="$(deployments_of_env "$ENV_NAME" | tr '\n' ' ')"
if [ -n "${deps// /}" ]; then left deployments "records: $deps"; else clean deployments "no subscription deployment record"; fi

roles="$(az role assignment list --all --query "[?contains(scope, '/resourceGroups/$resource_group')].{role:roleDefinitionName,scope:scope}" --output json 2>/dev/null || echo '[]')"
count="$(jq 'length' <<<"$roles" 2>/dev/null || echo 0)"
if [ "${count:-0}" -gt 0 ]; then left roles "$count role assignment(s) still scoped under $resource_group"; else clean roles "no role assignment under $resource_group"; fi

if [ "$SKIP_LOCAL" != "1" ]; then
    if [ -d ".azure/$ENV_NAME" ]; then left local ".azure/$ENV_NAME exists (generated secrets in clear text; azure-down.sh removes it)"; else clean local "no local azd state for $ENV_NAME"; fi
fi

echo
if [ "$LEFT" -gt 0 ]; then
    echo "verify-clean: $LEFT class(es) LEFT for '$ENV_NAME'. Run: scripts/azure-down.sh --env $ENV_NAME --purge-only" >&2
    exit 1
fi
echo "verify-clean: every class is CLEAN for '$ENV_NAME'"
exit 0
