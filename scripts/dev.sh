#!/usr/bin/env bash
# ============================================================
# E-Commerce Agents — Development Environment Setup
# Usage:
#   ./scripts/dev.sh              Full rebuild and start everything (Python backend)
#   ./scripts/dev.sh --dotnet     Same, but targets the .NET backend instead
#   ./scripts/dev.sh --clean      Nuke volumes, rebuild from scratch
#   ./scripts/dev.sh --seed-only  Re-run seeder against existing DB
#   ./scripts/dev.sh --infra-only Start db + redis + aspire only
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

for arg in "$@"; do
    case $arg in
        --clean)      CLEAN=true ;;
        --seed-only)  SEED_ONLY=true ;;
        --infra-only) INFRA_ONLY=true ;;
        --dotnet)     DOTNET=true ;;
        --demo)       DEMO=true ;;
        --switch)     SWITCH=true ;;
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
            echo "  --help        Show this help"
            echo ""
            echo "  --demo is the fast path: no local build, one command, ~2 minutes."
            echo "  Override the image tag with IMAGE_TAG (default: latest):"
            echo "    IMAGE_TAG=main ./scripts/dev.sh --demo"
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

# --demo pulls release images; --dotnet targets a stack built from source. There
# is no .NET demo image set, so the combination has no meaning.
if [ "$DEMO" = true ] && [ "$DOTNET" = true ]; then
    error "--demo and --dotnet cannot be combined (no prebuilt .NET images are published)"
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
    THIS_STACK="e-commerce-agents-dotnet"
    OTHER_STACK="e-commerce-agents"
    OTHER_STACK_LABEL="Python"
    OTHER_STACK_FILE="docker-compose.yml"
else
    COMPOSE=(docker compose)
    APP_PROFILES=(--profile seed --profile agents --profile frontend)
    RUN_PROFILES=(--profile agents --profile frontend)
    PGDATA_VOLUME_PATTERN='_pgdata$'
    THIS_STACK="e-commerce-agents"
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

    step "Starting the stack"
    IMAGE_TAG="$TAG" "${DEMO_COMPOSE[@]}" up -d

    wait_for_http "Orchestrator" "http://localhost:8080/health" 120
    wait_for_http "Frontend" "http://localhost:3000" 120

    print_summary
    echo -e "  Sign in as ${BOLD}alice.johnson@gmail.com${NC} / ${BOLD}customer123${NC}"
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

# Verify the SCHEMA matches, not just the credentials.
#
# A volume can authenticate perfectly and still carry a schema from before the
# last init.sql change. That is the more common way to land here — pulling new
# commits and restarting the same stack, rather than switching stacks — and it
# is far nastier than an auth failure, because nothing errors. Search just
# quietly returns nothing and the agent says "I couldn't find any", which reads
# like an empty catalogue rather than a broken index.
#
# products.search_vector is the canary: it arrived with the v1.2.0 full-text
# search work, so any volume predating that lacks it. Hardcoded on purpose —
# deriving the check from init.sql would be clever and would drift.
if ! "${COMPOSE[@]}" exec -T db sh -c 'PGPASSWORD=ecommerce_secret psql -h 127.0.0.1 -U ecommerce -d ecommerce_agents -tAc "SELECT 1 FROM information_schema.columns WHERE table_name = '"'"'products'"'"' AND column_name = '"'"'search_vector'"'"'"' 2>/dev/null | grep -q 1; then
    echo ""
    warn "Database schema is out of date — products.search_vector is missing."
    echo ""
    echo "  Your Postgres volume predates the full-text search change. The stack will"
    echo "  start and look healthy, and every product search will silently return"
    echo "  nothing. Refusing to start into that."
    echo ""
    echo "  Two ways forward:"
    echo ""
    echo "    Recreate the volume (loses local data, reseeds from scratch):"
    echo "      ./scripts/dev.sh --clean"
    echo ""
    echo "    Or apply the schema in place, keeping your data:"
    echo "      docker compose exec -T db psql -U ecommerce -d ecommerce_agents < docker/postgres/init.sql"
    echo ""
    exit 1
fi

success "Infrastructure is ready"

# ── Run Seeder ────────────────────────────────────────────────

step "Running database seeder"
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

wait_for_http "Orchestrator"      "http://localhost:8080/health"
wait_for_http "Product Discovery" "http://localhost:8081/health"
wait_for_http "Order Management"  "http://localhost:8082/health"
wait_for_http "Pricing & Promos"  "http://localhost:8083/health"
wait_for_http "Review & Sentim."  "http://localhost:8084/health"
wait_for_http "Inventory & Ful."  "http://localhost:8085/health"

success "All agents are running"

wait_for_http "Frontend" "http://localhost:3000" 90

success "Frontend is running"

# ── Summary ───────────────────────────────────────────────────

print_summary
success "E-Commerce Agents is ready!"
