"""Agent authentication middleware.

Supports two modes:
1. Inter-agent: ``local`` mode uses AGENT_SHARED_SECRET in the X-Agent-Secret
   header; ``oauth`` mode uses an AS-issued RS256 service token instead
   (``aud=ecommerce-agents``, ``scope=agent:invoke``) — the shared secret is
   rejected outright. Either way, X-User-Email/X-User-Role/X-Session-Id
   forward the actual end-user identity; missing headers (system/health
   flows) default to ``role=system``.
2. User: JWT Bearer token in Authorization header (``local`` mode only —
   specialists never receive genuine end-user tokens directly in this
   architecture; the orchestrator's own routes validate those separately).
"""

from __future__ import annotations

import hmac
import logging

import jwt
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from shared.config import settings
from shared.context import current_session_id, current_user_email, current_user_role
from shared.identity_token import HEADER_NAME, IdentityTokenError
from shared.identity_token import verify as verify_identity
from shared.jwt_utils import decode_token
from shared.log_safe import log_safe

logger = logging.getLogger(__name__)

# Paths that skip authentication
PUBLIC_PATHS = {"/health", "/ready", "/.well-known/agent-card.json"}

# Roles the platform recognizes. 'system' is the inter-agent sentinel used when
# a call originates without an end user (internal / health flows). 'support'
# is the staff role that reviews approvals routed to it; 'power_user' is a
# seeded customer variant that was previously rejected as unknown.
_ALLOWED_ROLES = {"customer", "power_user", "seller", "support", "admin", "system"}


def _identity_anomaly(email: str, role: str) -> str | None:
    """Return a reason string if the forwarded identity looks spoofed, else None.

    The inter-agent credential (shared secret or, in oauth mode, the service
    token) authenticates the *caller* (another agent), but the forwarded
    ``x-user-email`` / ``x-user-role`` headers are otherwise trusted blindly.
    This flags obviously-bad values so they can be logged — and rejected
    under ``GUARDRAILS_STRICT_IDENTITY`` — instead of silently granting
    access if a credential ever leaks.
    """
    if role.lower() not in _ALLOWED_ROLES:
        return f"unknown_role:{role}"
    if email != "system" and "@" not in email:
        return "malformed_email"
    return None


def _apply_forwarded_identity(request: Request, agent_name: str) -> str | None:
    """Resolve which user the inter-agent caller speaks for, flag spoofing, stamp ContextVars.

    Shared by both inter-agent credential paths (shared secret in ``local`` mode, service token in ``oauth``
    mode) since forwarded-identity handling is identical either way. Returns a rejection reason if the request
    must be refused, else ``None``.

    Where the identity comes from is ``A2A_IDENTITY`` (B-14, ``shared/identity_token.py``):

    * ``headers``: the forwarded ``x-user-email`` / ``x-user-role`` / ``x-session-id``, trusted on the strength of
      the inter-agent credential (the original behaviour; any ``x-user-context`` is ignored).
    * ``both``: a signed ``x-user-context`` token when one is present, else the headers. A token that does not
      verify is refused, never ignored (a caller that sent one meant it), and a header beside a valid token must
      say the same thing: a forged ``x-user-email`` next to someone else's token is refused.
    * ``token``: only a verified token; the headers are not read for identity at all.
    """
    mode = settings.A2A_IDENTITY
    token = request.headers.get(HEADER_NAME, "") if mode != "headers" else ""
    header_email = request.headers.get("x-user-email")
    header_role = request.headers.get("x-user-role")
    header_session = request.headers.get("x-session-id", "")

    if token:
        try:
            verified = verify_identity(token, audience=agent_name)
        except IdentityTokenError as exc:
            logger.warning("security.user_context_rejected agent=%s reason=%s", log_safe(agent_name), exc.reason)
            return "Invalid user context"
        if (
            (header_email is not None and header_email != verified.email)
            or (header_role is not None and header_role != verified.role)
            or (header_session and header_session != verified.session_id)
        ):
            logger.warning("security.user_context_mismatch agent=%s", log_safe(agent_name))
            return "Identity does not match the user context"
        email, role, session_id = verified.email, verified.role, verified.session_id
    elif mode == "token":
        logger.warning("security.user_context_missing agent=%s", log_safe(agent_name))
        return "User context required"
    else:
        email = header_email if header_email is not None else "system"
        role = header_role if header_role is not None else "system"
        session_id = header_session

    anomaly = _identity_anomaly(email, role)
    if anomaly:
        logger.warning(
            "security.identity_spoof_suspected agent=%s reason=%s email=%s role=%s",
            log_safe(agent_name),
            log_safe(anomaly),
            log_safe(email),
            log_safe(role),
        )
        if settings.GUARDRAILS_STRICT_IDENTITY:
            return "Invalid forwarded identity"

    current_user_email.set(email)
    current_user_role.set(role)
    current_session_id.set(session_id)
    return None


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

        agent_secret = request.headers.get("x-agent-secret")

        # oauth mode retires the shared secret entirely — a request bearing
        # it is rejected outright rather than silently falling through to
        # the service-token path below.
        if settings.AUTH_MODE == "oauth" and agent_secret:
            logger.warning("auth.denied agent=%s reason=agent_secret_disabled_in_oauth_mode", self.agent_name)
            return JSONResponse({"error": "Inter-agent shared secret is disabled in oauth mode"}, status_code=401)

        # Inter-agent authentication (local mode): static shared secret.
        if agent_secret:
            if not hmac.compare_digest(agent_secret.encode(), settings.AGENT_SHARED_SECRET.encode()):
                logger.warning("auth.denied agent=%s reason=invalid_agent_secret", self.agent_name)
                return JSONResponse({"error": "Invalid agent secret"}, status_code=401)

            rejection = _apply_forwarded_identity(request, self.agent_name)
            if rejection:
                return JSONResponse({"error": rejection}, status_code=401)

            logger.info(
                "auth.agent agent=%s user=%s role=%s",
                self.agent_name,
                log_safe(current_user_email.get()),
                log_safe(current_user_role.get()),
            )
            return await call_next(request)

        auth_header = request.headers.get("authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            logger.warning("auth.denied agent=%s reason=missing_token", self.agent_name)
            return JSONResponse({"error": "Missing or invalid Authorization header"}, status_code=401)

        token = auth_header.removeprefix("Bearer ")

        if settings.AUTH_MODE == "oauth":
            # Inter-agent authentication (oauth mode): AS-issued service
            # token proves the caller is a legitimate first-party agent;
            # the actual end-user identity still travels via the forwarded
            # x-user-* headers, exactly as the shared-secret path above.
            from shared.factory import get_token_verifier

            try:
                get_token_verifier().decode(token, audience=settings.AUTH_AGENT_AUDIENCE, required_scope="agent:invoke")
            except jwt.ExpiredSignatureError:
                logger.warning("auth.denied agent=%s reason=expired_service_token", self.agent_name)
                return JSONResponse({"error": "Service token expired"}, status_code=401)
            except jwt.PyJWTError:
                logger.warning("auth.denied agent=%s reason=invalid_service_token", self.agent_name)
                return JSONResponse({"error": "Invalid service token"}, status_code=401)

            rejection = _apply_forwarded_identity(request, self.agent_name)
            if rejection:
                return JSONResponse({"error": rejection}, status_code=401)

            logger.info(
                "auth.agent agent=%s user=%s role=%s",
                self.agent_name,
                log_safe(current_user_email.get()),
                log_safe(current_user_role.get()),
            )
            return await call_next(request)

        # User JWT authentication (local mode only)
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

        current_user_email.set(email)
        current_user_role.set(role)
        current_session_id.set(request.headers.get("x-session-id", ""))

        logger.info("auth.user agent=%s user=%s role=%s", self.agent_name, log_safe(email), log_safe(role))
        return await call_next(request)
