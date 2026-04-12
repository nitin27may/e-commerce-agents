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
from shared.db import close_db_pool, init_db_pool
from shared.telemetry import instrument_fastapi, setup_telemetry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: telemetry + DB pool. Shutdown: close pool."""
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
    allow_origins=["*"],
    allow_credentials=True,
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
