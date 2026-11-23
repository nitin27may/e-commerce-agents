#!/usr/bin/env bash
# Run profiles for the shell scripts. Sourced, not executed.
#
# A profile is agents/python/config/profiles/<name>.env: KEY=VALUE overlays that set chat, embeddings and the
# model together and never a secret. Precedence, high to low:
#
#     exported in the shell  >  profile  >  .env  >  defaults
#
# so a variable you exported still wins, a secret in .env is untouched (a profile holds none), and a profile
# beats the provider values a copied .env.example leaves behind. A value of <set-me> means "supply this": the
# profile leaves the shell and .env alone for it and the run is refused when neither has a value.
#
# Mirrors agents/python/shared/profiles.py (the Python tools) and scripts/lib/Profile.ps1 (PowerShell).
# Tested by scripts/test-profiles.sh. Bash 3.2 compatible (macOS): no associative arrays.

PROFILE_PLACEHOLDER="<set-me>"
PROFILE_SECRET_NAME_RE='KEY|SECRET|TOKEN|PASSWORD'

# Set by apply_profile, for the caller to print.
PROFILE_APPLIED=()
PROFILE_KEPT=()
PROFILE_OVERRIDES=()
PROFILE_UNRESOLVED=()

_profile_dir() {
    printf '%s' "${PROFILE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/agents/python/config/profiles}"
}

# list_profiles: one name per line, sorted.
list_profiles() {
    local dir f
    dir="$(_profile_dir)"
    for f in "$dir"/*.env; do
        [ -e "$f" ] || continue
        basename "$f" .env
    done | LC_ALL=C sort
}

# profile_valid NAME: the name matches the strict pattern AND a file exists. The pattern is checked first so a
# name such as ../x never reaches the filesystem.
profile_valid() {
    local name="$1" re='^[a-z0-9]+(-[a-z0-9]+)*$'
    [[ "$name" =~ $re ]] || return 1
    [ -f "$(_profile_dir)/${name}.env" ]
}

# profile_refuse NAME: print the error and the valid names to stderr.
profile_refuse() {
    echo "unknown profile '$1'; valid profiles: $(list_profiles | tr '\n' ' ' | sed 's/ $//; s/ /, /g')" >&2
}

# dotenv_value NAME [FILE]: the last NAME= in the file (default .env), quotes and CR dropped; empty if absent.
dotenv_value() {
    local name="$1" file="${2:-${ENV_FILE:-.env}}" value
    [ -f "$file" ] || return 0
    value="$(grep -E "^${name}=" "$file" | tail -n 1 | cut -d= -f2- || true)"
    value="${value%$'\r'}"
    value="${value#\"}"; value="${value%\"}"
    value="${value#\'}"; value="${value%\'}"
    printf '%s' "$value"
}

# dotenv_has NAME [FILE]: the variable is assigned in the file, even to an empty value.
dotenv_has() {
    local file="${2:-${ENV_FILE:-.env}}"
    [ -f "$file" ] && grep -qE "^${1}=" "$file"
}

_profile_is_secret_name() {
    [[ "$1" =~ $PROFILE_SECRET_NAME_RE ]]
}

# profile_meta NAME FIELD: the header line "# @FIELD: text" (first line only), for display.
profile_meta() {
    sed -n "s/^# @$2: *//p" "$(_profile_dir)/$1.env" | head -n 1
}

# apply_profile NAME [containers]: export the profile's variables that the shell has not set.
# With "containers", a *_BASE_URL on localhost / 127.0.0.1 becomes host.docker.internal (what a container calls
# the host). Returns 1 for an unknown profile or an unresolved <set-me>. Never prints a secret value.
apply_profile() {
    local name="$1" containers="${2:-}" file line key value prev re='^([A-Z][A-Z0-9_]*)=(.*)$'
    PROFILE_APPLIED=(); PROFILE_KEPT=(); PROFILE_OVERRIDES=(); PROFILE_UNRESOLVED=()
    if ! profile_valid "$name"; then
        profile_refuse "$name"
        return 1
    fi
    file="$(_profile_dir)/${name}.env"
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line%$'\r'}"
        [[ "$line" =~ $re ]] || continue
        key="${BASH_REMATCH[1]}"
        value="${BASH_REMATCH[2]}"
        if [ "$value" = "$PROFILE_PLACEHOLDER" ]; then
            if [ -n "${!key+x}" ] || [ -n "$(dotenv_value "$key")" ]; then
                continue
            fi
            PROFILE_UNRESOLVED+=("$key")
            continue
        fi
        if [ -n "${!key+x}" ]; then
            PROFILE_KEPT+=("$key")
            continue
        fi
        if [ "$containers" = "containers" ] && [[ "$key" == *_BASE_URL ]]; then
            value="$(printf '%s' "$value" | sed -E 's#^(https?://)(localhost|127\.0\.0\.1)([:/]|$)#\1host.docker.internal\3#')"
        fi
        if dotenv_has "$key"; then
            prev="$(dotenv_value "$key")"
            if [ "$prev" != "$value" ]; then
                if _profile_is_secret_name "$key"; then
                    PROFILE_OVERRIDES+=("$key")
                else
                    PROFILE_OVERRIDES+=("$key (.env: ${prev:-empty} -> ${value:-empty})")
                fi
            fi
        fi
        export "$key=$value"
        PROFILE_APPLIED+=("$key")
    done < "$file"
    export RUN_PROFILE="$name"
    if [ "${#PROFILE_UNRESOLVED[@]}" -gt 0 ]; then
        echo "profile '$name' needs a value for: ${PROFILE_UNRESOLVED[*]} (set them in .env or export them)" >&2
        return 1
    fi
    return 0
}
