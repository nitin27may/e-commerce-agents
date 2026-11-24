# Database Schema

This page is the table-by-table reference for the application's one PostgreSQL database, for anyone writing a query, a tool or a migration against it. Both stacks share it.

It is PostgreSQL 16 with the **pgvector** extension for embedding-based semantic search and **pgcrypto** for UUID generation. The frozen baseline (`docker/postgres/init.sql`) has 34 tables in 12 logical groups. Migrations `0003`, `0006`, `0009`, `0011`, `0012`, `0016` and `0017` add 13 more (47 in all), described in the sections for refresh tokens, approvals, merchant proposals, event autonomy, customer confirmations and more autonomy events below. Migrations `0002`, `0004`, `0005`, `0007`, `0008`, `0010`, `0013`, `0014` and `0015` only alter or backfill existing tables.

## Entity-Relationship Diagram

```mermaid
erDiagram
    %% ── Auth & Users ──────────────────────────────────
    users {
        uuid id PK
        varchar email UK
        varchar password_hash
        varchar name
        varchar role
        varchar loyalty_tier
        decimal total_spend
        timestamptz created_at
        boolean is_active
    }

    %% ── Product Catalog ───────────────────────────────
    products {
        uuid id PK
        varchar name
        text description
        varchar category
        varchar brand
        decimal price
        decimal original_price
        varchar image_url
        decimal rating
        integer review_count
        jsonb specs
        boolean is_active
        timestamptz created_at
    }

    product_embeddings {
        uuid id PK
        uuid product_id FK
        vector_1536 embedding
        timestamptz created_at
    }

    price_history {
        uuid id PK
        uuid product_id FK
        decimal price
        timestamptz recorded_at
    }

    %% ── Orders & Returns ──────────────────────────────
    orders {
        uuid id PK
        uuid user_id FK
        varchar status
        decimal total
        jsonb shipping_address
        varchar shipping_carrier
        varchar tracking_number
        varchar coupon_code
        decimal discount_amount
        timestamptz created_at
    }

    order_items {
        uuid id PK
        uuid order_id FK
        uuid product_id FK
        integer quantity
        decimal unit_price
        decimal subtotal
    }

    order_status_history {
        uuid id PK
        uuid order_id FK
        varchar status
        text notes
        varchar location
        timestamptz timestamp
    }

    returns {
        uuid id PK
        uuid order_id FK
        uuid user_id FK
        varchar reason
        varchar status
        varchar return_label_url
        varchar refund_method
        decimal refund_amount
        timestamptz created_at
        timestamptz resolved_at
    }

    %% ── Reviews ───────────────────────────────────────
    reviews {
        uuid id PK
        uuid product_id FK
        uuid user_id FK
        integer rating
        varchar title
        text body
        boolean verified_purchase
        integer helpful_count
        boolean is_flagged
        timestamptz created_at
    }

    %% ── Inventory & Shipping ──────────────────────────
    warehouses {
        uuid id PK
        varchar name
        varchar location
        varchar region
    }

    warehouse_inventory {
        uuid warehouse_id PK_FK
        uuid product_id PK_FK
        integer quantity
        integer reorder_threshold
    }

    carriers {
        uuid id PK
        varchar name
        varchar speed_tier
        decimal base_rate
    }

    shipping_rates {
        uuid id PK
        uuid carrier_id FK
        varchar region_from
        varchar region_to
        decimal price
        integer estimated_days_min
        integer estimated_days_max
    }

    restock_schedule {
        uuid id PK
        uuid product_id FK
        uuid warehouse_id FK
        integer expected_quantity
        date expected_date
    }

    %% ── Pricing & Promotions ─────────────────────────
    coupons {
        uuid id PK
        varchar code UK
        text description
        varchar discount_type
        decimal discount_value
        decimal min_spend
        decimal max_discount
        integer usage_limit
        integer times_used
        timestamptz valid_from
        timestamptz valid_until
        text_array applicable_categories
        varchar user_specific_email
        boolean is_active
    }

    promotions {
        uuid id PK
        varchar name
        varchar type
        jsonb rules
        timestamptz start_date
        timestamptz end_date
        boolean is_active
    }

    loyalty_tiers {
        uuid id PK
        varchar name UK
        decimal min_spend
        decimal discount_pct
        decimal free_shipping_threshold
        boolean priority_support
    }

    %% ── Marketplace ──────────────────────────────────
    agent_catalog {
        uuid id PK
        varchar name UK
        varchar display_name
        text description
        varchar category
        varchar icon
        varchar status
        varchar version
        text_array capabilities
        text_array input_types
        text_array output_types
        boolean requires_approval
        text_array allowed_roles
        jsonb config
    }

    access_requests {
        uuid id PK
        uuid user_id FK
        varchar agent_name FK
        varchar role_requested
        text use_case
        varchar status
        text admin_notes
        uuid reviewed_by FK
        timestamptz created_at
        timestamptz resolved_at
    }

    agent_permissions {
        uuid id PK
        uuid user_id FK
        varchar agent_name FK
        varchar role
        timestamptz granted_at
        uuid granted_by FK
    }

    %% ── Conversations & Usage ────────────────────────
    conversations {
        uuid id PK
        uuid user_id FK
        varchar title
        boolean is_active
        timestamptz created_at
        timestamptz last_message_at
    }

    messages {
        uuid id PK
        uuid conversation_id FK
        varchar role
        text content
        varchar agent_name
        text_array agents_involved
        jsonb metadata
        integer tokens_in
        integer tokens_out
        timestamptz created_at
    }

    usage_logs {
        uuid id PK
        uuid user_id FK
        varchar agent_name
        uuid session_id
        varchar trace_id
        text input_summary
        integer tokens_in
        integer tokens_out
        integer tool_calls_count
        integer duration_ms
        varchar status
        text error_message
        timestamptz created_at
        timestamptz priced_at
        text price_source
    }

    agent_execution_steps {
        uuid id PK
        uuid usage_log_id FK
        integer step_index
        varchar tool_name
        jsonb tool_input
        jsonb tool_output
        varchar status
        integer duration_ms
        timestamptz created_at
    }

    %% ── Relationships ─────────────────────────────────

    users ||--o{ orders : places
    users ||--o{ reviews : writes
    users ||--o{ returns : requests
    users ||--o{ conversations : has
    users ||--o{ access_requests : submits
    users ||--o{ agent_permissions : granted
    users ||--o{ usage_logs : generates

    products ||--o| product_embeddings : has
    products ||--o{ price_history : tracks
    products ||--o{ order_items : sold_in
    products ||--o{ reviews : reviewed_in
    products ||--o{ warehouse_inventory : stocked_at
    products ||--o{ restock_schedule : restocked_by

    orders ||--o{ order_items : contains
    orders ||--o{ order_status_history : tracked_by
    orders ||--o| returns : returned_via

    warehouses ||--o{ warehouse_inventory : holds
    warehouses ||--o{ restock_schedule : receives
    carriers ||--o{ shipping_rates : priced_at

    agent_catalog ||--o{ access_requests : requested_for
    agent_catalog ||--o{ agent_permissions : controls

    conversations ||--o{ messages : contains
    usage_logs ||--o{ agent_execution_steps : details
```

---

## Table Groups

### Auth & Users

| Table | Key Columns | Notes |
|-------|-------------|-------|
| **users** | `id` (PK), `email` (unique), `password_hash`, `name`, `role`, `loyalty_tier`, `total_spend`, `is_active` | Roles: `customer`, `power_user`, `seller`, `admin`. Loyalty tiers: `bronze`, `silver`, `gold`. Passwords hashed with bcrypt. |
| **refresh_tokens** (`0003`) | `jti` (PK), `user_email` (FK -> users.email, cascades), `family_id`, `expires_at`, `revoked_at`, `replaced_by`, `family_expires_at` (`0005`) | One row per refresh token. Rotation revokes the presented token and records its successor in `replaced_by`. `family_expires_at` is stamped when a login starts a family and every successor inherits it, so a session cannot refresh forever (`AUTH_REFRESH_FAMILY_MAX_DAYS`, default 30). |

### Product Catalog

| Table | Key Columns | Notes |
|-------|-------------|-------|
| **products** | `id` (PK), `name`, `category`, `brand`, `price`, `original_price`, `rating`, `specs` (JSONB) | Categories: Electronics, Clothing, Home, Sports, Books. `specs` stores product-specific attributes as flexible JSON. |
| **product_embeddings** | `id` (PK), `product_id` (FK -> products), `embedding` (vector(1536)), `embedding_model` (migration 0008) | One embedding per product. Defaults to `text-embedding-3-small` (1536 dimensions); the width is `EMBEDDING_DIM` and changes only through `scripts/reset_embeddings.py` (see [configuration](configuration.md#embeddings-are-configured-separately)). `embedding_model` records which model wrote the vector (`replay-hashing-vectorizer` for offline ones; NULL for rows from before 0008). IVFFlat index with 10 lists for cosine similarity search. |
| **price_history** | `id` (PK), `product_id` (FK -> products), `price`, `recorded_at` | Daily price snapshots. Seeder generates 90 days of history. |

**Indexes**
- `idx_products_category` on `products(category)`
- `idx_products_price` on `products(price)`
- `idx_products_rating` on `products(rating DESC)`
- `idx_product_embedding` on `product_embeddings(embedding)` using IVFFlat with `vector_cosine_ops`


### `promotions.rules` schema

`rules` is untyped JSONB, so this table is the contract between the writer
(`scripts/seed.py`) and the reader (`pricing_promotions/tools.py::optimize_cart`).
A key name outside it means the promotion does not apply, and it usually fails
silently.

| type | Accepted keys | Meaning |
|---|---|---|
| `bundle` | `products` (names) **or** `product_ids` (UUIDs), `discount_pct` | Percentage off the listed items, only when **all** of them are in the cart |
| `buy_x_get_y` | `buy_quantity` + `free_quantity`, optional `category`/`categories` | Genuine buy-X-get-Y-free |
| `buy_x_get_y` | `min_quantity` + `discount_pct`, optional `category`/`categories` | Percentage off once a minimum quantity is reached — what "Buy 2 Books Get 10% Off" actually means |
| `flash_sale` | `categories` **and/or** `product_ids`, `discount_pct` | Percentage off matching items |

Both singular (`category`) and plural (`categories`) are accepted, because the
seeded rows use both.

Two rules the reader enforces:

- **An empty requirement list never matches.** Without this rule a `bundle` with
  no `products` and no `product_ids` matches *every* cart, because `all([])` is
  `True`, and contributes £0 to each.
- **Unparseable rules are skipped, not guessed at.** A `buy_x_get_y` row
  describing neither shape would otherwise read `buy_quantity` and
  `free_quantity` as `0`, making `quantity >= 0 + 0` always true and dividing by
  zero on the next line. A discount outside 0–100% is treated as a data error and ignored rather
  than applied.

> **Gotcha — an IVFFlat index must be built *after* the vectors exist.**
> IVFFlat partitions the vector space into `lists` clusters using whatever data
> is present when the index is built. `init.sql` creates this index on an empty
> table, so it has nothing to derive centroids from, and at the default
> `ivfflat.probes = 1` a query probes one degenerate partition and returns
> whatever happens to be in it — or nothing.
>
> Measured on a seeded database, same data and same query, index alone being
> the difference:
>
> | | Top result | Similarity |
> |---|---|---|
> | through the index | Patagonia Better Sweater | 0.000 |
> | exact scan | Sony WH-1000XM5 | 0.420 |
>
> Nothing errors. Semantic search simply returns unrelated products, which is
> why it is easy to miss: it looks like a weak embedding model rather than a broken
> index. Two defences are in place — `scripts/generate_embeddings.py` runs
> `REINDEX INDEX idx_product_embedding` after writing, and `semantic_search` /
> `find_similar_products` raise `ivfflat.probes` for their own query so
> correctness does not depend on someone having remembered to reindex. The
> same applies after any wholesale re-embedding: centroids computed for the
> previous vectors do not describe the new ones.
- `idx_price_history` on `price_history(product_id, recorded_at DESC)`

### Orders & Returns

| Table | Key Columns | Notes |
|-------|-------------|-------|
| **orders** | `id` (PK), `user_id` (FK -> users), `status`, `total`, `shipping_address` (JSONB), `coupon_code`, `discount_amount` | Statuses: `placed`, `confirmed`, `shipped`, `out_for_delivery`, `delivered`, `cancelled`, `returned`. Shipping address stored as `{street, city, state, zip, country}`. |
| **order_items** | `id` (PK), `order_id` (FK -> orders), `product_id` (FK -> products), `quantity`, `unit_price`, `subtotal` | Line items for each order. |
| **order_status_history** | `id` (PK), `order_id` (FK -> orders), `status`, `notes`, `location`, `timestamp` | Tracking timeline with location info per status change. |
| **returns** | `id` (PK), `order_id` (FK -> orders), `user_id` (FK -> users), `reason`, `status`, `refund_method`, `refund_amount` | Return statuses: `requested`, `approved`, `shipped_back`, `received`, `refunded`, `denied`. Refund methods: `original_payment`, `store_credit`. |

**Indexes**
- `idx_orders_user` on `orders(user_id, created_at DESC)`
- `idx_orders_status` on `orders(status)`

### Reviews

| Table | Key Columns | Notes |
|-------|-------------|-------|
| **reviews** | `id` (PK), `product_id` (FK -> products), `user_id` (FK -> users), `rating` (1-5), `title`, `body`, `verified_purchase`, `is_flagged` | `CHECK (rating BETWEEN 1 AND 5)`. `is_flagged` marks reviews detected as potentially fake by the sentiment agent. |

**Indexes**
- `idx_reviews_product` on `reviews(product_id, created_at DESC)`
- `idx_reviews_rating` on `reviews(product_id, rating)`

### Inventory & Shipping

| Table | Key Columns | Notes |
|-------|-------------|-------|
| **warehouses** | `id` (PK), `name`, `location`, `region` | 3 warehouses: East (Richmond, VA), Central (Chicago, IL), West (Portland, OR). |
| **warehouse_inventory** | `warehouse_id` + `product_id` (composite PK), `quantity`, `reorder_threshold` | Per-warehouse stock levels. Composite primary key. |
| **carriers** | `id` (PK), `name`, `speed_tier`, `base_rate` | Speed tiers: `standard`, `express`, `overnight`. |
| **shipping_rates** | `id` (PK), `carrier_id` (FK -> carriers), `region_from`, `region_to`, `price`, `estimated_days_min/max` | Region-to-region pricing with delivery time estimates. |
| **restock_schedule** | `id` (PK), `product_id` (FK -> products), `warehouse_id` (FK -> warehouses), `expected_quantity`, `expected_date` | Scheduled incoming inventory. |

**Indexes**
- `idx_warehouse_inv` on `warehouse_inventory(product_id)`

### Pricing & Promotions

| Table | Key Columns | Notes |
|-------|-------------|-------|
| **coupons** | `id` (PK), `code` (unique), `discount_type`, `discount_value`, `min_spend`, `max_discount`, `applicable_categories` (TEXT[]), `user_specific_email` | Types: `percentage`, `fixed`. `applicable_categories` is NULL for all-category coupons. `user_specific_email` is NULL for universal coupons. |
| **promotions** | `id` (PK), `name`, `type`, `rules` (JSONB), `start_date`, `end_date` | Types: `bundle`, `buy_x_get_y`, `flash_sale`. See the rules schema below — "flexible JSONB" is what let the seed and the reader drift apart. |
| **loyalty_tiers** | `id` (PK), `name` (unique), `min_spend`, `discount_pct`, `free_shipping_threshold`, `priority_support` | 3 tiers: bronze ($0, 0%), silver ($1000, 5%), gold ($3000, 10%). |

### Marketplace

| Table | Key Columns | Notes |
|-------|-------------|-------|
| **agent_catalog** | `id` (PK), `name` (unique), `display_name`, `description`, `capabilities` (TEXT[]), `requires_approval`, `allowed_roles` (TEXT[]) | 6 agents registered. `name` is the FK target (not `id`) for simpler references. |
| **access_requests** | `id` (PK), `user_id` (FK -> users), `agent_name` (FK -> agent_catalog.name), `role_requested`, `use_case`, `status`, `reviewed_by` (FK -> users) | Statuses: `pending`, `approved`, `denied`. Admin review tracked with `reviewed_by` and `resolved_at`. |
| **agent_permissions** | `id` (PK), `user_id` (FK -> users), `agent_name` (FK -> agent_catalog.name), `role`, `granted_by` (FK -> users) | UNIQUE constraint on `(user_id, agent_name)`. Upserted on approval. |

**Indexes**
- `idx_access_requests_status` on `access_requests(status, created_at DESC)`

### Conversations & Usage

| Table | Key Columns | Notes |
|-------|-------------|-------|
| **conversations** | `id` (PK), `user_id` (FK -> users), `title`, `is_active`, `last_message_at` | Soft-delete via `is_active`. Title auto-set from first user message (first 100 chars). |
| **messages** | `id` (PK), `conversation_id` (FK -> conversations), `role`, `content`, `agent_name`, `agents_involved` (TEXT[]), `metadata` (JSONB) | Roles: `user`, `assistant`, `system`. `agents_involved` tracks which specialist agents contributed. `metadata` stores tool call data and trace info. |
| **usage_logs** | `id` (PK), `user_id` (FK -> users), `agent_name`, `trace_id`, `tokens_in`, `tokens_out`, `tokens_cached_in`, `model`, `cost_usd`, `tool_calls_count`, `duration_ms`, `status` | `model`, `tokens_cached_in` and `cost_usd` (migration `0007`) are NULL when the run's usage was not captured; `cost_usd = 0` is a mode that makes no LLM call. `price_source` (`models.yaml#<digest>` or `override:<file>#<digest>`, comma-joined when several) and `priced_at` (migration `0015`) say which pricing file priced the row and when; both are NULL for older rows, uncaptured usage, an unpriced model and a no-LLM mode. `trace_id` correlates with OTel traces in the Aspire Dashboard. Status: `success` or `error`. |
| **agent_execution_steps** | `id` (PK), `usage_log_id` (FK -> usage_logs), `step_index`, `tool_name`, `tool_input` (JSONB), `tool_output` (JSONB), `duration_ms` | Ordered by `step_index`. Records each tool invocation within an agent execution. |

**Indexes**
- `idx_usage_logs_user` on `usage_logs(user_id, created_at DESC)`
- `idx_usage_logs_agent` on `usage_logs(agent_name, created_at DESC)`
- `idx_usage_logs_trace` on `usage_logs(trace_id)`
- `idx_messages_conversation` on `messages(conversation_id, created_at)`

### Cart & Checkout

| Table | Key Columns | Notes |
|-------|-------------|-------|
| **carts** | `id` (PK), `user_id` (FK -> users, **UNIQUE**), `shipping_address` (JSONB), `billing_address` (JSONB), `billing_same_as_shipping`, `coupon_code`, `discount_amount` | One cart per user, enforced by the unique constraint — a second cart is impossible rather than merely unlikely. Addresses are JSONB (`{name, street, city, state, zip, country, phone}`), which is why the UI has to handle both string and object shapes when rendering one. |
| **cart_items** | `id` (PK), `cart_id` (FK -> carts, `ON DELETE CASCADE`), `product_id` (FK -> products), `quantity` (`CHECK > 0`) | `UNIQUE(cart_id, product_id)` — adding the same product twice increments the quantity instead of inserting a second row. |

### Agent Memory

| Table | Key Columns | Notes |
|-------|-------------|-------|
| **agent_memories** | `id` (PK), `user_id` (FK -> users), `category`, `content`, `importance` (SMALLINT), `embedding` (`vector(1536)`; resized with `product_embeddings` by `scripts/reset_embeddings.py`), `expires_at`, `is_active` | Long-term per-user memory, distinct from conversation history: written by `store_memory` and read by `recall_memories`. Embedded, so recall is semantic rather than keyword. Soft-deleted via `is_active`; `expires_at` lets a memory age out. |

### Human-in-the-Loop & Durability

| Table | Key Columns | Notes |
|-------|-------------|-------|
| **tool_approval_requests** | `id` (PK), `session_id`, `user_email`, `agent_name`, `tool_name`, `tool_input` (JSONB), `status`, `approved_by`, `execution_result` (JSONB) | The *middleware* HITL path: a gated tool is intercepted before it runs, a row is written, and the tool returns `pending_approval` without calling through. Approval re-executes the operation directly — the LLM loop is never resumed. Writes fail **closed**: if the row cannot be written the tool does not execute. |
| **hitl_requests** | `id` (PK), `workflow_run_id` (FK -> usage_logs, `ON DELETE CASCADE`), `request_id`, `checkpoint_id` (FK -> workflow_checkpoints), `kind`, `payload` (JSONB), `status`, `response` (JSONB) | The *in-workflow* HITL path, and a genuinely different mechanism: `request_id` is MAF's own resume token and `checkpoint_id` points at the paused graph, so approving one resumes the real workflow from where it stopped. Status: `pending`, `approved`, `rejected`, `timeout`. |
| **workflow_checkpoints** | `checkpoint_id` (PK), `workflow_name`, `payload` (JSONB), `usage_log_id` (FK -> usage_logs) | Encoded MAF `WorkflowCheckpoint`. A workflow that pauses does not survive as a live object across requests — resume rebuilds a fresh graph from this row plus the response. |
| **idempotency_keys** | `key` (PK, VARCHAR(600)), `scope`, `status` (`in_progress` \| `completed`), `result` (JSONB), `completed_at` | Reserved with `INSERT ... ON CONFLICT DO NOTHING`, so a duplicate is refused rather than racing. A completed reservation replays its cached `result`; one older than 60s is reclaimed, which is how a crashed process recovers. This is what stops an approved refund from executing twice. |

**Note on the FK between them:** `hitl_requests.checkpoint_id` references `workflow_checkpoints`, so a bare `TRUNCATE workflow_checkpoints` fails regardless of the FK's `ON DELETE` action — truncate both together or use `CASCADE`.

### Approvals Foundation (migration `0006`)

One inbox for every approval kind, with an audit trail and an outbox. Python repository only (`shared/approvals/repo.py`, `shared/outbox.py`). `approvals` and `approval_events` are written by every gated tool call and every paused return and by the approvals API; `outbox` and `notifications` are written too: entering `pending_review` enqueues a reminder and an SLA timer in the approval's transaction (`shared/outbox.py`), and the worker in `agents/python/worker/` claims those rows (migration `0011` adds `locked_by`, `failed_at` and role-addressed notifications) and writes `notifications` (`shared/notifications.py`).

| Table | Key Columns | Notes |
|-------|-------------|-------|
| **approvals** | `id` (PK), `source` (`tool` \| `workflow` \| `proposal` \| `event`), `source_ref`, `kind`, `requester_email`, `subject_email`, `amount`, `policy_version`, `route` (`auto` \| `support` \| `manager`), `assigned_role`, `status`, `recommendation` / `decision` (JSONB), `decided_by`, `due_at`, `escalation_level`, `claimed_at` / `claimed_by` | Status: `pending_confirm`, `pending_review`, `needs_info`, `approved`, `approved_with_edits`, `rejected`, `expired`, `executed`, `failed`. The CHECK `approvals_no_self_approval` forbids `decided_by = requester_email` (case-insensitive); the repository refuses it first. `claimed_at` fences whoever executes a decision. One row per `(source, source_ref)`. |
| **approval_events** | `id` (BIGSERIAL PK), `approval_id` (FK, `ON DELETE RESTRICT`), `actor_email`, `actor_role`, `action`, `payload` (JSONB) | Append-only: a trigger refuses UPDATE and DELETE. |
| **outbox** | `id` (BIGSERIAL PK), `topic`, `aggregate_id`, `payload` (JSONB), `dedupe_key` (UNIQUE), `available_at`, `attempts`, `locked_until`, `processed_at`, `last_error` | Events and timers (a timer is a future `available_at`). `enqueue()` runs on the caller's connection so it joins that transaction. |
| **notifications** | `id` (PK), `user_email` (nullable), `role` (`support` \| `admin`, nullable), `kind`, `title`, `body`, `link`, `approval_id` (FK, `ON DELETE SET NULL`), `read_at` | Addressed to a person or to a role, never both (`notifications_one_addressee`, migration `0011`). |
| **notification_reads** (`0011`) | `(notification_id, user_email)` (PK), `read_at` | Per-reader read state for role-addressed notifications, where one row has many readers. |

`tool_approval_requests` and `hitl_requests` each gained a nullable `approval_id` pointing at `approvals`; they stay as the per-source execution records.

### Merchant proposals (migration `0009`)

Seller markdown and restock suggestions ([merchant-proposals.md](merchant-proposals.md)). Python only. One `approvals` row per batch (source `proposal`); these tables hold the batch and its lines.

| Table | Key Columns | Notes |
|-------|-------------|-------|
| **proposals** | `id` (PK), `seller_id` (FK -> users), `kind` (`markdown` \| `restock` \| `mixed`), `trigger` (`manual` \| `scheduled` \| `event`), `status` (`pending_review` \| `decided` \| `executed` \| `cancelled`) | One batch per generation run. |
| **proposal_lines** | `id` (PK), `proposal_id` (FK, cascades), `product_id` (FK), `field` (`price` \| `restock_quantity`), `current_value`, `proposed_value`, `final_value`, `rationale`, `evidence` / `guard_result` (JSONB), `status` (`proposed`, `accepted`, `edited`, `rejected`, `blocked`, `executed`, `failed`), `executed_at` | Unique on `(proposal_id, product_id, field)`. A line executes at most once. |
| **promotion_products** | `(promotion_id, product_id)` (PK) | Which products a promotion applies to. The same migration adds `products.unit_cost` (nullable, `>= 0`). |

### Event autonomy (migration `0012`)

What the system did on its own when an event arrived ([event-autonomy.md](event-autonomy.md)). Python only. Both tables are append-only: a trigger refuses `UPDATE` and `DELETE`, and refuses `TRUNCATE` while they hold rows (`reset_autonomy_log()` is the one named reset).

| Table | Key Columns | Notes |
|-------|-------------|-------|
| **agent_decisions** | `id` (PK), `entry_type` (`decision` \| `approved_execution` \| `reversal`), `event_key`, `event_type`, `outbox_id`, `action`, `budget_key`, `budget_day`, `user_id`, `order_id`, `amount`, `tier` (`auto`, `auto_notify`, `needs_approval`, `no_action`), `outcome` (`executed`, `awaiting_approval`, `no_action`, `reversed`), `reasons` / `input` / `budget` (JSONB), `policy_version`, `approval_id`, `reversible`, `reverses_id`, `actor_email` | Unique `event_key` per decision (an event acts once), unique `reverses_id` (a reversal happens once) and unique `approval_id` per approved execution. A reversal is a new row and does not return the day's budget. |
| **store_credit_ledger** | `id` (PK), `user_id` (FK), `amount` (signed, non-zero), `reason`, `decision_id` (FK -> agent_decisions) | Credit is positive, a reversal a negating entry. Unique per `decision_id`. |

Migration `0012` also adds the CHECK `approvals_no_beneficiary_decision`: a reviewer is never the approval's subject (merchant proposals are the named exemption).

### More autonomy events (migration `0017`, #299)

| Change | Notes |
|---|---|
| `agent_decisions.order_item_id` (FK -> order_items) | The order line a price adjustment is for. Partial unique index `uq_agent_decisions_line_adjust`: at most one paid adjustment (an executed decision or an approved execution) per line, a reversal does not free it |
| `notifications.decision_id` | The decision row a customer notification belongs to, so a drafted message can replace its text. No foreign key |
| **decision_drafts** | `id` (PK), `decision_id` (FK -> agent_decisions, unique), `body`, `source` (`llm` or `template`), `model`, `fallback_reason`, `grounding`. The ops-responder's message for a decision. Append-only, like the log it hangs off; `reset_autonomy_log()` clears it too |

### Return lifecycle columns (migration `0013`)

`returns` gains `approval_id`, `resolution` (`refund` \| `store_credit` \| `replacement`), `scanned_at` and `inspection` (JSONB). The two waits are `hitl_requests` rows with `kind` `carrier_scan` / `return_inspection`, so there is no new table ([return-lifecycle.md](return-lifecycle.md)).

### Customer confirmations (migration `0016`)

| Table | Key columns | Notes |
|---|---|---|
| **customer_confirmations** | `id` (PK), `user_email`, `session_id` (text, `''` when none), `tool_name`, `args_key` (sha256 of the normalised identifying arguments), `tool_input` (JSONB), `asked_by_message`, `status` (`pending` \| `confirmed` \| `declined` \| `expired`), `created_at`, `expires_at`, `resolved_at` | The customer's own confirmation of a gated action, asked before the call parks for staff (`shared/customer_confirmation.py`, [approvals.md](approvals.md)). One open row per (lower-cased user, session, tool, `args_key`) by a partial unique index. Not an approval: no `approvals` row and no audit event. `asked_by_message` is the normalised user message that raised the question, which can never also answer it. |

### OAuth2 Authorization Server

Present only when `AUTH_MODE=oauth`; the default `local` mode never touches these.

| Table | Key Columns | Notes |
|-------|-------------|-------|
| **oauth_clients** | `client_id` (PK), `client_secret_hash`, `client_name`, `is_confidential`, `allowed_grant_types` (TEXT[]), `allowed_scopes` (TEXT[]), `allowed_audiences` (TEXT[]), `token_endpoint_auth_method` | Secrets are hashed, never stored raw. The three array columns are what make cross-scope and cross-audience token requests refusable. |
| **oauth_signing_keys** | `kid` (PK), `alg` (default RS256), `public_jwk` (JSONB), `private_pem_enc` (BYTEA), `is_active`, `not_after`, `retired_at` | The private key is Fernet-encrypted at rest; only `public_jwk` is served from the JWKS endpoint. A rotated-out key stays in the JWKS until `not_after` (migration `0014`), so tokens still in flight keep verifying; `retired_at` records when it was dropped. A unique index allows one active key. |
| **oauth_tokens** | `id` (PK), `client_id` (FK -> oauth_clients), `subject`, `token_type`, `token_hash`, `scope`, `audience`, `expires_at`, `revoked` | Only refresh tokens are persisted, and only as a SHA-256 digest — the raw token is never stored. `subject` is the user's email for password-grant tokens and NULL for client-credentials service tokens. |

---

## Seed Data Summary

The seeder (`scripts/seed.py`) populates the database with deterministic data (`random.seed(42)`):

| Table | Count | Details |
|-------|-------|---------|
| users | 22 | 1 admin, 2 power users, 2 sellers, 15 customers, plus 1 support staff user and the demo-tour customer (`tour@example.com`) appended after them |
| products | 50 | 10 per category (Electronics, Clothing, Home, Sports, Books) |
| product_embeddings | 50 under `LLM_PROVIDER=replay`; otherwise 0 until `scripts/data/product_embeddings.v1.jsonl.gz` exists | Filled by the seeder (replay vectors, or the precomputed file when its model matches `EMBEDDING_MODEL`); `scripts/generate_embeddings.py` regenerates, `--export` writes the file |
| orders | 200 | Distributed across statuses (placed through delivered/cancelled) |
| order_items | ~400 | 1-4 items per order |
| order_status_history | ~600 | Full tracking timeline per order |
| returns | ~20 | Subset of delivered orders |
| reviews | 500 | 5% flagged as potentially fake |
| warehouses | 3 | East, Central, West |
| warehouse_inventory | 150 | Each product stocked at all 3 warehouses |
| carriers | 3 | Standard, Express, Overnight |
| shipping_rates | ~27 | All region-to-region combinations for all carriers |
| restock_schedule | ~30 | Upcoming restocks for low-inventory products |
| coupons | 15 | Mix of percentage and fixed discounts, some user-specific |
| promotions | 5 | Bundle deals, buy-X-get-Y, flash sales |
| loyalty_tiers | 3 | Bronze (0%), Silver (5%), Gold (10%) |
| price_history | ~4500 | 90 daily snapshots per product |
| agent_catalog | 6 | One entry per specialist agent |
| agent_permissions | ~8 | Pre-seeded for admin and power users |

### Default Login Credentials

| Role | Email | Password |
|------|-------|----------|
| Admin | `admin.demo@gmail.com` | `admin123` |
| Power User | `power.demo@gmail.com` | `power123` |
| Customer | `alice.johnson@gmail.com` | `customer123` |

---

## Extensions

```sql
CREATE EXTENSION IF NOT EXISTS vector;     -- pgvector for embedding search
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()
```

## Gotchas

- **product_embeddings** is filled by the seeder: replay-scheme vectors under `LLM_PROVIDER=replay`, or the precomputed `scripts/data/product_embeddings.v1.jsonl.gz` under openai/azure when its model matches `EMBEDDING_MODEL` (the seeder never calls an embedding API; a human generates the file with `generate_embeddings --export`). With no file the table stays empty and `semantic_search` runs its full-text arm alone; it also does so whenever the query-time embedding call fails, and `find_similar_products` falls back to same-category-by-rating for a product with no vector.
- **product_embeddings** uses an IVFFlat index (`lists = 10`). This index type requires the table to have existing data before creation, or it will be empty, so the seeder and `generate_embeddings.py` both reindex after writing.
- **agent_catalog.name** is the FK target for `access_requests` and `agent_permissions`, not the `id` column. This simplifies lookups but means agent names cannot be renamed without cascading updates.
- **warehouse_inventory** has a composite primary key `(warehouse_id, product_id)` rather than a surrogate UUID.
- **usage_logs.user_id** is typed as `UUID REFERENCES users(id)`, but some queries cast it with `::uuid` defensively in the audit endpoint.
- **shipping_address** on orders is stored as JSONB, not normalized. Expected shape: `{street, city, state, zip, country}`.

---

## Related

- [`docs/architecture.md`](architecture.md) — data flow showing how queries reach these tables
- [`docs/api-reference.md`](api-reference.md) — REST endpoints that read and write these tables
- [`docs/deployment.md`](deployment.md) — `init.sql` location and volume/seeder lifecycle
- [Project README](../README.md)
