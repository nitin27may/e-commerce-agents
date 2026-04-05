-- ============================================================
-- E-Commerce Agents — Database Schema
-- PostgreSQL 16 + pgvector
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ============================================================
-- AUTH & USERS
-- ============================================================

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL DEFAULT 'customer',  -- customer, power_user, seller, admin
    loyalty_tier VARCHAR(50) DEFAULT 'bronze',      -- bronze, silver, gold
    total_spend DECIMAL(10, 2) DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    is_active BOOLEAN DEFAULT TRUE
);

-- ============================================================
-- PRODUCT CATALOG
-- ============================================================

CREATE TABLE products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    category VARCHAR(100) NOT NULL,   -- Electronics, Clothing, Home, Sports, Books
    brand VARCHAR(100),
    price DECIMAL(10, 2) NOT NULL,
    original_price DECIMAL(10, 2),    -- For showing discounts
    image_url VARCHAR(500),
    rating DECIMAL(3, 2) DEFAULT 0,
    review_count INTEGER DEFAULT 0,
    specs JSONB DEFAULT '{}',          -- Product-specific attributes
    is_active BOOLEAN DEFAULT TRUE,
    seller_id UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE product_embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID REFERENCES products(id) ON DELETE CASCADE,
    embedding vector(1536),            -- text-embedding-3-small dimension
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_product_embedding ON product_embeddings
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

CREATE TABLE price_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID REFERENCES products(id) ON DELETE CASCADE,
    price DECIMAL(10, 2) NOT NULL,
    recorded_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- ORDERS & RETURNS
-- ============================================================

CREATE TABLE orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    status VARCHAR(50) NOT NULL DEFAULT 'placed',
        -- placed, confirmed, shipped, out_for_delivery, delivered, cancelled, returned
    total DECIMAL(10, 2) NOT NULL,
    shipping_address JSONB NOT NULL,    -- {street, city, state, zip, country}
    shipping_carrier VARCHAR(100),
    tracking_number VARCHAR(255),
    coupon_code VARCHAR(50),
    discount_amount DECIMAL(10, 2) DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE order_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID REFERENCES orders(id) ON DELETE CASCADE,
    product_id UUID REFERENCES products(id),
    quantity INTEGER NOT NULL,
    unit_price DECIMAL(10, 2) NOT NULL,
    subtotal DECIMAL(10, 2) NOT NULL
);

CREATE TABLE order_status_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID REFERENCES orders(id) ON DELETE CASCADE,
    status VARCHAR(50) NOT NULL,
    notes TEXT,
    location VARCHAR(255),            -- Tracking location
    timestamp TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE returns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID REFERENCES orders(id),
    user_id UUID REFERENCES users(id),
    reason VARCHAR(255) NOT NULL,
    status VARCHAR(50) DEFAULT 'requested',  -- requested, approved, shipped_back, received, refunded, denied
    return_label_url VARCHAR(500),
    refund_method VARCHAR(50),          -- original_payment, store_credit
    refund_amount DECIMAL(10, 2),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

-- ============================================================
-- REVIEWS
-- ============================================================

CREATE TABLE reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID REFERENCES products(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id),
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    title VARCHAR(255),
    body TEXT NOT NULL,
    verified_purchase BOOLEAN DEFAULT FALSE,
    helpful_count INTEGER DEFAULT 0,
    is_flagged BOOLEAN DEFAULT FALSE,   -- Flagged as potentially fake
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- INVENTORY & SHIPPING
-- ============================================================

CREATE TABLE warehouses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,         -- "East", "Central", "West"
    location VARCHAR(255) NOT NULL,     -- "Richmond, VA"
    region VARCHAR(50) NOT NULL         -- "east", "central", "west"
);

CREATE TABLE warehouse_inventory (
    warehouse_id UUID REFERENCES warehouses(id),
    product_id UUID REFERENCES products(id),
    quantity INTEGER NOT NULL DEFAULT 0,
    reorder_threshold INTEGER DEFAULT 10,
    PRIMARY KEY (warehouse_id, product_id)
);

CREATE TABLE carriers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,         -- "Standard Shipping", "Express", "Overnight"
    speed_tier VARCHAR(50) NOT NULL,    -- "standard", "express", "overnight"
    base_rate DECIMAL(10, 2) NOT NULL
);

CREATE TABLE shipping_rates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    carrier_id UUID REFERENCES carriers(id),
    region_from VARCHAR(50) NOT NULL,
    region_to VARCHAR(50) NOT NULL,
    price DECIMAL(10, 2) NOT NULL,
    estimated_days_min INTEGER NOT NULL,
    estimated_days_max INTEGER NOT NULL
);

CREATE TABLE restock_schedule (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID REFERENCES products(id),
    warehouse_id UUID REFERENCES warehouses(id),
    expected_quantity INTEGER NOT NULL,
    expected_date DATE NOT NULL
);

-- ============================================================
-- PRICING & PROMOTIONS
-- ============================================================

CREATE TABLE coupons (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(50) UNIQUE NOT NULL,
    description TEXT,
    discount_type VARCHAR(20) NOT NULL,  -- "percentage", "fixed"
    discount_value DECIMAL(10, 2) NOT NULL,
    min_spend DECIMAL(10, 2) DEFAULT 0,
    max_discount DECIMAL(10, 2),         -- Cap for percentage discounts
    usage_limit INTEGER,
    times_used INTEGER DEFAULT 0,
    valid_from TIMESTAMPTZ DEFAULT NOW(),
    valid_until TIMESTAMPTZ,
    applicable_categories TEXT[],         -- NULL = all categories
    user_specific_email VARCHAR(255),     -- NULL = all users
    is_active BOOLEAN DEFAULT TRUE
);

CREATE TABLE promotions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    type VARCHAR(50) NOT NULL,           -- "bundle", "buy_x_get_y", "flash_sale"
    rules JSONB NOT NULL,                -- Flexible rule definitions
    start_date TIMESTAMPTZ NOT NULL,
    end_date TIMESTAMPTZ NOT NULL,
    is_active BOOLEAN DEFAULT TRUE
);

CREATE TABLE loyalty_tiers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(50) UNIQUE NOT NULL,    -- "bronze", "silver", "gold"
    min_spend DECIMAL(10, 2) NOT NULL,   -- Spend threshold to reach tier
    discount_pct DECIMAL(5, 2) NOT NULL, -- Tier-wide discount percentage
    free_shipping_threshold DECIMAL(10, 2),
    priority_support BOOLEAN DEFAULT FALSE
);

-- ============================================================
-- MARKETPLACE (Agent Catalog & Access Control)
-- ============================================================

CREATE TABLE agent_catalog (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) UNIQUE NOT NULL,   -- "product-discovery"
    display_name VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    category VARCHAR(100),
    icon VARCHAR(50),
    status VARCHAR(50) DEFAULT 'active',
    version VARCHAR(20) DEFAULT '1.0',
    capabilities TEXT[] DEFAULT '{}',
    input_types TEXT[] DEFAULT '{text}',
    output_types TEXT[] DEFAULT '{text}',
    requires_approval BOOLEAN DEFAULT TRUE,
    allowed_roles TEXT[] DEFAULT '{power_user,admin}',
    config JSONB DEFAULT '{}'
);

CREATE TABLE access_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    agent_name VARCHAR(100) REFERENCES agent_catalog(name),
    role_requested VARCHAR(50) NOT NULL,
    use_case TEXT NOT NULL,
    status VARCHAR(50) DEFAULT 'pending',  -- pending, approved, denied
    admin_notes TEXT,
    reviewed_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

CREATE TABLE agent_permissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    agent_name VARCHAR(100) REFERENCES agent_catalog(name),
    role VARCHAR(50) NOT NULL,
    granted_at TIMESTAMPTZ DEFAULT NOW(),
    granted_by UUID REFERENCES users(id),
    UNIQUE(user_id, agent_name)
);

-- ============================================================
-- CONVERSATIONS & USAGE
-- ============================================================

CREATE TABLE conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    title VARCHAR(255),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_message_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
    role VARCHAR(50) NOT NULL,           -- "user", "assistant", "system"
    content TEXT NOT NULL,
    agent_name VARCHAR(100),             -- Which agent generated this response
    agents_involved TEXT[],              -- For multi-agent responses
    metadata JSONB DEFAULT '{}',         -- Tool calls, trace data, etc.
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE usage_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    agent_name VARCHAR(100) NOT NULL,
    session_id UUID,
    trace_id VARCHAR(64),               -- OTel trace_id for correlation with Aspire
    input_summary TEXT,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    tool_calls_count INTEGER DEFAULT 0,
    duration_ms INTEGER,
    status VARCHAR(50) DEFAULT 'success',
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE agent_execution_steps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    usage_log_id UUID REFERENCES usage_logs(id) ON DELETE CASCADE,
    step_index INTEGER NOT NULL,
    tool_name VARCHAR(255),
    tool_input JSONB,
    tool_output JSONB,
    status VARCHAR(50) DEFAULT 'success',
    duration_ms INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- INDEXES
-- ============================================================

CREATE INDEX idx_products_seller ON products(seller_id);
CREATE INDEX idx_products_category ON products(category);
CREATE INDEX idx_products_price ON products(price);
CREATE INDEX idx_products_rating ON products(rating DESC);
CREATE INDEX idx_orders_user ON orders(user_id, created_at DESC);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_reviews_product ON reviews(product_id, created_at DESC);
CREATE INDEX idx_reviews_rating ON reviews(product_id, rating);
CREATE INDEX idx_warehouse_inv ON warehouse_inventory(product_id);
CREATE INDEX idx_price_history ON price_history(product_id, recorded_at DESC);
CREATE INDEX idx_access_requests_status ON access_requests(status, created_at DESC);
CREATE INDEX idx_usage_logs_user ON usage_logs(user_id, created_at DESC);
CREATE INDEX idx_usage_logs_agent ON usage_logs(agent_name, created_at DESC);
CREATE INDEX idx_usage_logs_trace ON usage_logs(trace_id);
CREATE INDEX idx_messages_conversation ON messages(conversation_id, created_at);

-- ── Agent Memory ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id),
    category VARCHAR(50) NOT NULL,
    content TEXT NOT NULL,
    importance SMALLINT DEFAULT 5,
    embedding vector(1536),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    is_active BOOLEAN DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS idx_memories_user ON agent_memories(user_id, is_active);
CREATE INDEX IF NOT EXISTS idx_memories_category ON agent_memories(user_id, category);
