#!/usr/bin/env bash
# Region, model, quota and soft-delete preflight. Sourced; needs lib/common.sh, `az` and `jq`.
# Every check reads Azure, so under AZURE_DRY_RUN it only prints what it would run.
#
# The JSON shapes parsed here (`az cognitiveservices model list`, `usage list`, `list-deleted`,
# `az postgres flexible-server list-skus`) are written from the CLI documentation and have not been
# checked against a live subscription; a parse that finds nothing fails with a message rather than passing.

# check_postgres_sku <location> <sku>
check_postgres_sku() {
    local location="$1" sku="$2" found
    log "PostgreSQL Flexible Server: SKU $sku in $location"
    [ "$AZURE_DRY_RUN" = "1" ] && { run az postgres flexible-server list-skus --location "$location" --output json; return 0; }
    found="$(az postgres flexible-server list-skus --location "$location" --output json 2>/dev/null \
        | jq -r --arg sku "$sku" '[.[] | select(.name == $sku)] | length' 2>/dev/null || echo 0)"
    if [ "${found:-0}" -lt 1 ]; then
        local alternates
        alternates="$(az postgres flexible-server list-skus --location "$location" --output json 2>/dev/null \
            | jq -r '[.[].name] | .[0:8] | join(", ")' 2>/dev/null || true)"
        die "PostgreSQL SKU $sku is not offered to this subscription in $location (offer restricted regions are common for new subscriptions). Offered here: ${alternates:-none}. Pick another AZURE_LOCATION."
    fi
}

# check_model <openai-location> <model> <version> <capacity-K-TPM>
check_model() {
    local location="$1" model="$2" version="$3" capacity="$4" listing lifecycle usage used limit free
    log "Azure OpenAI: $model $version (GlobalStandard, ${capacity}K TPM) in $location"
    if [ "$AZURE_DRY_RUN" = "1" ]; then
        run az cognitiveservices model list --location "$location" --output json
        run az cognitiveservices usage list --location "$location" --output json
        return 0
    fi
    listing="$(az cognitiveservices model list --location "$location" --output json 2>/dev/null || true)"
    [ -n "$listing" ] || die "could not list Azure OpenAI models in $location (is the Microsoft.CognitiveServices provider registered?)"
    lifecycle="$(jq -r --arg m "$model" --arg v "$version" \
        '[.[] | .model | select(.name == $m and .version == $v and ([.skus[]?.name] | index("GlobalStandard")))] | .[0].lifecycleStatus // "missing"' \
        <<<"$listing" 2>/dev/null || echo missing)"
    case "$lifecycle" in
        missing) die "$model $version with the GlobalStandard SKU is not available in $location. Set AZURE_OPENAI_LOCATION to a region that has it, or AZURE_CHAT_MODEL / AZURE_CHAT_MODEL_VERSION." ;;
        Deprecated|Deprecating) die "$model $version is $lifecycle in $location; new deployments are refused. Choose a current model." ;;
        *) log "  lifecycle: $lifecycle" ;;
    esac
    usage="$(az cognitiveservices usage list --location "$location" --output json 2>/dev/null || true)"
    used="$(jq -r --arg m "$model" '[.[] | select(.name.value == ("OpenAI.GlobalStandard." + $m))][0].currentValue // empty' <<<"$usage" 2>/dev/null || true)"
    limit="$(jq -r --arg m "$model" '[.[] | select(.name.value == ("OpenAI.GlobalStandard." + $m))][0].limit // empty' <<<"$usage" 2>/dev/null || true)"
    if [ -z "$limit" ]; then
        warn "no quota row for OpenAI.GlobalStandard.$model in $location; cannot confirm ${capacity}K TPM is free. Continuing."
        return 0
    fi
    free="$(awk -v l="$limit" -v u="${used:-0}" 'BEGIN { printf "%d", l - u }')"
    # Quota rows count in units of 1K TPM for GlobalStandard.
    if [ "$free" -lt "$capacity" ]; then
        die "quota for $model in $location: $free free of $limit, need $capacity. Lower the capacity in infra/profiles, request quota, or set AZURE_OPENAI_LOCATION to another region."
    fi
    log "  quota: $free free of $limit"
}

# check_soft_deleted <azd-env-name> [purge]
# A deleted vault or Cognitive Services account keeps its name until purged, and a re-deploy of the same
# environment collides with it. Prints leftovers; with "purge" removes them. Returns 1 when any remain.
check_soft_deleted() {
    local env_name="$1" action="${2:-}" rg_marker left=0 name location rg
    rg_marker="/resourcegroups/rg-$(tr '[:upper:]' '[:lower:]' <<<"$env_name")/"
    log "soft-deleted Key Vault / Cognitive Services for $env_name"
    if [ "$AZURE_DRY_RUN" = "1" ]; then
        run az keyvault list-deleted --output json
        run az cognitiveservices account list-deleted --output json
        return 0
    fi
    local vaults accounts
    vaults="$(az keyvault list-deleted --output json 2>/dev/null || echo '[]')"
    accounts="$(az cognitiveservices account list-deleted --output json 2>/dev/null || echo '[]')"

    while IFS=$'\t' read -r name location; do
        [ -n "$name" ] || continue
        if [ "$action" = "purge" ]; then
            log "  purging Key Vault $name ($location)"
            az keyvault purge --name "$name" --location "$location" >/dev/null || { warn "purge of $name failed"; left=1; }
        else
            warn "soft-deleted Key Vault: $name ($location)"; left=1
        fi
    done < <(jq -r --arg m "$rg_marker" '.[] | select((.properties.vaultId // "" | ascii_downcase) | contains($m)) | [.name, .properties.location] | @tsv' <<<"$vaults")

    while IFS=$'\t' read -r name location rg; do
        [ -n "$name" ] || continue
        if [ "$action" = "purge" ]; then
            log "  purging Cognitive Services account $name ($location)"
            az cognitiveservices account purge --name "$name" --resource-group "$rg" --location "$location" >/dev/null \
                || { warn "purge of $name failed"; left=1; }
        else
            warn "soft-deleted Cognitive Services account: $name ($location)"; left=1
        fi
    done < <(jq -r --arg m "$rg_marker" '.[] | select((.id // "" | ascii_downcase) | contains($m)) | [.name, .location, (.id | capture("(?i)resourceGroups/(?<rg>[^/]+)").rg)] | @tsv' <<<"$accounts")
    return "$left"
}
