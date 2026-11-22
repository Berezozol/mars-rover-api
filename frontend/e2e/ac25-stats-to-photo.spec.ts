import { expect, test } from "@playwright/test";
import { settle, watchPage } from "./helpers";

// AC-25: Stats -> By camera -> Search link -> results grid -> photo card -> credit visible.
test("AC-25 stats by camera leads through search to a photo card with its credit", async ({
  page,
}) => {
  const watch = watchPage(page);

  await page.goto("/stats");
  await expect(page.getByRole("heading", { level: 1, name: "Stats" })).toBeVisible();

  await page.getByTestId("stats-tab-camera").click();
  await expect(page.getByTestId("stats-tab-camera")).toHaveAttribute("aria-selected", "true");

  const searchLink = page.getByRole("link", { name: /^Search .+ photos of .+/ }).first();
  await expect(searchLink).toBeVisible();
  await searchLink.click();

  await expect(page).toHaveURL(/\/search\?/);
  await expect(page.getByRole("heading", { level: 1, name: "Search photos" })).toBeVisible();

  const results = page.getByTestId("search-results");
  const firstTile = results.locator("ul.photo-grid a[href^='/photos/']").first();
  await expect(firstTile).toBeVisible();
  await firstTile.click();

  await expect(page).toHaveURL(/\/photos\/\d+/);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  const credit = page.locator(".credit-line");
  await expect(credit).toBeVisible();
  await expect(credit).toContainText("Credit:");
  await expect(credit.locator("strong")).not.toBeEmpty();

  await settle(page);
  expect(watch.badResponses).toEqual([]);
  expect(watch.consoleErrors).toEqual([]);
});
