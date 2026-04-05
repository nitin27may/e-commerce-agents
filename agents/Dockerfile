# ============================================================
# AgentBazaar — Multi-target Agent Dockerfile
# Build context: ./agents
# Usage: docker build --build-arg AGENT_NAME=product_discovery .
# ============================================================

FROM python:3.12-slim AS base

# System deps for asyncpg (libpq) and bcrypt (gcc)
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev curl && \
    rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Create non-root user
RUN groupadd -r agent && useradd -r -g agent -d /app -s /sbin/nologin agent

WORKDIR /app

# Install Python dependencies (cached layer)
COPY pyproject.toml .
RUN uv sync --no-dev --no-install-project

# Patch MAF v1.0 empty __init__.py (ships without re-exports)
COPY patch_maf.py .
RUN uv run --no-project python patch_maf.py

# Copy shared library and prompt configs (all agents need these)
COPY shared/ shared/
COPY config/ config/

# ── Agent-specific copy ──────────────────────────────────────
ARG AGENT_NAME=orchestrator
ARG AGENT_PORT=8080

# Copy agent module (Python package name uses underscores)
COPY ${AGENT_NAME}/ ${AGENT_NAME}/

# Give non-root user ownership of the app directory (for uv cache + runtime)
RUN chown -R agent:agent /app

ENV AGENT_NAME=${AGENT_NAME}
ENV AGENT_PORT=${AGENT_PORT}
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV UV_CACHE_DIR=/app/.cache/uv

# Switch to non-root
USER agent

EXPOSE ${AGENT_PORT}

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:${AGENT_PORT}/health || exit 1

# Use shell form so env vars are expanded at runtime
# --no-project: skip building the project package (we run modules directly)
CMD uv run --no-project uvicorn ${AGENT_NAME}.main:app --host 0.0.0.0 --port ${AGENT_PORT}
