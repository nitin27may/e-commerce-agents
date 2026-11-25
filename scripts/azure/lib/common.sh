#!/usr/bin/env bash
# Shared helpers for scripts/azure/*.sh and scripts/azure-{up,down}.sh. Sourced, not executed.
#
# AZURE_DRY_RUN=1 (or --dry-run on every entry script) turns `run` into "print the command":
# nothing that talks to Azure runs, which is how these scripts are exercised offline.

AZURE_DRY_RUN="${AZURE_DRY_RUN:-0}"

log()  { printf '[azure] %s\n' "$*" >&2; }
warn() { printf '[azure] WARN: %s\n' "$*" >&2; }
die()  { printf '[azure] ERROR: %s\n' "$*" >&2; exit 1; }

# run <cmd...>: execute, or only print under AZURE_DRY_RUN.
run() {
    if [ "$AZURE_DRY_RUN" = "1" ]; then
        printf '[dry-run] %s\n' "$*" >&2
        return 0
    fi
    "$@"
}

# capture <cmd...>: like run, but prints stdout for the caller. Under dry-run prints nothing and succeeds.
capture() {
    if [ "$AZURE_DRY_RUN" = "1" ]; then
        printf '[dry-run] %s\n' "$*" >&2
        return 0
    fi
    "$@"
}

have() { command -v "$1" >/dev/null 2>&1; }

# version_ge <actual> <minimum>: numeric dotted comparison (missing parts are 0).
version_ge() {
    local IFS=. a b i
    read -ra a <<<"$1"
    read -ra b <<<"$2"
    for i in 0 1 2; do
        local x="${a[$i]:-0}" y="${b[$i]:-0}"
        x="${x//[!0-9]/}"; y="${y//[!0-9]/}"
        x="${x:-0}"; y="${y:-0}"
        if [ "$x" -gt "$y" ]; then return 0; fi
        if [ "$x" -lt "$y" ]; then return 1; fi
    done
    return 0
}

# first_version <text>: the first x.y.z in a string.
first_version() { grep -Eo '[0-9]+\.[0-9]+(\.[0-9]+)?' <<<"$1" | head -n 1; }

repo_root() { cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd; }

# Value of an azd environment variable, empty when unset. AZURE_SECRET_STORE_FILE (a dotenv file)
# replaces azd so the same code runs offline.
env_get() {
    local key="$1"
    if [ -n "${AZURE_SECRET_STORE_FILE:-}" ]; then
        [ -f "$AZURE_SECRET_STORE_FILE" ] || return 0
        { grep -E "^${key}=" "$AZURE_SECRET_STORE_FILE" || true; } | tail -n 1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//'
        return 0
    fi
    if [ -n "${!key:-}" ]; then printf '%s' "${!key}"; return 0; fi
    # azd 1.39 prints "ERROR: key not found in environment values" on STDOUT and exits 1 for a missing key, so the output is
    # a value only when the exit code is 0. Reading the text as a value made every unset secret look present, and
    # ensure_azure_secrets generated nothing.
    local out
    if out="$(azd env get-value "$key" 2>/dev/null)"; then printf '%s' "$out"; fi
    return 0
}

env_set() {
    local key="$1" value="$2"
    if [ -n "${AZURE_SECRET_STORE_FILE:-}" ]; then
        touch "$AZURE_SECRET_STORE_FILE"
        local tmp
        tmp="$(mktemp)"
        { grep -vE "^${key}=" "$AZURE_SECRET_STORE_FILE" || true; } >"$tmp"
        printf '%s="%s"\n' "$key" "$value" >>"$tmp"
        mv "$tmp" "$AZURE_SECRET_STORE_FILE"
        return 0
    fi
    if [ "$AZURE_DRY_RUN" = "1" ]; then
        printf '[dry-run] azd env set %s <value>\n' "$key" >&2
        return 0
    fi
    azd env set "$key" "$value" >/dev/null
}
