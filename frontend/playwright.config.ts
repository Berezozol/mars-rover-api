import { defineConfig, devices } from "@playwright/test";

// The server (FastAPI serving frontend/dist against the local dev DB) is started separately;
// there is no webServer here. The tests only read.
export default defineConfig({
  testDir: "e2e",
  reporter: "list",
  retries: 0,
  // The API limits concurrent requests per IP.
  workers: 1,
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:8000",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 } },
    },
    {
      name: "mobile",
      use: {
        ...devices["Pixel 7"],
        viewport: { width: 375, height: 812 },
      },
    },
  ],
});
