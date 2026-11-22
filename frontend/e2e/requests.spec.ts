import { expect, test } from "@playwright/test";
import { settle, watchPage } from "./helpers";

// The first load must not flood the API: at most 4 /api requests in flight at once.
for (const path of ["/search", "/stats"]) {
  test(`first load of ${path} keeps at most 4 concurrent API requests`, async ({ page }) => {
    const watch = watchPage(page);
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
    await settle(page);
    expect(watch.maxInFlightApi()).toBeGreaterThan(0);
    expect(watch.maxInFlightApi()).toBeLessThanOrEqual(4);
  });
}
