// Build the static preview (#301) into web/out.
//
//   node scripts/build-preview.mjs                      # basePath from PREVIEW_BASE_PATH (default: none)
//   PREVIEW_BASE_PATH=/e-commerce-agents/preview node scripts/build-preview.mjs
//
// A static export cannot contain a route handler, so src/app/api (the orchestrator proxy) is moved to a
// private folder (underscore prefix: not routable) for the build and put back afterwards, even on failure.
import { spawnSync } from "node:child_process";
import { existsSync, renameSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const web = join(dirname(fileURLToPath(import.meta.url)), "..");
const api = join(web, "src", "app", "api");
const aside = join(web, "src", "app", "_api_proxy_disabled");

if (existsSync(aside)) {
  console.error(`${aside} exists from an interrupted build; move it back to src/app/api first.`);
  process.exit(1);
}

renameSync(api, aside);
let status = 1;
try {
  const result = spawnSync("pnpm", ["exec", "next", "build"], {
    cwd: web,
    stdio: "inherit",
    env: { ...process.env, NEXT_PUBLIC_PREVIEW: "1" },
  });
  status = result.status ?? 1;
} finally {
  renameSync(aside, api);
}
process.exit(status);
