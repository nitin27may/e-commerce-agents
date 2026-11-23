-- 0015 -- which price a usage row's cost was computed with, and when.
--
-- cost_usd is an estimate from config/pricing/models.yaml at the time of the run. Prices change, so a
-- stored dollar figure alone cannot say whether it is stale. Two facts are persisted beside it:
--   price_source  the pricing file and version that priced the row, e.g. 'models.yaml#3fa2c1d09b'
--                 (a short content digest), 'override:<file>#<digest>' for an override file; several
--                 sources are comma-joined when the run's agents were priced from different files
--   priced_at     when that price was applied
-- Both are NULL for a row written before this migration, for a run whose usage was not captured, for
-- a run on a model the pricing file does not list (its cost is NULL too), and for a mode that made no
-- LLM call (an explicit cost of 0 with no price applied). Idempotent.

ALTER TABLE usage_logs ADD COLUMN IF NOT EXISTS price_source TEXT;
ALTER TABLE usage_logs ADD COLUMN IF NOT EXISTS priced_at TIMESTAMPTZ;
