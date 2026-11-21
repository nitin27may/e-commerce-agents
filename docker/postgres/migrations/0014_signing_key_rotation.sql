-- 0014 — signing-key rotation for the self-hosted auth-server (AUTH-KR).
--
-- oauth_signing_keys already carries is_active and retired_at (init.sql). Rotation adds the one thing it
-- lacked: a deadline. When a key is rotated out it stops signing (is_active = FALSE) but stays published in
-- the JWKS until not_after, which the rotation sets to now + AUTH_KEY_RETIRE_AFTER_SECONDS (the longest
-- access-token lifetime plus clock skew). After that the key is left out of the JWKS and retired_at records
-- when the sweep noticed.
--
--   not_after    last instant this key may still be published; NULL for the active key and for a retired-by-hand
--                key (the pre-rotation behaviour: published until retired_at is set)
--   retired_at   unchanged column, set by keys.retire_expired() once not_after has passed

ALTER TABLE oauth_signing_keys
    ADD COLUMN IF NOT EXISTS not_after TIMESTAMPTZ;

-- Exactly one key signs at a time. Two auth-server replicas booting together on an empty table race on the
-- bootstrap insert; this index makes the loser fail loudly instead of leaving two active keys.
CREATE UNIQUE INDEX IF NOT EXISTS uq_oauth_signing_keys_one_active
    ON oauth_signing_keys ((TRUE))
    WHERE is_active;
