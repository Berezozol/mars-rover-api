import { expect, test, type Page } from "@playwright/test";
import {
  CUR_NOW,
  PER_NOW,
  curSol,
  perSol,
  weatherDetail,
  weatherList,
  type WItem,
} from "../tests/fixtures/weather";
import { horizontalOverflow, settle, watchPage } from "./helpers";

// Stage 17, AC-46: the Weather section of the Data page. The weather API and the two rover cards are
// intercepted (fixed sols, fixed Rover.max_sol); everything else comes from the local server.

const per: WItem[] = Array.from({ length: 60 }, (_, i) => perSol(100 + i));
per[40] = perSol(140, {
  humidity_pct: { min: 0, max: 137.5, mean: 20.1 },
  flags: [{ code: "humidity_over_100", parameter: "humidity_pct", count: 12 }],
});
per[30] = perSol(130, {
  hours_covered: 14,
  coverage: 0.3,
  flags: [{ code: "partial_coverage", parameter: null, count: null }],
});
const cur: WItem[] = Array.from({ length: 60 }, (_, i) => curSol(10 + i));

const rover = (id: string, name: string, max: number) => ({
  id,
  name,
  mission: id === "curiosity" ? "msl" : "mars2020",
  launch_date: "2020-07-30",
  landing_date: "2021-02-18",
  end_date: null,
  status: "active",
  max_sol: max,
  max_date: "2026-11-06",
  total_photos: 1,
  cameras_count: 1,
  last_synced_at: "2026-11-17T06:00:12Z",
  data_source: "sync",
});

const json = (body: unknown, status = 200) => ({
  status,
  contentType: "application/json",
  body: JSON.stringify(body),
});

async function mockWeather(page: Page, hits: string[] = []) {
  await page.route("**/api/v2/rovers/perseverance", (r) =>
    r.fulfill(json(rover("perseverance", "Perseverance", PER_NOW))),
  );
  await page.route("**/api/v2/rovers/curiosity", (r) =>
    r.fulfill(json(rover("curiosity", "Curiosity", CUR_NOW))),
  );
  await page.route("**/api/v2/rovers/*/weather**", (route) => {
    const url = new URL(route.request().url());
    hits.push(url.pathname + url.search);
    const m = url.pathname.match(/\/rovers\/(perseverance|curiosity)\/weather(?:\/(\d+))?$/);
    if (!m)
      return route.fulfill(
        json({ error: { code: "not_found", message: "x", details: null } }, 404),
      );
    const id = m[1] as "perseverance" | "curiosity";
    const items = id === "perseverance" ? per : cur;
    if (m[2] === undefined) return route.fulfill(json(weatherList(id, items)));
    const item = items.find((i) => i.sol === Number(m[2]));
    return route.fulfill(
      item
        ? json(weatherDetail(id, item))
        : json(
            { error: { code: "sol_not_found", message: "No weather data", details: null } },
            404,
          ),
    );
  });
}

test("AC-46 header -> Data -> Weather -> Curiosity: the parameters follow the rover", async ({
  page,
}) => {
  const hits: string[] = [];
  await mockWeather(page, hits);
  const w = watchPage(page);
  await page.goto("/");
  await page
    .getByRole("navigation", { name: "Main" })
    .getByRole("button", { name: /^Data/ })
    .click();
  await page
    .getByRole("navigation", { name: "Main" })
    .getByRole("link", { name: "Weather" })
    .click();
  await expect(page).toHaveURL(/\/data\/weather/);
  const panel = page.getByRole("main");
  await expect(panel.getByRole("img", { name: /daily/i })).toBeVisible();
  expect(hits.filter((h) => h === "/api/v2/rovers/perseverance/weather")).toHaveLength(1);
  await expect(
    panel.getByRole("group", { name: /Parameter/ }).getByRole("button", { name: /^Wind/ }),
  ).toBeVisible();

  // switch the rover
  // the native radio is visually hidden (pointer-events: none): a user clicks its label
  await panel.locator("label", { hasText: /^Curiosity / }).click();
  await expect(panel.getByRole("radio", { name: /Curiosity/ })).toBeChecked();
  await expect(
    panel.getByRole("group", { name: /Parameter/ }).getByRole("button", { name: /^UV/ }),
  ).toBeVisible();
  await expect(
    panel.getByRole("group", { name: /Parameter/ }).getByRole("button", { name: /^Wind/ }),
  ).toHaveCount(0);
  expect(hits).toContain("/api/v2/rovers/curiosity/weather");

  // the old inspector is gone (small fixes 2026-11-26): the page asks for the list only
  await expect(panel.getByRole("slider", { name: /next loaded sol/i })).toHaveCount(0);
  await expect(panel.getByRole("img", { name: /by local hour/i })).toHaveCount(0);
  expect(hits.filter((h) => /\/weather\/\d+$/.test(h))).toEqual([]);
  expect(w.consoleErrors).toEqual([]);
});

test("AC-46 a flagged sol has a marker, and hovering it shows the flag in words", async ({
  page,
}) => {
  await mockWeather(page);
  await page.goto("/data/weather");
  const panel = page.getByRole("main");
  await panel
    .getByRole("group", { name: /Parameter/ })
    .getByRole("button", { name: /^Humidity/ })
    .click();
  const marker = panel.getByTestId("weather-flag-marker").first();
  await expect(marker).toHaveAttribute("data-sol", "140");
  await marker.hover();
  await expect(page.getByTestId("weather-tooltip")).toContainText(/humidity above 100/i);
});

test("AC-46 no Previous / Next anomaly buttons and no inspector on the weather page (AC-8)", async ({
  page,
}) => {
  await mockWeather(page);
  await page.goto("/data/weather");
  const panel = page.getByRole("main");
  await expect(panel.getByRole("table")).toBeVisible();
  await expect(panel.getByRole("button", { name: /anomaly/i })).toHaveCount(0);
  await expect(panel.getByText(/Inspect a sol/i)).toHaveCount(0);
  // the flagged sol is still named in its row, in words
  await expect(panel.getByRole("row").filter({ hasText: "140" }).first()).toContainText(
    /humidity above 100/i,
  );
});

test("AC-46 the lag follows the rover's current sol", async ({ page }) => {
  await mockWeather(page);
  await page.goto("/data/weather");
  const panel = page.getByRole("main");
  await expect(panel).toContainText(new RegExp(`${PER_NOW - 159} sols`));
});

for (const scheme of ["light", "dark"] as const) {
  test.describe(`AC-46 360 px, ${scheme} theme, charts`, () => {
    test.use({ viewport: { width: 360, height: 800 }, colorScheme: scheme });

    test(`AC-46 /data/weather at 360 px (${scheme}): no horizontal scroll, no nested scroll areas, credit visible`, async ({
      page,
    }) => {
      await mockWeather(page);
      await page.goto("/data/weather");
      const panel = page.getByRole("main");
      await expect(panel.getByRole("table")).toBeVisible();
      await settle(page);
      for (const r of ["Perseverance", "Curiosity"]) {
        await panel.locator("label", { hasText: new RegExp(`^${r} `) }).click();
        await expect(panel.getByRole("radio", { name: new RegExp(r) })).toBeChecked();
        await expect(panel.getByRole("table")).toBeVisible();
        await settle(page);
        const { scrollWidth, innerWidth } = await horizontalOverflow(page);
        expect(scrollWidth, `${r}: page width`).toBeLessThanOrEqual(innerWidth);
        const box = (await panel.getByRole("img", { name: /daily/i }).boundingBox())!;
        expect(box.x).toBeGreaterThanOrEqual(0);
        expect(box.x + box.width).toBeLessThanOrEqual(innerWidth);
        await expect(panel).toContainText(/MEDA|REMS/);
        const nested = await page.evaluate(
          () =>
            [...document.querySelectorAll<HTMLElement>("main *")].filter((el) => {
              const o = getComputedStyle(el).overflowY;
              const x = getComputedStyle(el).overflowX;
              return (
                ((o === "auto" || o === "scroll") && el.scrollHeight > el.clientHeight + 1) ||
                ((x === "auto" || x === "scroll") && el.scrollWidth > el.clientWidth + 1)
              );
            }).length,
        );
        expect(nested, `${r}: nested scroll areas`).toBe(0);
      }
      await page.screenshot({ path: `test-results/weather-360-${scheme}.png`, fullPage: true });
    });
  });
}
