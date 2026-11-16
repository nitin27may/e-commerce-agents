"""Orchestrator (Customer Support) — FastAPI entry point.

This is the main gateway for all client requests. Unlike specialist agents
which use A2AAgentHost, the orchestrator is a full FastAPI app that handles
auth, chat, marketplace, and admin endpoints.

Run locally:
    cd agents && uv run uvicorn orchestrator.main:app --port 8080 --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from asyncpg.exceptions import ForeignKeyViolationError
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from orchestrator.routes import router
from shared.config import settings
from shared.db import close_db_pool, init_db_pool
from shared.prompt_loader import validate_model_overrides
from shared.readiness import register_readiness
from shared.telemetry import instrument_fastapi, setup_telemetry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def check_approval_policy() -> bool:
    """Load and validate ``approvals.yaml`` now, so a broken file is an error in the startup log.

    Deliberately does not stop the process: the return gate fails closed on its own (a broken policy routes
    every return to a manager and never auto-approves), and refusing to boot would also take down replay and
    demo installs that never decide an approval. What it must not do is stay silent until the first return.
    """
    try:
        from shared.policy import get_policy

        get_policy()
    except Exception:
        logger.exception(
            "orchestrator.approval_policy_invalid: approvals.yaml could not be loaded; "
            "every return will be routed to a manager until it is fixed"
        )
        return False
    return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: telemetry + DB pool. Shutdown: close pool."""
    validate_model_overrides()  # a malformed per-agent ``model:`` block fails here, naming the file
    check_approval_policy()
    setup_telemetry("ecommerce.orchestrator")
    instrument_fastapi(app)
    await init_db_pool()
    logger.info("orchestrator.started")
    yield
    await close_db_pool()
    logger.info("orchestrator.stopped")


app = FastAPI(
    title="E-Commerce Agents",
    description="E-Commerce Multi-Agent Platform — Orchestrator API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    # Explicit origins, no credentials: auth is a Bearer header, never a
    # cookie, and the browser reaches this API through the Next.js same-origin
    # proxy anyway. Set CORS_ALLOWED_ORIGINS if you call it directly
    # (NEXT_PUBLIC_API_URL).
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.exception_handler(ForeignKeyViolationError)
async def stale_user_handler(request: Request, exc: ForeignKeyViolationError) -> JSONResponse:
    # JWT carries a user_id that's no longer in the users table — typically
    # after a DB reseed. Map to 401 so the frontend clears its token.
    detail = str(exc)
    if "user_id" in detail:
        logger.warning("stale_jwt.fk_violation path=%s", request.url.path)
        return JSONResponse(
            status_code=401,
            content={"detail": "Session invalid — please log in again."},
        )
    logger.exception("fk_violation.unhandled path=%s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Database constraint error"},
    )


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok", "service": "orchestrator"}


register_readiness(app, "orchestrator")
