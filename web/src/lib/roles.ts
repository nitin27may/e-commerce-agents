/**
 * Platform roles, mirroring the backend allowlist (shared/auth.py `_ALLOWED_ROLES`).
 * `support` and `admin` are staff: they review approvals routed to them.
 */
export const STAFF_ROLES = ["support", "admin"] as const;

export function isStaffRole(role: string | null | undefined): boolean {
  return role === "support" || role === "admin";
}
