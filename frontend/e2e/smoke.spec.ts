import { expect, test } from "@playwright/test";
import { settle, watchPage } from "./helpers";

// Read-only smoke of the main screens against real data: no console errors, no 4xx/5xx from the API.
const SCREENS: { name: string; path: string }[] = [
  { name: "home", path: "/" },
  { name: "photos browser (latest sol)", path: "/rovers/curiosity" },
  { name: "timeline", path: "/rovers/curiosity/timeline" },
  { name: "route map", path: "/rovers/curiosity/map" },
  { name: "flights", path: "/rovers/ingenuity/flights" },
  { name: "samples", path: "/rovers/perseverance/samples" },
  { name: "search", path: "/search" },
  { name: "stats", path: "/stats" },
  { name: "status", path: "/status" },
];

for (const screen of SCREENS) {
  test(`smoke: ${screen.name} loads cleanly`, async ({ page }) => {
    const watch = watchPage(page);
    await page.goto(screen.path);
    await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
    await settle(page);
    expect(watch.badResponses).toEqual([]);
    expect(watch.consoleErrors).toEqual([]);
  });
}

test("smoke: a photo card opened from the latest sol shows its credit", async ({ page }) => {
  const watch = watchPage(page);
  await page.goto("/rovers/curiosity");
  const tile = page.locator("ul.photo-grid a[href^='/photos/']").first();
  await expect(tile).toBeVisible();
  await tile.click();
  await expect(page).toHaveURL(/\/photos\/\d+/);
  await expect(page.locator(".credit-line")).toContainText("Credit:");
  await settle(page);
  expect(watch.badResponses).toEqual([]);
  expect(watch.consoleErrors).toEqual([]);
});

test("regression: samples table rows start below the header (Atmospheric filter)", async ({
  page,
}, testInfo) => {
  // On phones the table turns into cards, so the header-versus-row check is for the desktop layout.
  test.skip(testInfo.project.name === "mobile", "table layout is desktop only");
  await page.goto("/rovers/perseverance/samples");
  await page.getByRole("button", { name: /^Atmospheric/ }).click();
  const rows = page.locator("table.samples tbody tr");
  await expect(rows.first()).toBeVisible();
  const head = await page.locator("table.samples thead").boundingBox();
  const first = await rows.first().boundingBox();
  expect(head).not.toBeNull();
  expect(first).not.toBeNull();
  expect(first!.y).toBeGreaterThanOrEqual(head!.y + head!.height - 1);
});
