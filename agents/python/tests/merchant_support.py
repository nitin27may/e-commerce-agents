"""Test helpers for the merchant proposal tests: a small catalogue in a real database (never mocked)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import asyncpg

SELLER = "seller-one@example.com"
OTHER_SELLER = "seller-two@example.com"
ADMIN = "boss@example.com"
CUSTOMER = "shopper@example.com"
SUPPORT = "support-agent@example.com"


@dataclass
class World:
    pool: asyncpg.Pool
    seller_id: uuid.UUID
    other_seller_id: uuid.UUID
    warehouses: list[uuid.UUID] = field(default_factory=list)
    customer_id: uuid.UUID | None = None


async def user(pool: asyncpg.Pool, email: str, role: str) -> uuid.UUID:
    user_id = uuid.uuid4()
    await pool.execute(
        "INSERT INTO users (id, email, password_hash, name, role) VALUES ($1, $2, 'hash', $3, $4)",
        user_id,
        email,
        email.split("@")[0],
        role,
    )
    return user_id


async def world(pool: asyncpg.Pool) -> World:
    seller = await user(pool, SELLER, "seller")
    other = await user(pool, OTHER_SELLER, "seller")
    await user(pool, ADMIN, "admin")
    await user(pool, SUPPORT, "support")
    customer = await user(pool, CUSTOMER, "customer")
    warehouses = []
    for name in ("East", "West"):
        wid = uuid.uuid4()
        await pool.execute(
            "INSERT INTO warehouses (id, name, location, region) VALUES ($1, $2, 'somewhere', $3)",
            wid,
            name,
            name.lower(),
        )
        warehouses.append(wid)
    return World(pool, seller, other, warehouses, customer)


async def product(
    w: World,
    name: str,
    *,
    price: str = "100.00",
    cost: str | None = "50.00",
    seller: uuid.UUID | None = None,
    stock: tuple[int, int] = (200, 200),
    threshold: int = 10,
    sold: int = 0,
    active: bool = True,
) -> uuid.UUID:
    """A product with stock in both warehouses and ``sold`` units in the last 90 days (one delivered order)."""
    product_id = uuid.uuid4()
    await w.pool.execute(
        """INSERT INTO products (id, name, description, category, brand, price, unit_cost, seller_id, is_active)
           VALUES ($1, $2, 'd', 'Electronics', 'b', $3, $4, $5, $6)""",
        product_id,
        name,
        Decimal(price),
        None if cost is None else Decimal(cost),
        seller or w.seller_id,
        active,
    )
    for warehouse, quantity in zip(w.warehouses, stock, strict=True):
        await w.pool.execute(
            "INSERT INTO warehouse_inventory (warehouse_id, product_id, quantity, reorder_threshold) "
            "VALUES ($1, $2, $3, $4)",
            warehouse,
            product_id,
            quantity,
            threshold,
        )
    if sold:
        order_id = uuid.uuid4()
        await w.pool.execute(
            """INSERT INTO orders (id, user_id, status, total, shipping_address, created_at)
               VALUES ($1, $2, 'delivered', $3, '{}'::jsonb, NOW() - interval '10 days')""",
            order_id,
            w.customer_id,
            Decimal(price) * sold,
        )
        await w.pool.execute(
            "INSERT INTO order_items (order_id, product_id, quantity, unit_price, subtotal) "
            "VALUES ($1, $2, $3, $4, $5)",
            order_id,
            product_id,
            sold,
            Decimal(price),
            Decimal(price) * sold,
        )
    return product_id


async def active_promotion(pool: asyncpg.Pool, product_id: uuid.UUID) -> None:
    promo = uuid.uuid4()
    await pool.execute(
        """INSERT INTO promotions (id, name, type, rules, start_date, end_date, is_active)
           VALUES ($1, 'Flash', 'flash_sale', '{}'::jsonb, NOW() - interval '1 day', NOW() + interval '1 day', TRUE)""",
        promo,
    )
    await pool.execute("INSERT INTO promotion_products (promotion_id, product_id) VALUES ($1, $2)", promo, product_id)


async def one(pool: asyncpg.Pool, sql: str, *args: Any) -> Any:
    return await pool.fetchval(sql, *args)
