"""B-14: the signed user-context token (shared/identity_token.py) and where a specialist takes identity from.

Two halves. The token itself: derivation, the cross-stack golden vectors, and every way a token must be refused.
And the middleware: with ``A2A_IDENTITY`` at ``headers`` / ``both`` / ``token``, a forged ``X-User-Email`` beside a
valid token for another user is refused, an expired token is refused, a token for another specialist is refused, and
``both`` (the default) still serves a caller that sends only the headers.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from shared.auth import AgentAuthMiddleware
from shared.config import settings
from shared.context import current_session_id, current_user_email, current_user_role
from shared.identity_token import (
    KDF_INFO,
    MAX_LIFETIME_SECONDS,
    TOKEN_TYPE,
    IdentityTokenError,
    derive_key,
    hkdf_sha256,
    mint,
    verify,
)
from shared.oauth.service_client import build_a2a_headers

VECTORS = json.loads(
    (Path(__file__).resolve().parents[1] / "config" / "identity" / "testdata" / "user-context.v1.json").read_text()
)
KEY = derive_key("test-identity-secret-0123456789abcdef")
OTHER_KEY = derive_key("another-identity-secret-0123456789abc")
SECRET = "inter-agent-secret-0123456789abcdef"
ALICE = {"email": "alice@example.com", "role": "customer", "session_id": "6f1c0a5e-2d3b-4c7e-9a10-1b2c3d4e5f60"}


def _mint(**overrides: object) -> str:
    args: dict = {**ALICE, "audience": "product-discovery", "key": KEY, **overrides}
    return mint(**args)


# ─────────────────────── derivation and the cross-stack vectors ───────────────────────


def test_hkdf_matches_the_reference_implementation() -> None:
    for secret in ("a", "x" * 40, "päss wörd ☃"):
        reference = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=KDF_INFO).derive(secret.encode())
        assert hkdf_sha256(secret.encode(), KDF_INFO) == reference == derive_key(secret)


def test_rfc5869_test_case_1_for_the_hand_rolled_hkdf() -> None:
    # RFC 5869 appendix A.1 uses a salt; with none, the spec's zero salt must give the same as a 32-zero-byte salt.
    ikm = bytes.fromhex("0b" * 22)
    expected = HKDF(algorithm=hashes.SHA256(), length=42, salt=b"\x00" * 32, info=b"").derive(ikm)
    assert hkdf_sha256(ikm, b"", 42) == expected


def test_the_golden_vectors_are_verified_by_this_stack() -> None:
    assert derive_key(VECTORS["secret"]).hex() == VECTORS["derived_key_hex"]
    assert VECTORS["kdf_info"] == KDF_INFO.decode()
    key = bytes.fromhex(VECTORS["derived_key_hex"])
    for name, case in VECTORS["tokens"].items():
        context = verify(case["token"], audience=VECTORS["audience"], key=key, now=VECTORS["verify_at"])
        assert (context.email, context.role, context.session_id) == (case["email"], case["role"], case["session_id"]), (
            name
        )


def test_the_golden_vectors_have_not_drifted() -> None:
    """The committed tokens are exactly what this stack mints for those inputs (the .NET test verifies the same)."""
    key = bytes.fromhex(VECTORS["derived_key_hex"])
    for name, case in VECTORS["tokens"].items():
        again = mint(
            email=case["email"],
            role=case["role"],
            session_id=case["session_id"],
            audience=VECTORS["audience"],
            key=key,
            ttl_seconds=VECTORS["ttl_seconds"],
            now=VECTORS["issued_at"],
            jti=f"vector-jti-{name}",
        )
        assert again == case["token"], name


# ─────────────────────── the token ───────────────────────


def test_a_token_round_trips_and_names_its_audience() -> None:
    context = verify(_mint(), audience="product-discovery", key=KEY)

    assert (context.email, context.role, context.session_id) == (
        "alice@example.com",
        "customer",
        ALICE["session_id"],
    )
    assert context.audience == "product-discovery"


def test_the_claims_are_the_documented_ones() -> None:
    header = jwt.get_unverified_header(_mint())
    claims = jwt.decode(_mint(), options={"verify_signature": False})

    assert header == {"alg": "HS256", "typ": TOKEN_TYPE}
    assert claims["iss"] == "ecommerce-agents/orchestrator"
    assert claims["aud"] == "product-discovery"
    assert claims["sub"] == "alice@example.com"
    assert claims["act"] == {"client_id": "orchestrator"}
    assert claims["exp"] - claims["iat"] == settings.A2A_IDENTITY_TTL_SECONDS == 300
    assert claims["jti"]


def test_an_anonymous_caller_keeps_its_exact_identity() -> None:
    token = _mint(email="", role="anonymous", session_id="")

    assert jwt.decode(token, options={"verify_signature": False})["sub"] == "anonymous"
    context = verify(token, audience="product-discovery", key=KEY)
    assert (context.email, context.role, context.session_id) == ("", "anonymous", "")


def test_every_token_is_unique() -> None:
    assert _mint() != _mint()


@pytest.mark.parametrize(
    ("label", "make"),
    [
        ("expired", lambda: _mint(now=int(time.time()) - 3600)),
        ("signature", lambda: _mint(key=OTHER_KEY)),
        ("audience", lambda: _mint(audience="order-management")),
        ("lifetime", lambda: _mint(ttl_seconds=MAX_LIFETIME_SECONDS + 600)),
        ("malformed", lambda: "not-a-jwt"),
        ("malformed", lambda: ""),
        ("malformed", lambda: _mint()[:-3] + "AAA"),
    ],
)
def test_a_token_that_must_not_be_believed_is_refused(label: str, make) -> None:
    expected = "signature" if label == "malformed" and make().count(".") == 2 else label
    with pytest.raises(IdentityTokenError) as raised:
        verify(make(), audience="product-discovery", key=KEY)
    assert raised.value.reason in {label, expected}


def test_a_users_login_jwt_is_not_an_identity_token() -> None:
    """A local-mode login JWT is also HS256; the `typ` header and issuer keep the two apart."""
    login = jwt.encode(
        {"sub": "alice@example.com", "role": "admin", "type": "access", "exp": time.time() + 60}, KEY, "HS256"
    )

    with pytest.raises(IdentityTokenError, match="wrong_type"):
        verify(login, audience="product-discovery", key=KEY)


@pytest.mark.parametrize("algorithm", ["HS512", "HS384"])
def test_only_hs256_is_accepted(algorithm: str) -> None:
    now = int(time.time())
    claims = {
        "iss": "ecommerce-agents/orchestrator",
        "aud": "product-discovery",
        "sub": "a@b.c",
        "email": "a@b.c",
        "role": "admin",
        "sid": "",
        "iat": now,
        "exp": now + 60,
    }
    forged = jwt.encode(claims, KEY, algorithm=algorithm, headers={"typ": TOKEN_TYPE})

    with pytest.raises(IdentityTokenError, match="wrong_type"):
        verify(forged, audience="product-discovery", key=KEY)


def test_alg_none_is_refused() -> None:
    now = int(time.time())
    claims = {
        "iss": "ecommerce-agents/orchestrator",
        "aud": "product-discovery",
        "sub": "a@b.c",
        "iat": now,
        "exp": now + 60,
    }
    unsigned = jwt.encode(claims, "", algorithm="none", headers={"typ": TOKEN_TYPE})

    with pytest.raises(IdentityTokenError):
        verify(unsigned, audience="product-discovery", key=KEY)


def test_a_tampered_payload_fails_its_signature() -> None:
    header, payload, signature = _mint(role="customer").split(".")
    forged_payload = jwt.utils.base64url_encode(
        json.dumps({**json.loads(jwt.utils.base64url_decode(payload)), "role": "admin"}).encode()
    ).decode()

    with pytest.raises(IdentityTokenError, match="signature"):
        verify(f"{header}.{forged_payload}.{signature}", audience="product-discovery", key=KEY)


def test_the_error_reason_carries_nothing_of_the_token() -> None:
    with pytest.raises(IdentityTokenError) as raised:
        verify(_mint(email="secret.person@example.com", key=OTHER_KEY), audience="product-discovery", key=KEY)
    assert "secret.person" not in str(raised.value) and "secret.person" not in raised.value.reason


def test_the_key_is_a_config_choice_distinct_from_the_shared_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    from shared.identity_token import configured_key

    monkeypatch.setattr(settings, "AGENT_SHARED_SECRET", SECRET)
    monkeypatch.setattr(settings, "A2A_IDENTITY_KEY", "")
    assert configured_key() == derive_key(SECRET)

    monkeypatch.setattr(settings, "A2A_IDENTITY_KEY", "a-different-identity-key-0123456789ab")
    assert configured_key() == derive_key("a-different-identity-key-0123456789ab") != derive_key(SECRET)


# ─────────────────────── the sending side ───────────────────────


@pytest.fixture
def sender(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "local")
    monkeypatch.setattr(settings, "AGENT_SHARED_SECRET", SECRET)
    monkeypatch.setattr(settings, "A2A_IDENTITY_KEY", "")
    tokens = [
        current_user_email.set(ALICE["email"]),
        current_user_role.set("customer"),
        current_session_id.set(ALICE["session_id"]),
    ]
    yield
    for var, token in zip((current_user_email, current_user_role, current_session_id), tokens, strict=True):
        var.reset(token)


async def test_both_mode_sends_the_token_and_the_headers(sender, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "A2A_IDENTITY", "both")

    headers = await build_a2a_headers("product-discovery")

    assert headers["x-agent-secret"] == SECRET
    assert (headers["x-user-email"], headers["x-user-role"], headers["x-session-id"]) == (
        "alice@example.com",
        "customer",
        ALICE["session_id"],
    )
    context = verify(headers["x-user-context"], audience="product-discovery")
    assert context.email == "alice@example.com"
    with pytest.raises(IdentityTokenError, match="audience"):
        verify(headers["x-user-context"], audience="order-management")


async def test_token_mode_sends_no_identity_headers(sender, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "A2A_IDENTITY", "token")

    headers = await build_a2a_headers("product-discovery")

    assert not {"x-user-email", "x-user-role", "x-session-id"} & set(headers)
    assert verify(headers["x-user-context"], audience="product-discovery").session_id == ALICE["session_id"]
    with pytest.raises(ValueError, match="specialist's name"):
        await build_a2a_headers()


async def test_headers_mode_sends_no_token(sender, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "A2A_IDENTITY", "headers")

    headers = await build_a2a_headers("product-discovery")

    assert "x-user-context" not in headers
    assert headers["x-user-email"] == "alice@example.com"


async def test_a_caller_that_does_not_know_the_audience_sends_headers_only_in_both_mode(
    sender, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "A2A_IDENTITY", "both")

    assert "x-user-context" not in await build_a2a_headers()


# ─────────────────────── the receiving side: AgentAuthMiddleware ───────────────────────


def _specialist() -> Starlette:
    async def who(request) -> JSONResponse:
        return JSONResponse(
            {"email": current_user_email.get(), "role": current_user_role.get(), "session": current_session_id.get()}
        )

    app = Starlette(routes=[Route("/who", who, methods=["GET", "POST"])])
    app.add_middleware(AgentAuthMiddleware, agent_name="product-discovery")
    return app


@pytest.fixture
def receiver(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "local")
    monkeypatch.setattr(settings, "AGENT_SHARED_SECRET", SECRET)
    monkeypatch.setattr(settings, "A2A_IDENTITY_KEY", "")
    monkeypatch.setattr(settings, "GUARDRAILS_STRICT_IDENTITY", False)
    key = derive_key(SECRET)

    async def call(*, mode: str, headers: dict[str, str]) -> httpx.Response:
        monkeypatch.setattr(settings, "A2A_IDENTITY", mode)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_specialist()), base_url="http://s") as client:
            return await client.get("/who", headers={"x-agent-secret": SECRET, **headers})

    def token(**overrides: object) -> str:
        return mint(**{**ALICE, "audience": "product-discovery", "key": key, **overrides})  # type: ignore[arg-type]

    return call, token


ALICE_HEADERS = {"x-user-email": "alice@example.com", "x-user-role": "customer", "x-session-id": ALICE["session_id"]}


@pytest.mark.parametrize("mode", ["both", "token"])
async def test_a_valid_token_sets_the_identity(receiver, mode: str) -> None:
    call, token = receiver

    response = await call(mode=mode, headers={"x-user-context": token()})

    assert response.status_code == 200
    assert response.json() == {"email": "alice@example.com", "role": "customer", "session": ALICE["session_id"]}


@pytest.mark.parametrize("mode", ["both", "token"])
async def test_a_forged_email_beside_a_valid_token_for_another_user_is_refused(receiver, mode: str) -> None:
    call, token = receiver

    response = await call(mode=mode, headers={"x-user-context": token(), "x-user-email": "admin@example.com"})

    assert response.status_code == 401
    assert response.json() == {"error": "Identity does not match the user context"}


@pytest.mark.parametrize("mode", ["both", "token"])
async def test_a_forged_role_beside_a_valid_token_is_refused(receiver, mode: str) -> None:
    call, token = receiver

    response = await call(mode=mode, headers={"x-user-context": token(), "x-user-role": "admin"})

    assert response.status_code == 401


async def test_a_session_header_that_contradicts_the_token_is_refused(receiver) -> None:
    call, token = receiver

    response = await call(
        mode="both", headers={"x-user-context": token(), "x-session-id": "someone-elses-conversation"}
    )

    assert response.status_code == 401


@pytest.mark.parametrize("mode", ["both", "token"])
async def test_an_expired_token_is_refused(receiver, mode: str) -> None:
    call, token = receiver

    response = await call(mode=mode, headers={"x-user-context": token(now=int(time.time()) - 3600), **ALICE_HEADERS})

    assert response.status_code == 401
    assert response.json() == {"error": "Invalid user context"}


async def test_an_invalid_token_is_never_ignored_in_favour_of_the_headers(receiver) -> None:
    call, _ = receiver

    response = await call(mode="both", headers={"x-user-context": "garbage", **ALICE_HEADERS})

    assert response.status_code == 401


@pytest.mark.parametrize("mode", ["both", "token"])
async def test_a_token_for_another_specialist_is_refused(receiver, mode: str) -> None:
    call, token = receiver

    response = await call(mode=mode, headers={"x-user-context": token(audience="order-management")})

    assert response.status_code == 401


async def test_a_token_signed_with_another_key_is_refused(receiver) -> None:
    call, token = receiver

    response = await call(mode="both", headers={"x-user-context": token(key=OTHER_KEY)})

    assert response.status_code == 401


async def test_both_mode_still_serves_a_caller_that_sends_only_the_headers(receiver) -> None:
    call, _ = receiver

    response = await call(mode="both", headers=ALICE_HEADERS)

    assert response.status_code == 200
    assert response.json()["email"] == "alice@example.com"


async def test_token_mode_refuses_headers_alone(receiver) -> None:
    call, _ = receiver

    response = await call(mode="token", headers=ALICE_HEADERS)

    assert response.status_code == 401
    assert response.json() == {"error": "User context required"}


async def test_headers_mode_ignores_a_token_entirely(receiver) -> None:
    call, token = receiver

    response = await call(mode="headers", headers={"x-user-context": "not even a jwt", **ALICE_HEADERS})

    assert response.status_code == 200
    assert response.json()["email"] == "alice@example.com"
    # ...and does not believe a valid one over the headers either: the headers are the identity here.
    other = await call(
        mode="headers",
        headers={"x-user-context": token(), "x-user-email": "bob@example.com", "x-user-role": "customer"},
    )
    assert other.json()["email"] == "bob@example.com"


async def test_the_token_alone_is_enough_the_headers_are_optional(receiver) -> None:
    call, token = receiver

    response = await call(
        mode="token", headers={"x-user-context": token(email="bob@example.com", role="seller", session_id="")}
    )

    assert response.json() == {"email": "bob@example.com", "role": "seller", "session": ""}


async def test_the_inter_agent_credential_is_still_required(receiver) -> None:
    _, token = receiver
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_specialist()), base_url="http://s") as client:
        response = await client.get("/who", headers={"x-user-context": token()})

    assert response.status_code == 401


async def test_strict_identity_still_judges_the_identity_a_token_carries(
    receiver, monkeypatch: pytest.MonkeyPatch
) -> None:
    call, token = receiver
    monkeypatch.setattr(settings, "GUARDRAILS_STRICT_IDENTITY", True)

    response = await call(mode="token", headers={"x-user-context": token(role="not-a-role")})

    assert response.status_code == 401
    assert response.json() == {"error": "Invalid forwarded identity"}
