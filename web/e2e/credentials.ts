/**
 * Staff passwords for the E2E suite.
 *
 * Against a deployment seeded with SEED_STAFF_PASSWORD (the Azure path generates one), the admin,
 * support and seller accounts no longer use the published demo passwords. Set E2E_STAFF_PASSWORD
 * to that value and every staff login in the suite picks it up. Unset, nothing changes: each call
 * returns the demo default it was given. Customers keep their published passwords.
 */
export function staffPassword(demoDefault: string): string {
  const override = process.env.E2E_STAFF_PASSWORD;
  return override && override.length > 0 ? override : demoDefault;
}
