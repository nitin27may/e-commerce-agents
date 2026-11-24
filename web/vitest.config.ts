import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tsconfigPaths from "vite-tsconfig-paths";

export default defineConfig({
  plugins: [react(), tsconfigPaths()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    // Playwright specs live under web/e2e and are run with @playwright/test.
    exclude: ["e2e/**", "node_modules/**", ".next/**"],
    css: false,
    // Node 25+ ships a native localStorage that is unusable without --localstorage-file and shadows
    // jsdom's, so every test touching storage saw undefined. Turn the native one off.
    execArgv: ["--no-experimental-webstorage"],
  },
});
