-- 0009 — merchant schema (#93, step M-1): proposals, proposal lines, promotion↔product links, unit cost.
--
-- Groundwork only. Nothing reads or writes these tables yet: the guards that use them are in
-- agents/python/shared/merchant/guards.py and the limits in config/policies/merchant_limits.yaml.
-- The merchant-ops agent (M-2) and the batch review (M-3) come later.
--
--   products.unit_cost     what the seller pays per unit. Nullable on purpose: a product with no
--                          known cost can never pass the margin guard ("missing cost -> blocked").
--   promotion_products     which products a promotion covers. promotions.rules is untyped JSONB, so
--                          "is this product in an active promotion?" had no queryable answer.
--   proposals              one batch of suggested changes for one seller.
--   proposal_lines         one suggested change to one product, with the guard verdict that applied
--                          when it was proposed and the value finally applied (they may differ if
--                          the seller edits the line).

-- ── products.unit_cost ────────────────────────────────────────────────────────
ALTER TABLE products
    ADD COLUMN IF NOT EXISTS unit_cost NUMERIC(10, 2)
    CHECK (unit_cost IS NULL OR unit_cost >= 0);

-- ── promotion_products ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS promotion_products (
    promotion_id UUID NOT NULL REFERENCES promotions(id) ON DELETE CASCADE,
    product_id   UUID NOT NULL REFERENCES products(id)   ON DELETE CASCADE,
    PRIMARY KEY (promotion_id, product_id)
);
-- "which promotions cover this product" is the guard's lookup; the primary key serves the other direction.
CREATE INDEX IF NOT EXISTS idx_promotion_products_product ON promotion_products (product_id);

-- ── proposals ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS proposals (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    seller_id  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL CHECK (kind IN ('markdown', 'restock', 'mixed')),
    trigger    TEXT NOT NULL DEFAULT 'manual' CHECK (trigger IN ('manual', 'scheduled', 'event')),
    status     TEXT NOT NULL DEFAULT 'pending_review'
               CHECK (status IN ('pending_review', 'decided', 'executed', 'cancelled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_proposals_seller ON proposals (seller_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals (status) WHERE status = 'pending_review';

-- ── proposal_lines ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS proposal_lines (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    proposal_id    UUID NOT NULL REFERENCES proposals(id) ON DELETE CASCADE,
    product_id     UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    field          TEXT NOT NULL CHECK (field IN ('price', 'restock_quantity')),
    current_value  NUMERIC(12, 2),                       -- NULL when there is no current value to compare
    proposed_value NUMERIC(12, 2) NOT NULL CHECK (proposed_value >= 0),
    final_value    NUMERIC(12, 2) CHECK (final_value IS NULL OR final_value >= 0),
    rationale      TEXT,
    evidence       JSONB NOT NULL DEFAULT '{}'::jsonb,
    guard_result   JSONB,                                -- shared/merchant/guards.py GuardResult.to_dict()
    status         TEXT NOT NULL DEFAULT 'proposed'
                   CHECK (status IN ('proposed', 'accepted', 'edited', 'rejected', 'blocked', 'executed', 'failed')),
    executed_at    TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- one suggestion per product and field per batch
    UNIQUE (proposal_id, product_id, field)
);
CREATE INDEX IF NOT EXISTS idx_proposal_lines_product ON proposal_lines (product_id);
CREATE INDEX IF NOT EXISTS idx_proposal_lines_status  ON proposal_lines (status);
