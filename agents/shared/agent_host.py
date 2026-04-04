"""Lightweight A2A-compatible host for specialist agents.

Provides a FastAPI app with /health, /message:send, and /.well-known/agent-card.json
endpoints. Replaces A2AAgentHost which has import issues in the MAF v1.0 beta.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def create_agent_app(
    *,
    agent: Any,
    agent_name: str,
    port: int,
    description: str = "",
    on_startup: Callable | None = None,
    on_shutdown: Callable | None = None,
) -> FastAPI:
    """Create a FastAPI app that hosts a MAF Agent with A2A-compatible endpoints.

    Args:
        agent: The MAF Agent instance
        agent_name: Agent identifier (e.g., "product-discovery")
        port: Port number for metadata
        description: Agent description for the agent card
        on_startup: Async callable for startup (init DB, telemetry, etc.)
        on_shutdown: Async callable for shutdown (close DB, etc.)
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if on_startup:
            await on_startup(app)
        logger.info("%s.started port=%d", agent_name, port)
        yield
        if on_shutdown:
            await on_shutdown()
        logger.info("%s.stopped", agent_name)

    app = FastAPI(title=agent_name, lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"status": "ok", "agent": agent_name, "port": port}

    @app.get("/.well-known/agent-card.json")
    async def agent_card():
        return {
            "name": agent_name,
            "description": description,
            "url": f"http://{agent_name}:{port}",
            "version": "1.0",
            "capabilities": {"streaming": False, "pushNotifications": False},
        }

    @app.post("/message:send")
    async def message_send(request: Request):
        """A2A-compatible message endpoint."""
        try:
            body = await request.json()
            message = body.get("message", "")
            if not message:
                return JSONResponse({"error": "No message provided"}, status_code=400)

            # Build messages list for the agent
            messages = [{"role": "user", "content": message}]

            # Run the agent
            result = await agent.run(messages=messages)

            # Extract response text
            response_text = ""
            if hasattr(result, "value") and result.value:
                response_text = str(result.value)
            elif hasattr(result, "text") and result.text:
                response_text = str(result.text)
            else:
                response_text = str(result)

            return {"response": response_text}

        except Exception:
            logger.exception("%s.message_error", agent_name)
            return JSONResponse(
                {"error": "Agent processing failed", "response": "I encountered an error processing your request."},
                status_code=500,
            )

    return app
