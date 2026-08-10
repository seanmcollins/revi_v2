import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import tsconfigPaths from "vite-tsconfig-paths";

export default defineConfig({
  plugins: [
    react(),
    // Tailwind v4 as a Vite plugin rather than through PostCSS. Same
    // compiler, same CSS-first config (`@theme` in `globals.css`, no JS
    // config file, no `@apply`) — the emitted sheet was diffed against the
    // PostCSS output before the PostCSS config was deleted.
    tailwindcss(),
    tsconfigPaths(),
  ],
  server: {
    // 3000, not Vite's 5173: the API's CORS allowlist
    // (`REVI_CORS_ORIGINS`) is pinned to `http://localhost:3000`, and the
    // Makefile's `make dev` runs both halves together. `strictPort` so a
    // port already in use fails loudly instead of silently landing on 3001
    // and being refused by CORS one fetch later.
    port: 3000,
    strictPort: true,
  },
  preview: {
    port: 3000,
    strictPort: true,
  },
});
