"""scripts/migrate.py — baseline + numbered migrations (#102).

Each test gets its own throwaway database inside the session's Postgres
container, so "empty database" really is empty (``postgres_pool`` is already
migrated and shared).
"""

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
BASELINE = migrate_mod.BASELINE_VERSION


@pytest_asyncio.fixture
async def fresh_url(database_url: str) -> AsyncGenerator[str, None]:
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


@pytest.fixture
def schema_dir(tmp_path: Path) -> Path:
    """A writable copy of docker/postgres so tamper tests never touch the real files."""
    dest = tmp_path / "postgres"
    shutil.copytree(POSTGRES_DIR, dest)
    return dest


def _real_versions() -> list[str]:
    return [m.version for m in migrate_mod.discover(POSTGRES_DIR)]


async def _versions(url: str) -> list[str]:
    conn = await asyncpg.connect(url)
    try:
        return [r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations ORDER BY version")]
    finally:
        await conn.close()


async def _has_claimed_at(url: str) -> bool:
    conn = await asyncpg.connect(url)
    try:
        return bool(
            await conn.fetchval(
                "SELECT 1 FROM information_schema.columns WHERE table_name='hitl_requests' AND column_name='claimed_at'"
            )
        )
    finally:
        await conn.close()


async def test_empty_database_gets_baseline_and_every_migration(fresh_url: str) -> None:
    applied = await migrate_mod.migrate(fresh_url, POSTGRES_DIR)

    assert applied == _real_versions()
    assert applied[0] == BASELINE and len(applied) >= 2
    assert await _versions(fresh_url) == sorted(applied)
    assert await _has_claimed_at(fresh_url)


async def test_existing_volume_records_baseline_without_rerunning_it(fresh_url: str) -> None:
    # What Postgres' initdb.d mount leaves behind: init.sql applied, no schema_migrations.
    conn = await asyncpg.connect(fresh_url)
    try:
        await conn.execute((POSTGRES_DIR / "init.sql").read_text())
        await conn.execute("INSERT INTO warehouses (name, location, region) VALUES ('sentinel', 'x', 'east')")
    finally:
        await conn.close()

    applied = await migrate_mod.migrate(fresh_url, POSTGRES_DIR)

    # Re-running the baseline would have raised on CREATE TABLE products; the
    # baseline is recorded, not applied, and only the migrations ran.
    assert BASELINE not in applied
    assert applied == _real_versions()[1:]
    assert await _versions(fresh_url) == sorted(_real_versions())
    assert await _has_claimed_at(fresh_url)
    conn = await asyncpg.connect(fresh_url)
    try:
        assert await conn.fetchval("SELECT count(*) FROM warehouses WHERE name='sentinel'") == 1
    finally:
        await conn.close()


async def test_rerun_is_a_no_op(fresh_url: str) -> None:
    await migrate_mod.migrate(fresh_url, POSTGRES_DIR)
    before = await _versions(fresh_url)

    assert await migrate_mod.migrate(fresh_url, POSTGRES_DIR) == []
    assert await _versions(fresh_url) == before


async def test_a_modified_applied_migration_fails(fresh_url: str, schema_dir: Path) -> None:
    await migrate_mod.migrate(fresh_url, schema_dir)

    target = next((schema_dir / "migrations").glob("0002_*.sql"))
    target.write_text(target.read_text() + "\n-- tampered\n")

    with pytest.raises(migrate_mod.MigrationError, match="modified after it was applied"):
        await migrate_mod.migrate(fresh_url, schema_dir)


async def test_a_modified_baseline_fails(fresh_url: str, schema_dir: Path) -> None:
    await migrate_mod.migrate(fresh_url, schema_dir)
    init = schema_dir / "init.sql"
    init.write_text(init.read_text() + "\n-- tampered\n")

    with pytest.raises(migrate_mod.MigrationError, match=BASELINE):
        await migrate_mod.migrate(fresh_url, schema_dir)


async def test_a_failing_migration_rolls_back_and_is_not_recorded(fresh_url: str, schema_dir: Path) -> None:
    bad = schema_dir / "migrations" / "0099_broken.sql"
    bad.write_text("CREATE TABLE half_applied (id int);\nSELECT * FROM table_that_does_not_exist;\n")

    with pytest.raises(migrate_mod.MigrationError, match="0099_broken"):
        await migrate_mod.migrate(fresh_url, schema_dir)

    assert "0099_broken" not in await _versions(fresh_url)
    conn = await asyncpg.connect(fresh_url)
    try:
        assert await conn.fetchval("SELECT to_regclass('public.half_applied')") is None
    finally:
        await conn.close()
    # The migrations before it stay applied (one transaction each).
    assert await _has_claimed_at(fresh_url)


async def test_bad_filenames_are_rejected(schema_dir: Path) -> None:
    (schema_dir / "migrations" / "add-thing.sql").write_text("SELECT 1;")
    with pytest.raises(migrate_mod.MigrationError, match="bad migration filename"):
        migrate_mod.discover(schema_dir)


async def test_an_existing_volume_from_before_the_current_baseline_is_refused_and_nothing_is_recorded(
    fresh_url: str,
) -> None:
    conn = await asyncpg.connect(fresh_url)
    try:
        await conn.execute((POSTGRES_DIR / "init.sql").read_text())
        # An older init.sql: no products.search_vector, none of the newest tables.
        await conn.execute("DROP INDEX idx_products_search")
        await conn.execute("ALTER TABLE products DROP COLUMN search_vector")
        await conn.execute("DROP TABLE hitl_requests")
    finally:
        await conn.close()

    with pytest.raises(migrate_mod.MigrationError, match=r"products\.search_vector.*hitl_requests|dev\.sh --clean"):
        await migrate_mod.migrate(fresh_url, POSTGRES_DIR)

    conn = await asyncpg.connect(fresh_url)
    try:
        # Nothing recorded - not even the baseline - and no migration ran.
        assert await conn.fetchval("SELECT count(*) FROM schema_migrations") == 0
    finally:
        await conn.close()


async def test_the_baseline_markers_are_all_declared_in_init_sql() -> None:
    init = (POSTGRES_DIR / "init.sql").read_text()
    for table, column in migrate_mod.BASELINE_MARKERS:
        assert f"CREATE TABLE IF NOT EXISTS {table} " in init or f"CREATE TABLE {table} " in init, table
        if column:
            assert column in init, f"{table}.{column}"
