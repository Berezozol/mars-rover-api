import { expect, test, type Page } from "@playwright/test";
import { horizontalOverflow, settle, watchPage } from "./helpers";

// Stage 21: the Data page. The radiation API is intercepted, so the tests do not depend on what the
// server has loaded. Everything else (rovers, samples) comes from the real local server.

const CREDIT = "MSL RAD (SwRI) / NASA PDS Planetary Plasma Interactions Node, MSL-M-RAD-3-RDR-V1.0";

const sol = (n: number, e: number, b: number, rate: number) => ({
  sol: n,
  earth_date: "2017-03-26",
  dose_b_ugy_day: { mean: b, min: b - 10, max: b + 10 },
  dose_e_ugy_day: { mean: e, min: e - 10, max: e + 10 },
  count_rate_b_per_s: rate,
  coverage: 0.86,
  observations: { used: 84, dropped_qc: 0, dropped_low_rate: 0, dropped_invalid: 0 },
  timing_caveat: false,
  source: {
    file: "DATA/x.TXT",
    link: "https://pds-ppi.igpp.ucla.edu/data/MSL-M-RAD-3-RDR-V1.0/DATA/x.TXT",
  },
});

// 200 consecutive sols, so the slider has room to move.
const items = Array.from({ length: 200 }, (_, i) =>
  sol(i + 1, 160 + (i % 40), 168 + (i % 40), 7 + (i % 5) * 0.4),
);

function body(list: unknown[]) {
  return {
    rover: "curiosity",
    credit: CREDIT,
    citation: "J. Peterson et al., MSL RAD RDR V1.0, NASA Planetary Data System, 2013.",
    source_url: "https://pds-ppi.igpp.ucla.edu/data/MSL-M-RAD-3-RDR-V1.0/",
    updated_at: list.length ? "2026-12-01T01:42:10Z" : null,
    source_max_sol: list.length ? 200 : null,
    source_release: list.length ? "2026-08-03" : null,
    items: list,
  };
}

async function mockRadiation(page: Page, list: unknown[] = items) {
  const card = (id: string, max: number) => ({
    id,
    name: id,
    mission: "x",
    launch_date: "2011-11-26",
    landing_date: "2012-08-06",
    end_date: null,
    status: "active",
    max_sol: max,
    max_date: "2026-11-06",
    total_photos: 1,
    cameras_count: 1,
    last_synced_at: "2026-11-17T06:00:12Z",
    data_source: "sync",
  });
  const ok = (body: unknown) => ({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
  await page.route("**/api/v2/rovers", (r) =>
    r.fulfill(ok({ items: [card("perseverance", 2031), card("curiosity", 5066)] })),
  );
  await page.route("**/api/v2/rovers/curiosity", (r) => r.fulfill(ok(card("curiosity", 5066))));
  await page.route("**/api/v2/rovers/curiosity/radiation*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body(list)),
    }),
  );
}

test("header 'Data' -> 'Radiation' opens the radiation page with the range slider and the table", async ({
  page,
}) => {
  await mockRadiation(page);
  const w = watchPage(page);
  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "Main" });
  await nav.getByRole("button", { name: /^Data/ }).click();
  await nav.getByRole("link", { name: "Radiation" }).click();
  await expect(page).toHaveURL(/\/data\/radiation$/);
  await expect(page.getByRole("heading", { level: 1, name: /Radiation/ })).toBeVisible();
  await expect(page.getByRole("table")).toBeVisible();
  await expect(page.getByRole("slider", { name: /range start/i })).toBeVisible();
  expect(w.consoleErrors).toEqual([]);
});

test("the slider moves with the arrow keys, one sol at a time", async ({ page }) => {
  await mockRadiation(page);
  await page.goto("/data/radiation");
  const slider = page.getByRole("slider", { name: /Inspect a sol/ });
  await expect(slider).toBeVisible();
  await expect(slider).toHaveValue("200");
  const panel = page.getByRole("main");
  await expect(panel).toContainText("Sol 200");
  await slider.focus();
  await page.keyboard.press("ArrowLeft");
  await expect(slider).toHaveValue("199");
  await expect(panel).toContainText("Sol 199");
  await page.keyboard.press("ArrowRight");
  await expect(slider).toHaveValue("200");
  await expect(panel).toContainText(/µGy\/day|events\/s/);
});

test("'not loaded yet' when the API has no data", async ({ page }) => {
  await mockRadiation(page, []);
  await page.goto("/data/radiation");
  await expect(page.getByText("Radiation data not loaded yet")).toBeVisible();
  await expect(page.getByRole("slider")).toHaveCount(0);
  await expect(page.getByRole("table")).toHaveCount(0);
});

test("an API error shows 'Try again', which loads the data", async ({ page }) => {
  let n = 0;
  await page.route("**/api/v2/rovers/curiosity/radiation*", (route) => {
    n++;
    if (n === 1) {
      return route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          error: { code: "db_unavailable", message: "Database is unavailable", details: null },
        }),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body(items)),
    });
  });
  await page.goto("/data/radiation");
  await expect(page.getByRole("alert")).toBeVisible();
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("table")).toBeVisible();
});

test.describe("AC-46 narrow screen and dark theme", () => {
  test.use({ viewport: { width: 360, height: 800 }, colorScheme: "dark" });

  test("AC-46 /data#radiation at 360 px, dark: no horizontal scroll, credit and chart visible", async ({
    page,
  }) => {
    await mockRadiation(page);
    await page.goto("/data/radiation");
    await expect(page.getByRole("table")).toBeVisible();
    await settle(page);
    const { scrollWidth, innerWidth } = await horizontalOverflow(page);
    expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
    const panel = page.getByRole("main");
    await expect(panel).toContainText(CREDIT);
    const chart = panel.getByRole("img").first();
    const box = await chart.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(innerWidth);
    // the page itself has no nested scroll areas that would eat the mouse wheel
    const nested = await page.evaluate(
      () =>
        [...document.querySelectorAll<HTMLElement>("main *")].filter((el) => {
          const o = getComputedStyle(el).overflowY;
          return (o === "auto" || o === "scroll") && el.scrollHeight > el.clientHeight + 1;
        }).length,
    );
    expect(nested).toBe(0);
    await page.screenshot({ path: "test-results/data-radiation-360-dark.png", fullPage: true });
  });
});

test("the Data page at the project's mobile width (375 px) does not scroll sideways", async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await mockRadiation(page);
  await page.goto("/data/radiation");
  await expect(page.getByRole("table")).toBeVisible();
  await settle(page);
  const { scrollWidth, innerWidth } = await horizontalOverflow(page);
  expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
});
