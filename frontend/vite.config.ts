/// <reference types="vitest/config" />
import preact from "@preact/preset-vite";
import { defineConfig } from "vite";

// Dev server proxies the API and the server-rendered pages to the local backend.
const backend = "http://127.0.0.1:8000";

export default defineConfig({
  base: "/",
  plugins: [preact()],
  server: {
    proxy: Object.fromEntries(
      ["/api", "/health", "/docs", "/openapi.json", "/developers"].map((p) => [
        p,
        { target: backend, changeOrigin: false },
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
