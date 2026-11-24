#!/usr/bin/env bash
# ============================================================
# E-Commerce Agents — Development Environment Setup
# Usage:
#   ./scripts/dev.sh              Full rebuild and start everything (Python backend)
#   ./scripts/dev.sh --dotnet     Same, but targets the .NET backend instead
#   ./scripts/dev.sh --clean      Nuke volumes, rebuild from scratch
#   ./scripts/dev.sh --seed-only  Re-run seeder against existing DB
#   ./scripts/dev.sh --infra-only Start db + redis + aspire only
#   ./scripts/dev.sh --no-key     Replay recorded model responses: no API key needed
#                                 (combines with --demo; from source it layers
#                                 docker-compose.nokey.yml on docker-compose.yml)
#   ./scripts/dev.sh --profile NAME
#                                 Apply a run profile (agents/python/config/profiles/NAME.env): chat,
#                                 embeddings and model for one provider, no secrets. See docs/run-profiles.md.
#                                 Precedence: your shell > the profile > .env.
#
# --dotnet composes with docker-compose.dotnet.yml instead of the default
# docker-compose.yml, and adds the "mcp" profile (the .NET MCP inventory
# host). Every other flag combines freely with it, e.g. --clean --dotnet.
# ============================================================

set -euo pipefail

# ── Colors ───────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ── Helpers ──────────────────────────────────────────────────

info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; }
step()    { echo -e "\n${BOLD}${CYAN}── $* ──${NC}\n"; }

wait_for_health() {
    local name="$1"
    local check_cmd="$2"
    local max_retries="${3:-30}"
    local retry=0

    info "Waiting for ${name}..."
    while [ $retry -lt $max_retries ]; do
        if eval "$check_cmd" > /dev/null 2>&1; then
            success "${name} is ready"
            return 0
        fi
        retry=$((retry + 1))
        sleep 1
    done
    error "${name} failed to become ready after ${max_retries}s"
    return 1
}

wait_for_http() {
    local name="$1"
    local url="$2"
    local max_retries="${3:-60}"
    local retry=0

    info "Waiting for ${name} at ${url}..."
    while [ $retry -lt $max_retries ]; do
        if curl -sf "$url" > /dev/null 2>&1; then
            success "${name} is ready"
            return 0
        fi
        retry=$((retry + 1))
        sleep 1
    done
    error "${name} failed to respond at ${url} after ${max_retries}s"
    return 1
}

print_summary() {
    echo ""
    echo -e "${BOLD}${CYAN}============================================================${NC}"
    echo -e "${BOLD}${CYAN}  E-Commerce Agents — Services Running${NC}"
    echo -e "${BOLD}${CYAN}============================================================${NC}"
    echo ""
    echo -e "  ${BOLD}Infrastructure${NC}"
    echo -e "    PostgreSQL        http://localhost:5432"
    echo -e "    Redis             http://localhost:6379"
    echo -e "    ${GREEN}Aspire Dashboard  http://localhost:18888${NC}"
    echo ""

    if [ "${INFRA_ONLY:-false}" = "false" ] && [ "${SEED_ONLY:-false}" = "false" ]; then
        echo -e "  ${BOLD}Agents${NC}"
        echo -e "    Orchestrator      http://localhost:8080"
        echo -e "    Product Discovery http://localhost:8081"
        echo -e "    Order Management  http://localhost:8082"
        echo -e "    Pricing & Promos  http://localhost:8083"
        echo -e "    Review & Sentim.  http://localhost:8084"
        echo -e "    Inventory & Ful.  http://localhost:8085"
        echo ""
        echo -e "  ${BOLD}Frontend${NC}"
        echo -e "    Next.js           http://localhost:3000"
        echo ""
    fi

    echo -e "${BOLD}${CYAN}============================================================${NC}"
    echo ""
}

# ── Parse Flags ──────────────────────────────────────────────

CLEAN=false
SEED_ONLY=false
INFRA_ONLY=false
DOTNET=false
DEMO=false
SWITCH=false
NO_KEY=false

PROFILE=""
_PROFILE_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/profile.sh"
# shellcheck source=lib/profile.sh
source "$_PROFILE_LIB"

while [ $# -gt 0 ]; do
    arg="$1"
    shift
    case $arg in
        --clean)      CLEAN=true ;;
        --seed-only)  SEED_ONLY=true ;;
        --infra-only) INFRA_ONLY=true ;;
        --dotnet)     DOTNET=true ;;
        --demo)       DEMO=true ;;
        --switch)     SWITCH=true ;;
        --no-key)     NO_KEY=true ;;
        --profile)
            if [ $# -eq 0 ]; then
                error "--profile needs a name. Valid profiles: $(list_profiles | tr '\n' ' ')"
                exit 1
            fi
            PROFILE="$1"
            shift
            ;;
        --profile=*)  PROFILE="${arg#--profile=}" ;;
        --help|-h)
            echo "Usage: ./scripts/dev.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --demo        Pull prebuilt images from GHCR instead of building"
            echo "  --clean       Remove volumes and rebuild from scratch"
            echo "  --seed-only   Re-run seeder against existing DB"
            echo "  --infra-only  Start db + redis + aspire only"
            echo "  --dotnet      Target the .NET backend instead of Python"
            echo "  --switch      Tear the other stack down first, volumes and all"
            echo "  --no-key      Play back recorded model responses; needs no API key (Python only)"
            echo "  --profile N   Apply run profile N (chat + embeddings + model for one provider)"
            echo "  --help        Show this help"
            echo ""
            echo "  Profiles: $(list_profiles | tr '\n' ' ')"
            echo "  A profile holds no secrets (keys stay in .env). Your exported variables beat the profile,"
            echo "  and the profile beats .env. Table and commands: docs/run-profiles.md"
            echo ""
            echo "  --demo is the fast path: no local build, one command, about a minute on a first run (rough)."
            echo "  Override the image tag with IMAGE_TAG (default: latest):"
            echo "    IMAGE_TAG=main ./scripts/dev.sh --demo"
            echo ""
            echo "  --no-key layers docker-compose.nokey.yml, which hardcodes LLM_PROVIDER=replay and the"
            echo "  tour settings. Only the recorded demo prompts get a model answer:"
            echo "    ./scripts/dev.sh --demo --no-key"
            echo ""
            echo "  Only one stack runs at a time — they publish the same ports."
            echo "  --switch is the clean way to change: ./scripts/dev.sh --dotnet --switch"
            exit 0
            ;;
        *)
            error "Unknown option: $arg"
            exit 1
            ;;
    esac
done

# A profile is validated before anything else happens (no Docker, no .env needed to be told it is wrong).
if [ -n "$PROFILE" ]; then
    if ! profile_valid "$PROFILE"; then
        error "$(profile_refuse "$PROFILE" 2>&1)"
        exit 1
    fi
    # keyless IS the no-key overlay: same thing, one name.
    if [ "$PROFILE" = "keyless" ]; then
        NO_KEY=true
    elif [ "$NO_KEY" = true ]; then
        error "--no-key replays recorded responses and cannot be combined with --profile $PROFILE (use --profile keyless)"
        exit 1
    fi
fi

# --demo pulls release images; --dotnet targets a stack built from source. There
# is no .NET demo image set, so the combination has no meaning.
if [ "$DEMO" = true ] && [ "$DOTNET" = true ]; then
    error "--demo and --dotnet cannot be combined (no prebuilt .NET images are published)"
    exit 1
fi

# The overlay replaces the Python stack's model with recorded responses. The
# .NET compose file has no equivalent overlay.
if [ "$NO_KEY" = true ] && [ "$DOTNET" = true ]; then
    error "--no-key and --dotnet cannot be combined (the replay overlay is Python-only)"
    exit 1
fi

# ── Stack selection ──────────────────────────────────────────
# Both compose files gate agents/seeder/frontend behind profiles; only
# infra (db, redis, aspire) is unconditional. Everything below uses these
# arrays instead of hardcoding "docker compose"/profile flags, so the rest
# of the script is stack-agnostic.
#   APP_PROFILES — down/build: every profile-gated service (incl. seed)
#   RUN_PROFILES — final `up -d`: agents + frontend only (seed already ran
#                  as its own one-shot `run --rm` step, so it's excluded
#                  here to avoid re-seeding)

if [ "$DOTNET" = true ]; then
    COMPOSE=(docker compose -f docker-compose.dotnet.yml)
    APP_PROFILES=(--profile seed --profile agents --profile mcp --profile frontend)
    RUN_PROFILES=(--profile agents --profile mcp --profile frontend)
    PGDATA_VOLUME_PATTERN='pgdata-dotnet$'
    OTHER_STACK="e-commerce-agents"
    OTHER_STACK_LABEL="Python"
    OTHER_STACK_FILE="docker-compose.yml"
else
    COMPOSE=(docker compose)
    # Explicit -f for the base file too: naming any -f turns off compose's
    # default file discovery (and its docker-compose.override.yml).
    [ "$NO_KEY" = true ] && COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.nokey.yml)
    APP_PROFILES=(--profile seed --profile agents --profile frontend)
    RUN_PROFILES=(--profile agents --profile frontend)
    PGDATA_VOLUME_PATTERN='_pgdata$'
    OTHER_STACK="e-commerce-agents-dotnet"
    OTHER_STACK_LABEL=".NET"
    OTHER_STACK_FILE="docker-compose.dotnet.yml"
fi

# ── Navigate to project root ─────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# ── Check prerequisites ──────────────────────────────────────

if ! command -v docker &> /dev/null; then
    error "Docker is not installed or not in PATH"
    exit 1
fi

if ! docker compose version &> /dev/null; then
    error "Docker Compose v2 is required"
    exit 1
fi

# ── One stack at a time ──────────────────────────────────────
#
# The Python and .NET stacks publish the same host ports (3000, 5432, 6379,
# 8080-8085, 8090, 18888), so they cannot run simultaneously. That is a
# deliberate choice, not a limitation to work around: running both would need a
# second published port set, and every host-published port here is already
# claimed by whichever stack came up first.
#
# What is worth fixing is the failure mode. Without this check, starting the
# second stack fails partway through with a raw Docker port-binding error, after
# some containers have already started — leaving a half-up mess that neither
# stack's `down` fully cleans.
#
# Comparing compose project labels rather than probing ports, because a port
# being busy says nothing about *what* is holding it.

other_running=$(docker ps --filter "label=com.docker.compose.project=${OTHER_STACK}" --format '{{.Names}}' 2>/dev/null | wc -l | tr -d ' ')

if [ "$other_running" -gt 0 ]; then
    if [ "$SWITCH" = true ]; then
        step "Switching stacks — tearing down the ${OTHER_STACK_LABEL} stack"
        docker compose -f "$OTHER_STACK_FILE" --profile seed --profile agents --profile mcp --profile frontend down -v --remove-orphans 2>/dev/null || true
        success "${OTHER_STACK_LABEL} stack removed, volumes included"
    else
        echo ""
        error "The ${OTHER_STACK_LABEL} stack is already running (${other_running} containers)."
        echo ""
        echo "  Both stacks publish the same ports, so only one runs at a time."
        echo ""
        echo "  Switch cleanly — brings the other down, volumes and all:"
        echo ""
        echo "    $0 $* --switch"
        echo ""
        echo "  Or do it by hand:"
        echo ""
        echo "    docker compose -f ${OTHER_STACK_FILE} --profile agents --profile frontend down -v"
        echo ""
        exit 1
    fi
fi

# ── Check for .env file ──────────────────────────────────────

if [ ! -f .env ]; then
    warn ".env file not found. Copying from .env.example..."
    if [ -f .env.example ]; then
        cp .env.example .env
        warn "Created .env from .env.example — edit it with your API keys"
    else
        error "No .env.example found either. Create a .env file first."
        exit 1
    fi
fi

# A fresh .env carries the public placeholders from .env.example. Replace those
# (and only those) with random values; secrets you already set are left alone.
# shellcheck source=lib/env-secrets.sh
source "$SCRIPT_DIR/lib/env-secrets.sh"
GENERATED_SECRETS="$(ensure_local_secrets .env)"
if [ -n "$GENERATED_SECRETS" ]; then
    info "Generated local secrets in .env: $(echo "$GENERATED_SECRETS" | tr '\n' ' ')"
    info "Tokens issued before this run are no longer valid; sign in again."
fi

# ── Run profile ──────────────────────────────────────────────
# Exports the profile's variables the shell has not set, so compose interpolation sees them ahead of .env.
# localhost URLs become host.docker.internal for the containers (docker-compose*.yml map it to the host).

if [ -n "$PROFILE" ]; then
    if ! apply_profile "$PROFILE" containers; then
        error "Profile $PROFILE could not be applied."
        exit 1
    fi
    info "Profile ${PROFILE}: $(profile_meta "$PROFILE" summary)"
    if [ "${#PROFILE_KEPT[@]}" -gt 0 ]; then
        info "Kept from your shell (not overridden): ${PROFILE_KEPT[*]}"
    fi
    if [ "${#PROFILE_OVERRIDES[@]}" -gt 0 ]; then
        warn "The profile overrides .env for: $(printf '%s; ' "${PROFILE_OVERRIDES[@]}")"
        warn "Export the variable in your shell to keep your own value."
    fi
    info "Verified how: $(profile_meta "$PROFILE" verified)"
    info "Key needed: $(profile_meta "$PROFILE" key)"
fi

# ── LLM key preflight ────────────────────────────────────────
# With LLM_PROVIDER=openai or azure and no key, the stack starts and then every
# answer is an apology. Stop before building or touching volumes, and say how to
# go on. (--infra-only and --seed-only never call a model.) scripts/dev.ps1
# mirrors this.

# env_value NAME — the shell's value if set, otherwise the last NAME= line of .env.
env_value() {
    local name="$1" value
    value="${!name:-}"
    if [ -z "$value" ]; then
        value="$(grep -E "^${name}=" .env | tail -n 1 | cut -d= -f2- || true)"
    fi
    value="${value%$'\r'}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    value="${value#\"}"; value="${value%\"}"
    value="${value#\'}"; value="${value%\'}"
    printf '%s' "$value"
}

# A key that is unset, empty or still the .env.example placeholder is no key.
has_llm_key() {
    local value="$1"
    [ -n "$value" ] && [ "$value" != "sk-your-openai-api-key-here" ]
}

if [ "$NO_KEY" = true ]; then
    # docker-compose.nokey.yml hardcodes LLM_PROVIDER=replay, so a key (or the
    # lack of one) in .env or the shell is irrelevant here.
    info "No-key mode: recorded model responses, LLM_PROVIDER=replay (key preflight skipped)"
elif [ "$INFRA_ONLY" != true ] && [ "$SEED_ONLY" != true ]; then
    LLM_PROVIDER_VALUE="$(env_value LLM_PROVIDER | tr '[:upper:]' '[:lower:]')"
    LLM_PROVIDER_VALUE="${LLM_PROVIDER_VALUE:-openai}"
    LLM_KEY_MISSING=false
    case "$LLM_PROVIDER_VALUE" in
        openai)
            has_llm_key "$(env_value OPENAI_API_KEY)" || LLM_KEY_MISSING=true
            ;;
        azure)
            # AZURE_OPENAI_AUTH=identity sends an Entra token and no key.
            if [ "$(env_value AZURE_OPENAI_AUTH | tr '[:upper:]' '[:lower:]')" != "identity" ]; then
                if ! has_llm_key "$(env_value AZURE_OPENAI_KEY)" && ! has_llm_key "$(env_value AZURE_OPENAI_API_KEY)"; then
                    LLM_KEY_MISSING=true
                fi
            fi
            ;;
        anthropic)
            has_llm_key "$(env_value ANTHROPIC_API_KEY)" || LLM_KEY_MISSING=true
            ;;
    esac
    if [ "$LLM_KEY_MISSING" = true ]; then
        error "LLM_PROVIDER=${LLM_PROVIDER_VALUE} but no API key is set, so every answer would fail."
        echo ""
        echo "  Two ways forward:"
        echo "    1. Add your key to .env (OPENAI_API_KEY, AZURE_OPENAI_KEY for LLM_PROVIDER=azure, or"
        echo "       ANTHROPIC_API_KEY for LLM_PROVIDER=anthropic),"
        echo "       then run this again."
        echo "    2. Run with --no-key (-NoKey on PowerShell) to play back recorded model responses."
        echo "       Only the recorded demo prompts answer; anything else says it was not recorded."
        echo ""
        exit 1
    fi
fi

# ── Embedding dimension ──────────────────────────────────────
# A profile may change the embedding width (a local nomic-embed-text is 768, the baseline column is 1536).
# scripts/reset_embeddings.py --if-needed is a no-op when the columns already match; otherwise it drops the stored
# vectors (they could not be compared across widths anyway). Only runs under --profile: without one the width is
# whatever you configured, as before.
reconcile_embedding_dim() {
    [ -n "$PROFILE" ] || return 0
    local dim="${EMBEDDING_DIM:-}"
    [ -n "$dim" ] || return 0
    info "Checking the embedding column width against EMBEDDING_DIM=${dim}"
    if ! "${COMPOSE[@]}" --profile seed run --rm seeder uv run --no-project python -m scripts.reset_embeddings --if-needed --dim "$dim"; then
        error "Could not set the embedding dimension to ${dim} (see scripts/reset_embeddings.py)."
        exit 1
    fi
}

# ── Clean (if requested) ─────────────────────────────────────

if [ "$CLEAN" = true ]; then
    step "Cleaning up (removing containers, volumes, orphans)"
    "${COMPOSE[@]}" "${APP_PROFILES[@]}" down -v --remove-orphans
    # Aspire has no persistent volume — removing the container clears all in-memory
    # telemetry (traces, structured logs, metrics). Explicitly remove it to be certain.
    "${COMPOSE[@]}" rm -f aspire 2>/dev/null || true
    success "Clean complete — containers, volumes, and Aspire telemetry data cleared"
fi

# ── Demo mode ─────────────────────────────────────────────────
#
# Deliberately a self-contained fast path with its own exit, rather than a set
# of conditionals threaded through the build/seed/up flow below. Almost nothing
# in that flow applies here: there is no build step, docker-compose.demo.yml
# carries no agents/frontend/seed profiles, and its `depends_on:
# service_completed_successfully` means compose sequences the seeder itself.
# Keeping it separate means --demo cannot destabilise the from-source path.

if [ "$DEMO" = true ]; then
    DEMO_COMPOSE=(docker compose -f docker-compose.demo.yml)
    [ "$NO_KEY" = true ] && DEMO_COMPOSE=(docker compose -f docker-compose.demo.yml -f docker-compose.nokey.yml)
    # The auth-server sits behind the `oauth` profile; it is only wanted under AUTH_MODE=oauth
    # (exported, or set in .env). Without it the default demo is 11 running containers (#86).
    _demo_auth_mode="${AUTH_MODE:-}"
    if [ -z "$_demo_auth_mode" ] && [ -f .env ]; then
        _demo_auth_mode="$(grep -E '^AUTH_MODE=' .env | tail -n1 | cut -d= -f2- | tr -d '"'"'"' \r')"
    fi
    [ "$_demo_auth_mode" = oauth ] && DEMO_COMPOSE+=(--profile oauth)
    TAG="${IMAGE_TAG:-latest}"

    step "Demo mode — pulling prebuilt images (tag: ${TAG})"
    info "No local build. Images come from ghcr.io/nitin27may/e-commerce-agents"

    if ! IMAGE_TAG="$TAG" "${DEMO_COMPOSE[@]}" pull; then
        error "Could not pull one or more images."
        echo ""
        echo "  Most likely causes:"
        echo "    - The tag does not exist yet. Check https://github.com/nitin27may/e-commerce-agents/pkgs/container/e-commerce-agents%2Forchestrator"
        echo "    - A package is still private. Anonymous pulls fail with an auth error"
        echo "      that reads like a network problem — see docs/releasing.md."
        echo ""
        echo "  To build from source instead, drop the --demo flag:"
        echo "    ./scripts/dev.sh"
        exit 1
    fi
    success "Images pulled"

    # --clean above tears down the from-source project; the demo stack is a
    # different compose project, so clear it too. Matters most for --no-key:
    # re-seeding an already-seeded database makes some replay fixtures miss.
    if [ "$CLEAN" = true ]; then
        step "Cleaning the demo stack (volumes included)"
        IMAGE_TAG="$TAG" "${DEMO_COMPOSE[@]}" down -v --remove-orphans
    fi

    step "Starting the stack"
    IMAGE_TAG="$TAG" "${DEMO_COMPOSE[@]}" up -d

    wait_for_http "Orchestrator" "http://localhost:8080/health" 120
    wait_for_http "Frontend" "http://localhost:3000" 120

    print_summary
    echo -e "  Sign in as ${BOLD}alice.johnson@gmail.com${NC} / ${BOLD}customer123${NC}"
    if [ "$NO_KEY" = true ]; then
        echo -e "  No key: model answers are recorded, so use the suggested prompts in the chat. The tour"
        echo -e "  account is ${BOLD}tour@example.com${NC} / ${BOLD}tour123${NC} (docs/demo-guide.md, No-key mode)."
    fi
    echo ""
    exit 0
fi

# ── Seed Only ─────────────────────────────────────────────────

if [ "$SEED_ONLY" = true ]; then
    step "Running seeder"

    # Ensure infra is running
    "${COMPOSE[@]}" up -d db redis aspire
    wait_for_health "PostgreSQL" "${COMPOSE[*]} exec db pg_isready -h 127.0.0.1 -U ecommerce -d ecommerce_agents" 60
    wait_for_health "Redis" "${COMPOSE[*]} exec redis redis-cli ping"

    # Verify DB credentials work (catches stale volumes)
    if ! "${COMPOSE[@]}" exec -T db sh -c 'PGPASSWORD=ecommerce_secret psql -h 127.0.0.1 -U ecommerce -d ecommerce_agents -c "SELECT 1"' > /dev/null 2>&1; then
        warn "Database auth failed — stale Docker volume. Reinitializing..."
        "${COMPOSE[@]}" stop db
        "${COMPOSE[@]}" rm -f db
        docker volume ls -q | grep "$PGDATA_VOLUME_PATTERN" | xargs docker volume rm 2>/dev/null || true
        "${COMPOSE[@]}" up -d db
        wait_for_health "PostgreSQL" "${COMPOSE[*]} exec db pg_isready -h 127.0.0.1 -U ecommerce -d ecommerce_agents" 60
        success "Database reinitialized with correct credentials"
    fi

    reconcile_embedding_dim
    "${COMPOSE[@]}" --profile seed run --rm seeder
    success "Seeder complete"
    exit 0
fi

# ── Stop existing ─────────────────────────────────────────────

step "Stopping existing containers"
"${COMPOSE[@]}" "${APP_PROFILES[@]}" down --remove-orphans 2>/dev/null || true

# ── Build ─────────────────────────────────────────────────────

step "Building images"
if [ "$CLEAN" = true ]; then
    "${COMPOSE[@]}" "${APP_PROFILES[@]}" build --no-cache
else
    "${COMPOSE[@]}" "${APP_PROFILES[@]}" build
fi

# ── Start Infrastructure ──────────────────────────────────────

step "Starting infrastructure (db, redis, aspire)"
"${COMPOSE[@]}" up -d db redis aspire

wait_for_health "PostgreSQL" "${COMPOSE[*]} exec db pg_isready -h 127.0.0.1 -U ecommerce -d ecommerce_agents" 60
wait_for_health "Redis" "${COMPOSE[*]} exec redis redis-cli ping"

# Verify DB credentials work (catches stale volumes with old passwords)
if ! "${COMPOSE[@]}" exec -T db sh -c 'PGPASSWORD=ecommerce_secret psql -h 127.0.0.1 -U ecommerce -d ecommerce_agents -c "SELECT 1"' > /dev/null 2>&1; then
    warn "Database auth failed — stale Docker volume detected. Reinitializing..."
    "${COMPOSE[@]}" stop db
    "${COMPOSE[@]}" rm -f db
    docker volume ls -q | grep "$PGDATA_VOLUME_PATTERN" | xargs docker volume rm 2>/dev/null || true
    "${COMPOSE[@]}" up -d db
    wait_for_health "PostgreSQL" "${COMPOSE[*]} exec db pg_isready -h 127.0.0.1 -U ecommerce -d ecommerce_agents" 60
    success "Database reinitialized with correct credentials"
fi

# Bring the SCHEMA up to date, not just the credentials.
#
# A volume can authenticate perfectly and still carry a schema from before the
# last change; nothing errors, search just quietly returns nothing. Schema
# changes are numbered migrations now (docker/postgres/migrations, applied by
# scripts/migrate.py on top of the frozen init.sql baseline), so an old volume
# is upgraded in place, keeping its data. The seeder runs the same step again
# (idempotent) before it seeds.
step "Applying database migrations"
if ! "${COMPOSE[@]}" --profile seed run --rm seeder uv run --no-project python -m scripts.migrate; then
    echo ""
    warn "Schema migration failed — see the output above."
    echo ""
    echo "  If the volume is disposable, recreate it (loses local data, reseeds):"
    echo "      ./scripts/dev.sh --clean"
    echo ""
    exit 1
fi

success "Infrastructure is ready"

# ── Run Seeder ────────────────────────────────────────────────

step "Running database seeder"
reconcile_embedding_dim
"${COMPOSE[@]}" --profile seed run --rm seeder
success "Database seeded"

# ── Infra Only ────────────────────────────────────────────────

if [ "$INFRA_ONLY" = true ]; then
    INFRA_ONLY=true print_summary
    success "Infrastructure-only mode — agents not started"
    exit 0
fi

# ── Start Agents ──────────────────────────────────────────────

step "Starting agents and frontend"
"${COMPOSE[@]}" "${RUN_PROFILES[@]}" up -d

wait_for_http "Orchestrator"      "http://localhost:8080/ready"
wait_for_http "Product Discovery" "http://localhost:8081/ready"
wait_for_http "Order Management"  "http://localhost:8082/ready"
wait_for_http "Pricing & Promos"  "http://localhost:8083/ready"
wait_for_http "Review & Sentim."  "http://localhost:8084/ready"
wait_for_http "Inventory & Ful."  "http://localhost:8085/ready"

success "All agents are running"

wait_for_http "Frontend" "http://localhost:3000" 90

success "Frontend is running"

# ── Summary ───────────────────────────────────────────────────

print_summary
success "E-Commerce Agents is ready!"
