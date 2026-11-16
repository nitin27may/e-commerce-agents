-- 0002 — record when a resume claim was taken (#102).
--
-- POST /api/orchestration/{run_id}/resume flips a hitl_requests row from
-- 'pending' to 'processing' before the workflow runs. A process crash between
-- that claim and the release left the row in 'processing' forever, because
-- nothing recorded *when* it was claimed. claimed_at is that record: the claim
-- sets it, the release clears it, and a claim may take over a 'processing' row
-- whose claimed_at is older than HITL_CLAIM_TTL_SECONDS.
--
-- Nullable on purpose: existing rows have no claim time, and a 'processing'
-- row with a NULL claimed_at (claimed by an older build) is treated as stale.

ALTER TABLE hitl_requests ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ;
