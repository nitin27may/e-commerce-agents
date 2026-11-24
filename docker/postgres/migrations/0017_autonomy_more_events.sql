-- 0017 -- more autonomy events (#299): price drops, back in stock, and the optional ops-responder's drafts.
--
-- agent_decisions.order_item_id   the order LINE a price adjustment is for. A price drop pays per line, so "one
--                                 adjustment per line" needs the line on the row. Nullable: a shipment-delay credit
--                                 is per order, a back-in-stock notice has no order.
-- uq_agent_decisions_line_adjust  the database's own backstop for that rule, beside the engine's check under the
--                                 order lock: at most one adjustment that actually paid (an autonomous decision or a
--                                 staff-approved execution) per line, ever. A reversal does not free the line.
-- notifications.decision_id       the decision row a customer notification belongs to, so a drafted message can replace
--                                 the template text on that notification. No foreign key: agent_decisions is
--                                 append-only and reset on its own path (reset_autonomy_log), and a dangling id on a
--                                 notification is harmless.
-- decision_drafts                 the customer-facing message the ops-responder drafted for a decision (or the template
--                                 it fell back to), APPEND-ONLY like the log it hangs off. One per decision row. It
--                                 words a decision; it can never change one (nothing here references an amount or tier).

ALTER TABLE agent_decisions
    ADD COLUMN IF NOT EXISTS order_item_id UUID REFERENCES order_items(id);

CREATE INDEX IF NOT EXISTS idx_agent_decisions_item ON agent_decisions (order_item_id)
    WHERE order_item_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_decisions_line_adjust
    ON agent_decisions (order_item_id)
    WHERE action = 'price_adjustment'
      AND ((entry_type = 'decision' AND outcome = 'executed') OR entry_type = 'approved_execution');

ALTER TABLE notifications
    ADD COLUMN IF NOT EXISTS decision_id UUID;

CREATE INDEX IF NOT EXISTS idx_notifications_decision ON notifications (decision_id)
    WHERE decision_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS decision_drafts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    decision_id     UUID NOT NULL UNIQUE REFERENCES agent_decisions(id),
    body            TEXT NOT NULL CHECK (length(body) BETWEEN 1 AND 1000),
    source          TEXT NOT NULL CHECK (source IN ('llm', 'template')),
    model           TEXT,                      -- the model that wrote it; NULL for the template
    fallback_reason TEXT,                      -- why the template was used instead of a model draft
    grounding       JSONB NOT NULL DEFAULT '{}'::jsonb,   -- what the verifier checked: amounts and ids found in the text
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DROP TRIGGER IF EXISTS decision_drafts_append_only ON decision_drafts;
CREATE TRIGGER decision_drafts_append_only
    BEFORE UPDATE OR DELETE ON decision_drafts
    FOR EACH ROW EXECUTE FUNCTION autonomy_log_reject_mutation();

DROP TRIGGER IF EXISTS decision_drafts_no_truncate ON decision_drafts;
CREATE TRIGGER decision_drafts_no_truncate
    BEFORE TRUNCATE ON decision_drafts
    FOR EACH STATEMENT EXECUTE FUNCTION autonomy_log_reject_truncate();

-- The one named reset now clears the drafts too (they reference agent_decisions, so a TRUNCATE of it needs them listed).
CREATE OR REPLACE FUNCTION reset_autonomy_log() RETURNS void AS $$
BEGIN
    PERFORM set_config('ecommerce.autonomy_log_reset', 'on', true);
    TRUNCATE store_credit_ledger, decision_drafts, agent_decisions;
    PERFORM set_config('ecommerce.autonomy_log_reset', 'off', true);
END;
$$ LANGUAGE plpgsql;
