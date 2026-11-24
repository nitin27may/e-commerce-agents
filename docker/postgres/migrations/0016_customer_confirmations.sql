-- 0016 -- the customer's own confirmation of a gated action (#302, D-5b).
--
-- Before a gated tool call (cancel_order, modify_order, initiate_return, process_refund, place_backorder) is
-- parked for staff approval, the customer is asked to confirm the exact action in plain words. That is a
-- different step from staff approval and has its own record: one row per (session, user, tool, arguments),
-- created when the model first requests the call, consumed when the same call arrives on a later turn after an
-- explicit yes from the user, and dead after expires_at. Nothing here is an approval: no inbox row, no approvals
-- audit event. A declined, ignored or expired confirmation leaves no approval behind.
--
--   session_id   the conversation id as the specialist sees it ('' when a caller bound none)
--   args_key     sha256 of the normalised identifying arguments (shared/customer_confirmation.py::args_key)
--   asked_by_message
--                the user message the request was made in (normalised): a "yes" can only answer a question
--                asked on an earlier turn, never the message that raised it
--   status       pending | confirmed | declined | expired
-- Idempotent.

CREATE TABLE IF NOT EXISTS customer_confirmations (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_email       TEXT NOT NULL,
    session_id       TEXT NOT NULL DEFAULT '',
    tool_name        TEXT NOT NULL,
    args_key         TEXT NOT NULL,
    tool_input       JSONB NOT NULL DEFAULT '{}'::jsonb,
    asked_by_message TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'confirmed', 'declined', 'expired')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at       TIMESTAMPTZ NOT NULL,
    resolved_at      TIMESTAMPTZ
);

-- At most one open question per caller, session, tool and argument set.
CREATE UNIQUE INDEX IF NOT EXISTS uq_customer_confirmations_pending
    ON customer_confirmations (lower(user_email), session_id, tool_name, args_key)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_customer_confirmations_expiry
    ON customer_confirmations (expires_at) WHERE status = 'pending';
