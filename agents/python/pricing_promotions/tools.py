"""Pricing & Promotions tools — coupon validation, cart optimization, deals, bundles."""

from __future__ import annotations

import json
from typing import Annotated

from agent_framework import tool
from pydantic import Field

from shared.context import current_user_email
from shared.db import get_pool


@tool(name="validate_coupon", description="Validate a coupon code. Checks expiry, min spend, usage limit, applicable categories, and user-specific restrictions.")
async def validate_coupon(
    code: Annotated[str, Field(description="Coupon code to validate")],
    cart_total: Annotated[float, Field(description="Current cart total before discount")],
    category: Annotated[str | None, Field(description="Product category to check applicability")] = None,
) -> dict:
    email = current_user_email.get()
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id, code, description, discount_type, discount_value,
                      min_spend, max_discount, usage_limit, times_used,
                      valid_from, valid_until, applicable_categories,
                      user_specific_email, is_active
               FROM coupons WHERE UPPER(code) = UPPER($1)""",
            code,
        )
        if not row:
            return {"valid": False, "error": f"Coupon '{code}' not found"}

        # Check active
        if not row["is_active"]:
            return {"valid": False, "code": code, "error": "Coupon is no longer active"}

        # Check expiry
        if row["valid_until"]:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            if now > row["valid_until"]:
                return {"valid": False, "code": code, "error": "Coupon has expired"}
            if now < row["valid_from"]:
                return {"valid": False, "code": code, "error": "Coupon is not yet valid"}

        # Check usage limit
        if row["usage_limit"] is not None and row["times_used"] >= row["usage_limit"]:
            return {"valid": False, "code": code, "error": "Coupon usage limit reached"}

        # Check min spend
        if row["min_spend"] and cart_total < float(row["min_spend"]):
            return {
                "valid": False,
                "code": code,
                "error": f"Minimum spend of ${float(row['min_spend']):.2f} not met (cart: ${cart_total:.2f})",
            }

        # Check applicable categories
        if row["applicable_categories"] and category:
            if category not in row["applicable_categories"]:
                return {
                    "valid": False,
                    "code": code,
                    "error": f"Coupon not valid for category '{category}'. Valid for: {', '.join(row['applicable_categories'])}",
                }

        # Check user-specific restriction
        if row["user_specific_email"] and row["user_specific_email"] != email:
            return {"valid": False, "code": code, "error": "This coupon is restricted to a specific user"}

        # Calculate discount
        discount_type = row["discount_type"]
        discount_value = float(row["discount_value"])
        if discount_type == "percentage":
            discount_amount = cart_total * (discount_value / 100)
            if row["max_discount"]:
                discount_amount = min(discount_amount, float(row["max_discount"]))
        else:
            discount_amount = min(discount_value, cart_total)

        return {
            "valid": True,
            "code": row["code"],
            "description": row["description"],
            "discount_type": discount_type,
            "discount_value": discount_value,
            "discount_amount": round(discount_amount, 2),
            "new_total": round(cart_total - discount_amount, 2),
            "applicable_categories": row["applicable_categories"],
        }


def _as_list(value: object) -> list[str]:
    """Normalise a rules field that may be a scalar or a list.

    `promotions.rules` is untyped JSONB and the seeded rows are inconsistent:
    `buy_x_get_y` uses a singular `category`, `flash_sale` a plural
    `categories`. Reading only one spelling is what made both promotions
    silently match nothing.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return []


def _pct(value: object) -> float:
    """A percentage from untyped JSONB, or 0.0 if it isn't one."""
    try:
        pct = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    # A negative or >100% discount is a data error, not a bigger discount.
    return pct if 0.0 <= pct <= 100.0 else 0.0


@tool(name="optimize_cart", description="Find the best combination of coupons, promotions, and loyalty discounts for a cart. Returns the optimal savings breakdown.")
async def optimize_cart(
    product_ids_with_quantities: Annotated[
        list[dict],
        Field(description="List of items: [{\"product_id\": \"uuid\", \"quantity\": 1}, ...]"),
    ],
) -> dict:
    email = current_user_email.get()
    pool = get_pool()
    async with pool.acquire() as conn:
        # Fetch product details
        cart_items = []
        for item in product_ids_with_quantities:
            pid = item.get("product_id", "")
            qty = item.get("quantity", 1)
            row = await conn.fetchrow(
                "SELECT id, name, price, category FROM products WHERE id = $1",
                pid,
            )
            if not row:
                return {"error": f"Product not found: {pid}"}
            cart_items.append({
                "product_id": str(row["id"]),
                "name": row["name"],
                "price": float(row["price"]),
                "category": row["category"],
                "quantity": qty,
                "subtotal": float(row["price"]) * qty,
            })

        original_total = sum(i["subtotal"] for i in cart_items)
        categories = list({i["category"] for i in cart_items})
        product_ids = [i["product_id"] for i in cart_items]
        product_names = [i["name"] for i in cart_items]
        savings = []

        # 1. Find applicable coupons
        coupons = await conn.fetch(
            """SELECT code, description, discount_type, discount_value,
                      min_spend, max_discount, applicable_categories,
                      user_specific_email
               FROM coupons
               WHERE is_active = TRUE
                 AND (valid_until IS NULL OR valid_until > NOW())
                 AND valid_from <= NOW()
                 AND (usage_limit IS NULL OR times_used < usage_limit)
                 AND (user_specific_email IS NULL OR user_specific_email = $1)
               ORDER BY discount_value DESC""",
            email,
        )

        best_coupon = None
        best_coupon_savings = 0.0
        for c in coupons:
            # Check min spend
            if c["min_spend"] and original_total < float(c["min_spend"]):
                continue
            # Check category applicability
            if c["applicable_categories"]:
                if not any(cat in c["applicable_categories"] for cat in categories):
                    continue
            # Calculate savings
            if c["discount_type"] == "percentage":
                amount = original_total * (float(c["discount_value"]) / 100)
                if c["max_discount"]:
                    amount = min(amount, float(c["max_discount"]))
            else:
                amount = min(float(c["discount_value"]), original_total)
            if amount > best_coupon_savings:
                best_coupon_savings = amount
                best_coupon = c

        if best_coupon:
            savings.append({
                "type": "coupon",
                "code": best_coupon["code"],
                "description": best_coupon["description"],
                "amount": round(best_coupon_savings, 2),
            })

        # 2. Find applicable promotions
        promos = await conn.fetch(
            """SELECT name, type, rules
               FROM promotions
               WHERE is_active = TRUE
                 AND start_date <= NOW()
                 AND end_date >= NOW()""",
        )
        for promo in promos:
            rules = promo["rules"] if isinstance(promo["rules"], dict) else json.loads(promo["rules"])
            promo_type = promo["type"]

            if promo_type == "bundle":
                # Accept ids or names. `scripts/seed.py` writes product *names*
                # under `products`; this only ever read `product_ids`, so
                # `required` was always empty — and `all()` over an empty list
                # is True, so every bundle promotion "matched" every cart and
                # then contributed £0, because the sum below found no items
                # whose id was in that same empty list. Silent noise on every
                # cart-optimisation call since the promotions were seeded.
                required_ids = [str(x) for x in rules.get("product_ids", [])]
                required_names = [str(x) for x in rules.get("products", [])]
                if not required_ids and not required_names:
                    continue  # never vacuously match

                in_cart = (
                    all(pid in product_ids for pid in required_ids)
                    if required_ids
                    else all(name in product_names for name in required_names)
                )
                if not in_cart:
                    continue

                bundle_total = sum(
                    i["subtotal"]
                    for i in cart_items
                    if i["product_id"] in required_ids or i["name"] in required_names
                )
                amount = bundle_total * (_pct(rules.get("discount_pct")) / 100)
                if amount > 0:
                    savings.append({
                        "type": "bundle_promotion",
                        "name": promo["name"],
                        "amount": round(amount, 2),
                    })

            elif promo_type == "buy_x_get_y":
                # Two rule shapes, because the seeded data uses the second and
                # this only understood the first. "Buy 2 Books Get 10% Off" is
                # a percentage discount above a minimum quantity, not a
                # free-units BOGO — and reading it as one meant
                # `buy_quantity`/`free_quantity` both defaulted to 0, so
                # `quantity >= 0 + 0` was always true and the next line divided
                # by zero. That crashed the whole tool (#51).
                cats = _as_list(rules.get("categories") or rules.get("category"))
                buy_qty = int(rules.get("buy_quantity") or 0)
                free_qty = int(rules.get("free_quantity") or 0)
                min_qty = int(rules.get("min_quantity") or 0)
                discount_pct = _pct(rules.get("discount_pct"))

                for item in cart_items:
                    if cats and item["category"] not in cats:
                        continue

                    if buy_qty > 0 and free_qty > 0:
                        # Genuine buy-X-get-Y-free.
                        group = buy_qty + free_qty
                        if item["quantity"] < group:
                            continue
                        free_units = item["quantity"] // group * free_qty
                        amount = item["price"] * free_units
                    elif min_qty > 0 and discount_pct > 0:
                        # Percentage off once a minimum quantity is reached.
                        if item["quantity"] < min_qty:
                            continue
                        amount = item["subtotal"] * (discount_pct / 100)
                    else:
                        # Rules describe neither shape — skip rather than guess.
                        continue

                    if amount > 0:
                        savings.append({
                            "type": "buy_x_get_y",
                            "name": promo["name"],
                            "product": item["name"],
                            "amount": round(amount, 2),
                        })

            elif promo_type == "flash_sale":
                # Same mismatch again: the seed scopes flash sales by
                # `categories`, this only read `product_ids`, so no item ever
                # matched and the promotion was a silent no-op.
                flash_ids = [str(x) for x in rules.get("product_ids", [])]
                flash_cats = _as_list(rules.get("categories") or rules.get("category"))
                if not flash_ids and not flash_cats:
                    continue

                discount_pct = _pct(rules.get("discount_pct"))
                for item in cart_items:
                    matches = item["product_id"] in flash_ids or item["category"] in flash_cats
                    if not matches:
                        continue
                    amount = item["subtotal"] * (discount_pct / 100)
                    if amount > 0:
                        savings.append({
                            "type": "flash_sale",
                            "name": promo["name"],
                            "product": item["name"],
                            "amount": round(amount, 2),
                        })


        # 3. Calculate loyalty discount
        if email:
            user = await conn.fetchrow(
                """SELECT u.loyalty_tier, lt.discount_pct
                   FROM users u
                   JOIN loyalty_tiers lt ON lt.name = u.loyalty_tier
                   WHERE u.email = $1""",
                email,
            )
            if user and float(user["discount_pct"]) > 0:
                loyalty_amount = original_total * (float(user["discount_pct"]) / 100)
                savings.append({
                    "type": "loyalty_discount",
                    "tier": user["loyalty_tier"],
                    "discount_pct": float(user["discount_pct"]),
                    "amount": round(loyalty_amount, 2),
                })

        total_savings = sum(s["amount"] for s in savings)
        final_total = max(0, original_total - total_savings)

        return {
            "cart_items": cart_items,
            "original_total": round(original_total, 2),
            "savings": savings,
            "total_savings": round(total_savings, 2),
            "final_total": round(final_total, 2),
            "savings_percentage": round((total_savings / original_total) * 100, 1) if original_total > 0 else 0,
        }


@tool(name="get_active_deals", description="List all currently active promotions and non-expired coupons.")
async def get_active_deals() -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        coupons = await conn.fetch(
            """SELECT code, description, discount_type, discount_value,
                      min_spend, max_discount, valid_until, applicable_categories
               FROM coupons
               WHERE is_active = TRUE
                 AND (valid_until IS NULL OR valid_until > NOW())
                 AND valid_from <= NOW()
                 AND (usage_limit IS NULL OR times_used < usage_limit)
                 AND user_specific_email IS NULL
               ORDER BY discount_value DESC""",
        )

        promotions = await conn.fetch(
            """SELECT name, type, rules, start_date, end_date
               FROM promotions
               WHERE is_active = TRUE
                 AND start_date <= NOW()
                 AND end_date >= NOW()
               ORDER BY end_date ASC""",
        )

        return {
            "coupons": [
                {
                    "code": c["code"],
                    "description": c["description"],
                    "discount_type": c["discount_type"],
                    "discount_value": float(c["discount_value"]),
                    "min_spend": float(c["min_spend"]) if c["min_spend"] else None,
                    "max_discount": float(c["max_discount"]) if c["max_discount"] else None,
                    "valid_until": c["valid_until"].isoformat() if c["valid_until"] else None,
                    "applicable_categories": c["applicable_categories"],
                }
                for c in coupons
            ],
            "promotions": [
                {
                    "name": p["name"],
                    "type": p["type"],
                    "rules": p["rules"] if isinstance(p["rules"], dict) else json.loads(p["rules"]),
                    "start_date": p["start_date"].isoformat(),
                    "end_date": p["end_date"].isoformat(),
                }
                for p in promotions
            ],
            "total_deals": len(coupons) + len(promotions),
        }


@tool(name="check_bundle_eligibility", description="Check if a set of products qualifies for any bundle promotions.")
async def check_bundle_eligibility(
    product_ids: Annotated[list[str], Field(description="List of product UUIDs to check for bundle deals")],
) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        # Fetch product details
        products = []
        for pid in product_ids:
            row = await conn.fetchrow(
                "SELECT id, name, price, category FROM products WHERE id = $1", pid,
            )
            if row:
                products.append({
                    "product_id": str(row["id"]),
                    "name": row["name"],
                    "price": float(row["price"]),
                    "category": row["category"],
                })

        if not products:
            return {"eligible": False, "error": "No valid products found"}

        # Check bundle promotions
        promos = await conn.fetch(
            """SELECT name, type, rules, start_date, end_date
               FROM promotions
               WHERE is_active = TRUE
                 AND type = 'bundle'
                 AND start_date <= NOW()
                 AND end_date >= NOW()""",
        )

        eligible_bundles = []
        for promo in promos:
            rules = promo["rules"] if isinstance(promo["rules"], dict) else json.loads(promo["rules"])
            required_ids = rules.get("product_ids", [])
            required_categories = rules.get("categories", [])

            # Check by product IDs
            if required_ids:
                matching = [pid for pid in product_ids if pid in required_ids]
                if len(matching) == len(required_ids):
                    discount_pct = rules.get("discount_pct", 0)
                    bundle_total = sum(
                        p["price"] for p in products if p["product_id"] in required_ids
                    )
                    savings = bundle_total * (discount_pct / 100)
                    eligible_bundles.append({
                        "promotion_name": promo["name"],
                        "discount_pct": discount_pct,
                        "bundle_total": round(bundle_total, 2),
                        "savings": round(savings, 2),
                        "end_date": promo["end_date"].isoformat(),
                        "qualifying_products": [
                            p["name"] for p in products if p["product_id"] in required_ids
                        ],
                    })

            # Check by categories
            if required_categories:
                cart_categories = [p["category"] for p in products]
                if all(cat in cart_categories for cat in required_categories):
                    discount_pct = rules.get("discount_pct", 0)
                    matching_products = [
                        p for p in products if p["category"] in required_categories
                    ]
                    bundle_total = sum(p["price"] for p in matching_products)
                    savings = bundle_total * (discount_pct / 100)
                    eligible_bundles.append({
                        "promotion_name": promo["name"],
                        "discount_pct": discount_pct,
                        "bundle_total": round(bundle_total, 2),
                        "savings": round(savings, 2),
                        "end_date": promo["end_date"].isoformat(),
                        "qualifying_products": [p["name"] for p in matching_products],
                    })

        # Also check buy_x_get_y promotions
        bxgy_promos = await conn.fetch(
            """SELECT name, type, rules, end_date
               FROM promotions
               WHERE is_active = TRUE
                 AND type = 'buy_x_get_y'
                 AND start_date <= NOW()
                 AND end_date >= NOW()""",
        )

        bxgy_eligible = []
        for promo in bxgy_promos:
            rules = promo["rules"] if isinstance(promo["rules"], dict) else json.loads(promo["rules"])
            applicable_cats = rules.get("categories", [])
            buy_qty = rules.get("buy_quantity", 0)
            free_qty = rules.get("free_quantity", 0)
            matching = [p for p in products if not applicable_cats or p["category"] in applicable_cats]
            if matching:
                bxgy_eligible.append({
                    "promotion_name": promo["name"],
                    "buy_quantity": buy_qty,
                    "free_quantity": free_qty,
                    "applicable_products": [p["name"] for p in matching],
                    "end_date": promo["end_date"].isoformat(),
                })

        return {
            "eligible": len(eligible_bundles) > 0 or len(bxgy_eligible) > 0,
            "products_checked": [p["name"] for p in products],
            "bundle_deals": eligible_bundles,
            "buy_x_get_y_deals": bxgy_eligible,
        }
