"""Shared cart tools — add/remove items, addresses, coupons."""

from __future__ import annotations

import json
import uuid
from typing import Annotated

from agent_framework import tool
from pydantic import Field

from shared.context import current_user_email
from shared.db import get_pool


async def _get_or_create_cart(conn, user_id: str) -> str:
    """Get existing cart or create new one. Returns cart_id."""
    cart = await conn.fetchrow("SELECT id FROM carts WHERE user_id = $1", user_id)
    if cart:
        return str(cart["id"])
    row = await conn.fetchrow(
        "INSERT INTO carts (user_id) VALUES ($1) RETURNING id", user_id
    )
    return str(row["id"])


@tool(name="add_to_cart", description="Add a product to the user's shopping cart. If the product is already in the cart, the quantity is increased.")
async def add_to_cart(
    product_id: Annotated[str, Field(description="UUID of the product to add")],
    quantity: Annotated[int, Field(description="Quantity to add (default 1)")] = 1,
) -> dict:
    email = current_user_email.get()
    if not email:
        return {"error": "No user context available"}

    if quantity < 1:
        return {"error": "Quantity must be at least 1"}

    pool = get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT id FROM users WHERE email = $1", email)
        if not user:
            return {"error": "User not found"}

        # Validate product exists and is active
        product = await conn.fetchrow(
            "SELECT id, name, price, is_active FROM products WHERE id = $1",
            uuid.UUID(product_id),
        )
        if not product:
            return {"error": f"Product not found: {product_id}"}
        if not product["is_active"]:
            return {"error": f"Product '{product['name']}' is no longer available"}

        cart_id = await _get_or_create_cart(conn, user["id"])

        # Upsert: insert or add to existing quantity
        row = await conn.fetchrow(
            """INSERT INTO cart_items (cart_id, product_id, quantity)
               VALUES ($1, $2, $3)
               ON CONFLICT (cart_id, product_id)
               DO UPDATE SET quantity = cart_items.quantity + EXCLUDED.quantity
               RETURNING quantity""",
            uuid.UUID(cart_id), uuid.UUID(product_id), quantity,
        )

        return {
            "product_id": product_id,
            "product_name": product["name"],
            "price": float(product["price"]),
            "quantity_in_cart": row["quantity"],
            "message": f"Added {quantity} x '{product['name']}' to cart (total in cart: {row['quantity']}).",
        }


@tool(name="get_cart", description="Get the user's current shopping cart with all items, totals, and addresses.")
async def get_cart() -> dict:
    email = current_user_email.get()
    if not email:
        return {"error": "No user context available"}

    pool = get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT id FROM users WHERE email = $1", email)
        if not user:
            return {"error": "User not found"}

        cart = await conn.fetchrow(
            """SELECT id, shipping_address, billing_address, billing_same_as_shipping,
                      coupon_code, discount_amount, notes
               FROM carts WHERE user_id = $1""",
            user["id"],
        )
        if not cart:
            return {"message": "Cart is empty", "items": [], "item_count": 0, "subtotal": 0, "discount": 0, "total": 0}

        items = await conn.fetch(
            """SELECT ci.product_id, ci.quantity, p.name, p.brand, p.category,
                      p.price, p.image_url,
                      (p.price * ci.quantity) AS subtotal
               FROM cart_items ci
               JOIN products p ON ci.product_id = p.id
               WHERE ci.cart_id = $1
               ORDER BY ci.added_at""",
            cart["id"],
        )

        if not items:
            return {"message": "Cart is empty", "items": [], "item_count": 0, "subtotal": 0, "discount": 0, "total": 0}

        item_list = [
            {
                "product_id": str(item["product_id"]),
                "name": item["name"],
                "brand": item["brand"],
                "category": item["category"],
                "price": float(item["price"]),
                "quantity": item["quantity"],
                "image_url": item["image_url"],
                "subtotal": float(item["subtotal"]),
            }
            for item in items
        ]

        subtotal = sum(i["subtotal"] for i in item_list)
        discount = float(cart["discount_amount"]) if cart["discount_amount"] else 0.0
        total = max(subtotal - discount, 0.0)
        item_count = sum(i["quantity"] for i in item_list)

        # Parse JSONB addresses (asyncpg returns them as dicts or None)
        shipping_address = cart["shipping_address"]
        billing_address = cart["billing_address"]
        if isinstance(shipping_address, str):
            shipping_address = json.loads(shipping_address)
        if isinstance(billing_address, str):
            billing_address = json.loads(billing_address)

        return {
            "cart_id": str(cart["id"]),
            "items": item_list,
            "item_count": item_count,
            "subtotal": round(subtotal, 2),
            "discount": round(discount, 2),
            "coupon_code": cart["coupon_code"],
            "total": round(total, 2),
            "shipping_address": shipping_address,
            "billing_address": billing_address,
            "billing_same_as_shipping": cart["billing_same_as_shipping"],
            "notes": cart["notes"],
        }


@tool(name="remove_from_cart", description="Remove a product entirely from the user's shopping cart.")
async def remove_from_cart(
    product_id: Annotated[str, Field(description="UUID of the product to remove")],
) -> dict:
    email = current_user_email.get()
    if not email:
        return {"error": "No user context available"}

    pool = get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT id FROM users WHERE email = $1", email)
        if not user:
            return {"error": "User not found"}

        cart = await conn.fetchrow("SELECT id FROM carts WHERE user_id = $1", user["id"])
        if not cart:
            return {"error": "Cart is empty, nothing to remove"}

        deleted = await conn.fetchrow(
            """DELETE FROM cart_items ci
               USING products p
               WHERE ci.cart_id = $1 AND ci.product_id = $2 AND p.id = ci.product_id
               RETURNING p.name""",
            cart["id"], uuid.UUID(product_id),
        )
        if not deleted:
            return {"error": f"Product {product_id} not found in cart"}

        return {
            "product_id": product_id,
            "product_name": deleted["name"],
            "message": f"Removed '{deleted['name']}' from cart.",
        }


@tool(name="update_cart_quantity", description="Update the quantity of a product in the user's cart. If quantity is 0 or less, the item is removed.")
async def update_cart_quantity(
    product_id: Annotated[str, Field(description="UUID of the product to update")],
    quantity: Annotated[int, Field(description="New quantity for the product")],
) -> dict:
    email = current_user_email.get()
    if not email:
        return {"error": "No user context available"}

    pool = get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT id FROM users WHERE email = $1", email)
        if not user:
            return {"error": "User not found"}

        cart = await conn.fetchrow("SELECT id FROM carts WHERE user_id = $1", user["id"])
        if not cart:
            return {"error": "Cart is empty, nothing to update"}

        # If quantity <= 0, remove the item
        if quantity <= 0:
            deleted = await conn.fetchrow(
                """DELETE FROM cart_items ci
                   USING products p
                   WHERE ci.cart_id = $1 AND ci.product_id = $2 AND p.id = ci.product_id
                   RETURNING p.name""",
                cart["id"], uuid.UUID(product_id),
            )
            if not deleted:
                return {"error": f"Product {product_id} not found in cart"}
            return {
                "product_id": product_id,
                "product_name": deleted["name"],
                "quantity": 0,
                "message": f"Removed '{deleted['name']}' from cart.",
            }

        # Update quantity
        updated = await conn.fetchrow(
            """UPDATE cart_items ci
               SET quantity = $3
               FROM products p
               WHERE ci.cart_id = $1 AND ci.product_id = $2 AND p.id = ci.product_id
               RETURNING p.name, p.price, ci.quantity""",
            cart["id"], uuid.UUID(product_id), quantity,
        )
        if not updated:
            return {"error": f"Product {product_id} not found in cart"}

        return {
            "product_id": product_id,
            "product_name": updated["name"],
            "price": float(updated["price"]),
            "quantity": updated["quantity"],
            "subtotal": round(float(updated["price"]) * updated["quantity"], 2),
            "message": f"Updated '{updated['name']}' quantity to {updated['quantity']}.",
        }


@tool(name="set_shipping_address", description="Set the shipping address on the user's cart.")
async def set_shipping_address(
    name: Annotated[str, Field(description="Recipient full name")],
    street: Annotated[str, Field(description="Street address")],
    city: Annotated[str, Field(description="City")],
    state: Annotated[str, Field(description="State or province")],
    zip_code: Annotated[str, Field(description="ZIP or postal code")],
    country: Annotated[str, Field(description="Country code (default US)")] = "US",
    phone: Annotated[str, Field(description="Phone number (optional)")] = "",
) -> dict:
    email = current_user_email.get()
    if not email:
        return {"error": "No user context available"}

    address = {
        "name": name,
        "street": street,
        "city": city,
        "state": state,
        "zip_code": zip_code,
        "country": country,
        "phone": phone,
    }

    pool = get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT id FROM users WHERE email = $1", email)
        if not user:
            return {"error": "User not found"}

        cart_id = await _get_or_create_cart(conn, user["id"])

        await conn.execute(
            """UPDATE carts
               SET shipping_address = $2, updated_at = NOW()
               WHERE id = $1""",
            uuid.UUID(cart_id), json.dumps(address),
        )

        return {
            "message": "Shipping address saved.",
            "shipping_address": address,
        }


@tool(name="set_billing_address", description="Set a separate billing address on the user's cart.")
async def set_billing_address(
    name: Annotated[str, Field(description="Billing name")],
    street: Annotated[str, Field(description="Street address")],
    city: Annotated[str, Field(description="City")],
    state: Annotated[str, Field(description="State or province")],
    zip_code: Annotated[str, Field(description="ZIP or postal code")],
    country: Annotated[str, Field(description="Country code (default US)")] = "US",
    phone: Annotated[str, Field(description="Phone number (optional)")] = "",
) -> dict:
    email = current_user_email.get()
    if not email:
        return {"error": "No user context available"}

    address = {
        "name": name,
        "street": street,
        "city": city,
        "state": state,
        "zip_code": zip_code,
        "country": country,
        "phone": phone,
    }

    pool = get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT id FROM users WHERE email = $1", email)
        if not user:
            return {"error": "User not found"}

        cart_id = await _get_or_create_cart(conn, user["id"])

        await conn.execute(
            """UPDATE carts
               SET billing_address = $2, billing_same_as_shipping = FALSE, updated_at = NOW()
               WHERE id = $1""",
            uuid.UUID(cart_id), json.dumps(address),
        )

        return {
            "message": "Billing address saved (different from shipping).",
            "billing_address": address,
            "billing_same_as_shipping": False,
        }


@tool(name="set_billing_same_as_shipping", description="Set the billing address to be the same as the shipping address.")
async def set_billing_same_as_shipping() -> dict:
    email = current_user_email.get()
    if not email:
        return {"error": "No user context available"}

    pool = get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT id FROM users WHERE email = $1", email)
        if not user:
            return {"error": "User not found"}

        cart = await conn.fetchrow(
            "SELECT id, shipping_address FROM carts WHERE user_id = $1",
            user["id"],
        )
        if not cart:
            return {"error": "No cart found. Add items first."}

        if not cart["shipping_address"]:
            return {"error": "No shipping address set yet. Set a shipping address first."}

        # Copy shipping to billing
        await conn.execute(
            """UPDATE carts
               SET billing_address = shipping_address,
                   billing_same_as_shipping = TRUE,
                   updated_at = NOW()
               WHERE id = $1""",
            cart["id"],
        )

        shipping = cart["shipping_address"]
        if isinstance(shipping, str):
            shipping = json.loads(shipping)

        return {
            "message": "Billing address set to same as shipping address.",
            "billing_same_as_shipping": True,
            "address": shipping,
        }


@tool(name="apply_coupon_to_cart", description="Apply a coupon code to the user's cart for a discount.")
async def apply_coupon_to_cart(
    code: Annotated[str, Field(description="Coupon code to apply")],
) -> dict:
    email = current_user_email.get()
    if not email:
        return {"error": "No user context available"}

    pool = get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT id FROM users WHERE email = $1", email)
        if not user:
            return {"error": "User not found"}

        cart = await conn.fetchrow("SELECT id FROM carts WHERE user_id = $1", user["id"])
        if not cart:
            return {"error": "Cart is empty. Add items before applying a coupon."}

        # Validate coupon
        coupon = await conn.fetchrow(
            """SELECT id, code, description, discount_type, discount_value,
                      min_spend, max_discount, usage_limit, times_used,
                      valid_from, valid_until, applicable_categories,
                      user_specific_email, is_active
               FROM coupons
               WHERE UPPER(code) = UPPER($1)""",
            code,
        )
        if not coupon:
            return {"error": f"Coupon '{code}' not found"}

        if not coupon["is_active"]:
            return {"error": f"Coupon '{code}' is no longer active"}

        # Check expiry
        is_expired = await conn.fetchval(
            "SELECT CASE WHEN $1::timestamptz IS NOT NULL AND $1::timestamptz < NOW() THEN TRUE ELSE FALSE END",
            coupon["valid_until"],
        )
        if is_expired:
            return {"error": f"Coupon '{code}' has expired"}

        # Check not before valid_from
        is_before_start = await conn.fetchval(
            "SELECT CASE WHEN $1::timestamptz > NOW() THEN TRUE ELSE FALSE END",
            coupon["valid_from"],
        )
        if is_before_start:
            return {"error": f"Coupon '{code}' is not yet valid"}

        # Check usage limit
        if coupon["usage_limit"] and coupon["times_used"] >= coupon["usage_limit"]:
            return {"error": f"Coupon '{code}' has reached its usage limit"}

        # Check user-specific coupon
        if coupon["user_specific_email"] and coupon["user_specific_email"] != email:
            return {"error": f"Coupon '{code}' is not valid for your account"}

        # Calculate cart subtotal
        subtotal_row = await conn.fetchrow(
            """SELECT COALESCE(SUM(p.price * ci.quantity), 0) AS subtotal
               FROM cart_items ci
               JOIN products p ON ci.product_id = p.id
               WHERE ci.cart_id = $1""",
            cart["id"],
        )
        subtotal = float(subtotal_row["subtotal"])

        if subtotal == 0:
            return {"error": "Cart is empty. Add items before applying a coupon."}

        # Check min spend
        min_spend = float(coupon["min_spend"]) if coupon["min_spend"] else 0
        if subtotal < min_spend:
            return {"error": f"Cart subtotal (${subtotal:.2f}) does not meet minimum spend of ${min_spend:.2f} for this coupon."}

        # Calculate discount
        discount_value = float(coupon["discount_value"])
        if coupon["discount_type"] == "percentage":
            discount = subtotal * (discount_value / 100)
            # Apply max_discount cap
            if coupon["max_discount"]:
                max_disc = float(coupon["max_discount"])
                discount = min(discount, max_disc)
        else:
            # Fixed discount
            discount = min(discount_value, subtotal)

        discount = round(discount, 2)
        new_total = round(subtotal - discount, 2)

        # Save coupon and discount on cart
        await conn.execute(
            """UPDATE carts
               SET coupon_code = $2, discount_amount = $3, updated_at = NOW()
               WHERE id = $1""",
            cart["id"], coupon["code"], discount,
        )

        # Increment usage counter
        await conn.execute(
            "UPDATE coupons SET times_used = times_used + 1 WHERE id = $1",
            coupon["id"],
        )

        desc = coupon["description"] or f"{discount_value}{'%' if coupon['discount_type'] == 'percentage' else ' off'}"

        return {
            "coupon_code": coupon["code"],
            "description": desc,
            "discount_type": coupon["discount_type"],
            "discount_amount": discount,
            "subtotal": subtotal,
            "new_total": new_total,
            "message": f"Coupon '{coupon['code']}' applied! You saved ${discount:.2f}. New total: ${new_total:.2f}.",
        }
