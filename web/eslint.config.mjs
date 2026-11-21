import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    // Older checkouts may hold a .next-dotnet/ from when `dev.sh --dotnet` used a
    // separate build directory (NEXT_DIST_DIR, since removed). Keep it out of the
    // lint scope: it is generated, and a gate that only fails gets skipped.
    ".next-*/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
