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

test("AC-46 header -> Data -> #weather -> Curiosity -> pick a sol -> hourly charts", async ({
  page,
}) => {
  const hits: string[] = [];
  await mockWeather(page, hits);
  const w = watchPage(page);
  await page.goto("/");
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Data" }).click();
  await expect(page).toHaveURL(/\/data$/);
  await page.getByRole("button", { name: /Weather by sol/ }).click();
  await expect(page).toHaveURL(/\/data#weather$/);
  const panel = page.getByRole("region", { name: /Weather by sol/ });
  await expect(panel.getByRole("img", { name: /daily/i })).toBeVisible();
  expect(hits.filter((h) => h === "/api/v2/rovers/perseverance/weather")).toHaveLength(1);
  await expect(panel).toContainText(/not published yet/i);
  await expect(panel.getByRole("button", { name: /^Wind/ })).toBeVisible();

  // switch the rover
  // the native radio is visually hidden (pointer-events: none): a user clicks its label
  await panel.locator("label", { hasText: /^Curiosity / }).click();
  await expect(panel.getByRole("radio", { name: /Curiosity/ })).toBeChecked();
  await expect(panel.getByRole("button", { name: /^UV/ })).toBeVisible();
  await expect(panel.getByRole("button", { name: /^Wind/ })).toHaveCount(0);
  expect(hits).toContain("/api/v2/rovers/curiosity/weather");

  // the slider moves with the keyboard, one loaded sol at a time
  const slider = panel.getByRole("slider");
  await slider.focus();
  const before = await slider.getAttribute("aria-valuetext");
  await page.keyboard.press("ArrowLeft");
  await expect(slider).not.toHaveAttribute("aria-valuetext", before ?? "");
  await expect
    .poll(() => hits.filter((h) => /\/curiosity\/weather\/\d+$/.test(h)).length)
    .toBeGreaterThan(0);
  await expect(panel.getByRole("img", { name: /by local hour/i })).toHaveCount(5);
  await expect(panel).toContainText(/LMST hours/i);

  // a click on the daily chart picks a sol too
  const asked = hits.length;
  const chart = panel.getByRole("img", { name: /daily/i });
  const box = (await chart.boundingBox())!;
  await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.5);
  await expect.poll(() => hits.length).toBeGreaterThan(asked);
  await expect(panel.getByRole("img", { name: /by local hour/i })).toHaveCount(5);
  expect(w.consoleErrors).toEqual([]);
});

test("AC-46 a flagged sol has a marker, and hovering it shows the flag in words", async ({
  page,
}) => {
  await mockWeather(page);
  await page.goto("/data#weather");
  const panel = page.getByRole("region", { name: /Weather by sol/ });
  await panel.getByRole("button", { name: /^Humidity/ }).click();
  const marker = panel.getByTestId("weather-flag-marker").first();
  await expect(marker).toHaveAttribute("data-sol", "140");
  await marker.hover();
  await expect(page.getByTestId("weather-tooltip")).toContainText(/humidity above 100/i);
});

test("AC-46 Previous / Next anomaly move the inspector by keyboard", async ({ page }) => {
  await mockWeather(page);
  await page.goto("/data#weather");
  const panel = page.getByRole("region", { name: /Weather by sol/ });
  await expect(panel.getByRole("slider")).toBeVisible();
  const prev = panel.getByRole("button", { name: /Previous anomaly/ });
  await prev.focus();
  await page.keyboard.press("Enter");
  await expect(panel).toContainText(/Sol 140\b/);
  await expect(panel.getByRole("list", { name: /Flags for sol 140/ })).toContainText(
    /humidity above 100/i,
  );
});

for (const scheme of ["light", "dark"] as const) {
  test.describe(`AC-46 360 px, ${scheme} theme`, () => {
    test.use({ viewport: { width: 360, height: 800 }, colorScheme: scheme });

    test(`AC-46 /data#weather at 360 px (${scheme}): no horizontal scroll, no nested scroll areas, credit visible`, async ({
      page,
    }) => {
      await mockWeather(page);
      await page.goto("/data#weather");
      const panel = page.getByRole("region", { name: /Weather by sol/ });
      await expect(panel.getByRole("slider")).toBeVisible();
      // the hourly charts appear once a sol is picked (here: by keyboard on the slider)
      await panel.getByRole("slider").focus();
      await page.keyboard.press("ArrowLeft");
      await expect(panel.getByRole("img", { name: /by local hour/i }).first()).toBeVisible();
      await settle(page);
      for (const r of ["Perseverance", "Curiosity"]) {
        await panel.locator("label", { hasText: new RegExp(`^${r} `) }).click();
        await expect(panel.getByRole("radio", { name: new RegExp(r) })).toBeChecked();
        await expect(panel.getByRole("slider")).toBeVisible();
        await settle(page);
        const { scrollWidth, innerWidth } = await horizontalOverflow(page);
        expect(scrollWidth, `${r}: page width`).toBeLessThanOrEqual(innerWidth);
        const chart = panel.getByRole("img", { name: /daily/i });
        const box = (await chart.boundingBox())!;
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
      // below 680 px the table rows become cards: still no sideways scroll
      await expect(panel.getByRole("table")).toBeVisible();
      await page.screenshot({ path: `test-results/weather-360-${scheme}.png`, fullPage: true });
    });
  });
}

test("AC-46 the grey zone and the lag follow the rover's current sol", async ({ page }) => {
  await mockWeather(page);
  await page.goto("/data#weather");
  const panel = page.getByRole("region", { name: /Weather by sol/ });
  await expect(panel).toContainText(new RegExp(`${PER_NOW - 159} sols`));
});
