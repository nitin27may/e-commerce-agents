#!/usr/bin/env bash
# Local-secret generation for scripts/dev.sh. Sourced, not executed.
#
# A fresh `.env` is a copy of `.env.example`, whose JWT_SECRET, AGENT_SHARED_SECRET
# and OAUTH_SEED_KEY are public placeholders. Anything reachable with a public
# placeholder is effectively unauthenticated, so the first run replaces them.
# Values a user has already set are never touched: only a missing, empty or
# known-placeholder value is rewritten. scripts/dev.ps1 mirrors this logic.

SECRET_VARS=(JWT_SECRET AGENT_SHARED_SECRET OAUTH_SEED_KEY)

# Keep in step with _UNSAFE_SECRET_DEFAULTS (agents/python/shared/config.py) and
# UnsafeDefaults (AgentSettingsValidator.cs), plus the empty string.
SECRET_PLACEHOLDERS=(
    ""
    "change-me-in-production"
    "change-me-generate-a-random-256-bit-key"
    "change-me-in-production-use-random-256-bit-key"
    "agent-internal-secret"
    "agent-internal-shared-secret"
    "dev-oauth-seed-change-me"
)

# 64 hex chars (256 bits). openssl first, Python's `secrets` as the fallback.
generate_secret() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 32
    elif command -v python3 >/dev/null 2>&1; then
        python3 -c 'import secrets; print(secrets.token_hex(32))'
    elif command -v python >/dev/null 2>&1; then
        python -c 'import secrets; print(secrets.token_hex(32))'
    else
        return 1
    fi
}

_is_placeholder_secret() {
    local value="$1" p
    for p in "${SECRET_PLACEHOLDERS[@]}"; do
        [ "$value" = "$p" ] && return 0
    done
    return 1
}

# ensure_local_secrets <env-file>
# Prints the names of the variables it generated, one per line.
ensure_local_secrets() {
    local file="$1" var line value new tmp
    for var in "${SECRET_VARS[@]}"; do
        line="$(grep -E "^${var}=" "$file" | tail -n 1 || true)"
        if [ -n "$line" ]; then
            value="${line#*=}"
            value="${value%$'\r'}"
            value="${value#"${value%%[![:space:]]*}"}"
            value="${value%"${value##*[![:space:]]}"}"
            value="${value#\"}"; value="${value%\"}"
            value="${value#\'}"; value="${value%\'}"
            _is_placeholder_secret "$value" || continue
        fi
        new="$(generate_secret)" || { echo "cannot generate ${var}: need openssl or python3" >&2; return 1; }
        if [ -n "$line" ]; then
            tmp="$(mktemp)"
            # Replace every assignment of the variable; hex is safe in awk.
            awk -v var="$var" -v new="$new" 'index($0, var "=") == 1 { print var "=" new; next } { print }' "$file" > "$tmp"
            cat "$tmp" > "$file"
            rm -f "$tmp"
        else
            if [ -s "$file" ] && [ -n "$(tail -c1 "$file")" ]; then echo >> "$file"; fi
            echo "${var}=${new}" >> "$file"
        fi
        echo "$var"
    done
}
