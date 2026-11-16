"""D-3 -- migration 0006 creates the approvals foundation on a fresh database and on one already at 0005."""

from __future__ import annotations

import shutil
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from tests.conftest import POSTGRES_DIR, _load_migrate

pytestmark = pytest.mark.integration

migrate_mod = _load_migrate()
VERSION = "0006_approvals_foundation"
NEW_TABLES = ("approvals", "approval_events", "outbox", "notifications")
# Tables 0006 itself must not create: they belong to later migrations (#93 merchant in 0009, #94 autonomy in 0012). A
# fresh database has them by now, so this checks the migration file, not the final schema.
LATER_TABLES = (
    "agent_decisions",
    "store_credit_ledger",
)


@pytest_asyncio.fixture
async def throwaway_url(database_url: str) -> AsyncGenerator[str, None]:
    name = f"mig_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(database_url)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    yield database_url.rsplit("/", 1)[0] + f"/{name}"
    admin = await asyncpg.connect(database_url)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


async def _tables(url: str) -> set[str]:
    conn = await asyncpg.connect(url)
    try:
        rows = await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        return {r["tablename"] for r in rows}
    finally:
        await conn.close()


async def _has_column(url: str, table: str, column: str) -> bool:
    conn = await asyncpg.connect(url)
    try:
        return bool(
            await conn.fetchval(
                "SELECT 1 FROM information_schema.columns WHERE table_name = $1 AND column_name = $2", table, column
            )
        )
    finally:
        await conn.close()


def test_the_migration_file_exists_and_is_numbered_after_the_last_applied_one() -> None:
    versions = [m.version for m in migrate_mod.discover(POSTGRES_DIR)]
    assert VERSION in versions
    assert versions.index(VERSION) == versions.index("0005_refresh_family_expiry") + 1


async def test_a_fresh_database_gets_the_foundation(throwaway_url: str) -> None:
    applied = await migrate_mod.migrate(throwaway_url, POSTGRES_DIR)

    assert VERSION in applied
    tables = await _tables(throwaway_url)
    assert set(NEW_TABLES) <= tables
    sql = (POSTGRES_DIR / "migrations" / f"{VERSION}.sql").read_text(encoding="utf-8")
    assert not [t for t in LATER_TABLES if f"CREATE TABLE IF NOT EXISTS {t}" in sql or f"CREATE TABLE {t}" in sql]
    assert await _has_column(throwaway_url, "tool_approval_requests", "approval_id")
    assert await _has_column(throwaway_url, "hitl_requests", "approval_id")
    assert await _has_column(throwaway_url, "approvals", "claimed_at")


async def test_a_database_already_at_the_previous_version_upgrades_in_place(throwaway_url: str, tmp_path: Path) -> None:
    schema = tmp_path / "postgres"
    shutil.copytree(POSTGRES_DIR, schema)
    held_back: dict[str, Path] = {}
    for path in sorted((schema / "migrations").glob("*.sql")):
        if path.name >= f"{VERSION}.sql":
            held_back[path.name] = tmp_path / path.name
            path.rename(held_back[path.name])

    previous = await migrate_mod.migrate(throwaway_url, schema)
    assert VERSION not in previous
    assert not set(NEW_TABLES) & await _tables(throwaway_url)

    # Rows that predate the migration must survive it untouched.
    conn = await asyncpg.connect(throwaway_url)
    try:
        await conn.execute(
            """INSERT INTO tool_approval_requests (user_email, agent_name, tool_name, tool_input)
               VALUES ('old@example.com', 'order-management', 'cancel_order', '{}'::jsonb)"""
        )
    finally:
        await conn.close()

    for name, aside in held_back.items():
        shutil.copy(aside, schema / "migrations" / name)
    upgraded = await migrate_mod.migrate(throwaway_url, schema)

    assert upgraded[0] == VERSION
    assert set(NEW_TABLES) <= await _tables(throwaway_url)
    conn = await asyncpg.connect(throwaway_url)
    try:
        row = await conn.fetchrow("SELECT user_email, approval_id FROM tool_approval_requests")
        assert row["user_email"] == "old@example.com" and row["approval_id"] is None
    finally:
        await conn.close()
    # And running it again applies nothing.
    assert await migrate_mod.migrate(throwaway_url, schema) == []


async def test_the_linking_columns_reference_approvals(clean_db: asyncpg.Pool) -> None:
    approval_id = await clean_db.fetchval(
        "INSERT INTO approvals (source, kind, requester_email) "
        "VALUES ('tool', 'cancel_order', 'a@example.com') RETURNING id"
    )
    tool_row = await clean_db.fetchval(
        """INSERT INTO tool_approval_requests (user_email, agent_name, tool_name, tool_input, approval_id)
           VALUES ('a@example.com', 'order-management', 'cancel_order', '{}'::jsonb, $1) RETURNING id""",
        approval_id,
    )
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await clean_db.execute(
            "UPDATE tool_approval_requests SET approval_id = $2 WHERE id = $1", tool_row, uuid.uuid4()
        )
    # Deleting an approval without history leaves the execution record and clears its link.
    await clean_db.execute("DELETE FROM approvals WHERE id = $1", approval_id)
    assert await clean_db.fetchval("SELECT approval_id FROM tool_approval_requests WHERE id = $1", tool_row) is None
