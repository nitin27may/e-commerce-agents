#!/usr/bin/env bash
# Secret generation for scripts/azure/preprovision.sh. Sourced, not executed; needs lib/common.sh.
#
# Generated once per azd environment and never overwritten: a rotated POSTGRES_ADMIN_PASSWORD or
# AUTH_SIGNING_KEY_ENCRYPTION_KEY would lock the deployment out of its own data. Never newGuid()
# (changes every deploy) or uniqueString() (derived from public values) in Bicep for these.
#
# Formats (docs/azure-deployment.md):
#   POSTGRES_ADMIN_PASSWORD   32 alphanumerics, upper + lower + digit, so DATABASE_URL needs no URL-encoding
#   JWT_SECRET, AGENT_SHARED_SECRET, A2A_IDENTITY_KEY, OAUTH_SEED_KEY, AUTH_SIGNING_KEY_ENCRYPTION_KEY
#                             64 hex characters (256 bits); A2A_IDENTITY_KEY differs from AGENT_SHARED_SECRET
#   SEED_STAFF_PASSWORD       20 alphanumerics, upper + lower + digit

HEX_SECRET_VARS=(JWT_SECRET AGENT_SHARED_SECRET A2A_IDENTITY_KEY OAUTH_SEED_KEY AUTH_SIGNING_KEY_ENCRYPTION_KEY)
PG_PASSWORD_LENGTH=32
STAFF_PASSWORD_LENGTH=20

generate_hex64() {
    if have openssl; then
        openssl rand -hex 32
    elif have python3; then
        python3 -c 'import secrets; print(secrets.token_hex(32))'
    else
        return 1
    fi
}

# generate_alnum <length>: letters and digits only, with at least one upper, one lower and one digit.
generate_alnum() {
    local length="$1" candidate raw
    while :; do
        if have openssl; then
            raw="$(openssl rand -base64 96)"
        elif have python3; then
            raw="$(python3 -c 'import secrets; print(secrets.token_urlsafe(72))')"
        else
            return 1
        fi
        # tr never sees a closed pipe here (no `head`), so pipefail stays quiet.
        candidate="$(printf '%s' "$raw" | tr -dc 'A-Za-z0-9')"
        candidate="${candidate:0:length}"
        [ "${#candidate}" -eq "$length" ] || continue
        [[ "$candidate" =~ [A-Z] && "$candidate" =~ [a-z] && "$candidate" =~ [0-9] ]] && { printf '%s' "$candidate"; return 0; }
    done
}

# ensure_azure_secrets: sets every missing secret in the azd environment; prints the names it generated.
ensure_azure_secrets() {
    local var value
    if [ -z "$(env_get POSTGRES_ADMIN_PASSWORD)" ]; then
        env_set POSTGRES_ADMIN_PASSWORD "$(generate_alnum "$PG_PASSWORD_LENGTH")"
        echo POSTGRES_ADMIN_PASSWORD
    fi
    for var in "${HEX_SECRET_VARS[@]}"; do
        [ -z "$(env_get "$var")" ] || continue
        value="$(generate_hex64)"
        if [ "$var" = "A2A_IDENTITY_KEY" ]; then
            # Distinct from AGENT_SHARED_SECRET, or `token` mode protects nothing.
            while [ "$value" = "$(env_get AGENT_SHARED_SECRET)" ]; do value="$(generate_hex64)"; done
        fi
        env_set "$var" "$value"
        echo "$var"
    done
    if [ -z "$(env_get SEED_STAFF_PASSWORD)" ]; then
        env_set SEED_STAFF_PASSWORD "$(generate_alnum "$STAFF_PASSWORD_LENGTH")"
        echo SEED_STAFF_PASSWORD
    fi
    if [ "$(env_get A2A_IDENTITY_KEY)" = "$(env_get AGENT_SHARED_SECRET)" ]; then
        warn "A2A_IDENTITY_KEY equals AGENT_SHARED_SECRET (both were set by hand); A2A_IDENTITY=token will not protect against a caller holding the shared secret."
    fi
    return 0
}
