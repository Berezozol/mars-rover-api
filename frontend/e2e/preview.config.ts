import { defineConfig } from "@playwright/test";
import base from "../playwright.config";

// Local run without the backend: serves `dist` with `vite preview` (build first: `npm run build`) and runs the
// specs whose API is fully intercepted (the stage 22 sol report and sol range, the small fixes of 2026-11-26). `npx playwright test -c e2e/preview.config.ts`.
export default defineConfig({
  ...base,
  testDir: ".",
  testMatch: [
    "sol-report.spec.ts",
    "sol-report-polish.spec.ts",
    "sol-range.spec.ts",
    "small-fixes.spec.ts",
  ],
  use: { ...base.use, baseURL: "http://127.0.0.1:4173" },
  webServer: {
    command: "npm run preview -- --port 4173 --strictPort",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: true,
    cwd: "..",
  },
});
