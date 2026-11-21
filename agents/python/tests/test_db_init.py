"""Azure P1 -- db_init, ``seed --if-empty`` and SEED_STAFF_PASSWORD.

The seeder is deterministic (``random.seed(42)``) and replay fixtures key on its output, so the staff
password override must change only the staff password hashes and nothing else.
"""

from __future__ import annotations

import hashlib
import random
import sys
from pathlib import Path

import asyncpg
import bcrypt
import pytest

# scripts/ lives at the repo root, outside the agents/python project.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts import db_init  # noqa: E402
from scripts import seed as seed_mod  # noqa: E402

from shared.demo_tour import TOUR_EMAIL  # noqa: E402


async def _no_embeddings(conn: asyncpg.Connection) -> int:
    return 0


@pytest.fixture
def seeder(database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(seed_mod, "DATABASE_URL", database_url)
    monkeypatch.setattr(seed_mod, "seed_product_embeddings", _no_embeddings)
    monkeypatch.delenv("SEED_STAFF_PASSWORD", raising=False)
    monkeypatch.delenv("SEED_MODE", raising=False)


async def _seed(*, if_empty: bool = False) -> None:
    random.seed(42)
    await seed_mod.main(if_empty=if_empty)


async def _add_marker(pool: asyncpg.Pool) -> None:
    await pool.execute(
        "INSERT INTO users (email, password_hash, name, role) VALUES ('marker@x.test', 'h', 'M', 'customer')"
    )


async def _digest(pool: asyncpg.Pool) -> str:
    """Digest of seeded rows, excluding bcrypt hashes (salted, so never byte-stable) and timestamps."""
    h = hashlib.sha256()
    async with pool.acquire() as conn:
        for sql in (
            "SELECT email, name, role, loyalty_tier, total_spend FROM users ORDER BY email",
            "SELECT name, category, brand, price, rating FROM products ORDER BY name",
            "SELECT o.status, o.total, o.discount_amount, u.email FROM orders o JOIN users u ON u.id = o.user_id "
            "ORDER BY u.email, o.total, o.status",
            "SELECT r.rating, r.title, u.email FROM reviews r JOIN users u ON u.id = r.user_id "
            "ORDER BY u.email, r.title, r.rating",
        ):
            for row in await conn.fetch(sql):
                h.update(repr(tuple(row)).encode())
    return h.hexdigest()


async def _hashes(pool: asyncpg.Pool) -> dict[str, str]:
    rows = await pool.fetch("SELECT email, role, password_hash FROM users")
    return {r["email"]: (r["role"], r["password_hash"]) for r in rows}  # type: ignore[misc]


def _matches(password: str, stored: str) -> bool:
    return bcrypt.checkpw(password.encode(), stored.encode())


@pytest.mark.asyncio
async def test_if_empty_seeds_an_empty_database(clean_db: asyncpg.Pool, seeder: None) -> None:
    await _seed(if_empty=True)
    assert await clean_db.fetchval("SELECT COUNT(*) FROM users") > 0


@pytest.mark.asyncio
async def test_if_empty_skips_a_seeded_database_and_clears_nothing(clean_db: asyncpg.Pool, seeder: None) -> None:
    await _seed()
    await _add_marker(clean_db)
    before = await clean_db.fetchval("SELECT COUNT(*) FROM users")

    await _seed(if_empty=True)

    assert await clean_db.fetchval("SELECT COUNT(*) FROM users") == before
    assert await clean_db.fetchval("SELECT 1 FROM users WHERE email = 'marker@x.test'") == 1


@pytest.mark.asyncio
async def test_seed_mode_env_is_equivalent(
    clean_db: asyncpg.Pool, seeder: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed()
    await _add_marker(clean_db)
    monkeypatch.setenv("SEED_MODE", "if-empty")
    await _seed()
    assert await clean_db.fetchval("SELECT 1 FROM users WHERE email = 'marker@x.test'") == 1


@pytest.mark.asyncio
async def test_staff_password_applies_to_staff_only_and_leaves_other_data_identical(
    clean_db: asyncpg.Pool, seeder: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed()
    baseline_digest = await _digest(clean_db)
    default_hashes = await _hashes(clean_db)
    assert _matches("admin123", default_hashes["admin.demo@gmail.com"][1])

    monkeypatch.setenv("SEED_STAFF_PASSWORD", "Chang3d-Staff-Pw")
    await _seed()

    assert await _digest(clean_db) == baseline_digest
    hashes = await _hashes(clean_db)
    staff = {e for e, (role, _) in hashes.items() if role in {"admin", "support", "seller"}}
    assert {"admin.demo@gmail.com", "seller.demo@gmail.com", "seller2.demo@gmail.com", seed_mod.SUPPORT_EMAIL} <= staff
    for email, (role, stored) in hashes.items():
        if email in staff:
            assert _matches("Chang3d-Staff-Pw", stored), email
        elif role == "customer" and email != TOUR_EMAIL:
            assert _matches("customer123", stored), email


@pytest.mark.asyncio
async def test_unset_staff_password_keeps_published_demo_passwords(clean_db: asyncpg.Pool, seeder: None) -> None:
    await _seed()
    hashes = await _hashes(clean_db)
    assert _matches("seller123", hashes["seller.demo@gmail.com"][1])
    assert _matches("support123", hashes[seed_mod.SUPPORT_EMAIL][1])


@pytest.mark.asyncio
async def test_db_init_runs_steps_in_order_and_is_idempotent(
    clean_db: asyncpg.Pool, database_url: str, seeder: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []
    real = dict(db_init.STEPS)

    def tracking(name: str):  # noqa: ANN202
        async def step(dsn: str) -> None:
            order.append(name)
            await real[name](dsn)

        return step

    monkeypatch.setattr(db_init, "STEPS", tuple((n, tracking(n)) for n, _ in db_init.STEPS))
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    from shared.config import settings

    monkeypatch.setattr(settings, "LLM_PROVIDER", "replay", raising=False)

    await db_init.run(database_url)
    assert order == ["migrate", "seed", "embeddings", "analyze"]
    users = await clean_db.fetchval("SELECT COUNT(*) FROM users")
    assert users > 0

    # A second run seeds nothing and clears nothing.
    await _add_marker(clean_db)
    await db_init.run(database_url)
    assert await clean_db.fetchval("SELECT COUNT(*) FROM users") == users + 1


@pytest.mark.asyncio
async def test_db_init_skips_embeddings_without_a_provider(
    clean_db: asyncpg.Pool, database_url: str, seeder: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[str] = []
    from scripts import generate_embeddings

    async def boom(*_: object, **__: object) -> None:
        called.append("generate")

    monkeypatch.setattr(generate_embeddings, "main", boom)
    monkeypatch.setattr("shared.embedding_config.embedding_provider", lambda: "replay")
    await db_init.step_migrate(database_url)
    await db_init.step_embeddings(database_url)
    assert called == []


def test_db_init_main_exits_nonzero_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing(dsn: str) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(db_init, "STEPS", (("migrate", failing),))
    assert db_init.main() == 1
