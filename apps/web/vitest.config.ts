import react from "@vitejs/plugin-react";
import tsconfigPaths from "vite-tsconfig-paths";
import { defineConfig } from "vitest/config";

/**
 * The test run's own Vite config — Vitest prefers this file over
 * `vite.config.ts`, so the Tailwind plugin never runs for a suite that
 * renders into jsdom and asserts on class names rather than on computed
 * styles.
 *
 * ENV: the app reads three `import.meta.env.VITE_REVI_*` settings. Under
 * Vite's production build those are statically replaced (exactly as Next
 * replaced `process.env.NEXT_PUBLIC_*`); under Vitest `import.meta.env`
 * stays a live object, which is what lets `vi.stubEnv` drive the
 * driver-selection tests. Nothing is defined here on purpose — a `define`
 * would freeze the values and those tests would silently stop testing
 * anything.
 */
export default defineConfig({
  plugins: [react(), tsconfigPaths()],
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
