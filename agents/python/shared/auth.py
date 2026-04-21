"""Agent authentication middleware.

Supports two modes:
1. Inter-agent: AGENT_SHARED_SECRET in X-Agent-Secret header, X-User-Email for identity
2. User: JWT Bearer token in Authorization header
"""

from __future__ import annotations

import logging

import jwt
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from shared.config import settings
from shared.context import current_user_email, current_user_role, current_session_id
from shared.jwt_utils import decode_token

logger = logging.getLogger(__name__)

# Paths that skip authentication
PUBLIC_PATHS = {"/health", "/.well-known/agent-card.json"}


class AgentAuthMiddleware(BaseHTTPMiddleware):
    """Authenticate requests via inter-agent secret or JWT."""

    def __init__(self, app, agent_name: str = "unknown"):
        super().__init__(app)
        self.agent_name = agent_name

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        # Skip auth for health and agent card endpoints
        if path in PUBLIC_PATHS:
            return await call_next(request)

        # Inter-agent authentication
        agent_secret = request.headers.get("x-agent-secret")
        if agent_secret:
            if agent_secret != settings.AGENT_SHARED_SECRET:
                logger.warning("auth.denied agent=%s reason=invalid_agent_secret", self.agent_name)
                return JSONResponse({"error": "Invalid agent secret"}, status_code=401)

            email = request.headers.get("x-user-email", "system")
            role = request.headers.get("x-user-role", "system")
            session_id = request.headers.get("x-session-id", "")

            current_user_email.set(email)
            current_user_role.set(role)
            current_session_id.set(session_id)

            logger.info("auth.agent agent=%s user=%s role=%s", self.agent_name, email, role)
            return await call_next(request)

        # User JWT authentication
        auth_header = request.headers.get("authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            logger.warning("auth.denied agent=%s reason=missing_token", self.agent_name)
            return JSONResponse({"error": "Missing or invalid Authorization header"}, status_code=401)

        token = auth_header.removeprefix("Bearer ")
        try:
            payload = decode_token(token)
        except jwt.ExpiredSignatureError:
            logger.warning("auth.denied agent=%s reason=expired_token", self.agent_name)
            return JSONResponse({"error": "Token expired"}, status_code=401)
        except jwt.InvalidTokenError:
            logger.warning("auth.denied agent=%s reason=invalid_token", self.agent_name)
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        if payload.get("type") != "access":
            return JSONResponse({"error": "Invalid token type"}, status_code=401)

        email = payload.get("sub", "")
        role = payload.get("role", "customer")
        user_id = payload.get("user_id", "")

        current_user_email.set(email)
        current_user_role.set(role)
        current_session_id.set(request.headers.get("x-session-id", ""))

        logger.info("auth.user agent=%s user=%s role=%s", self.agent_name, email, role)
        return await call_next(request)
