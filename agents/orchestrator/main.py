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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from orchestrator.routes import router
from shared.db import close_db_pool, init_db_pool
from shared.telemetry import instrument_fastapi, setup_telemetry

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: telemetry + DB pool. Shutdown: close pool."""
    setup_telemetry("agentbazaar.orchestrator")
    instrument_fastapi(app)
    await init_db_pool()
    logger.info("orchestrator.started")
    yield
    await close_db_pool()
    logger.info("orchestrator.stopped")


app = FastAPI(
    title="AgentBazaar",
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


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok", "service": "orchestrator"}
