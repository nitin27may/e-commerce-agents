-- 0005 -- an absolute lifetime for a refresh-token family.
--
-- Rotation (0003) minted every successor with a fresh 7-day expiry, so a session that kept
-- refreshing never ended. family_expires_at is stamped once, when a login or signup starts a
-- family (now + AUTH_REFRESH_FAMILY_MAX_DAYS, default 30), and every successor inherits it;
-- rotation refuses once it has passed (401, the user logs in again), and a successor's own
-- expires_at is capped at it.
--
-- Existing rows are backfilled from their family's first issue plus 30 days (the default cap),
-- so sessions already running get the same bound rather than an unbounded one. The column default
-- only matters for an insert from a build that predates this migration.

ALTER TABLE refresh_tokens ADD COLUMN IF NOT EXISTS family_expires_at TIMESTAMPTZ;

UPDATE refresh_tokens r
   SET family_expires_at = f.first_issued + interval '30 days'
  FROM (SELECT family_id, min(created_at) AS first_issued FROM refresh_tokens GROUP BY family_id) f
 WHERE r.family_id = f.family_id AND r.family_expires_at IS NULL;

ALTER TABLE refresh_tokens ALTER COLUMN family_expires_at SET DEFAULT (NOW() + interval '30 days');
ALTER TABLE refresh_tokens ALTER COLUMN family_expires_at SET NOT NULL;
