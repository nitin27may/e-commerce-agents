-- 0020 -- how many LLM turns of a run the cost budget would have refused, counted in observe mode.
--
-- COST_BUDGET_MODE defaults to observe, and the only evidence for ever moving it to enforce is how often a normal
-- turn crosses the ceiling. The count was a log line and an OTel counter; neither can be joined to a run, a user or a
-- cost. would_block is that count on the run's own usage_logs row, measured against the whole turn's spend (the
-- orchestrator's calls plus every specialist's) rather than one process's share.
--
-- NULL means the run could not count: the budget was off or enforcing (it refuses instead of counting), the row was
-- written before this migration, or the row is written by the .NET stack (not ported, parity row 58). 0 means it
-- counted and the turn stayed under the ceiling. A rate is would_block > 0 over rows where it IS NOT NULL.
-- Idempotent.

ALTER TABLE usage_logs ADD COLUMN IF NOT EXISTS would_block INTEGER;
