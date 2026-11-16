"""F4 — migration 0004 lower-cases user emails safely, and login still reaches every row.

A throwaway database is migrated to 0003, seeded with the mixed-case rows an older Python
signup would have left, then migrated to 0004 (the same file, so the real SQL is exercised).
The login half runs the real route handlers against the shared test database.
"""

from __future__ import annotations

import shutil
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import shared.db as shared_db
from orchestrator.routes import router
from shared.config import settings
from shared.jwt_utils import hash_password
from tests.conftest import POSTGRES_DIR, _load_migrate

pytestmark = pytest.mark.integration

migrate_mod = _load_migrate()
PASSWORD = "correct horse battery"


@pytest_asyncio.fixture
async def old_db(database_url: str, tmp_path: Path) -> AsyncGenerator[tuple[str, Path], None]:
    """A database migrated to 0003 only, plus a schema dir that still holds 0004 aside."""
    name = f"mig_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(database_url)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    url = database_url.rsplit("/", 1)[0] + f"/{name}"

    schema = tmp_path / "postgres"
    shutil.copytree(POSTGRES_DIR, schema)
    held_back = tmp_path / "0004.sql"
    next((schema / "migrations").glob("0004_*.sql")).rename(held_back)
    await migrate_mod.migrate(url, schema)
    shutil.copy(held_back, schema / "migrations" / "0004_lowercase_user_emails.sql")

    yield url, schema

    admin = await asyncpg.connect(database_url)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


async def _add_user(conn: asyncpg.Connection, email: str) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO users (email, password_hash, name, role) VALUES ($1, $2, 'T', 'customer') RETURNING id",
        email,
        hash_password(PASSWORD),
    )


async def test_it_lowercases_safe_rows_follows_references_and_leaves_collisions(
    old_db: tuple[str, Path],
) -> None:
    url, schema = old_db
    conn = await asyncpg.connect(url)
    try:
        alice_id = await _add_user(conn, "Alice@Example.com")
        run_id = await conn.fetchval(
            "INSERT INTO usage_logs (user_id, agent_name, input_summary, status) "
            "VALUES ($1, 'orchestrator', 'x', 'success') RETURNING id",
            alice_id,
        )
        await _add_user(conn, "already@example.com")
        await _add_user(conn, "Dup@Example.com")
        await _add_user(conn, "dup@example.com")
        await conn.execute(
            "INSERT INTO refresh_tokens (jti, user_email, family_id, expires_at) "
            "VALUES ('j1', 'Alice@Example.com', gen_random_uuid(), NOW() + interval '1 day')"
        )
        await conn.execute(
            "INSERT INTO hitl_requests (workflow_run_id, user_email, kind, status, payload) "
            "VALUES ('" + str(run_id) + "', 'Alice@Example.com', 'return_approval', 'pending', '{}'::jsonb)"
        )
        await conn.execute(
            "INSERT INTO tool_approval_requests (user_email, agent_name, tool_name, tool_input, approved_by) "
            "VALUES ('Alice@Example.com', 'a', 't', '{}'::jsonb, 'Alice@Example.com')"
        )
        await conn.execute(
            "INSERT INTO coupons (code, discount_type, discount_value, user_specific_email) "
            "VALUES ('C1', 'fixed', 5, 'Alice@Example.com')"
        )
    finally:
        await conn.close()

    assert await migrate_mod.migrate(url, schema) == ["0004_lowercase_user_emails"]

    conn = await asyncpg.connect(url)
    try:
        emails = sorted(r["email"] for r in await conn.fetch("SELECT email FROM users"))
        # Alice was lower-cased; the Dup pair collides, so both rows are exactly as they were.
        assert emails == ["Dup@Example.com", "alice@example.com", "already@example.com", "dup@example.com"]
        assert await conn.fetchval("SELECT user_email FROM refresh_tokens") == "alice@example.com"
        assert await conn.fetchval("SELECT user_email FROM hitl_requests") == "alice@example.com"
        row = await conn.fetchrow("SELECT user_email, approved_by FROM tool_approval_requests")
        assert (row["user_email"], row["approved_by"]) == ("alice@example.com", "alice@example.com")
        assert await conn.fetchval("SELECT user_specific_email FROM coupons") == "alice@example.com"
    finally:
        await conn.close()


# ─────────────────────────────── login after migration ───────────────────────────────


@pytest_asyncio.fixture
async def login_env(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> asyncpg.Pool:
    monkeypatch.setattr(shared_db, "_pool", clean_db)
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(settings, "AUTH_MODE", "local")
    monkeypatch.setattr(settings, "AUTH_REFRESH_ROTATION", False)
    return clean_db


def _client() -> AsyncClient:
    app = FastAPI()
    app.include_router(router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_a_mixed_case_user_can_log_in_however_they_type_it(login_env: asyncpg.Pool) -> None:
    # A row the migration skipped (or that pre-dates it): stored as typed at signup.
    await _add_user(login_env, "Alice@Example.com")
    async with _client() as c:
        for typed in ("Alice@Example.com", "alice@example.com", " ALICE@EXAMPLE.COM "):
            resp = await c.post("/api/auth/login", json={"email": typed, "password": PASSWORD})
            assert resp.status_code == 200, typed
            assert resp.json()["user"]["email"] == "Alice@Example.com"


async def test_a_colliding_pair_stays_reachable_by_exact_spelling(login_env: asyncpg.Pool) -> None:
    upper_id = await _add_user(login_env, "Dup@Example.com")
    lower_id = await _add_user(login_env, "dup@example.com")
    async with _client() as c:
        exact = await c.post("/api/auth/login", json={"email": "Dup@Example.com", "password": PASSWORD})
        lower = await c.post("/api/auth/login", json={"email": "dup@example.com", "password": PASSWORD})
        # A third spelling matches both ignoring case; it must still resolve (to the oldest row), not 500.
        other = await c.post("/api/auth/login", json={"email": "DUP@EXAMPLE.COM", "password": PASSWORD})
    assert exact.json()["user"]["id"] == str(upper_id)
    assert lower.json()["user"]["id"] == str(lower_id)
    assert other.status_code == 200


async def test_signup_refuses_an_address_that_differs_only_in_case_from_an_existing_row(
    login_env: asyncpg.Pool,
) -> None:
    await _add_user(login_env, "Taken@Example.com")
    async with _client() as c:
        resp = await c.post(
            "/api/auth/signup", json={"email": "taken@example.com", "password": PASSWORD, "name": "New"}
        )
    assert resp.status_code == 409
