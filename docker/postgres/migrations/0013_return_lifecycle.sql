-- 0013 — the return lifecycle after approval (#95, D-9a/D-9b).
--
-- A return now outlives the request that opened it: it waits for the carrier's first scan, then for an
-- inspection, and only then pays out. These columns hold what each wait learns. The waits themselves are
-- hitl_requests rows (kind 'carrier_scan' / 'return_inspection'), so no new table is needed.
--
--   approval_id  the inbox approval that authorised the return
--   resolution   refund | store_credit | replacement, set when the inspection resolves it
--   scanned_at   when the carrier first scanned the package
--   inspection   what support found: {outcome, condition, note, inspected_by, inspected_at}

ALTER TABLE returns
    ADD COLUMN IF NOT EXISTS approval_id UUID,
    ADD COLUMN IF NOT EXISTS resolution  TEXT,
    ADD COLUMN IF NOT EXISTS scanned_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS inspection  JSONB;

-- A wait is found by the return it belongs to, not by scanning every pending row's payload.
CREATE INDEX IF NOT EXISTS idx_hitl_return_wait
    ON hitl_requests ((payload->>'return_id'))
    WHERE kind IN ('carrier_scan', 'return_inspection');
