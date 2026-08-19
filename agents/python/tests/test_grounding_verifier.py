"""Claim verification against real data.

Policy: never mock the database — clean_db provisions a real Postgres
container (see tests/conftest.py). Ledger-tier tests need no DB at all.
"""

from __future__ import annotations

import asyncpg
import pytest
import pytest_asyncio

from shared.grounding.extractor import (
    AmountClaim,
    BareIdClaim,
    ExtractedClaims,
    OrderClaim,
    ProductClaim,
    TrackingClaim,
)
from shared.grounding.ledger import GroundingLedger, OrderFact, ProductFact
from shared.grounding.verifier import verify_claims

_MISSING_ID = "99999999-9999-9999-9999-999999999999"
_NOT_A_UUID = "sony-wh1000xm5-001"


@pytest_asyncio.fixture
async def db_pool(clean_db: asyncpg.Pool) -> asyncpg.Pool:
    return clean_db


@pytest_asyncio.fixture
async def seeded_product(db_pool: asyncpg.Pool) -> dict:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO products (name, description, category, brand, price, is_active)
               VALUES ('Widget', 'A widget', 'Electronics', 'Acme', 49.99, TRUE)
               RETURNING id, name, price""",
        )
        return dict(row)


@pytest_asyncio.fixture
async def seeded_order(db_pool: asyncpg.Pool) -> dict:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO orders (status, total, shipping_address)
               VALUES ('shipped', 129.50, '{"city": "Metropolis"}'::jsonb)
               RETURNING id, status, total""",
        )
        return dict(row)


# ─────────────────────── Ledger tier (no DB) ───────────────────────


@pytest.mark.asyncio
async def test_product_verified_from_ledger_alone() -> None:
    ledger = GroundingLedger()
    ledger.products["p1"] = ProductFact(id="p1", name="X", price=10.0)
    claims = ExtractedClaims(products=[ProductClaim(id="p1", name="X", price=10.0, image_url=None)])

    report = await verify_claims(claims, ledger, pool=None)
    assert report.verified_count == 1
    assert report.verdicts[0].source == "ledger"


@pytest.mark.asyncio
async def test_product_price_mismatch_against_ledger_has_corrected_value() -> None:
    ledger = GroundingLedger()
    ledger.products["p1"] = ProductFact(id="p1", name="X", price=10.0)
    claims = ExtractedClaims(products=[ProductClaim(id="p1", name="X", price=999.0, image_url=None)])

    report = await verify_claims(claims, ledger, pool=None)
    verdict = report.verdicts[0]
    assert verdict.status == "price_mismatch"
    assert verdict.corrected_value == 10.0


@pytest.mark.asyncio
async def test_order_verified_from_ledger_alone() -> None:
    ledger = GroundingLedger()
    ledger.orders["o1"] = OrderFact(id="o1", status="shipped", total=50.0)
    claims = ExtractedClaims(orders=[OrderClaim(id="o1", status="shipped", total=50.0, tracking=None)])

    report = await verify_claims(claims, ledger, pool=None)
    assert report.verified_count == 1


@pytest.mark.asyncio
async def test_amount_verified_against_known_ledger_prices() -> None:
    ledger = GroundingLedger()
    ledger.products["p1"] = ProductFact(id="p1", name="X", price=49.99)
    claims = ExtractedClaims(amounts=[AmountClaim(value=49.99)])

    report = await verify_claims(claims, ledger, pool=None)
    assert report.verdicts[0].status == "verified"


@pytest.mark.asyncio
async def test_amount_with_no_ledger_match_is_unverifiable() -> None:
    claims = ExtractedClaims(amounts=[AmountClaim(value=12345.67)])
    report = await verify_claims(claims, GroundingLedger(), pool=None)
    assert report.verdicts[0].status == "unverifiable"


@pytest.mark.asyncio
async def test_tracking_verified_against_ledger() -> None:
    ledger = GroundingLedger()
    ledger.orders["o1"] = OrderFact(id="o1", status="shipped", total=1.0, tracking="TRK1")
    claims = ExtractedClaims(trackings=[TrackingClaim(value="TRK1")])

    report = await verify_claims(claims, ledger, pool=None)
    assert report.verdicts[0].status == "verified"


@pytest.mark.asyncio
async def test_unresolved_claim_without_pool_is_unverifiable_not_not_found() -> None:
    # No ledger match and no DB connection available — must not be reported
    # as a fabrication just because verification couldn't run.
    claims = ExtractedClaims(products=[ProductClaim(id="p1", name="X", price=10.0, image_url=None)])
    report = await verify_claims(claims, GroundingLedger(), pool=None)
    assert report.verdicts[0].status == "unverifiable"


# ─────────────────────── DB tier (real Postgres) ───────────────────────


@pytest.mark.asyncio
async def test_product_verified_against_real_db(db_pool: asyncpg.Pool, seeded_product: dict) -> None:
    claims = ExtractedClaims(products=[
        ProductClaim(id=str(seeded_product["id"]), name="Widget", price=49.99, image_url=None),
    ])
    report = await verify_claims(claims, None, db_pool)
    assert report.verdicts[0].status == "verified"
    assert report.verdicts[0].source == "db"


@pytest.mark.asyncio
async def test_product_price_mismatch_against_real_db(db_pool: asyncpg.Pool, seeded_product: dict) -> None:
    claims = ExtractedClaims(products=[
        ProductClaim(id=str(seeded_product["id"]), name="Widget", price=1.0, image_url=None),
    ])
    report = await verify_claims(claims, None, db_pool)
    verdict = report.verdicts[0]
    assert verdict.status == "price_mismatch"
    assert verdict.corrected_value == 49.99


@pytest.mark.asyncio
async def test_fabricated_product_id_is_not_found(db_pool: asyncpg.Pool) -> None:
    claims = ExtractedClaims(products=[
        ProductClaim(id=_MISSING_ID, name="Ghost", price=1.0, image_url=None),
    ])
    report = await verify_claims(claims, None, db_pool)
    assert report.verdicts[0].status == "not_found"


@pytest.mark.asyncio
async def test_non_uuid_id_is_not_found_without_a_db_round_trip(db_pool: asyncpg.Pool) -> None:
    # A slug like "sony-wh1000xm5-001" is exactly grounding-rules.yaml's
    # documented example of a fabricated id — must fail fast, not crash on
    # an invalid ::uuid[] cast.
    claims = ExtractedClaims(products=[
        ProductClaim(id=_NOT_A_UUID, name="Ghost", price=1.0, image_url=None),
    ])
    report = await verify_claims(claims, None, db_pool)
    assert report.verdicts[0].status == "not_found"


@pytest.mark.asyncio
async def test_order_verified_against_real_db(db_pool: asyncpg.Pool, seeded_order: dict) -> None:
    claims = ExtractedClaims(orders=[
        OrderClaim(id=str(seeded_order["id"]), status="shipped", total=129.50, tracking=None),
    ])
    report = await verify_claims(claims, None, db_pool)
    assert report.verdicts[0].status == "verified"


@pytest.mark.asyncio
async def test_bare_id_verified_if_it_exists_as_either_product_or_order(
    db_pool: asyncpg.Pool, seeded_order: dict,
) -> None:
    claims = ExtractedClaims(bare_ids=[BareIdClaim(id=str(seeded_order["id"]))])
    report = await verify_claims(claims, None, db_pool)
    assert report.verdicts[0].status == "verified"


@pytest.mark.asyncio
async def test_bare_id_not_found_when_it_matches_nothing(db_pool: asyncpg.Pool) -> None:
    claims = ExtractedClaims(bare_ids=[BareIdClaim(id=_MISSING_ID)])
    report = await verify_claims(claims, None, db_pool)
    assert report.verdicts[0].status == "not_found"


@pytest.mark.asyncio
async def test_ledger_match_skips_db_round_trip(
    db_pool: asyncpg.Pool, seeded_product: dict,
) -> None:
    # Real product in the DB, but also present in the ledger with a
    # DIFFERENT price — the ledger tier must win (cheaper, checked first)
    # rather than silently falling through to the DB tier.
    ledger = GroundingLedger()
    pid = str(seeded_product["id"])
    ledger.products[pid] = ProductFact(id=pid, name="Widget", price=49.99)
    claims = ExtractedClaims(products=[ProductClaim(id=pid, name="Widget", price=49.99, image_url=None)])

    report = await verify_claims(claims, ledger, db_pool)
    assert report.verdicts[0].status == "verified"
    assert report.verdicts[0].source == "ledger"
