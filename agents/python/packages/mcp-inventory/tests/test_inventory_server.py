"""Tests for ecommerce-mcp-inventory server.

Two tiers:
- Registration smoke tests (no DB) — verify tool names are registered and
  the ASGI app is importable. These always run in CI.
- Integration tests (DB via testcontainers) — verify actual SQL queries against
  a real Postgres container with the production schema. Marked `integration`.
"""

from __future__ import annotations

import asyncpg
import pytest
import pytest_asyncio

from ecommerce_mcp_inventory.server import _get_pool, app, mcp

# ─────────────────────── Registration smoke ─────────────────────────────────


def test_mcp_server_name() -> None:
    assert mcp.name == "inventory-fulfillment-mcp"


def test_tool_names_registered() -> None:
    """All 5 inventory tools must be discoverable without a DB connection."""
    tool_names = {t.name for t in mcp._tool_manager.list_tools()}
    expected = {
        "check_stock",
        "get_warehouse_availability",
        "get_restock_schedule",
        "estimate_shipping",
        "compare_carriers",
    }
    assert expected == tool_names


def test_asgi_app_importable() -> None:
    """app must be a callable ASGI app (uvicorn entry-point check)."""
    assert callable(app)


def test_get_pool_raises_before_startup() -> None:
    """_get_pool() must fail loudly if called before lifespan starts."""
    with pytest.raises(RuntimeError, match="DB pool not initialized"):
        _get_pool()


# ─────────────────────── Integration (live DB) ──────────────────────────────


@pytest.fixture
async def product_and_warehouse(postgres_pool: asyncpg.Pool) -> dict:
    """Seed minimal product + warehouse + inventory rows; return ids."""
    async with postgres_pool.acquire() as conn:
        seller_id = await conn.fetchval(
            """INSERT INTO users (email, name, role, password_hash)
               VALUES ('inv_seller@test.com', 'Inv Seller', 'seller', 'hash')
               ON CONFLICT (email) DO UPDATE SET name = EXCLUDED.name
               RETURNING id"""
        )
        pid = await conn.fetchval(
            """INSERT INTO products
                   (name, category, brand, price, original_price, rating,
                    review_count, description, seller_id, is_active)
               VALUES ('Inv Widget', 'Electronics', 'Acme', 49.99, 59.99, 4.0,
                       5, 'An inventory widget', $1, TRUE)
               ON CONFLICT DO NOTHING
               RETURNING id""",
            seller_id,
        )
        if pid is None:
            pid = await conn.fetchval("SELECT id FROM products WHERE name = 'Inv Widget' LIMIT 1")

        wid = await conn.fetchval(
            """INSERT INTO warehouses (name, region, location)
               VALUES ('Test East WH', 'east', 'Boston, MA')
               ON CONFLICT DO NOTHING
               RETURNING id"""
        )
        if wid is None:
            wid = await conn.fetchval("SELECT id FROM warehouses WHERE name = 'Test East WH' LIMIT 1")

        await conn.execute(
            """INSERT INTO warehouse_inventory (product_id, warehouse_id, quantity, reorder_threshold)
               VALUES ($1, $2, 100, 10)
               ON CONFLICT (product_id, warehouse_id) DO UPDATE SET quantity = 100""",
            pid,
            wid,
        )
        return {"product_id": str(pid), "warehouse_id": str(wid)}


@pytest_asyncio.fixture
async def _patched_pool(postgres_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch):
    """Patch module-level _pool so tool functions use the test container."""
    import ecommerce_mcp_inventory.server as srv

    monkeypatch.setattr(srv, "_pool", postgres_pool)
    yield


@pytest.mark.integration
async def test_check_stock_in_stock(
    product_and_warehouse: dict,
    _patched_pool: None,
) -> None:
    from ecommerce_mcp_inventory.server import check_stock

    result = await check_stock(product_id=product_and_warehouse["product_id"])
    assert result["in_stock"] is True
    assert result["total_quantity"] == 100
    assert len(result["warehouses"]) >= 1


@pytest.mark.integration
async def test_check_stock_not_found(
    _patched_pool: None,
) -> None:
    from ecommerce_mcp_inventory.server import check_stock

    result = await check_stock(product_id="00000000-0000-0000-0000-000000000000")
    assert result["in_stock"] is False
    assert result["total_quantity"] == 0


@pytest.mark.integration
async def test_get_warehouse_availability(
    product_and_warehouse: dict,
    _patched_pool: None,
) -> None:
    from ecommerce_mcp_inventory.server import get_warehouse_availability

    result = await get_warehouse_availability(product_id=product_and_warehouse["product_id"])
    assert "warehouses" in result
    assert "upcoming_restocks" in result
    assert any(w["region"] == "east" for w in result["warehouses"])


@pytest.mark.integration
async def test_get_restock_schedule_empty(
    product_and_warehouse: dict,
    _patched_pool: None,
) -> None:
    from ecommerce_mcp_inventory.server import get_restock_schedule

    # No restock rows seeded — should return empty list without error
    result = await get_restock_schedule(product_id=product_and_warehouse["product_id"])
    assert isinstance(result, list)


@pytest.mark.integration
async def test_compare_carriers_no_rates(
    _patched_pool: None,
) -> None:
    from ecommerce_mcp_inventory.server import compare_carriers

    # No shipping_rates seeded — should return empty list without error
    result = await compare_carriers(region_from="east", region_to="west")
    assert isinstance(result, list)
