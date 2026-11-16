-- 0012 — event autonomy (#94, steps V-1 / V-2): the decision log and the store-credit ledger.
--
-- agent_decisions   every decision the autonomy engine takes on an event, one row each, APPEND-ONLY (a trigger
--                   refuses UPDATE and DELETE, as for approval_events). The row records who/what/why, the facts and
--                   the budget it drew on. What happens to a decision afterwards is a NEW row, never an edit:
--                     decision           the engine's verdict on an event (acted alone, asked for approval, or
--                                        found nothing to do). One per event: event_key is unique.
--                     approved_execution a staff-approved action that then ran. One per approval.
--                     reversal           an undo of an executed action. One per reversed row.
-- store_credit_ledger the customer's store credit: signed entries (a credit is positive, its reversal the
--                   negating entry). The balance is the sum. Append-only for the same reason.
--
-- budget_day is the UTC day a draw is counted against; the engine serialises draws on a budget with an advisory
-- lock, so "check the cap, then spend" is one atomic step (see shared/autonomy/actions.py).
--
-- An 'event' approval source already exists in 0006's CHECK; nothing else changes.

CREATE TABLE IF NOT EXISTS agent_decisions (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entry_type    TEXT NOT NULL CHECK (entry_type IN ('decision', 'approved_execution', 'reversal')),
    event_key     TEXT,                                  -- 'topic:event_id'; set on 'decision' rows
    event_type    TEXT,
    outbox_id     BIGINT,                                -- the outbox row that carried the event
    agent_name    TEXT NOT NULL DEFAULT 'autonomy-engine',
    action        TEXT NOT NULL,                         -- goodwill_credit, or 'none'
    budget_key    TEXT,
    user_id       UUID REFERENCES users(id),             -- the customer the action concerns
    order_id      UUID REFERENCES orders(id),
    amount        NUMERIC(12, 2) CHECK (amount IS NULL OR amount >= 0),   -- always the positive amount
    tier          TEXT CHECK (tier IS NULL OR tier IN ('auto', 'auto_notify', 'needs_approval', 'no_action')),
    outcome       TEXT NOT NULL
                  CHECK (outcome IN ('executed', 'awaiting_approval', 'no_action', 'reversed')),
    reasons       JSONB NOT NULL DEFAULT '[]'::jsonb,    -- why this tier (rule + any cap that tripped)
    input         JSONB NOT NULL DEFAULT '{}'::jsonb,    -- the event and the facts the rule saw
    budget        JSONB NOT NULL DEFAULT '{}'::jsonb,    -- caps, usage before, and what this row drew
    policy_version TEXT,
    approval_id   UUID REFERENCES approvals(id),
    reversible    BOOLEAN NOT NULL DEFAULT FALSE,
    reverses_id   UUID REFERENCES agent_decisions(id),   -- on a 'reversal': the row it undoes
    actor_email   TEXT,                                  -- who approved / reversed; NULL for the engine
    note          TEXT,
    budget_day    DATE,                                  -- UTC day an autonomous draw counts against
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK ((entry_type = 'decision') = (event_key IS NOT NULL)),
    CHECK ((entry_type = 'reversal') = (reverses_id IS NOT NULL))
);

-- An event acts once: a replayed event finds its row here instead of inserting another.
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_decisions_event
    ON agent_decisions (event_key) WHERE entry_type = 'decision';
-- One reversal per executed row; one execution per approval.
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_decisions_reversal
    ON agent_decisions (reverses_id) WHERE entry_type = 'reversal';
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_decisions_approval_execution
    ON agent_decisions (approval_id) WHERE entry_type = 'approved_execution';
-- Budget look-ups: what a budget drew today / what a customer or an order already drew.
CREATE INDEX IF NOT EXISTS idx_agent_decisions_budget
    ON agent_decisions (budget_key, budget_day) WHERE entry_type = 'decision' AND outcome = 'executed';
CREATE INDEX IF NOT EXISTS idx_agent_decisions_user  ON agent_decisions (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_decisions_order ON agent_decisions (order_id);
CREATE INDEX IF NOT EXISTS idx_agent_decisions_recent ON agent_decisions (created_at DESC);

CREATE TABLE IF NOT EXISTS store_credit_ledger (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id),
    amount      NUMERIC(12, 2) NOT NULL CHECK (amount <> 0),   -- signed: credit > 0, reversal < 0
    reason      TEXT NOT NULL,
    decision_id UUID REFERENCES agent_decisions(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_store_credit_user ON store_credit_ledger (user_id, created_at DESC);
-- One ledger entry per decision row, so a retried write cannot credit twice.
CREATE UNIQUE INDEX IF NOT EXISTS uq_store_credit_decision
    ON store_credit_ledger (decision_id) WHERE decision_id IS NOT NULL;

-- Append-only: rows can be inserted and read, never changed or removed.
CREATE OR REPLACE FUNCTION autonomy_log_reject_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only (% refused)', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS agent_decisions_append_only ON agent_decisions;
CREATE TRIGGER agent_decisions_append_only
    BEFORE UPDATE OR DELETE ON agent_decisions
    FOR EACH ROW EXECUTE FUNCTION autonomy_log_reject_mutation();

DROP TRIGGER IF EXISTS store_credit_ledger_append_only ON store_credit_ledger;
CREATE TRIGGER store_credit_ledger_append_only
    BEFORE UPDATE OR DELETE ON store_credit_ledger
    FOR EACH ROW EXECUTE FUNCTION autonomy_log_reject_mutation();

-- The party a staff-decided approval is FOR never decides it. 0006 already forbids decided_by = requester; for an
-- autonomous action the requester is the system actor, so the person who would receive the credit (subject_email) is
-- the one a CHECK must name. A merchant proposal is the exception that proves the rule: its subject is the seller
-- and the seller IS its reviewer (on the seller routes), so that source is left out. Tool and workflow approvals
-- have subject = requester, so for them this restates 0006 and changes nothing.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'approvals_no_beneficiary_decision') THEN
        ALTER TABLE approvals ADD CONSTRAINT approvals_no_beneficiary_decision
            CHECK (source = 'proposal' OR decided_by IS NULL OR subject_email IS NULL
                   OR lower(btrim(decided_by)) <> lower(btrim(subject_email)));
    END IF;
END $$;

-- TRUNCATE is not a row operation, so the row triggers above never see it: `TRUNCATE users CASCADE` (the seeder's
-- reset) silently emptied both logs. A statement-level trigger now refuses a TRUNCATE that would destroy rows. An empty
-- table is let through (nothing is lost, and every test fixture's blanket TRUNCATE keeps working). The one deliberate
-- way to clear the logs is reset_autonomy_log() below, for the dev/demo reseed and the test fixtures; it is not called
-- by any application path.
CREATE OR REPLACE FUNCTION autonomy_log_reject_truncate() RETURNS trigger AS $$
DECLARE
    has_rows BOOLEAN;
BEGIN
    IF coalesce(current_setting('ecommerce.autonomy_log_reset', true), '') = 'on' THEN
        RETURN NULL;
    END IF;
    EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I.%I)', TG_TABLE_SCHEMA, TG_TABLE_NAME) INTO has_rows;
    IF has_rows THEN
        RAISE EXCEPTION '% is append-only (TRUNCATE refused; use reset_autonomy_log() for a dev/demo reset)', TG_TABLE_NAME
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS agent_decisions_no_truncate ON agent_decisions;
CREATE TRIGGER agent_decisions_no_truncate
    BEFORE TRUNCATE ON agent_decisions
    FOR EACH STATEMENT EXECUTE FUNCTION autonomy_log_reject_truncate();

DROP TRIGGER IF EXISTS store_credit_ledger_no_truncate ON store_credit_ledger;
CREATE TRIGGER store_credit_ledger_no_truncate
    BEFORE TRUNCATE ON store_credit_ledger
    FOR EACH STATEMENT EXECUTE FUNCTION autonomy_log_reject_truncate();

-- THE one named path for clearing the append-only logs on purpose (dev/demo reseed, test fixtures). It opens the
-- guard for this transaction only, clears both tables, and closes it again. Never call it from application code.
CREATE OR REPLACE FUNCTION reset_autonomy_log() RETURNS void AS $$
BEGIN
    PERFORM set_config('ecommerce.autonomy_log_reset', 'on', true);
    TRUNCATE store_credit_ledger, agent_decisions;
    PERFORM set_config('ecommerce.autonomy_log_reset', 'off', true);
END;
$$ LANGUAGE plpgsql;
