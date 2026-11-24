-- 0018 -- order-risk holds, damaged-item remedies and monthly budgets (#299).
--
-- orders.on_hold / held_at / hold_decision_id
--     The reversible hold the engine places on an order it scored as high risk. A hold is a flag, not a status: it
--     never cancels, refunds or changes the order's status, and releasing it (an admin reversal, or a staff approval of
--     the review) restores exactly what was there. hold_decision_id names the agent_decisions row that placed it, so a
--     release only ever undoes its own hold. No foreign key: agent_decisions is append-only and cleared by its own
--     named reset, and a TRUNCATE of it must not need orders listed.
-- uq_agent_decisions_line_damage
--     The database's own backstop for "one damaged-item remedy per order line, ever", beside the engine's check under
--     the order lock (the same shape as uq_agent_decisions_line_adjust): at most one remedy that actually went out
--     (an autonomous decision or a staff-approved execution) per line. A reversal does not free the line.
-- idx_agent_decisions_budget_month
--     Month usage is read as the sum of the budget's per-day draws (budget_day) over the UTC month, so the index the
--     daily check uses already serves it; this one only lets the month range scan skip the 'decision'/'executed'
--     filter's non-matching rows. No column is added: a month cap is a different window over the same append-only rows.

ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS on_hold BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS held_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS hold_decision_id UUID;

CREATE INDEX IF NOT EXISTS idx_orders_on_hold ON orders (held_at) WHERE on_hold;

CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_decisions_line_damage
    ON agent_decisions (order_item_id)
    WHERE action = 'damage_goodwill'
      AND ((entry_type = 'decision' AND outcome = 'executed') OR entry_type = 'approved_execution');

CREATE INDEX IF NOT EXISTS idx_agent_decisions_budget_month
    ON agent_decisions (budget_key, budget_day)
    WHERE entry_type = 'decision' AND outcome = 'executed' AND budget_day IS NOT NULL;
