-- 0019 -- a restock batch over the PO-value threshold waits for a manager (#298).
--
-- A merchant proposal is decided by its seller (0009). A batch whose accepted restock lines total more than the
-- threshold in config/policies/approvals.yaml (kind merchant_proposal, fact `amount`) now needs a second decision from
-- staff, on the SAME approvals row: the seller's accepted restock lines are recorded and wait, and the row is handed
-- to the manager queue (route 'manager', assigned_role 'admin', a fresh SLA clock).
--
--   proposals.status       + awaiting_manager   the seller has decided; the PO tier has not
--   proposal_lines.status  + awaiting_manager   accepted by the seller, held for the manager's decision
--                          + expired            held, and the manager SLA ran out: never ordered
--   approvals_no_beneficiary_decision
--                          the proposal exemption now covers only the seller's own stage (route IS NULL). Once the
--                          row is routed to a manager the seller is just the subject again, and the subject never
--                          decides.
-- Idempotent.

ALTER TABLE proposals DROP CONSTRAINT IF EXISTS proposals_status_check;
ALTER TABLE proposals ADD CONSTRAINT proposals_status_check
    CHECK (status IN ('pending_review', 'awaiting_manager', 'decided', 'executed', 'cancelled'));

ALTER TABLE proposal_lines DROP CONSTRAINT IF EXISTS proposal_lines_status_check;
ALTER TABLE proposal_lines ADD CONSTRAINT proposal_lines_status_check
    CHECK (status IN ('proposed', 'accepted', 'edited', 'rejected', 'blocked', 'executed', 'failed',
                      'awaiting_manager', 'expired'));

ALTER TABLE approvals DROP CONSTRAINT IF EXISTS approvals_no_beneficiary_decision;
ALTER TABLE approvals ADD CONSTRAINT approvals_no_beneficiary_decision
    CHECK ((source = 'proposal' AND route IS NULL) OR decided_by IS NULL OR subject_email IS NULL
           OR lower(btrim(decided_by)) <> lower(btrim(subject_email)));

CREATE INDEX IF NOT EXISTS idx_proposal_lines_awaiting
    ON proposal_lines (proposal_id) WHERE status = 'awaiting_manager';
