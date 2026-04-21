"""
Shared pytest fixtures for every agents/ test module.

Policy:
- Never mock the database. Tests that touch DB use the `postgres_pool` fixture
  which provisions a real Postgres container via testcontainers.
- Never call a real LLM. Tests use the `fake_chat_client` fixture.
- Session-scoped containers so the test suite is fast; per-test clean slate
  via the `clean_db` fixture (truncates between tests, keeps schema).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from typing import Any

import asyncpg
import pytest
import pytest_asyncio
from testcontainers.postgres import PostgresContainer


REPO_ROOT = Path(__file__).resolve().parents[3]
INIT_SQL = REPO_ROOT / "docker" / "postgres" / "init.sql"


# ─────────────────────── Postgres fixture ───────────────────────

@pytest.fixture(scope="session")
def postgres_container() -> Generator[PostgresContainer, None, None]:
    """One Postgres container per test session — fast and isolated."""
    container = PostgresContainer("pgvector/pgvector:pg16", dbname="ecommerce_test")
    container.start()
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture(scope="session")
def database_url(postgres_container: PostgresContainer) -> str:
    url = postgres_container.get_connection_url()
    # testcontainers returns a psycopg driver URL; asyncpg wants plain postgresql://
    return url.replace("postgresql+psycopg2://", "postgresql://")


@pytest_asyncio.fixture(scope="session")
async def _schema_applied(database_url: str) -> None:
    """Apply the production schema from docker/postgres/init.sql once per session."""
    sql = INIT_SQL.read_text()
    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


@pytest_asyncio.fixture
async def postgres_pool(
    database_url: str,
    _schema_applied: None,
) -> AsyncGenerator[asyncpg.Pool, None]:
    """Asyncpg pool against the shared test container."""
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
    try:
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture
async def clean_db(postgres_pool: asyncpg.Pool) -> AsyncGenerator[asyncpg.Pool, None]:
    """Truncate all data tables before the test; schema stays. Use when the test mutates DB."""
    async with postgres_pool.acquire() as conn:
        tables = await conn.fetch(
            """
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public'
              AND tablename NOT LIKE 'pg_%'
            """
        )
        if tables:
            names = ", ".join(f'"{row["tablename"]}"' for row in tables)
            await conn.execute(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE")
    yield postgres_pool


# ─────────────────────── Fake LLM fixtures ──────────────────────

class FakeChatClient:
    """
    Deterministic stand-in for the MAF ChatClient. Queue canned responses and
    assert on observed inputs. No real LLM traffic.
    """

    def __init__(self) -> None:
        self._responses: list[str] = []
        self.call_count: int = 0
        self.received_prompts: list[list[dict[str, Any]]] = []

    def enqueue(self, *responses: str) -> "FakeChatClient":
        self._responses.extend(responses)
        return self

    async def complete(self, messages: list[dict[str, Any]], **_kwargs: Any) -> str:
        self.call_count += 1
        self.received_prompts.append(messages)
        if not self._responses:
            raise RuntimeError(
                "FakeChatClient has no enqueued responses. "
                "Call enqueue(...) before invoking."
            )
        return self._responses.pop(0)


@pytest.fixture
def fake_chat_client() -> FakeChatClient:
    return FakeChatClient()


# ─────────────────────── Canary fixture ─────────────────────────

@pytest.fixture
def sample_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """
    A minimal set of env vars every service needs. Use monkeypatch to set more per test.
    Keeps tests from accidentally reading the developer's real .env.
    """
    env = {
        "LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": "test-key",
        "LLM_MODEL": "gpt-4.1",
        "JWT_SECRET": "test-secret-" + "0" * 48,
        "AGENT_SHARED_SECRET": "test-agent-secret",
        "OTEL_ENABLED": "false",
        "DATABASE_URL": os.environ.get("DATABASE_URL", "postgresql://test:test@localhost/test"),
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return env
