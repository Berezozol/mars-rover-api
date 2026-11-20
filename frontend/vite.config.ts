/// <reference types="vitest/config" />
import preact from "@preact/preset-vite";
import { defineConfig } from "vite";

// Dev server proxies the API and the server-rendered pages to the backend: the local one by
// default, or another (e.g. the public deployment) via SOL_INDEX_BACKEND.
const backend = process.env.SOL_INDEX_BACKEND || "http://127.0.0.1:8000";
const https = backend.startsWith("https://");

export default defineConfig({
  base: "/",
  plugins: [preact()],
  server: {
    proxy: Object.fromEntries(
      ["/api", "/health", "/docs", "/openapi.json", "/developers"].map((p) => [
        p,
        // An https target needs its own Host header and certificate checks.
        { target: backend, changeOrigin: https, secure: https },
      ]),
    ),
  },
  build: { outDir: "dist", emptyOutDir: true, sourcemap: false },
  test: {
    environment: "jsdom",
    globals: false,
    setupFiles: ["tests/setup.ts"],
    include: ["tests/**/*.test.{ts,tsx}"],
  },
});
