/**
 * Static preview switches (#301). `NEXT_PUBLIC_PREVIEW=1` makes `api.ts` answer from
 * `preview-data.json` (built by scripts/build_preview_data.py) instead of calling the
 * orchestrator. The flag is inlined at build time, so a normal build carries none of it.
 */
export const PREVIEW = process.env.NEXT_PUBLIC_PREVIEW === "1";

/** Where "run it locally" points: the README's quick start. */
export const QUICKSTART_URL = "https://github.com/nitin27may/e-commerce-agents#quick-start";

/** The synthetic tour account. Its password is the one the docs and the e2e specs already publish. */
export const TOUR_LOGIN = { email: "tour@example.com", password: "tour123" } as const;
