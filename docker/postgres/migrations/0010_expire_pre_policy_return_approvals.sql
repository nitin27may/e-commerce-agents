-- 0010 — return approvals paused under the pre-policy workflow cannot be resumed (D-8, #95).
--
-- Until now the return workflow was check -> initiate-return -> search -> hitl-gate -> discount -> finalize,
-- and its pause answered a bool. The workflow is now check -> policy-gate -> initiate-return -> ... and its pause
-- answers a ReturnDecision dataclass; the paused request also carries the whole WorkflowState. A checkpoint
-- written by the old graph stores the old request shape and a different executor graph, so resuming one under
-- the new code cannot work — and for those runs the return row, the label and the 'returned' order status
-- were ALREADY created before the pause, so there is nothing left for an approval to gate.
--
-- Mark every still-open return approval expired, and close the inbox approval linked to it (the row stays; its
-- audit trail gains an 'expired' event). Rows already decided are left alone.

WITH expired AS (
    UPDATE hitl_requests
       SET status = 'expired',
           responded_at = NOW(),
           claimed_at = NULL,
           response = jsonb_build_object(
               'approved', false,
               'reason', 'paused under the pre-policy return workflow; its checkpoint cannot be resumed (migration 0010)'
           )
     WHERE kind = 'return_approval'
       AND status IN ('pending', 'processing', 'needs_info')
 RETURNING id, approval_id
),
closed AS (
    UPDATE approvals a
       SET status = 'expired', claimed_at = NULL, claimed_by = NULL, updated_at = NOW()
      FROM expired e
     WHERE a.id = e.approval_id
       AND a.status IN ('pending_confirm', 'pending_review', 'needs_info')
 RETURNING a.id
)
INSERT INTO approval_events (approval_id, actor_email, actor_role, action, payload)
SELECT id, NULL, NULL, 'expired',
       jsonb_build_object('reason', 'pre-policy return workflow superseded (migration 0010)')
  FROM closed;
