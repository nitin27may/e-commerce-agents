"""RotationAwareJWKClient: an unknown ``kid`` refetches the JWKS once, rate-limited, so a rotation needs no restart.

No network and no DB: ``fetch_data`` is replaced with a counter over a mutable JWKS document, and the clock is
injected so the cooldown is exercised without sleeping.
"""

from __future__ import annotations

import time

import jwt as pyjwt
import pytest
from joserfc.jwk import RSAKey
from jwt.exceptions import PyJWKClientError

from shared.config import settings
from shared.oauth.jwks_client import UNKNOWN_KID_REFRESH_COOLDOWN, RotationAwareJWKClient
from shared.oauth.verifier import RS256Verifier

ISSUER = "http://test-auth-server"
AUDIENCE = "ecommerce-orchestrator"


class _Jwks:
    """The auth-server's published document, mutable, counting fetches."""

    def __init__(self, *keys: RSAKey) -> None:
        self.keys = list(keys)
        self.fetches = 0

    def __call__(self) -> dict:
        self.fetches += 1
        return {"keys": [k.as_dict(private=False) for k in self.keys]}


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _key() -> RSAKey:
    key = RSAKey.generate_key(2048, private=True)
    key.ensure_kid()
    return key


def _token(key: RSAKey) -> str:
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": AUDIENCE, "exp": now + 600, "iat": now, "sub": "a@example.com", "scope": "api:chat"}
    return pyjwt.encode(claims, key.as_pem(private=True), algorithm="RS256", headers={"kid": key.kid})


@pytest.fixture
def old_key() -> RSAKey:
    return _key()


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def jwks(old_key: RSAKey) -> _Jwks:
    return _Jwks(old_key)


@pytest.fixture
def client(jwks: _Jwks, clock: _Clock, monkeypatch: pytest.MonkeyPatch) -> RotationAwareJWKClient:
    c = RotationAwareJWKClient("http://auth/.well-known/jwks.json", cache_keys=True, lifespan=900, clock=clock)
    monkeypatch.setattr(c, "fetch_data", jwks)
    return c


def test_known_kid_never_refetches(client: RotationAwareJWKClient, jwks: _Jwks, old_key: RSAKey) -> None:
    for _ in range(3):
        client.get_signing_key_from_jwt(_token(old_key))
    assert jwks.fetches == 1


def test_rotated_in_key_is_picked_up_without_a_restart(
    client: RotationAwareJWKClient, jwks: _Jwks, old_key: RSAKey
) -> None:
    client.get_signing_key_from_jwt(_token(old_key))  # warm the cache with the pre-rotation JWKS
    new_key = _key()
    jwks.keys.append(new_key)  # the auth-server rotated; the old key stays published

    found = client.get_signing_key_from_jwt(_token(new_key))

    assert found.key_id == new_key.kid
    assert jwks.fetches == 2  # one warm-up fetch, one refresh for the unknown kid
    # The old key still validates after the rotation.
    assert client.get_signing_key_from_jwt(_token(old_key)).key_id == old_key.kid


def test_unknown_kid_refresh_is_rate_limited(client: RotationAwareJWKClient, jwks: _Jwks, clock: _Clock) -> None:
    client.get_signing_keys()  # warm
    stranger = _key()  # never published

    for _ in range(5):
        with pytest.raises(PyJWKClientError):
            client.get_signing_key_from_jwt(_token(stranger))
    assert jwks.fetches == 2  # warm-up + exactly one refresh, not one per attempt

    clock.now += UNKNOWN_KID_REFRESH_COOLDOWN + 1
    with pytest.raises(PyJWKClientError):
        client.get_signing_key_from_jwt(_token(stranger))
    assert jwks.fetches == 3  # the window reopened


def test_a_key_that_arrives_inside_the_cooldown_is_found_after_it(
    client: RotationAwareJWKClient, jwks: _Jwks, clock: _Clock
) -> None:
    client.get_signing_keys()
    new_key = _key()
    with pytest.raises(PyJWKClientError):
        client.get_signing_key_from_jwt(_token(new_key))  # spends the refresh; auth-server has not rotated yet

    jwks.keys.append(new_key)
    with pytest.raises(PyJWKClientError):
        client.get_signing_key_from_jwt(_token(new_key))  # still cooling down, fails closed

    clock.now += UNKNOWN_KID_REFRESH_COOLDOWN + 1
    assert client.get_signing_key_from_jwt(_token(new_key)).key_id == new_key.kid


def test_rs256_verifier_validates_old_and_new_tokens_across_a_rotation(
    monkeypatch: pytest.MonkeyPatch, old_key: RSAKey, jwks: _Jwks
) -> None:
    monkeypatch.setattr(settings, "AUTH_SERVER_ISSUER", ISSUER)
    monkeypatch.setattr(settings, "AUTH_SERVER_JWKS_URL", f"{ISSUER}/.well-known/jwks.json")
    verifier = RS256Verifier()
    monkeypatch.setattr(verifier._jwks_client, "fetch_data", jwks)

    assert verifier.decode(_token(old_key), audience=AUDIENCE)["sub"] == "a@example.com"
    new_key = _key()
    jwks.keys.append(new_key)
    assert verifier.decode(_token(new_key), audience=AUDIENCE)["sub"] == "a@example.com"
    assert verifier.decode(_token(old_key), audience=AUDIENCE)["sub"] == "a@example.com"
