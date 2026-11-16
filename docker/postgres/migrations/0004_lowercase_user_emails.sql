-- 0004 -- lower-case users.email where that cannot collide (login regression fix).
--
-- Login (both stacks) now lower-cases the typed address before the lookup, but until
-- this change the Python stack stored the address exactly as typed at signup, so an
-- account created as "Alice@Example.com" no longer matched. This migration brings
-- existing rows in line with what signup stores today (always lower-case).
--
-- A row is changed only when no other row has the same address ignoring case; a
-- colliding group is left exactly as it is (and reported with RAISE NOTICE, by id
-- never by address). Those rows stay reachable: the auth lookups also match
-- `lower(email)` and prefer the exact spelling the caller typed.
--
-- Tables that reference a user by email rather than by id are updated in step so rows
-- stay joined: refresh_tokens.user_email follows through its ON UPDATE CASCADE foreign
-- key; coupons.user_specific_email, tool_approval_requests.user_email / approved_by,
-- hitl_requests.user_email and oauth_tokens.subject are updated explicitly.
--
-- No functional index on lower(email) is added: users is small (the demo seeds a few
-- hundred rows) and the existing UNIQUE index still serves exact-match lookups elsewhere.

CREATE TEMP TABLE _email_fix AS
SELECT u.email AS old_email, lower(u.email) AS new_email
  FROM users u
 WHERE u.email <> lower(u.email)
   AND (SELECT count(*) FROM users o WHERE lower(o.email) = lower(u.email)) = 1;

UPDATE users u SET email = f.new_email FROM _email_fix f WHERE u.email = f.old_email;

UPDATE coupons c SET user_specific_email = f.new_email FROM _email_fix f WHERE c.user_specific_email = f.old_email;
UPDATE tool_approval_requests t SET user_email = f.new_email FROM _email_fix f WHERE t.user_email = f.old_email;
UPDATE tool_approval_requests t SET approved_by = f.new_email FROM _email_fix f WHERE t.approved_by = f.old_email;
UPDATE hitl_requests h SET user_email = f.new_email FROM _email_fix f WHERE h.user_email = f.old_email;
UPDATE oauth_tokens o SET subject = f.new_email FROM _email_fix f WHERE o.subject = f.old_email;

DO $$
DECLARE
    fixed INTEGER;
    skipped INTEGER;
    skipped_ids TEXT;
BEGIN
    SELECT count(*) INTO fixed FROM _email_fix;
    SELECT count(*), string_agg(u.id::text, ', ' ORDER BY u.created_at)
      INTO skipped, skipped_ids
      FROM users u
     WHERE u.email <> lower(u.email);
    RAISE NOTICE '0004: lower-cased % user email(s)', fixed;
    IF skipped > 0 THEN
        RAISE NOTICE '0004: left % mixed-case user row(s) untouched because another row has the same address ignoring case; ids: %',
            skipped, skipped_ids;
    END IF;
END $$;

DROP TABLE _email_fix;
