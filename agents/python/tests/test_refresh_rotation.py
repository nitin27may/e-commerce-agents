"""A-12 (#102) — refresh-token rotation with reuse detection, and signup validation.

Real Postgres (``clean_db``) through the real route handlers; only the rate-limit
dependency is switched off (it has its own tests in test_rate_limit_auth.py).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import asyncpg
import jwt
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import shared.db as shared_db
from orchestrator.routes import router
from shared.config import settings
from shared.jwt_utils import ALGORITHM, decode_token, hash_password

pytestmark = pytest.mark.integration

EMAIL = "rotate@example.com"
PASSWORD = "correct horse battery"


@pytest_asyncio.fixture(autouse=True)
async def _db(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> asyncpg.Pool:
    monkeypatch.setattr(shared_db, "_pool", clean_db)
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(settings, "AUTH_MODE", "local")
    monkeypatch.setattr(settings, "AUTH_REFRESH_ROTATION", True)
    await clean_db.execute(
        "INSERT INTO users (email, password_hash, name, role) VALUES ($1, $2, 'Rot Ate', 'customer')",
        EMAIL,
        hash_password(PASSWORD),
    )
    return clean_db


def _client() -> AsyncClient:
    app = FastAPI()
    app.include_router(router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _login(c: AsyncClient) -> dict:
    resp = await c.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _refresh(c: AsyncClient, token: str):
    return await c.post("/api/auth/refresh", json={"refresh_token": token})


# ─────────────────────────────── rotation ───────────────────────────────


async def test_login_records_a_token_with_a_jti(clean_db: asyncpg.Pool) -> None:
    async with _client() as c:
        body = await _login(c)
    jti = decode_token(body["refresh_token"])["jti"]
    row = await clean_db.fetchrow("SELECT user_email, revoked_at FROM refresh_tokens WHERE jti = $1", jti)
    assert row["user_email"] == EMAIL and row["revoked_at"] is None


async def test_refresh_rotates_and_marks_the_old_token_replaced(clean_db: asyncpg.Pool) -> None:
    async with _client() as c:
        first = (await _login(c))["refresh_token"]
        resp = await _refresh(c, first)
        assert resp.status_code == 200
        body = resp.json()
        assert body["access_token"] and body["refresh_token"] and body["refresh_token"] != first
        # The successor keeps working: the chain is not a one-shot.
        again = await _refresh(c, body["refresh_token"])
        assert again.status_code == 200

    old = await clean_db.fetchrow(
        "SELECT revoked_at, replaced_by, family_id FROM refresh_tokens WHERE jti = $1", decode_token(first)["jti"]
    )
    assert old["revoked_at"] is not None
    assert old["replaced_by"] == decode_token(body["refresh_token"])["jti"]
    assert await clean_db.fetchval("SELECT count(DISTINCT family_id) FROM refresh_tokens") == 1


async def test_reusing_a_rotated_token_revokes_the_whole_family(clean_db: asyncpg.Pool) -> None:
    async with _client() as c:
        first = (await _login(c))["refresh_token"]
        second = (await _refresh(c, first)).json()["refresh_token"]

        replay = await _refresh(c, first)  # the stolen copy
        assert replay.status_code == 401

        # The legitimate holder's successor is now dead too.
        assert (await _refresh(c, second)).status_code == 401
    assert await clean_db.fetchval("SELECT count(*) FROM refresh_tokens WHERE revoked_at IS NULL") == 0


async def test_other_families_survive_a_reuse(clean_db: asyncpg.Pool) -> None:
    async with _client() as c:
        phone = (await _login(c))["refresh_token"]
        laptop = (await _login(c))["refresh_token"]
        await _refresh(c, phone)
        assert (await _refresh(c, phone)).status_code == 401
        assert (await _refresh(c, laptop)).status_code == 200


async def test_simultaneous_refreshes_with_one_token_let_exactly_one_win() -> None:
    """Why the client must single-flight: two parallel uses of one token are indistinguishable from theft."""
    async with _client() as c:
        first = (await _login(c))["refresh_token"]
        a, b = await asyncio.gather(_refresh(c, first), _refresh(c, first))
    assert sorted([a.status_code, b.status_code]) == [200, 401]


async def test_token_without_a_jti_is_rejected() -> None:
    legacy = jwt.encode(
        {"sub": EMAIL, "type": "refresh", "exp": datetime.now(UTC) + timedelta(days=1)},
        settings.JWT_SECRET,
        algorithm=ALGORITHM,
    )
    async with _client() as c:
        assert (await _refresh(c, legacy)).status_code == 401


async def test_unknown_jti_and_wrong_user_are_rejected(clean_db: asyncpg.Pool) -> None:
    forged = jwt.encode(
        {
            "sub": EMAIL,
            "type": "refresh",
            "jti": "00000000-0000-0000-0000-000000000000",
            "exp": datetime.now(UTC) + timedelta(days=1),
        },
        settings.JWT_SECRET,
        algorithm=ALGORITHM,
    )
    async with _client() as c:
        assert (await _refresh(c, forged)).status_code == 401
        # A real row, but claimed under someone else's subject.
        real = (await _login(c))["refresh_token"]
        payload = decode_token(real)
        payload["sub"] = "someone.else@example.com"
        swapped = jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)
        assert (await _refresh(c, swapped)).status_code == 401


async def test_expired_row_is_rejected(clean_db: asyncpg.Pool) -> None:
    async with _client() as c:
        token = (await _login(c))["refresh_token"]
        await clean_db.execute("UPDATE refresh_tokens SET expires_at = NOW() - INTERVAL '1 second'")
        assert (await _refresh(c, token)).status_code == 401


async def test_access_token_cannot_be_used_as_a_refresh_token() -> None:
    async with _client() as c:
        access = (await _login(c))["access_token"]
        assert (await _refresh(c, access)).status_code == 401


async def test_deactivated_account_gets_403_and_the_token_is_not_consumed(clean_db: asyncpg.Pool) -> None:
    async with _client() as c:
        token = (await _login(c))["refresh_token"]
        await clean_db.execute("UPDATE users SET is_active = false WHERE email = $1", EMAIL)
        assert (await _refresh(c, token)).status_code == 403
    assert await clean_db.fetchval("SELECT count(*) FROM refresh_tokens WHERE revoked_at IS NULL") == 1


async def test_flag_off_keeps_the_old_contract(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "AUTH_REFRESH_ROTATION", False)
    async with _client() as c:
        token = (await _login(c))["refresh_token"]
        first = await _refresh(c, token)
        second = await _refresh(c, token)
    assert first.status_code == second.status_code == 200
    assert set(first.json()) == {"access_token"}
    assert await clean_db.fetchval("SELECT count(*) FROM refresh_tokens") == 0


# ───────────────────────────── signup validation ─────────────────────────────


async def _signup(c: AsyncClient, **over):
    body = {"email": "new.user@example.com", "password": "longenough1", "name": "New User"} | over
    return await c.post("/api/auth/signup", json=body)


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"email": "not-an-email"}, "Invalid email address"),
        ({"email": "a@b"}, "Invalid email address"),
        ({"email": "two words@example.com"}, "Invalid email address"),
        ({"email": ""}, "Invalid email address"),
        ({"password": "short7!"}, "Password must be between 8 and 72 bytes long"),
        ({"password": "a" * 73}, "Password must be between 8 and 72 bytes long"),
        # 37 two-byte characters: 37 chars but 74 bytes.
        ({"password": "é" * 37}, "Password must be between 8 and 72 bytes long"),
        ({"name": ""}, "Name must be between 1 and 100 characters"),
        ({"name": "   "}, "Name must be between 1 and 100 characters"),
        ({"name": "n" * 101}, "Name must be between 1 and 100 characters"),
    ],
)
async def test_signup_rejects_bad_input_with_400_and_a_message(over: dict, message: str) -> None:
    async with _client() as c:
        resp = await _signup(c, **over)
    assert resp.status_code == 400
    assert resp.json() == {"detail": message}


async def test_signup_accepts_the_boundaries_and_normalises() -> None:
    async with _client() as c:
        ok72 = await _signup(c, email="  Mixed.Case@Example.COM ", password="a" * 72, name="n" * 100)
        assert ok72.status_code == 200, ok72.text
        user = ok72.json()["user"]
        assert user["email"] == "mixed.case@example.com"
        assert decode_token(ok72.json()["refresh_token"])["jti"]
        # 8 bytes is the floor; login is case-insensitive on the email.
        assert (await _signup(c, email="b@example.com", password="12345678")).status_code == 200
        login = await c.post("/api/auth/login", json={"email": "MIXED.case@example.com", "password": "a" * 72})
        assert login.status_code == 200
        dup = await _signup(c, email="MIXED.CASE@example.com")
        assert dup.status_code == 409


async def test_overlong_password_on_login_is_a_401_not_a_500() -> None:
    async with _client() as c:
        resp = await c.post("/api/auth/login", json={"email": EMAIL, "password": "x" * 200})
    assert resp.status_code == 401


# ───────────────────── absolute family lifetime and pruning (migration 0005) ─────────────────────


async def test_successors_inherit_the_family_cap_and_never_outlive_it(clean_db: asyncpg.Pool) -> None:
    async with _client() as c:
        first = (await _login(c))["refresh_token"]
        await clean_db.execute("UPDATE refresh_tokens SET family_expires_at = NOW() + INTERVAL '2 hours'")
        resp = await _refresh(c, first)
        assert resp.status_code == 200

    rows = await clean_db.fetch("SELECT family_expires_at, expires_at, revoked_at FROM refresh_tokens")
    assert len(rows) == 2
    assert len({r["family_expires_at"] for r in rows}) == 1, "the cap is stamped once and inherited"
    newest = next(r for r in rows if r["revoked_at"] is None)  # the successor
    assert newest["expires_at"] <= newest["family_expires_at"], "a successor never outlives its family"


async def test_a_new_login_gets_the_configured_cap(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "AUTH_REFRESH_FAMILY_MAX_DAYS", 30)
    async with _client() as c:
        await _login(c)
    days = await clean_db.fetchval(
        "SELECT EXTRACT(EPOCH FROM (family_expires_at - created_at)) / 86400 FROM refresh_tokens"
    )
    assert 29.99 < days < 30.01


async def test_rotation_is_refused_once_the_family_cap_has_passed(clean_db: asyncpg.Pool) -> None:
    async with _client() as c:
        token = (await _login(c))["refresh_token"]
        # The token itself is still within its 7 days; only the session as a whole is over.
        await clean_db.execute("UPDATE refresh_tokens SET family_expires_at = NOW() - INTERVAL '1 second'")
        resp = await _refresh(c, token)
    assert resp.status_code == 401
    assert await clean_db.fetchval("SELECT count(*) FROM refresh_tokens") == 1, "no successor is minted"


async def test_rotation_prunes_dead_rows_but_not_live_or_recently_rotated_ones(clean_db: asyncpg.Pool) -> None:
    other = await clean_db.fetchval("SELECT gen_random_uuid()")
    dead_family = await clean_db.fetchval("SELECT gen_random_uuid()")

    async def insert(jti: str, family, *, expires: str, revoked: str | None) -> None:
        await clean_db.execute(
            f"""INSERT INTO refresh_tokens (jti, user_email, family_id, expires_at, revoked_at)
                VALUES ($1, $2, $3, {expires}, {revoked or "NULL"})""",
            jti,
            EMAIL,
            family,
        )

    await insert("expired-long-ago", other, expires="NOW() - INTERVAL '3 days'", revoked=None)
    await insert(
        "revoked-dead-family", dead_family, expires="NOW() + INTERVAL '3 days'", revoked="NOW() - INTERVAL '2 days'"
    )
    await insert("rotated-just-now", other, expires="NOW() + INTERVAL '3 days'", revoked="NOW() - INTERVAL '1 minute'")

    async with _client() as c:
        await _refresh(c, (await _login(c))["refresh_token"])

    jtis = {r["jti"] for r in await clean_db.fetch("SELECT jti FROM refresh_tokens")}
    assert "expired-long-ago" not in jtis
    assert "revoked-dead-family" not in jtis
    assert "rotated-just-now" in jtis, "a recently rotated token is kept: replaying it is how reuse is detected"
    assert len(jtis) == 3  # the kept one plus the login's token and its successor
