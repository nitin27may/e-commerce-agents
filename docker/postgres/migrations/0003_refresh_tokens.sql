-- 0003 -- refresh-token rotation with reuse detection (#102, AUTH_MODE=local).
--
-- Every refresh token issued by /api/auth/{signup,login,refresh} carries a `jti`
-- and gets a row here. Refreshing marks the presented token revoked + replaced_by
-- the new jti and inserts the successor in the same family. Presenting a token that
-- is already revoked means it leaked or was replayed, so the whole family
-- (family_id) is revoked and the user must log in again.
--
-- user_email references users(email) so deleting/truncating users cascades.
-- jti is TEXT: both stacks generate a UUID string, and a token whose jti is not
-- found here is simply rejected.

CREATE TABLE IF NOT EXISTS refresh_tokens (
    jti         TEXT PRIMARY KEY,
    user_email  VARCHAR(255) NOT NULL REFERENCES users(email) ON DELETE CASCADE ON UPDATE CASCADE,
    family_id   UUID NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    revoked_at  TIMESTAMPTZ,
    replaced_by TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_refresh_tokens_family ON refresh_tokens(family_id);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user ON refresh_tokens(user_email);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_expires ON refresh_tokens(expires_at);
