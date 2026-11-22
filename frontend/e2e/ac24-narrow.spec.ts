import { expect, test } from "@playwright/test";
import { horizontalOverflow, settle } from "./helpers";

// AC-24: at 360 px no page scrolls sideways, and the Search filters are folded into a button.
test.use({ viewport: { width: 360, height: 800 } });

const PAGES = [
  "/search",
  "/stats",
  "/rovers/curiosity/map?near=-4.7331,137.3985&radius_km=2",
  "/status",
  "/rovers/perseverance/samples",
];

for (const path of PAGES) {
  test(`AC-24 no horizontal page scroll at 360 px: ${path}`, async ({ page }) => {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
    await settle(page);
    const { scrollWidth, innerWidth } = await horizontalOverflow(page);
    expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
  });
}

test("AC-24 search filters are collapsed behind a Filters button at 360 px", async ({ page }) => {
  await page.goto("/search");
  const toggle = page.getByRole("button", { name: /^Filters/ });
  await expect(toggle).toBeVisible();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByTestId("search-submit")).toBeHidden();

  await toggle.click();
  await expect(page.getByRole("button", { name: /^Hide filters/ })).toHaveAttribute(
    "aria-expanded",
    "true",
  );
  await expect(page.getByTestId("search-submit")).toBeVisible();
});
