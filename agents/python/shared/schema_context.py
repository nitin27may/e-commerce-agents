"""Reusable DB schema context strings for agent system prompts.

Each constant describes the relevant database tables, columns, valid values,
and relationships so the LLM can reason about which tools to call and how to
interpret results.
"""

USER_SCHEMA_CONTEXT = """
## Database: Users
- **users**: id (UUID), email, name, role, loyalty_tier, total_spend, created_at, is_active
  - Roles: customer, power_user, seller, admin
  - Loyalty tiers: bronze (default), silver (spend >= $1000, 5% discount), gold (spend >= $3000, 10% discount + free shipping + priority support)
  - The current user's email is available — always use it to scope queries
"""

ORDER_SCHEMA_CONTEXT = """
## Database: Orders & Returns
- **orders**: id (UUID), user_id (FK→users), status, total, shipping_address (JSONB: street/city/state/zip/country), shipping_carrier, tracking_number, coupon_code, discount_amount, created_at
  - Status flow: placed → confirmed → shipped → out_for_delivery → delivered
  - Also: cancelled (from placed/confirmed), returned (from delivered)
- **order_items**: order_id (FK→orders), product_id (FK→products), quantity, unit_price, subtotal
- **order_status_history**: order_id (FK→orders), status, notes, location, timestamp — full tracking timeline
- **returns**: order_id (FK→orders), user_id (FK→users), reason, status, return_label_url, refund_method, refund_amount, created_at, resolved_at
  - Return statuses: requested → approved → shipped_back → received → refunded (or denied)
  - Refund methods: original_payment, store_credit
  - Return window: 30 days from delivery
"""

PRODUCT_SCHEMA_CONTEXT = """
## Database: Products
- **products**: id (UUID), name, description, category, brand, price, original_price, image_url, rating (1.0-5.0), review_count, specs (JSONB), is_active, created_at
  - Categories: Electronics, Clothing, Home, Sports, Books
  - specs JSONB varies by category (e.g., Electronics: battery, weight, connectivity; Clothing: material, fit)
  - If original_price > price, the product is on sale
- **product_embeddings**: product_id (FK→products), embedding (vector 1536-dim) — for semantic search
- **price_history**: product_id (FK→products), price, recorded_at — 90 days of daily prices
"""

INVENTORY_SCHEMA_CONTEXT = """
## Database: Inventory & Shipping
- **warehouses**: id (UUID), name, location, region
  - Regions: east (Richmond VA), central (Dallas TX), west (Portland OR)
- **warehouse_inventory**: warehouse_id + product_id (composite PK), quantity, reorder_threshold
  - low_stock = quantity <= reorder_threshold
- **carriers**: id (UUID), name, speed_tier, base_rate
  - Speed tiers: standard (5-7 days, ~$6-10), express (2-3 days, ~$15-21), overnight (1 day, ~$30-40)
- **shipping_rates**: carrier_id (FK→carriers), region_from, region_to, price, estimated_days_min, estimated_days_max
- **restock_schedule**: product_id (FK→products), warehouse_id (FK→warehouses), expected_quantity, expected_date
"""

PRICING_SCHEMA_CONTEXT = """
## Database: Pricing & Promotions
- **coupons**: code (unique), description, discount_type, discount_value, min_spend, max_discount, usage_limit, times_used, valid_from, valid_until, applicable_categories (TEXT[]), user_specific_email, is_active
  - Discount types: percentage (e.g., 10% off), fixed (e.g., $25 off)
  - applicable_categories: NULL means all categories, or array like {Electronics,Home}
  - user_specific_email: NULL means any user, or specific email for personal coupons
- **promotions**: name, type, rules (JSONB), start_date, end_date, is_active
  - Types: bundle (buy multiple products for discount), buy_x_get_y (quantity discount), flash_sale (category-wide discount)
  - rules JSONB varies: bundle has {products: [...], discount_pct: N}, buy_x_get_y has {category, min_quantity, discount_pct}
- **loyalty_tiers**: name, min_spend, discount_pct, free_shipping_threshold, priority_support
  - bronze: $0+, 0% discount
  - silver: $1000+, 5% discount, free shipping over $75
  - gold: $3000+, 10% discount, free shipping always, priority support
"""

REVIEW_SCHEMA_CONTEXT = """
## Database: Reviews
- **reviews**: id (UUID), product_id (FK→products), user_id (FK→users), rating (1-5 integer), title, body, verified_purchase (boolean), helpful_count, is_flagged (suspicious), created_at
  - verified_purchase: true if the reviewer actually bought the product
  - is_flagged: true for reviews detected as potentially fake (generic language, 5-star burst, unverified)
  - Rating distribution across all products roughly: 5★ 35%, 4★ 35%, 3★ 15%, 2★ 10%, 1★ 5%
"""
