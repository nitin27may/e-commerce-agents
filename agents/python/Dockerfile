# ============================================================
# E-Commerce Agents — Multi-target Agent Dockerfile
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

# Install Python dependencies (cached layer).
# Sync from the committed lockfile (--frozen) so the build is deterministic and
# does NOT re-resolve against live PyPI — agent-framework is a pre-release and
# upstream churn (e.g. agent-framework-azure-ai-search) makes fresh resolution
# unsatisfiable. The lock pins a known-good graph.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Patch MAF v1.0 empty __init__.py (ships without re-exports)
COPY patch_maf.py .
RUN uv run --no-project python patch_maf.py

# Copy shared library and prompt configs (all agents need these)
COPY shared/ shared/
COPY config/ config/

# The MAF workflow graphs. Three of the five orchestration modes import these
# lazily, inside the mode rather than at module scope:
#
#   workflow:pre-purchase    workflows.pre_purchase
#   workflow:return-replace  workflows.return_replace
#   group-chat               workflows.group_chat
#
# Omitting this directory does not break the build, or startup, or the image
# smoke-test — `orchestrator.main` imports perfectly well without it. It breaks
# at request time, and only for those three modes, with
# `ModuleNotFoundError: No module named 'workflows'` turning into a generic
# "I encountered an issue processing your request" after ~6ms.
#
# Nothing caught that: E2E is deliberately not run in CI, the eval harness runs
# in-process where workflows/ is on the path anyway, and the image smoke-test
# only imports <agent>.main. It was found by running the container and asking
# it a question.
COPY workflows/ workflows/

# ── Agent-specific copy ──────────────────────────────────────
ARG AGENT_NAME=orchestrator
ARG AGENT_PORT=8080

# Copy agent module (Python package name uses underscores)
COPY ${AGENT_NAME}/ ${AGENT_NAME}/

# The orchestrator's two workflow modes call specialist *tools* in-process
# rather than over A2A, so the orchestrator image needs those packages too:
#
#   workflow_mode.py  product_discovery.tools, order_management.tools,
#                     inventory_fulfillment.tools, review_sentiment.tools
#
# Like the workflows/ import above, these are lazy and inside the mode, so
# nothing fails until a request actually selects one of those modes — then
# `ModuleNotFoundError: No module named 'product_discovery'` becomes the same
# generic "I encountered an issue" after a few milliseconds.
#
# Copied unconditionally rather than only for AGENT_NAME=orchestrator because
# Dockerfile has no clean conditional COPY. The cost is a few hundred KB of
# .py files per image; the alternative is a mode that is silently dead in
# every containerised deployment.
#
# Worth saying plainly: a specialist's tools being imported into the
# orchestrator's process is a layering problem, not just a packaging one --
# these agents talk over A2A everywhere else. Fixing that properly is a
# refactor, and this line is what makes the shipped feature work meanwhile.
COPY product_discovery/ product_discovery/
COPY order_management/ order_management/
COPY inventory_fulfillment/ inventory_fulfillment/
COPY review_sentiment/ review_sentiment/
COPY pricing_promotions/ pricing_promotions/

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
