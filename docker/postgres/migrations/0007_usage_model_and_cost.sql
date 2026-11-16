-- 0007 -- what a run's usage row can now say about the model and its cost.
--
-- usage_logs held only tokens_in / tokens_out. Since the specialists' usage reaches the
-- orchestrator's row (and cached input is known), three more facts are persisted:
--   model            the model that carried most of the run's input tokens
--   tokens_cached_in the cached share of tokens_in (billed at a lower rate by providers)
--   cost_usd         the run's estimated cost, each agent priced at its own model
-- All nullable. NULL means "not captured" (a row written before this migration, or a mode that
-- reported no usage); a workflow mode that makes no LLM call writes an explicit 0 for cost_usd.

ALTER TABLE usage_logs ADD COLUMN IF NOT EXISTS model VARCHAR(100);
ALTER TABLE usage_logs ADD COLUMN IF NOT EXISTS tokens_cached_in INTEGER;
ALTER TABLE usage_logs ADD COLUMN IF NOT EXISTS cost_usd NUMERIC(12, 6);
