"""M-1 (#93) -- the merchant seed step (unit costs, promotion links) changes no pre-existing seeded value,
and the guards' facts read correctly from what it seeds.

The seeder is deterministic (``random.seed(42)``) and the replay fixtures are keyed on the data it produces,
so the new step must be appended without touching the global random stream or any existing column. The
byte-for-byte check compares every table the seeder wrote, column by column, with and without the step;
columns that differ between two *identical* runs (clocks, database-generated ids) are measured first and
left out, so the comparison cannot hide anything the step does.
"""

from __future__ import annotations

import random
import sys
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from shared.merchant import ReasonCode, check_price_change, get_limits
from shared.merchant.facts import get_product_facts

# scripts/ lives at the repo root, outside the agents/python project.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts import seed as seed_mod  # noqa: E402

NEW_TABLES = {"promotion_products", "proposals", "proposal_lines"}
NEW_COLUMNS = {("products", "unit_cost")}
# A bigserial that TRUNCATE (without RESTART IDENTITY) does not rewind: it counts, so it differs per run.
SEQUENCE_COLUMNS = {("workflow_checkpoints", "seq")}
VOLATILE_TYPES = {"timestamp with time zone", "timestamp without time zone", "uuid", "bytea"}


async def _no_embeddings(conn: asyncpg.Connection) -> int:
    return 0


async def _no_merchant(conn: asyncpg.Connection) -> None:
    return None


_REAL_MERCHANT_STEP = seed_mod.seed_merchant_data


async def _run_seed(database_url: str, monkeypatch: pytest.MonkeyPatch, *, merchant: bool) -> None:
    monkeypatch.setattr(seed_mod, "DATABASE_URL", database_url)
    monkeypatch.setattr(seed_mod, "seed_product_embeddings", _no_embeddings)
    monkeypatch.setattr(seed_mod, "hash_pw", lambda _pw: "test-hash")
    monkeypatch.setattr(seed_mod, "seed_merchant_data", _REAL_MERCHANT_STEP if merchant else _no_merchant)
    random.seed(42)
    await seed_mod.main()


async def _dump(pool: asyncpg.Pool) -> dict[str, tuple[list[str], list[tuple[Any, ...]]]]:
    """Every table's rows, as sorted tuples over its pre-existing columns (name list kept alongside)."""
    out: dict[str, tuple[list[str], list[tuple[Any, ...]]]] = {}
    async with pool.acquire() as conn:
        tables = [
            r["tablename"]
            for r in await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'schema_migrations' "
                "ORDER BY tablename"
            )
        ]
        for table in tables:
            if table in NEW_TABLES:
                continue
            cols = [
                (r["column_name"], r["data_type"])
                for r in await conn.fetch(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = $1 AND is_generated = 'NEVER' "
                    "ORDER BY ordinal_position",
                    table,
                )
                if (table, r["column_name"]) not in NEW_COLUMNS
            ]
            names = [name for name, _ in cols]
            select = ", ".join(f'"{n}"::text' for n in names)
            rows = [tuple(r) for r in await conn.fetch(f'SELECT {select} FROM "{table}"')]
            out[table] = (names, rows)
    return out


async def _column_types(pool: asyncpg.Pool) -> dict[tuple[str, str], str]:
    rows = await pool.fetch(
        "SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_schema = 'public'"
    )
    return {(r["table_name"], r["column_name"]): r["data_type"] for r in rows}


def _project(
    dump: dict[str, tuple[list[str], list[tuple[Any, ...]]]], drop: set[tuple[str, str]]
) -> dict[str, list[tuple[Any, ...]]]:
    result: dict[str, list[tuple[Any, ...]]] = {}
    for table, (names, rows) in dump.items():
        keep = [i for i, name in enumerate(names) if (table, name) not in drop]
        result[table] = sorted((tuple(row[i] for i in keep) for row in rows), key=repr)
    return result


@pytest.mark.asyncio
async def test_the_merchant_step_changes_no_pre_existing_seeded_value(
    clean_db: asyncpg.Pool, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    types = await _column_types(clean_db)

    # Two runs WITHOUT the step show which columns are noise between identical runs.
    await _run_seed(database_url, monkeypatch, merchant=False)
    first = await _dump(clean_db)
    assert first["products"][1] and first["orders"][1] and first["price_history"][1], "baseline seeded nothing"
    await _run_seed(database_url, monkeypatch, merchant=False)
    second = await _dump(clean_db)

    noise: set[tuple[str, str]] = set()
    for table, (names, rows) in first.items():
        for index, name in enumerate(names):
            if sorted((r[index] for r in rows), key=repr) != sorted((r[index] for r in second[table][1]), key=repr):
                noise.add((table, name))
    assert noise, "expected clocks and generated ids to differ between runs"
    # Only clocks and ids may be noise. Anything else differing between two identical runs would mean the
    # seeder is not deterministic, and this test could then prove nothing about the step.
    assert all(types[key] in VOLATILE_TYPES or key in SEQUENCE_COLUMNS for key in noise), sorted(
        k for k in noise if types[k] not in VOLATILE_TYPES and k not in SEQUENCE_COLUMNS
    )
    assert ("products", "price") not in noise and ("orders", "total") not in noise

    # The same run WITH the step: every table, every pre-existing column, the same.
    await _run_seed(database_url, monkeypatch, merchant=True)
    with_step = await _dump(clean_db)
    assert _project(with_step, noise) == _project(first, noise)
    assert set(with_step) == set(first)

    # ...and the step did its work.
    assert await clean_db.fetchval("SELECT count(*) FROM products WHERE unit_cost IS NULL") == 0
    assert await clean_db.fetchval("SELECT count(*) FROM promotion_products") > 0


@pytest.mark.asyncio
async def test_the_merchant_step_draws_no_random_numbers(clean_db: asyncpg.Pool) -> None:
    async with clean_db.acquire() as conn:
        user_ids = await seed_mod.seed_users(conn)
        products = await seed_mod.seed_products(conn, user_ids)
        await seed_mod.seed_promotions(conn)
        assert products
        random.seed(1234)
        before = random.getstate()
        await seed_mod.seed_merchant_data(conn)
    assert random.getstate() == before


@pytest.mark.asyncio
async def test_seeded_costs_follow_the_category_ratio_and_leave_a_real_margin(
    clean_db: asyncpg.Pool, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _run_seed(database_url, monkeypatch, merchant=True)
    rows = await clean_db.fetch("SELECT category, price, unit_cost FROM products")
    assert len(rows) == 50
    floor = get_limits().margin_floor_pct
    for row in rows:
        expected = (row["price"] * seed_mod.UNIT_COST_RATIO[row["category"]]).quantize(Decimal("0.01"))
        assert row["unit_cost"] == expected
        assert 0 < row["unit_cost"] < row["price"]
        # every seeded product starts above the margin floor, so a guard failure is about the change
        assert (row["price"] - row["unit_cost"]) / row["price"] * 100 > floor


@pytest.mark.asyncio
async def test_promotions_are_linked_to_the_products_their_rules_name(
    clean_db: asyncpg.Pool, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _run_seed(database_url, monkeypatch, merchant=True)
    rows = await clean_db.fetch(
        """SELECT pr.name AS promo, p.name AS product, p.category
           FROM promotion_products pp JOIN promotions pr ON pr.id = pp.promotion_id
           JOIN products p ON p.id = pp.product_id"""
    )
    by_promo: dict[str, set[str]] = {}
    for row in rows:
        by_promo.setdefault(row["promo"], set()).add(row["product"])
    assert by_promo["Tech Bundle Deal"] == {"Sony WH-1000XM5", "Samsung T7 Shield SSD 2TB"}
    assert by_promo["Fitness Starter Kit"] == {
        "Fitbit Charge 6",
        "Hydro Flask 32oz Wide Mouth",
        "Manduka PRO Yoga Mat 6mm",
    }
    flash = [r for r in rows if r["promo"] == "Spring Flash Sale"]
    assert flash and {r["category"] for r in flash} == {"Clothing", "Sports"}
    # the quantity discount on Books is not a markdown of particular products
    assert "Buy 2 Books Get 10% Off" not in by_promo


# ── the guards' facts, read from the seeded data ──────────────────────────────


async def _seed_for_facts(database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    await _run_seed(database_url, monkeypatch, merchant=True)


async def _pid(pool: asyncpg.Pool, name: str) -> uuid.UUID:
    return await pool.fetchval("SELECT id FROM products WHERE name = $1", name)


@pytest.mark.asyncio
async def test_facts_for_a_product_in_an_active_bundle(
    clean_db: asyncpg.Pool, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_for_facts(database_url, monkeypatch)
    pid = await _pid(clean_db, "Sony WH-1000XM5")
    row = await clean_db.fetchrow("SELECT price, unit_cost FROM products WHERE id = $1", pid)

    facts = await get_product_facts(pid, db=clean_db)

    assert facts is not None
    assert facts.current_price == row["price"] and facts.unit_cost == row["unit_cost"]
    assert facts.unit_cost == (row["price"] * Decimal("0.78")).quantize(Decimal("0.01"))
    assert [p.name for p in facts.promotions] == ["Tech Bundle Deal"]
    assert facts.promotions[0].is_active_at(datetime.now(UTC))
    # the seeded history is one row a day for the 90 days before now: nothing recorded today
    assert facts.prior_changes_today == ()

    result = check_price_change(
        current_price=facts.current_price,
        proposed_price=facts.current_price * Decimal("0.99"),
        unit_cost=facts.unit_cost,
        prior_changes_today=facts.prior_changes_today,
        promotions=facts.promotions,
        now=datetime.now(UTC),
        limits=get_limits(),
    )
    assert result.reasons == (ReasonCode.ACTIVE_PROMOTION,)


@pytest.mark.asyncio
async def test_facts_for_a_product_in_no_promotion_pass_a_small_change(
    clean_db: asyncpg.Pool, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_for_facts(database_url, monkeypatch)
    pid = await clean_db.fetchval(
        """SELECT id FROM products WHERE category IN ('Home', 'Electronics', 'Books')
             AND id NOT IN (SELECT product_id FROM promotion_products) ORDER BY name LIMIT 1"""
    )
    facts = await get_product_facts(pid, db=clean_db)

    assert facts is not None and facts.promotions == ()
    result = check_price_change(
        current_price=facts.current_price,
        proposed_price=(facts.current_price * Decimal("0.97")).quantize(Decimal("0.01")),
        unit_cost=facts.unit_cost,
        prior_changes_today=facts.prior_changes_today,
        promotions=facts.promotions,
        now=datetime.now(UTC),
        limits=get_limits(),
    )
    assert result.ok, result.to_dict()


@pytest.mark.asyncio
async def test_an_expired_linked_promotion_is_read_but_does_not_block(
    clean_db: asyncpg.Pool, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_for_facts(database_url, monkeypatch)
    pid = await _pid(clean_db, "Sony WH-1000XM5")
    await clean_db.execute("UPDATE promotions SET end_date = now() - interval '1 hour' WHERE name = 'Tech Bundle Deal'")

    facts = await get_product_facts(pid, db=clean_db)

    assert facts is not None and len(facts.promotions) == 1
    assert not facts.promotions[0].is_active_at(datetime.now(UTC))
    result = check_price_change(
        current_price=facts.current_price,
        proposed_price=(facts.current_price * Decimal("0.99")).quantize(Decimal("0.01")),
        unit_cost=facts.unit_cost,
        prior_changes_today=facts.prior_changes_today,
        promotions=facts.promotions,
        now=datetime.now(UTC),
        limits=get_limits(),
    )
    assert result.ok


@pytest.mark.asyncio
async def test_todays_price_history_becomes_prior_changes(
    clean_db: asyncpg.Pool, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_for_facts(database_url, monkeypatch)
    pid = await _pid(clean_db, "Sony WH-1000XM5")
    now = datetime(2026, 11, 16, 15, 0, tzinfo=UTC)
    # Yesterday's last price is the day's opening price; two changes today; one later than "now".
    await clean_db.execute("DELETE FROM price_history WHERE product_id = $1", pid)
    for price, when in (
        ("100.00", now - timedelta(days=1, hours=2)),
        ("96.00", now - timedelta(hours=5)),
        ("96.00", now - timedelta(hours=4)),
        ("98.88", now - timedelta(hours=3)),
        ("50.00", now + timedelta(hours=1)),
    ):
        await clean_db.execute(
            "INSERT INTO price_history (product_id, price, recorded_at) VALUES ($1, $2, $3)", pid, Decimal(price), when
        )

    facts = await get_product_facts(pid, db=clean_db, now=now)

    assert facts is not None
    assert facts.prior_changes_today == (Decimal("4"), Decimal("3"))


@pytest.mark.asyncio
async def test_a_product_without_a_cost_reads_as_none_and_an_unknown_product_as_none(
    clean_db: asyncpg.Pool, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_for_facts(database_url, monkeypatch)
    pid = await _pid(clean_db, "Sony WH-1000XM5")
    await clean_db.execute("UPDATE products SET unit_cost = NULL WHERE id = $1", pid)

    facts = await get_product_facts(pid, db=clean_db)
    assert facts is not None and facts.unit_cost is None
    result = check_price_change(
        current_price=facts.current_price,
        proposed_price=facts.current_price,
        unit_cost=facts.unit_cost,
        prior_changes_today=(),
        promotions=(),
        now=datetime.now(UTC),
        limits=get_limits(),
    )
    assert result.reasons == (ReasonCode.MISSING_COST,)

    assert await get_product_facts(uuid.uuid4(), db=clean_db) is None
