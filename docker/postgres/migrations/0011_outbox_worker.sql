-- 0011 — outbox worker and role-addressed notifications (D-7, #92 / #94).
--
-- outbox:        a lease owner (locked_by) so a worker whose lease was taken over cannot also complete the
--                row, and failed_at, the "parked" state: a row that used up its attempts is neither retried nor
--                processed, and an admin can list it.
-- notifications: may be addressed to a ROLE ("all support staff") instead of one person. Who has read a
--                role-addressed notification is per person, so it lives in notification_reads; a personal
--                notification keeps using notifications.read_at.

ALTER TABLE outbox
    ADD COLUMN IF NOT EXISTS locked_by TEXT,
    ADD COLUMN IF NOT EXISTS failed_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_outbox_failed
    ON outbox (failed_at)
    WHERE failed_at IS NOT NULL;

ALTER TABLE notifications ALTER COLUMN user_email DROP NOT NULL;
ALTER TABLE notifications
    ADD COLUMN IF NOT EXISTS role TEXT CHECK (role IS NULL OR role IN ('support', 'admin'));
-- Exactly one addressee: a person or a role.
ALTER TABLE notifications
    ADD CONSTRAINT notifications_one_addressee CHECK ((user_email IS NULL) <> (role IS NULL));

CREATE INDEX IF NOT EXISTS idx_notifications_role
    ON notifications (role, created_at DESC)
    WHERE role IS NOT NULL;

CREATE TABLE IF NOT EXISTS notification_reads (
    notification_id UUID NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    user_email      TEXT NOT NULL,
    read_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (notification_id, user_email)
);
