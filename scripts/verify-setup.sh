#!/usr/bin/env bash
# ============================================================
# verify-setup.sh — sanity-check a dev environment for MAF v1 tutorials
# Usage: ./scripts/verify-setup.sh
# ============================================================
# Checks:
#   - uv installed (Python package manager)
#   - Python 3.12+
#   - .NET 9 SDK
#   - Docker + docker compose
#   - .env file present (or .env.example noted)
#   - Required LLM env var set for active LLM_PROVIDER
#
# Exits 0 on success, non-zero on first failure.

set -o pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
RESET='\033[0m'

check_count=0
fail_count=0

check() {
    local label="$1"
    local cmd="$2"
    check_count=$((check_count + 1))
    if eval "$cmd" >/dev/null 2>&1; then
        printf "  ${GREEN}✓${RESET} %s\n" "$label"
    else
        printf "  ${RED}✗${RESET} %s\n" "$label"
        fail_count=$((fail_count + 1))
    fi
}

warn() {
    printf "  ${YELLOW}!${RESET} %s\n" "$1"
}

info() {
    printf "  ${GREEN}i${RESET} %s\n" "$1"
}

section() {
    printf "\n${GREEN}%s${RESET}\n" "$1"
}

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 2

section "Tooling"
check "uv (Python package manager)" "command -v uv"
check "Python 3.12+" "python3 --version | grep -E 'Python 3\.(12|13|14)'"
check ".NET SDK 9+" "dotnet --list-sdks | grep -E '^(9|10)\.'"
check "Docker" "command -v docker"
check "Docker Compose v2" "docker compose version"
check "Node 20+ (for Next.js frontend)" "node --version | grep -E 'v(20|21|22|23|24)'"
check "pnpm" "command -v pnpm"

section "Repository state"
if [[ -f "$REPO_ROOT/.env" ]]; then
    info ".env present"
    # shellcheck disable=SC1091
    set -a
    . "$REPO_ROOT/.env"
    set +a
elif [[ -f "$REPO_ROOT/.env.example" ]]; then
    warn ".env not found — copy .env.example → .env and fill in secrets"
    fail_count=$((fail_count + 1))
else
    printf "  ${RED}✗${RESET} neither .env nor .env.example found\n"
    fail_count=$((fail_count + 1))
fi

section "LLM provider configuration"
LLM_PROVIDER="${LLM_PROVIDER:-openai}"
echo "  LLM_PROVIDER=$LLM_PROVIDER"
case "$LLM_PROVIDER" in
    openai)
        if [[ -n "${OPENAI_API_KEY:-}" && "$OPENAI_API_KEY" != "sk-your-openai-api-key-here" ]]; then
            info "OPENAI_API_KEY set"
        else
            printf "  ${RED}✗${RESET} OPENAI_API_KEY is empty or placeholder\n"
            fail_count=$((fail_count + 1))
        fi
        ;;
    azure)
        check "AZURE_OPENAI_ENDPOINT set" "[[ -n \"\${AZURE_OPENAI_ENDPOINT:-}\" ]]"
        # Accept either AZURE_OPENAI_KEY (repo convention) or AZURE_OPENAI_API_KEY (MAF convention)
        if [[ -n "${AZURE_OPENAI_KEY:-}" || -n "${AZURE_OPENAI_API_KEY:-}" ]]; then
            info "Azure key set"
        else
            printf "  ${RED}✗${RESET} AZURE_OPENAI_KEY (or AZURE_OPENAI_API_KEY) is empty\n"
            fail_count=$((fail_count + 1))
        fi
        check "AZURE_OPENAI_DEPLOYMENT set" "[[ -n \"\${AZURE_OPENAI_DEPLOYMENT:-}\${AZURE_OPENAI_DEPLOYMENT_NAME:-}\" ]]"
        ;;
    *)
        printf "  ${RED}✗${RESET} unknown LLM_PROVIDER: %s (expected 'openai' or 'azure')\n" "$LLM_PROVIDER"
        fail_count=$((fail_count + 1))
        ;;
esac

section "Workspace structure"
check "tutorials/ present" "[[ -d tutorials ]]"
check "agents/dotnet/ solution present" "[[ -f agents/dotnet/ECommerceAgents.sln ]]"
check "docker-compose.yml present" "[[ -f docker-compose.yml ]]"
check "docker-compose.dotnet.yml present" "[[ -f docker-compose.dotnet.yml ]]"
check "agents/python/ backend present" "[[ -d agents/python ]]"
check "web/ Next.js frontend present" "[[ -d web ]]"

section "Quick-build smoke"
if command -v dotnet >/dev/null 2>&1; then
    if (cd agents/dotnet && dotnet build --nologo --verbosity quiet 2>&1 | grep -q "Build succeeded"); then
        info ".NET solution builds green"
    else
        warn ".NET solution build failed (run 'cd agents/dotnet && dotnet build' for details)"
        fail_count=$((fail_count + 1))
    fi
else
    warn ".NET SDK missing — skipping build smoke"
fi

section "Summary"
passed=$((check_count - fail_count))
if [[ $fail_count -eq 0 ]]; then
    printf "  ${GREEN}All %d checks passed.${RESET}\n" "$check_count"
    printf "  You're ready to run tutorials — start with ${GREEN}tutorials/01-first-agent/${RESET}.\n"
    exit 0
else
    printf "  ${RED}%d of %d checks failed.${RESET}\n" "$fail_count" "$check_count"
    printf "  Fix the items marked ${RED}✗${RESET} above and re-run this script.\n"
    exit 1
fi
