/**
 * Small shape guards for API responses. The backend returns `[]` or omits a key
 * in places (an empty table, a stack still warming up), and a page that calls
 * `.map` or `.length` on `undefined` falls through to the global error
 * boundary. Pages run their response through these and render an empty or error
 * state inline instead.
 */

export function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

export function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
