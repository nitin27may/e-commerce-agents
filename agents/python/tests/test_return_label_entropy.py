"""Return-label tokens carry 128 bits, legacy 48-bit ones expire, and unknown-token lookups are rate limited.

Real Postgres and Redis (testcontainers); the assertions are on what the public route answers.
"""

from __future__ import annotations

import pytest
import redis.asyncio as redis_asyncio

import shared.rate_limit as rate_limit_module
from shared.config import settings
from shared.return_label import is_legacy, is_well_formed, new_label_token
from tests.test_return_label_token import _get, _seed_return, pool  # noqa: F401

NEW_TOKEN = "0123456789abcdef0123456789abcdef"
LEGACY_TOKEN = "a1b2c3d4e5f6"


def test_new_tokens_are_128_bit_hex_and_distinct() -> None:
    tokens = {new_label_token() for _ in range(200)}
    assert len(tokens) == 200
    assert all(len(t) == 32 and is_well_formed(t) and not is_legacy(t) for t in tokens)


@pytest.mark.parametrize("token", ["", "a" * 11, "a" * 13, "a" * 31, "a" * 33, "A" * 32, "g" * 32, "%" * 32])
def test_only_12_or_32_lowercase_hex_is_well_formed(token: str) -> None:
    assert not is_well_formed(token)


@pytest.mark.asyncio
async def test_a_new_token_serves_its_label(pool) -> None:  # noqa: F811
    await _seed_return(pool, "carol@example.com", "Carol", NEW_TOKEN)
    resp = await _get(NEW_TOKEN)
    assert resp.status_code == 200
    assert NEW_TOKEN.upper().encode() in resp.content


@pytest.mark.asyncio
async def test_a_legacy_token_works_until_it_expires(pool) -> None:  # noqa: F811
    await _seed_return(pool, "dan@example.com", "Dan", LEGACY_TOKEN)
    assert (await _get(LEGACY_TOKEN)).status_code == 200

    await pool.execute("UPDATE returns SET created_at = NOW() - INTERVAL '31 days'")
    assert (await _get(LEGACY_TOKEN)).status_code == 404


@pytest.mark.asyncio
async def test_an_old_new_style_token_does_not_expire(pool) -> None:  # noqa: F811
    await _seed_return(pool, "eve@example.com", "Eve", NEW_TOKEN)
    await pool.execute("UPDATE returns SET created_at = NOW() - INTERVAL '400 days'")
    assert (await _get(NEW_TOKEN)).status_code == 200


@pytest.fixture
def limited(redis_client: redis_asyncio.Redis, monkeypatch: pytest.MonkeyPatch) -> redis_asyncio.Redis:
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_AUTH_MAX_REQUESTS", 3)
    monkeypatch.setattr(settings, "RATE_LIMIT_AUTH_WINDOW_SECONDS", 60.0)
    monkeypatch.setattr(rate_limit_module, "_redis_client", redis_client)
    return redis_client


@pytest.mark.asyncio
async def test_unknown_tokens_are_throttled_then_refused_up_front(pool, limited) -> None:  # noqa: F811
    for _ in range(3):
        assert (await _get("f" * 32)).status_code == 404
    blocked = await _get("e" * 32)
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


@pytest.mark.asyncio
async def test_a_blocked_address_cannot_use_a_valid_token_either(pool, limited) -> None:  # noqa: F811
    await _seed_return(pool, "fay@example.com", "Fay", NEW_TOKEN)
    for _ in range(3):
        await _get("d" * 32)
    assert (await _get(NEW_TOKEN)).status_code == 429


@pytest.mark.asyncio
async def test_valid_lookups_never_spend_the_budget(pool, limited) -> None:  # noqa: F811
    await _seed_return(pool, "gus@example.com", "Gus", NEW_TOKEN)
    for _ in range(8):
        assert (await _get(NEW_TOKEN)).status_code == 200


@pytest.mark.asyncio
async def test_malformed_tokens_spend_the_budget_too(pool, limited) -> None:  # noqa: F811
    for _ in range(3):
        assert (await _get("%25")).status_code == 404
    assert (await _get("%25")).status_code == 429
