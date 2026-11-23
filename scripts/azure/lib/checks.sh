#!/usr/bin/env bash
# Region, model, quota and soft-delete preflight. Sourced; needs lib/common.sh, `az` and `jq`.
# Every check reads Azure, so under AZURE_DRY_RUN it only prints what it would run.
#
# The JSON shapes parsed here were checked against live read-only listings (`az cognitiveservices model list` and
# `usage list`, `az postgres flexible-server list-skus`); the `list-deleted` shapes were not (nothing was deleted to
# look at). A parse that finds nothing fails with a message rather than passing.
USAGE_JSON=''

# check_postgres_sku <location> <sku>
# `list-skus` returns one capability object per region; the SKUs are nested under
# supportedServerEditions[].supportedServerSkus[].
check_postgres_sku() {
    local location="$1" sku="$2" listing found reason alternates
    log "PostgreSQL Flexible Server: SKU $sku in $location"
    [ "$AZURE_DRY_RUN" = "1" ] && { run az postgres flexible-server list-skus --location "$location" --output json; return 0; }
    listing="$(az postgres flexible-server list-skus --location "$location" --output json 2>/dev/null || true)"
    [ -n "$listing" ] || die "could not list PostgreSQL SKUs in $location (is Microsoft.DBforPostgreSQL registered, and is the location name valid? az account list-locations -o table)"
    found="$(jq -r --arg sku "$sku" \
        '[.[]? | .supportedServerEditions[]? | .supportedServerSkus[]? | select(.name == $sku and ((.status // "Available") != "Disabled"))] | length' \
        <<<"$listing" 2>/dev/null || echo 0)"
    if [ "${found:-0}" -lt 1 ]; then
        alternates="$(jq -r '[.[]? | .supportedServerEditions[]? | .supportedServerSkus[]? | .name] | .[0:8] | join(", ")' <<<"$listing" 2>/dev/null || true)"
        reason="$(jq -r '[.[]? | select(.status == "Disabled" or .restricted == "Enabled") | .reason // empty] | .[0] // empty' <<<"$listing" 2>/dev/null || true)"
        die "PostgreSQL SKU $sku is not offered to this subscription in $location${reason:+ ($reason)} (new subscriptions are often restricted in busy regions). Offered here: ${alternates:-none}. Pick another --location."
    fi
}

# check_quota <location> <usage-name> <needed-K-TPM> <label>: warns when the quota row is absent, dies when it is too small.
# Reads USAGE_JSON (set by check_model).
check_quota() {
    local location="$1" usage_name="$2" needed="$3" label="$4" used limit free
    used="$(jq -r --arg n "$usage_name" '[.[] | select(.name.value == $n)][0].currentValue // empty' <<<"$USAGE_JSON" 2>/dev/null || true)"
    limit="$(jq -r --arg n "$usage_name" '[.[] | select(.name.value == $n)][0].limit // empty' <<<"$USAGE_JSON" 2>/dev/null || true)"
    if [ -z "$limit" ]; then
        warn "no quota row for $usage_name in $location; cannot confirm ${needed}K TPM is free for $label. Continuing."
        return 0
    fi
    free="$(awk -v l="$limit" -v u="${used:-0}" 'BEGIN { printf "%d", l - u }')"
    # Quota rows count in units of 1K TPM for GlobalStandard.
    if [ "$free" -lt "$needed" ]; then
        die "quota for $label in $location: $free free of $limit (K TPM), need $needed. A new subscription often starts with little or none. Request quota (Azure portal, Foundry, Quotas), lower the capacity in infra/profiles, or pass --openai-location with a region that has quota. Another model: AZURE_CHAT_MODEL=<name> AZURE_CHAT_MODEL_VERSION=<version> scripts/azure-up.sh ... (it needs a row in agents/python/config/pricing/models.yaml, or its cost is reported as unknown)."
    fi
    log "  quota $label: $free free of $limit"
}

# check_model <openai-location> <model> <version> <capacity-K-TPM> [<embedding-capacity-K-TPM>]
check_model() {
    local location="$1" model="$2" version="$3" capacity="$4" embedding_capacity="${5:-}" listing lifecycle
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
        missing) die "$model $version with the GlobalStandard SKU is not available in $location. Pass --openai-location with a region that has it, or set AZURE_CHAT_MODEL / AZURE_CHAT_MODEL_VERSION. What the region offers: az cognitiveservices model list --location $location --query \"[?kind=='OpenAI'].model.name\" -o tsv | sort -u" ;;
        Deprecated|Deprecating) die "$model $version is $lifecycle in $location; new deployments are refused. Choose a current model." ;;
        *) log "  lifecycle: $lifecycle" ;;
    esac
    USAGE_JSON="$(az cognitiveservices usage list --location "$location" --output json 2>/dev/null || true)"
    check_quota "$location" "OpenAI.GlobalStandard.$model" "$capacity" "$model"
    if [ -n "$embedding_capacity" ]; then
        check_quota "$location" "OpenAI.GlobalStandard.text-embedding-3-small" "$embedding_capacity" "text-embedding-3-small"
    fi
}

# check_soft_deleted <azd-env-name> [purge]
# A deleted vault, Cognitive Services account or API Management service keeps its name until purged, and a re-deploy of
# the same environment collides with it. Prints leftovers; with "purge" removes them.
# Returns 0 when clean, 1 when purgeable leftovers remain, 2 when only purge-protected Key Vaults remain (the reference
# profile turns purge protection on: such a vault cannot be purged before its scheduled date, so that environment name
# is unusable until then).
check_soft_deleted() {
    local env_name="$1" action="${2:-}" rg_marker left=0 protected=0 name location rg protect until
    local vaults accounts apims
    rg_marker="/resourcegroups/rg-$(tr '[:upper:]' '[:lower:]' <<<"$env_name")/"
    log "soft-deleted Key Vault / Cognitive Services / API Management for $env_name"
    if [ "$AZURE_DRY_RUN" = "1" ]; then
        run az keyvault list-deleted --output json
        run az cognitiveservices account list-deleted --output json
        run az apim deletedservice list --output json
        return 0
    fi
    vaults="$(az keyvault list-deleted --output json 2>/dev/null || true)"
    accounts="$(az cognitiveservices account list-deleted --output json 2>/dev/null || true)"
    apims="$(az apim deletedservice list --output json 2>/dev/null || true)"
    [ -n "$vaults" ] || vaults='[]'
    [ -n "$accounts" ] || accounts='[]'
    [ -n "$apims" ] || apims='[]'

    while IFS=$'\t' read -r name location protect until; do
        [ -n "$name" ] || continue
        if [ "$protect" = "true" ]; then
            warn "soft-deleted Key Vault $name ($location) is purge-protected until ${until:-its scheduled purge date}; it cannot be purged earlier. Use a different --env name for the next deployment."
            protected=1
        elif [ "$action" = "purge" ]; then
            log "  purging Key Vault $name ($location)"
            az keyvault purge --name "$name" --location "$location" >/dev/null || { warn "purge of $name failed"; left=1; }
        else
            warn "soft-deleted Key Vault: $name ($location)"; left=1
        fi
    done < <(jq -r --arg m "$rg_marker" '.[] | select((.properties.vaultId // "" | ascii_downcase) | contains($m)) | [.name, .properties.location, (.properties.purgeProtectionEnabled // false), (.properties.scheduledPurgeDate // "")] | @tsv' <<<"$vaults")

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

    while IFS=$'\t' read -r name location; do
        [ -n "$name" ] || continue
        if [ "$action" = "purge" ]; then
            log "  purging API Management service $name ($location)"
            az apim deletedservice purge --service-name "$name" --location "$location" >/dev/null || { warn "purge of $name failed"; left=1; }
        else
            warn "soft-deleted API Management service: $name ($location)"; left=1
        fi
    done < <(jq -r --arg m "$rg_marker" '.[] | select((.serviceId // "" | ascii_downcase) | contains($m)) | [.name, .location] | @tsv' <<<"$apims")

    [ "$left" -eq 0 ] || return 1
    [ "$protected" -eq 0 ] || return 2
    return 0
}
