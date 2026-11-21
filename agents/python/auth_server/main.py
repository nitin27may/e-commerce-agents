"""Self-hosted OAuth2 Authorization Server — entry point.

Run as HTTP service:
    uvicorn auth_server.main:app --host 0.0.0.0 --port 8090

Endpoints:
    GET  /health
    GET  /.well-known/jwks.json
    GET  /.well-known/oauth-authorization-server   (RFC 8414 metadata)
    POST /oauth/token
    POST /oauth/register    (RFC 7591 — optional, AUTH_ALLOW_DYNAMIC_REGISTRATION)
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

import httpx
import joserfc.errors as jose_errors
from joserfc import jwt as joserfc_jwt
from joserfc.jwk import KeySet, RSAKey
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from auth_server import keys, register
from auth_server._bridge import bind_main_loop
from auth_server.clients import ClientStore
from auth_server.server import OAuthAuthorizationServer
from shared.config import settings
from shared.db import close_db_pool, get_pool, init_db_pool
from shared.log_safe import log_safe

logger = logging.getLogger(__name__)

_server: OAuthAuthorizationServer | None = None
_signing_key: RSAKey | None = None
# Every published public key (active plus rotated-out ones inside their window), so a registration token signed
# just before a rotation still verifies. None until the lifespan has loaded it.
_verify_keys: KeySet | None = None

# How often a running auth-server looks for a rotation made by another process (scripts/rotate_signing_key.py)
# and sweeps expired keys. Bounds how long a rotation takes to start signing, never how long old tokens validate.
KEY_MAINTENANCE_INTERVAL_SECONDS = 30.0


async def _maintain_keys_once(pool) -> None:
    """One pass: sweep keys past ``not_after``, adopt a new signer if another process rotated, reload verify keys."""
    global _signing_key, _verify_keys
    await keys.retire_expired(pool)
    kid, key, changed = await keys.refresh_active_key(pool)
    if changed and _server is not None:
        _server.use_signing_key(kid, key)
        logger.info("auth_server.signing_key_switched kid=%s", log_safe(kid))
    _signing_key = key
    _verify_keys = await keys.get_verification_keys(pool)


async def _key_maintenance_loop(pool) -> None:
    while True:
        await asyncio.sleep(KEY_MAINTENANCE_INTERVAL_SECONDS)
        try:
            await _maintain_keys_once(pool)
        except Exception:
            # A transient DB error must not kill the loop; the next pass retries.
            logger.exception("auth_server.key_maintenance_failed")


@asynccontextmanager
async def lifespan(app: Starlette):
    bind_main_loop()
    await init_db_pool()
    pool = get_pool()

    kid, signing_key = await keys.ensure_active_key(pool)

    client_store = ClientStore()
    await client_store.load(pool)

    global _server, _signing_key, _verify_keys
    _signing_key = signing_key
    _verify_keys = await keys.get_verification_keys(pool)
    _server = OAuthAuthorizationServer(
        client_store=client_store,
        pool=pool,
        issuer=settings.AUTH_SERVER_ISSUER,
        kid=kid,
        signing_key=signing_key,
    )
    logger.info("auth_server.ready issuer=%s kid=%s", settings.AUTH_SERVER_ISSUER, log_safe(kid))

    # The auth-server only runs under AUTH_MODE=oauth; AUTH_MODE=local never starts it, so never reaches this.
    maintenance = asyncio.create_task(_key_maintenance_loop(pool))
    try:
        yield
    finally:
        maintenance.cancel()
        await close_db_pool()


class _RegistrationTokenError(Exception):
    """Raised by ``_verify_registration_token`` on any validation failure."""


def _verify_registration_token(token: str) -> None:
    """Validate a bearer token presented to ``/oauth/register`` — entirely
    in-process, using the signing key this AS already holds in memory.

    Deliberately does NOT reuse ``shared/oauth/verifier.py::RS256Verifier``
    (the JWKS-over-HTTP verifier every *other* resource server uses): that
    verifier fetches JWKS via a blocking HTTP call, which live-testing
    against the real running server showed deadlocks here specifically —
    the AS is a single-worker asyncio process, and a request handler
    synchronously calling back into the very same event loop to fetch its
    own JWKS blocks forever (times out) waiting for a response the process
    can never produce while it's stuck waiting. No other resource server
    hits this because it isn't handling a request itself while its
    JwksTokenVerifier fetches from the AS's *separate* process. The fix
    that generalizes: the AS should never verify its own tokens over the
    network — it already holds the signing key, so this is just local
    signature verification plus manual claim checks (joserfc's ``decode``
    only verifies the signature; it does not check iss/aud/scope/exp).
    """
    verify_with = _verify_keys if _verify_keys is not None else _signing_key
    if verify_with is None:
        raise _RegistrationTokenError("auth-server not initialized")

    try:
        decoded = joserfc_jwt.decode(token, verify_with)
    except jose_errors.JoseError as exc:
        raise _RegistrationTokenError(str(exc)) from exc

    claims = decoded.claims
    if claims.get("iss") != settings.AUTH_SERVER_ISSUER:
        raise _RegistrationTokenError("wrong issuer")
    if settings.AUTH_SERVER_AUDIENCE not in (claims.get("aud") or []):
        raise _RegistrationTokenError("wrong audience")
    if "client:register" not in (claims.get("scope") or "").split():
        raise _RegistrationTokenError("missing required scope")
    if claims.get("exp", 0) <= time.time():
        raise _RegistrationTokenError("token expired")


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "auth-server"})


async def jwks(request: Request) -> JSONResponse:
    document = await keys.get_jwks(get_pool())
    return JSONResponse(document)


async def metadata(request: Request) -> JSONResponse:
    """RFC 8414 authorization server metadata."""
    issuer = settings.AUTH_SERVER_ISSUER
    return JSONResponse(
        {
            "issuer": issuer,
            "token_endpoint": f"{issuer}/oauth/token",
            "jwks_uri": f"{issuer}/.well-known/jwks.json",
            "registration_endpoint": f"{issuer}/oauth/register",
            "grant_types_supported": ["client_credentials", "password", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
            "scopes_supported": [
                "api:chat",
                "agent:invoke",
                settings.MCP_PRODUCT_REQUIRED_SCOPE,
                settings.MCP_INVENTORY_REQUIRED_SCOPE,
                "client:register",
            ],
        }
    )


async def token_endpoint(request: Request) -> JSONResponse:
    form = await request.form()
    form_dict = {k: v for k, v in form.items()}
    # ASGI headers arrive lowercase; authlib's client-auth extraction looks
    # up "Authorization" with that exact case, so a plain dict here would
    # silently never match. httpx.Headers is case-insensitive both ways
    # and is a plain mapping the sync bridge can safely read from a
    # worker thread.
    headers = httpx.Headers(dict(request.headers))

    assert _server is not None, "auth-server not initialized"
    status, body, response_headers = await asyncio.to_thread(
        _server.handle_token_request, form_dict, headers, str(request.url)
    )
    return JSONResponse(body, status_code=status, headers=dict(response_headers or {}))


def _unauthorized(error: str, description: str) -> JSONResponse:
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=401,
        headers={"WWW-Authenticate": f'Bearer error="{error}", error_description="{description}"'},
    )


async def register_endpoint(request: Request) -> JSONResponse:
    """RFC 7591 dynamic client registration — off by default.

    Requires a bearer token scoped ``client:register``, verified entirely
    in-process (see ``_verify_registration_token``) — never over the
    network. ``auth_server/register.py`` holds the validation/persistence
    logic for the request body itself.
    """
    if not settings.AUTH_ALLOW_DYNAMIC_REGISTRATION:
        return JSONResponse(
            {"error": "registration_disabled", "error_description": "Dynamic client registration is disabled"},
            status_code=403,
        )

    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return _unauthorized("invalid_token", "Authentication required")

    token = auth_header[len("Bearer ") :]
    try:
        _verify_registration_token(token)
    except _RegistrationTokenError as exc:
        logger.warning("auth_server.register_token_rejected error=%s", log_safe(exc))
        return _unauthorized("invalid_token", "Invalid, expired, or insufficiently-scoped token")

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"error": "invalid_client_metadata", "error_description": "Request body must be valid JSON"},
            status_code=400,
        )

    try:
        client_name, scopes = register.validate_registration_request(body)
    except register.RegistrationError as exc:
        return JSONResponse(exc.to_body(), status_code=exc.status)

    pool = get_pool()
    client_id, client_secret = await register.create_client(pool, client_name, scopes)

    assert _server is not None, "auth-server not initialized"
    await _server.client_store.load(pool)

    return JSONResponse(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "client_id_issued_at": int(time.time()),
            "client_secret_expires_at": 0,
            "client_name": client_name,
            "grant_types": ["client_credentials"],
            "scope": " ".join(scopes),
            "token_endpoint_auth_method": "client_secret_basic",
        },
        status_code=201,
    )


app = Starlette(
    lifespan=lifespan,
    routes=[
        Route("/health", health),
        Route("/.well-known/jwks.json", jwks),
        Route("/.well-known/oauth-authorization-server", metadata),
        Route("/oauth/token", token_endpoint, methods=["POST"]),
        Route("/oauth/register", register_endpoint, methods=["POST"]),
    ],
)


def main() -> None:
    """Console entry-point, mirrors the other agents' Dockerfile CMD."""
    import uvicorn

    uvicorn.run("auth_server.main:app", host="0.0.0.0", port=8090)


if __name__ == "__main__":
    main()
