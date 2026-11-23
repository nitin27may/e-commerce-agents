#!/usr/bin/env bash
# azd postprovision hook (azure.yaml): bootstrap the database, print where to log in, smoke test.
#
#   1. starts job-db-init (migrate, seed if empty, embeddings, ANALYZE) and polls the execution;
#   2. prints the outputs a user needs (no secret leaves the azd environment except the staff
#      password this deployment generated for them);
#   3. runs scripts/azure/smoke.sh --quick unless SKIP_SMOKE=1.
#
# With IMAGE_SOURCE=acr (the reference profile's default) it first puts the images into the registry the template just
# created: `az acr import` from GHCR, or `az acr build` from this tree when BUILD_FROM_SOURCE=true. Until the registry
# holds IMAGE_TAG preprovision keeps DEPLOY_APPS=false, so that first pass stops here; run `azd provision` again (or use
# scripts/azure-up.sh, which does) to start the apps, bootstrap the database and smoke test.
#
# The apps start before the job finishes and answer 503 on /ready until the schema exists; that is
# expected. Container Apps log queries lag by a minute or two, so a failed run prints the command
# to retry rather than the (possibly still empty) log.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
# shellcheck source=scripts/azure/lib/common.sh
source scripts/azure/lib/common.sh

JOB_TIMEOUT_SECONDS="${JOB_TIMEOUT_SECONDS:-1200}"
POLL_SECONDS="${POLL_SECONDS:-10}"
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) AZURE_DRY_RUN=1; shift ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

resource_group="$(env_get AZURE_RESOURCE_GROUP)"

# ---- 0. images into ACR (image source acr only) -------------------------------------------------
image_source="$(env_get IMAGE_SOURCE)"
registry="$(env_get AZURE_CONTAINER_REGISTRY_NAME)"
if [ "$AZURE_DRY_RUN" = "1" ] && [ "${image_source:-}" = acr ]; then registry="${registry:-acr-example}"; fi
if [ -n "$registry" ]; then
    image_tag="$(env_get IMAGE_TAG)"
    stack="$(env_get DEPLOY_STACK)"
    mode=import
    [ "$(env_get BUILD_FROM_SOURCE)" != true ] || mode=build
    acr_args=("$mode" --registry "$registry" --tag "${image_tag:-unset}" --stack "${stack:-python}")
    [ "$AZURE_DRY_RUN" != "1" ] || acr_args+=(--dry-run)
    scripts/azure/acr-images.sh "${acr_args[@]}"
    env_set ACR_IMAGES_TAG "${image_tag:-unset}"
    if [ "$(env_get DEPLOY_APPS)" = false ]; then
        log "registry holds $image_tag. Run 'azd provision' again to start the apps (scripts/azure-up.sh does this for you)."
        exit 0
    fi
fi
job_name="$(env_get DB_INIT_JOB_NAME)"
frontend_url="$(env_get FRONTEND_URL)"
if [ "$AZURE_DRY_RUN" = "1" ]; then
    resource_group="${resource_group:-rg-example}"; job_name="${job_name:-job-db-init}"; frontend_url="${frontend_url:-https://ca-frontend.example}"
fi
[ -n "$resource_group" ] && [ -n "$job_name" ] || die "AZURE_RESOURCE_GROUP / DB_INIT_JOB_NAME missing from the azd environment: did provisioning finish?"

# ---- 1. database job ---------------------------------------------------------------------------
log "starting $job_name"
execution=""
if [ "$AZURE_DRY_RUN" = "1" ]; then
    capture az containerapp job start --name "$job_name" --resource-group "$resource_group" --query name --output tsv
else
    # The job resource can answer 'not ready' for a short while after the deployment reports done.
    for attempt in 1 2 3 4; do
        execution="$(az containerapp job start --name "$job_name" --resource-group "$resource_group" --query name --output tsv 2>/dev/null || true)"
        [ -z "$execution" ] || break
        log "job start attempt $attempt returned nothing; retrying in 20s"
        sleep 20
    done
fi
if [ "$AZURE_DRY_RUN" = "1" ]; then
    run az containerapp job execution show --name "$job_name" --resource-group "$resource_group" --job-execution-name '<execution>'
else
    [ -n "$execution" ] || die "az containerapp job start returned no execution name after 4 tries (az extension add --name containerapp --upgrade; then: az containerapp job start --name $job_name --resource-group $resource_group)"
    log "execution $execution"
    deadline=$((SECONDS + JOB_TIMEOUT_SECONDS))
    while :; do
        status="$(az containerapp job execution show --name "$job_name" --resource-group "$resource_group" \
            --job-execution-name "$execution" --query properties.status --output tsv 2>/dev/null || echo Unknown)"
        case "$status" in
            Succeeded) log "db-init succeeded"; break ;;
            Failed|Stopped|Degraded)
                log "db-init $status. Logs (may lag a minute or two):"
                log "  az containerapp job logs show --name $job_name --resource-group $resource_group --execution $execution --container db-init --follow false"
                log "  or Log Analytics: ContainerAppConsoleLogs_CL | where ContainerJobName_s == '$job_name'"
                die "database bootstrap failed (status $status). Fix the cause and re-run: az containerapp job start --name $job_name --resource-group $resource_group"
                ;;
        esac
        [ "$SECONDS" -lt "$deadline" ] || die "db-init still '$status' after ${JOB_TIMEOUT_SECONDS}s"
        sleep "$POLL_SECONDS"
    done
fi

# ---- 2. outputs --------------------------------------------------------------------------------
admin_email="$(env_get ADMIN_LOGIN_EMAIL)"
{
    echo
    echo "  Frontend           : ${frontend_url}"
    echo "  Admin login        : ${admin_email:-admin.demo@gmail.com}"
    echo "  Staff password     : azd env get-value SEED_STAFF_PASSWORD   (admin, support and seller share it)"
    echo "  Customer demo user : alice.johnson@gmail.com / customer123 (published demo password)"
    echo "  Key Vault          : $(env_get AZURE_KEY_VAULT_NAME)"
    echo "  Resource group     : ${resource_group}"
    echo "  App Insights       : $(env_get APPLICATIONINSIGHTS_NAME)"
    echo "  Tear down          : scripts/azure-down.sh"
    echo
} >&2

# ---- 3. smoke ----------------------------------------------------------------------------------
if [ "${SKIP_SMOKE:-0}" = "1" ]; then
    log "SKIP_SMOKE=1: not running the smoke test"
    exit 0
fi
if [ ! -x scripts/azure/smoke.sh ]; then
    warn "scripts/azure/smoke.sh not found or not executable; skipping"
    exit 0
fi
if [ "$AZURE_DRY_RUN" = "1" ]; then
    run scripts/azure/smoke.sh --quick
    exit 0
fi
export FRONTEND_URL="$frontend_url" AZURE_RESOURCE_GROUP="$resource_group" DB_INIT_JOB_NAME="$job_name"
export E2E_STAFF_PASSWORD="${E2E_STAFF_PASSWORD:-$(env_get SEED_STAFF_PASSWORD)}"
scripts/azure/smoke.sh --quick
