import { defineConfig, devices } from "@playwright/test";

/**
 * `BACKEND_STACK` selects which orchestrator the suite is being run against
 * (`python` | `dotnet`). It does not start anything — both stacks bind the
 * same ports (3000, 5432, 6379, 8080-8085, 8090, 9001), so only one can be up
 * at a time and the runs are strictly serial. `scripts/e2e-both-stacks.sh`
 * brings each up in turn and sets this.
 *
 * The variable exists so `web/e2e/parity-gaps.ts` can tell which known gaps
 * apply. Everything else about the run is identical by design — the point of
 * the gate is that one suite must pass against both backends.
 */
const backend = process.env.BACKEND_STACK === "dotnet" ? "dotnet" : "python";

// Escape hatch for machines whose pre-installed Chromium is a different
// revision from the one this Playwright pins. Unset in CI.
const launchOptions = process.env.PW_CHROMIUM_PATH
  ? { executablePath: process.env.PW_CHROMIUM_PATH }
  : {};

// The specs that need no backend: they stub `/api/**` and seed auth in
// localStorage (see e2e/ui-mocks.ts). Only these run on the mobile project;
// everything else drives a real stack through the desktop layout, and running it
// twice would double the slowest part of the suite for no new information.
const BACKEND_FREE_SPECS = [/ui-smoke\.spec\.ts/, /not-found\.spec\.ts/, /a11y\.spec\.ts/];

// The static preview (#301) runs only on its own project, against `pnpm build:preview`'s output served on a
// free high port under the GitHub Pages sub-path; every other project leaves it alone.
const PREVIEW_PORT = Number(process.env.PREVIEW_PORT ?? 4173);
const PREVIEW_BASE = "/e-commerce-agents/preview";
const previewOnly = process.env.PREVIEW_E2E === "1";

export default defineConfig({
  testDir: "./e2e",
  testIgnore: previewOnly ? [] : [/preview\.spec\.ts/],
  webServer: previewOnly
    ? {
        command: `node scripts/serve-preview.mjs ${PREVIEW_PORT} ${PREVIEW_BASE}`,
        url: `http://127.0.0.1:${PREVIEW_PORT}${PREVIEW_BASE}/`,
        reuseExistingServer: false,
      }
    : undefined,
  timeout: 30000,
  expect: { timeout: 10000 },
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: "list",
  use: {
    // Overridable because port 3000 is not always the frontend: on a developer
    // machine another service can already own it (open-webui does on at least
    // one), in which case the compose frontend comes up with no published port
    // and every spec fails at login for reasons that have nothing to do with
    // the app. Set E2E_BASE_URL to point at wherever the frontend really is.
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    headless: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: previewOnly
    ? [
        {
          name: "preview",
          testMatch: /preview\.spec\.ts/,
          // The trailing slash matters: specs `goto("chat/")` relative to it.
          use: {
            browserName: "chromium",
            launchOptions,
            baseURL: `http://127.0.0.1:${PREVIEW_PORT}${PREVIEW_BASE}/`,
          },
        },
      ]
    : [
        {
          name: `chromium-${backend}`,
          use: { browserName: "chromium", launchOptions },
        },
        {
          // A phone-sized viewport with touch and a mobile user agent, on Chromium
          // (the Pixel preset is Chromium-based, so no extra browser is installed).
          name: "mobile-chromium",
          testMatch: BACKEND_FREE_SPECS,
          use: { ...devices["Pixel 7"], launchOptions },
        },
      ],
});
