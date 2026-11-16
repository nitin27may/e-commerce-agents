"""M-1 (#93) -- migration 0009 creates the merchant schema on a fresh database and on one already at the
previous version, and the constraints it declares actually hold."""

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
VERSION = "0009_merchant_proposals"
NEW_TABLES = ("proposals", "proposal_lines", "promotion_products")


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


def test_the_migration_file_exists() -> None:
    assert VERSION in [m.version for m in migrate_mod.discover(POSTGRES_DIR)]


async def test_a_fresh_database_gets_the_merchant_schema(throwaway_url: str) -> None:
    applied = await migrate_mod.migrate(throwaway_url, POSTGRES_DIR)

    assert VERSION in applied
    assert set(NEW_TABLES) <= await _tables(throwaway_url)
    assert await _has_column(throwaway_url, "products", "unit_cost")
    # Running it again applies nothing.
    assert await migrate_mod.migrate(throwaway_url, POSTGRES_DIR) == []


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
    assert not await _has_column(throwaway_url, "products", "unit_cost")

    # A product that predates the migration must survive it, with no cost.
    conn = await asyncpg.connect(throwaway_url)
    try:
        await conn.execute(
            "INSERT INTO products (name, description, category, price) VALUES ('Old', 'd', 'Books', 9.99)"
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
        row = await conn.fetchrow("SELECT name, price, unit_cost FROM products")
        assert row["name"] == "Old" and row["unit_cost"] is None
    finally:
        await conn.close()
    assert await migrate_mod.migrate(throwaway_url, schema) == []


# ── the constraints hold ──────────────────────────────────────────────────────


async def _seller_and_product(pool: asyncpg.Pool) -> tuple[uuid.UUID, uuid.UUID]:
    seller = await pool.fetchval(
        "INSERT INTO users (email, password_hash, name, role) VALUES ($1, 'x', 'S', 'seller') RETURNING id",
        f"s-{uuid.uuid4().hex[:8]}@example.com",
    )
    product = await pool.fetchval(
        "INSERT INTO products (name, description, category, price, seller_id) "
        "VALUES ($1, 'd', 'Books', 10, $2) RETURNING id",
        f"P-{uuid.uuid4().hex[:8]}",
        seller,
    )
    return seller, product


async def test_enumerated_values_and_numbers_are_checked(clean_db: asyncpg.Pool) -> None:
    seller, product = await _seller_and_product(clean_db)

    with pytest.raises(asyncpg.CheckViolationError):
        await clean_db.execute("UPDATE products SET unit_cost = -1 WHERE id = $1", product)
    with pytest.raises(asyncpg.CheckViolationError):
        await clean_db.execute("INSERT INTO proposals (seller_id, kind) VALUES ($1, 'bogus')", seller)
    with pytest.raises(asyncpg.CheckViolationError):
        await clean_db.execute("INSERT INTO proposals (seller_id, kind, trigger) VALUES ($1, 'markdown', 'x')", seller)

    proposal = await clean_db.fetchrow(
        "INSERT INTO proposals (seller_id, kind) VALUES ($1, 'markdown') RETURNING *", seller
    )
    assert proposal["status"] == "pending_review" and proposal["trigger"] == "manual"

    line = await clean_db.fetchrow(
        """INSERT INTO proposal_lines (proposal_id, product_id, field, current_value, proposed_value)
           VALUES ($1, $2, 'price', 10, 9) RETURNING *""",
        proposal["id"],
        product,
    )
    assert line["status"] == "proposed" and line["final_value"] is None
    for bad in (
        "INSERT INTO proposal_lines (proposal_id, product_id, field, proposed_value) VALUES ($1, $2, 'colour', 1)",
        "INSERT INTO proposal_lines (proposal_id, product_id, field, proposed_value) VALUES ($1, $2, 'price', -1)",
    ):
        with pytest.raises(asyncpg.CheckViolationError):
            await clean_db.execute(bad, proposal["id"], product)
    with pytest.raises(asyncpg.CheckViolationError):
        await clean_db.execute("UPDATE proposal_lines SET status = 'bogus' WHERE id = $1", line["id"])
    # blocked is a per-line status.
    await clean_db.execute("UPDATE proposal_lines SET status = 'blocked' WHERE id = $1", line["id"])


async def test_one_line_per_product_and_field_per_batch(clean_db: asyncpg.Pool) -> None:
    seller, product = await _seller_and_product(clean_db)
    proposal = await clean_db.fetchval(
        "INSERT INTO proposals (seller_id, kind) VALUES ($1, 'mixed') RETURNING id", seller
    )
    insert = "INSERT INTO proposal_lines (proposal_id, product_id, field, proposed_value) VALUES ($1, $2, $3, 5)"
    await clean_db.execute(insert, proposal, product, "price")
    await clean_db.execute(insert, proposal, product, "restock_quantity")
    with pytest.raises(asyncpg.UniqueViolationError):
        await clean_db.execute(insert, proposal, product, "price")


async def test_foreign_keys_and_cascades(clean_db: asyncpg.Pool) -> None:
    seller, product = await _seller_and_product(clean_db)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await clean_db.execute("INSERT INTO proposals (seller_id, kind) VALUES ($1, 'markdown')", uuid.uuid4())

    proposal = await clean_db.fetchval(
        "INSERT INTO proposals (seller_id, kind) VALUES ($1, 'markdown') RETURNING id", seller
    )
    await clean_db.execute(
        "INSERT INTO proposal_lines (proposal_id, product_id, field, proposed_value) VALUES ($1, $2, 'price', 5)",
        proposal,
        product,
    )
    promo = await clean_db.fetchval(
        "INSERT INTO promotions (name, type, rules, start_date, end_date) "
        "VALUES ('p', 'flash_sale', '{}'::jsonb, now(), now() + interval '1 day') RETURNING id"
    )
    await clean_db.execute("INSERT INTO promotion_products VALUES ($1, $2)", promo, product)
    with pytest.raises(asyncpg.UniqueViolationError):
        await clean_db.execute("INSERT INTO promotion_products VALUES ($1, $2)", promo, product)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await clean_db.execute("INSERT INTO promotion_products VALUES ($1, $2)", uuid.uuid4(), product)

    # Deleting the proposal takes its lines; deleting the promotion or product takes the link.
    await clean_db.execute("DELETE FROM proposals WHERE id = $1", proposal)
    assert await clean_db.fetchval("SELECT count(*) FROM proposal_lines") == 0
    await clean_db.execute("DELETE FROM promotions WHERE id = $1", promo)
    assert await clean_db.fetchval("SELECT count(*) FROM promotion_products") == 0
