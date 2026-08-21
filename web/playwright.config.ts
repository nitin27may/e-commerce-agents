import { defineConfig } from "@playwright/test";

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

export default defineConfig({
  testDir: "./e2e",
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
  projects: [
    {
      name: `chromium-${backend}`,
      use: { browserName: "chromium" },
    },
  ],
});
