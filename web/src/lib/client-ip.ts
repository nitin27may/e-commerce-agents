import { isIP } from "node:net";

/**
 * The caller's address, as far as a Next.js route handler can honestly know it.
 *
 * What a route handler can see (Next 16.2, `next start`): `NextRequest` has no
 * `ip` (removed in 15 — "these values are provided by your hosting provider"),
 * and the socket is not reachable from a route handler at all. The only thing
 * Next does is, in `base-server.js`, `req.headers['x-forwarded-for'] ??=
 * socket.remoteAddress` — it fills the header only when it is ABSENT. A browser
 * that sends its own `X-Forwarded-For` keeps it, so from inside the handler a
 * Next-filled value and an attacker-chosen one are indistinguishable, and
 * behind Docker's userland proxy the socket address is the bridge gateway
 * rather than the browser anyway.
 *
 * So the header is only believable when a reverse proxy that WE operate sits in
 * front of Next and appends to it. `WEB_TRUSTED_PROXY_HOPS` says how many such
 * proxies there are (default 0 = none): with `n` hops the client is the entry
 * `n` places from the right, i.e. the address the outermost trusted proxy saw.
 * Entries to its left are the caller's claim and are never used.
 *
 * Returns `null` when no trustworthy address exists. The caller then forwards
 * no `X-Forwarded-For` at all and the orchestrator falls back to its TCP peer
 * (this server), which means anonymous clients share a rate-limit bucket. That
 * is the documented limitation — preferable to inventing an address.
 */
export function trustedClientAddress(
  forwardedFor: string | null,
  trustedHops: number,
): string | null {
  if (!forwardedFor || !Number.isInteger(trustedHops) || trustedHops < 1) return null;
  const chain = forwardedFor
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
  if (chain.length < trustedHops) return null;
  const candidate = chain[chain.length - trustedHops];
  return isIP(candidate) ? candidate : null;
}

/** `WEB_TRUSTED_PROXY_HOPS`, read per request like `ORCHESTRATOR_URL`; junk means 0. */
export function trustedProxyHops(raw: string | undefined = process.env.WEB_TRUSTED_PROXY_HOPS): number {
  const n = Number.parseInt(raw ?? "", 10);
  return Number.isFinite(n) && n > 0 ? n : 0;
}
