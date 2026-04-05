#!/usr/bin/env bash
# ============================================================
# E-Commerce Agents — Development Environment Setup
# Usage:
#   ./scripts/dev.sh              Full rebuild and start everything
#   ./scripts/dev.sh --clean      Nuke volumes, rebuild from scratch
#   ./scripts/dev.sh --seed-only  Re-run seeder against existing DB
#   ./scripts/dev.sh --infra-only Start db + redis + aspire only
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

for arg in "$@"; do
    case $arg in
        --clean)      CLEAN=true ;;
        --seed-only)  SEED_ONLY=true ;;
        --infra-only) INFRA_ONLY=true ;;
        --help|-h)
            echo "Usage: ./scripts/dev.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --clean       Remove volumes and rebuild from scratch"
            echo "  --seed-only   Re-run seeder against existing DB"
            echo "  --infra-only  Start db + redis + aspire only"
            echo "  --help        Show this help"
            exit 0
            ;;
        *)
            error "Unknown option: $arg"
            exit 1
            ;;
    esac
done

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
    docker compose --profile seed --profile agents --profile frontend down -v --remove-orphans
    success "Clean complete"
fi

# ── Seed Only ─────────────────────────────────────────────────

if [ "$SEED_ONLY" = true ]; then
    step "Running seeder"

    # Ensure infra is running
    docker compose up -d db redis aspire
    wait_for_health "PostgreSQL" "docker compose exec db pg_isready -U ecommerce"
    wait_for_health "Redis" "docker compose exec redis redis-cli ping"

    docker compose --profile seed run --rm seeder
    success "Seeder complete"
    exit 0
fi

# ── Stop existing ─────────────────────────────────────────────

step "Stopping existing containers"
docker compose --profile seed --profile agents --profile frontend down --remove-orphans 2>/dev/null || true

# ── Build ─────────────────────────────────────────────────────

step "Building images"
if [ "$CLEAN" = true ]; then
    docker compose --profile seed --profile agents build --no-cache
else
    docker compose --profile seed --profile agents build
fi

# ── Start Infrastructure ──────────────────────────────────────

step "Starting infrastructure (db, redis, aspire)"
docker compose up -d db redis aspire

wait_for_health "PostgreSQL" "docker compose exec db pg_isready -U ecommerce"
wait_for_health "Redis" "docker compose exec redis redis-cli ping"
success "Infrastructure is ready"

# ── Run Seeder ────────────────────────────────────────────────

step "Running database seeder"
docker compose --profile seed run --rm seeder
success "Database seeded"

# ── Infra Only ────────────────────────────────────────────────

if [ "$INFRA_ONLY" = true ]; then
    INFRA_ONLY=true print_summary
    success "Infrastructure-only mode — agents not started"
    exit 0
fi

# ── Start Agents ──────────────────────────────────────────────

step "Starting agents"
docker compose --profile agents up -d

wait_for_http "Orchestrator"      "http://localhost:8080/health"
wait_for_http "Product Discovery" "http://localhost:8081/health"
wait_for_http "Order Management"  "http://localhost:8082/health"
wait_for_http "Pricing & Promos"  "http://localhost:8083/health"
wait_for_http "Review & Sentim."  "http://localhost:8084/health"
wait_for_http "Inventory & Ful."  "http://localhost:8085/health"

success "All agents are running"

# ── Start Frontend ────────────────────────────────────────────

step "Starting frontend"
docker compose --profile frontend up -d

wait_for_http "Frontend" "http://localhost:3000" 90

success "Frontend is running"

# ── Summary ───────────────────────────────────────────────────

print_summary
success "E-Commerce Agents is ready!"
