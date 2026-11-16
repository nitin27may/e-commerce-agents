-- 0006 — approvals foundation (#92): one inbox, an audit trail, an outbox, notifications.
--
-- `approvals` is the single inbox every approval kind points into. The two existing
-- per-source execution records stay as they are — tool_approval_requests (the tool
-- middleware's row) and hitl_requests (carries MAF's resume token) — and each gains an
-- approval_id linking it to its inbox row.
--
-- Nothing here is wired to a route yet; this migration and shared/approvals/repo.py
-- + shared/outbox.py are the foundation later steps build on. The merchant (#93) and
-- autonomy (#94) tables are deliberately NOT here: they belong to their own steps.
--
-- Emails are compared case-insensitively (migration 0004 normalised the stored ones to
-- lower case; the CHECKs below do not rely on every writer having done so).

-- ── approvals ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS approvals (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source           TEXT NOT NULL
                     CHECK (source IN ('tool', 'workflow', 'proposal', 'event')),
    -- the per-source record this approval stands for (tool_approval_requests.id,
    -- hitl_requests.id, ...). No FK: it points into different tables per source.
    source_ref       UUID,
    kind             TEXT NOT NULL,                    -- 'return_approval', a gated tool name, ...
    requester_email  TEXT NOT NULL,                    -- who asked (and who may confirm / answer questions)
    subject_email    TEXT,                             -- whose order/account it concerns, if different
    conversation_id  UUID REFERENCES conversations(id) ON DELETE SET NULL,
    amount           NUMERIC(10, 2) CHECK (amount IS NULL OR amount >= 0),
    policy_version   TEXT,                             -- config/policies/approvals.yaml policy_version used to route
    route            TEXT CHECK (route IS NULL OR route IN ('auto', 'support', 'manager')),
    assigned_role    TEXT CHECK (assigned_role IS NULL OR assigned_role IN ('support', 'admin')),
    status           TEXT NOT NULL DEFAULT 'pending_confirm'
                     CHECK (status IN (
                         'pending_confirm', 'pending_review', 'needs_info', 'approved',
                         'approved_with_edits', 'rejected', 'expired', 'executed', 'failed'
                     )),
    recommendation   JSONB,                            -- deterministic: policy reasons + evidence
    decision         JSONB,                            -- {decision, edits, note, ...}
    decided_by       TEXT,
    decided_at       TIMESTAMPTZ,
    due_at           TIMESTAMPTZ,                      -- SLA deadline for the current review level
    escalation_level INTEGER NOT NULL DEFAULT 0 CHECK (escalation_level >= 0),
    -- Fencing for whoever is executing the decision (resume, tool execution). A claim
    -- older than a TTL can be taken over; a NULL claimed_at on a claimed status is stale.
    -- hitl_requests needed this after the fact (0002); approvals carries it from the start.
    claimed_at       TIMESTAMPTZ,
    claimed_by       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Nobody decides their own request. Enforced here and again in the repository.
    CONSTRAINT approvals_no_self_approval
        CHECK (decided_by IS NULL OR lower(decided_by) <> lower(requester_email))
);

-- Reviewer queue: what is waiting for a given role, oldest deadline first.
CREATE INDEX IF NOT EXISTS idx_approvals_pending_by_role
    ON approvals (assigned_role, due_at)
    WHERE status IN ('pending_review', 'needs_info');
-- "My approvals", newest first.
CREATE INDEX IF NOT EXISTS idx_approvals_requester
    ON approvals (requester_email, created_at DESC);
-- SLA sweep: open approvals by deadline.
CREATE INDEX IF NOT EXISTS idx_approvals_open_due
    ON approvals (due_at)
    WHERE status IN ('pending_confirm', 'pending_review', 'needs_info');
-- One approval per source record; lets a retried creator find its row instead of duplicating it.
CREATE UNIQUE INDEX IF NOT EXISTS uq_approvals_source_ref
    ON approvals (source, source_ref)
    WHERE source_ref IS NOT NULL;

-- ── approval_events: the append-only audit trail ──────────────────────────────
CREATE TABLE IF NOT EXISTS approval_events (
    id           BIGSERIAL PRIMARY KEY,
    -- RESTRICT, not CASCADE: an approval with history cannot be deleted out from under it.
    approval_id  UUID NOT NULL REFERENCES approvals(id) ON DELETE RESTRICT,
    actor_email  TEXT,                                 -- NULL for the system (worker, policy)
    actor_role   TEXT,
    action       TEXT NOT NULL,                        -- created, confirmed, routed, decided, executed, ...
    payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_approval_events_approval
    ON approval_events (approval_id, id);

-- Append-only: rows can be inserted and read, never changed or removed.
CREATE OR REPLACE FUNCTION approval_events_reject_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'approval_events is append-only (% refused)', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS approval_events_append_only ON approval_events;
CREATE TRIGGER approval_events_append_only
    BEFORE UPDATE OR DELETE ON approval_events
    FOR EACH ROW EXECUTE FUNCTION approval_events_reject_mutation();

-- ── outbox: events and timers ─────────────────────────────────────────────────
-- A timer is a row with a future available_at. Rows are enqueued in the same
-- transaction as the business write that caused them.
CREATE TABLE IF NOT EXISTS outbox (
    id            BIGSERIAL PRIMARY KEY,
    topic         TEXT NOT NULL,                       -- 'domain.past_tense', e.g. 'approval.created'
    aggregate_id  UUID,                                -- the approval / return / order it is about
    payload       JSONB NOT NULL DEFAULT '{}'::jsonb,
    dedupe_key    TEXT UNIQUE,                         -- NULL = never deduplicated
    available_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    attempts      INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    locked_until  TIMESTAMPTZ,
    processed_at  TIMESTAMPTZ,
    last_error    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- The worker's poll: unprocessed rows by when they become due.
CREATE INDEX IF NOT EXISTS idx_outbox_due
    ON outbox (available_at, id)
    WHERE processed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_outbox_aggregate
    ON outbox (aggregate_id)
    WHERE aggregate_id IS NOT NULL;

-- ── notifications ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS notifications (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_email   TEXT NOT NULL,
    kind         TEXT NOT NULL,
    title        TEXT NOT NULL,
    body         TEXT NOT NULL DEFAULT '',
    link         TEXT,
    approval_id  UUID REFERENCES approvals(id) ON DELETE SET NULL,
    read_at      TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_notifications_user
    ON notifications (user_email, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_unread
    ON notifications (user_email)
    WHERE read_at IS NULL;

-- ── link the per-source execution records to the inbox ───────────────────────
ALTER TABLE tool_approval_requests
    ADD COLUMN IF NOT EXISTS approval_id UUID REFERENCES approvals(id) ON DELETE SET NULL;
ALTER TABLE hitl_requests
    ADD COLUMN IF NOT EXISTS approval_id UUID REFERENCES approvals(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_tool_approvals_approval
    ON tool_approval_requests (approval_id) WHERE approval_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_hitl_approval
    ON hitl_requests (approval_id) WHERE approval_id IS NOT NULL;
