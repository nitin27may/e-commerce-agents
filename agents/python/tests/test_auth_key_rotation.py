"""Signing-key rotation against a real Postgres (``clean_db``): overlap, retirement, and the migration's guard."""

from __future__ import annotations

from types import SimpleNamespace

import asyncpg
import jwt as pyjwt
import pytest
from joserfc.jwk import RSAKey

from auth_server import cli, keys
from auth_server.token import AccessTokenGenerator
from shared.config import settings
from tests.conftest import POSTGRES_DIR

pytestmark = pytest.mark.integration

ISSUER = "http://test-auth-server"


@pytest.fixture(autouse=True)
def _reset_key_cache():
    keys.reset_cache_for_tests()
    yield
    keys.reset_cache_for_tests()


def _mint(generator: AccessTokenGenerator) -> str:
    """A real access token from the auth-server's own generator."""
    client = SimpleNamespace(get_client_id=lambda: "svc")
    return generator.access_token_generator(client, "client_credentials", None, "api:chat")


def _validate(token: str, jwks: dict) -> dict:
    """Validate the way a resource server does: pick the JWKS entry by the token's kid."""
    kid = pyjwt.get_unverified_header(token)["kid"]
    entry = next((k for k in jwks["keys"] if k["kid"] == kid), None)
    if entry is None:
        raise pyjwt.InvalidTokenError(f"no published key for kid {kid}")
    public = pyjwt.PyJWK.from_dict(entry).key
    return pyjwt.decode(token, public, algorithms=["RS256"], options={"verify_aud": False})


async def test_rotate_keeps_old_key_published_and_signs_with_the_new_one(clean_db: asyncpg.Pool) -> None:
    old_kid, old_key = await keys.ensure_active_key(clean_db)
    generator = AccessTokenGenerator(ISSUER, old_kid, old_key)
    old_token = _mint(generator)

    result = await keys.rotate(clean_db)
    generator.use_signing_key(*await keys.ensure_active_key(clean_db))
    new_token = _mint(generator)

    assert result.old_kid == old_kid and result.new_kid != old_kid
    assert pyjwt.get_unverified_header(new_token)["kid"] == result.new_kid
    assert pyjwt.get_unverified_header(old_token)["kid"] == old_kid

    jwks = await keys.get_jwks(clean_db)
    assert {k["kid"] for k in jwks["keys"]} == {old_kid, result.new_kid}
    assert all("d" not in k for k in jwks["keys"])
    assert _validate(old_token, jwks)["client_id"] == "svc"  # still valid during the overlap
    assert _validate(new_token, jwks)["client_id"] == "svc"

    active = await clean_db.fetch("SELECT kid FROM oauth_signing_keys WHERE is_active")
    assert [r["kid"] for r in active] == [result.new_kid]


async def test_retire_window_defaults_to_token_ttl_plus_skew(clean_db: asyncpg.Pool, monkeypatch) -> None:
    monkeypatch.setattr(settings, "AUTH_KEY_RETIRE_AFTER_SECONDS", 0)
    monkeypatch.setattr(settings, "AUTH_ACCESS_TOKEN_TTL", 1000)
    monkeypatch.setattr(settings, "AUTH_KEY_CLOCK_SKEW_SECONDS", 200)
    assert keys.retire_after_seconds() == 1200
    monkeypatch.setattr(settings, "AUTH_KEY_RETIRE_AFTER_SECONDS", 90)
    assert keys.retire_after_seconds() == 90

    old_kid, _ = await keys.ensure_active_key(clean_db)
    await keys.rotate(clean_db)

    remaining = await clean_db.fetchval(
        "SELECT EXTRACT(EPOCH FROM not_after - now()) FROM oauth_signing_keys WHERE kid = $1", old_kid
    )
    assert 80 < float(remaining) <= 90


async def test_old_token_is_refused_once_the_old_key_is_retired(clean_db: asyncpg.Pool) -> None:
    old_kid, old_key = await keys.ensure_active_key(clean_db)
    old_token = _mint(AccessTokenGenerator(ISSUER, old_kid, old_key))
    result = await keys.rotate(clean_db, retire_after=3600)

    assert await keys.retire_expired(clean_db) == []  # inside its window: nothing to retire
    assert _validate(old_token, await keys.get_jwks(clean_db))

    # Move the deadline into the past rather than sleeping through it.
    await clean_db.execute(
        "UPDATE oauth_signing_keys SET not_after = now() - interval '1 second' WHERE kid = $1", old_kid
    )

    # Left out of the JWKS by the clock alone, before any sweep runs.
    jwks = await keys.get_jwks(clean_db)
    assert [k["kid"] for k in jwks["keys"]] == [result.new_kid]
    with pytest.raises(pyjwt.InvalidTokenError):
        _validate(old_token, jwks)

    assert await keys.retire_expired(clean_db) == [old_kid]
    assert await clean_db.fetchval("SELECT retired_at IS NOT NULL FROM oauth_signing_keys WHERE kid = $1", old_kid)
    assert await keys.retire_expired(clean_db) == []  # idempotent


async def test_refresh_active_key_notices_a_rotation_from_another_process(clean_db: asyncpg.Pool) -> None:
    old_kid, _ = await keys.ensure_active_key(clean_db)
    await keys.rotate(clean_db)
    # What a different process looks like: its cache still holds the pre-rotation key.
    keys._active_kid, keys._active_key = old_kid, RSAKey.generate_key(2048, private=True)

    kid, _key, changed = await keys.refresh_active_key(clean_db)

    assert changed and kid != old_kid


async def test_two_rotations_leave_one_signer_and_three_published_keys(clean_db: asyncpg.Pool) -> None:
    first, _ = await keys.ensure_active_key(clean_db)
    second = (await keys.rotate(clean_db)).new_kid
    third = (await keys.rotate(clean_db)).new_kid

    jwks = await keys.get_jwks(clean_db)
    assert {k["kid"] for k in jwks["keys"]} == {first, second, third}
    assert await clean_db.fetchval("SELECT count(*) FROM oauth_signing_keys WHERE is_active") == 1
    verification = await keys.get_verification_keys(clean_db)
    assert len(verification.keys) == 3


async def test_rotate_rejects_a_non_positive_window(clean_db: asyncpg.Pool) -> None:
    await keys.ensure_active_key(clean_db)
    with pytest.raises(ValueError):
        await keys.rotate(clean_db, retire_after=0)


async def test_rotate_on_an_empty_table_still_yields_one_active_key(clean_db: asyncpg.Pool) -> None:
    result = await keys.rotate(clean_db)
    assert result.old_kid is None
    assert await clean_db.fetchval("SELECT count(*) FROM oauth_signing_keys WHERE is_active") == 1


async def test_a_key_demoted_by_hand_keeps_the_pre_rotation_meaning(clean_db: asyncpg.Pool) -> None:
    """Rows written before migration 0014 have no not_after: published until retired_at is set."""
    kid, _ = await keys.ensure_active_key(clean_db)
    await clean_db.execute("UPDATE oauth_signing_keys SET is_active = FALSE WHERE kid = $1", kid)
    assert [k["kid"] for k in (await keys.get_jwks(clean_db))["keys"]] == [kid]
    await clean_db.execute("UPDATE oauth_signing_keys SET retired_at = now() WHERE kid = $1", kid)
    assert (await keys.get_jwks(clean_db))["keys"] == []


async def test_database_refuses_a_second_active_key(clean_db: asyncpg.Pool) -> None:
    await keys.ensure_active_key(clean_db)
    with pytest.raises(asyncpg.UniqueViolationError):
        await clean_db.execute(
            """INSERT INTO oauth_signing_keys (kid, public_jwk, private_pem_enc, is_active)
               VALUES ('rogue', '{}'::jsonb, '\\x00', TRUE)"""
        )


async def test_migration_0014_is_idempotent(clean_db: asyncpg.Pool) -> None:
    sql = next((POSTGRES_DIR / "migrations").glob("0014_*.sql")).read_text()
    await keys.ensure_active_key(clean_db)
    await clean_db.execute(sql)  # already applied by the fixture; a re-run must be harmless
    await clean_db.execute(sql)

    columns = {
        r["column_name"]
        for r in await clean_db.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'oauth_signing_keys'"
        )
    }
    assert {"not_after", "retired_at", "is_active"} <= columns


async def test_cli_rotates_and_sweeps(clean_db: asyncpg.Pool, database_url: str, capsys) -> None:
    first, _ = await keys.ensure_active_key(clean_db)

    assert await cli.run(database_url) == 0
    out = capsys.readouterr().out
    assert "new active kid=" in out and f"previous kid={first}" in out
    assert await clean_db.fetchval("SELECT count(*) FROM oauth_signing_keys WHERE is_active") == 1

    await clean_db.execute("UPDATE oauth_signing_keys SET not_after = now() - interval '1 second' WHERE NOT is_active")
    assert await cli.run(database_url, sweep_only=True) == 0
    assert f"retired kid={first}" in capsys.readouterr().out
    assert [k["kid"] for k in (await keys.get_jwks(clean_db))["keys"]] != []
    assert first not in {k["kid"] for k in (await keys.get_jwks(clean_db))["keys"]}


def test_a_token_minted_before_a_switch_keeps_its_own_kid() -> None:
    first, second = RSAKey.generate_key(2048, private=True), RSAKey.generate_key(2048, private=True)
    first.ensure_kid()
    second.ensure_kid()
    generator = AccessTokenGenerator(ISSUER, first.kid, first)
    before = _mint(generator)
    generator.use_signing_key(second.kid, second)
    after = _mint(generator)

    assert pyjwt.get_unverified_header(before)["kid"] == first.kid
    assert pyjwt.get_unverified_header(after)["kid"] == second.kid
