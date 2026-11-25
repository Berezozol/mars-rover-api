import { expect, test, type Page } from "@playwright/test";
import { CUR_NOW, PER_NOW, weatherDetail, weatherList } from "../tests/fixtures/weather";
import { radiationResponse } from "../tests/fixtures/radiation";
import { PER_SOURCE_MAX, curLong, perLong, radLong } from "../tests/fixtures/range";
import { horizontalOverflow, settle, watchPage } from "./helpers";

// Stage 22 phase A in a real browser (docs/proposals/sol-conditions.md): the range slider, the sol
// table, the address, a click on the chart, and the 360 px layout (AC-3, AC-4, AC-9, AC-10, AC-11,
// AC-12). The whole API is intercepted, so the numbers are fixed: Perseverance has sols 3-1859
// (a gap at 1800-1805) and is at sol 2031; Curiosity RAD has sols 15-4843.

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

async function mockApi(page: Page) {
  const per = rover("perseverance", "Perseverance", PER_NOW);
  const cur = rover("curiosity", "Curiosity", CUR_NOW);
  await page.route("**/api/v2/rovers", (r) => r.fulfill(json({ items: [per, cur] })));
  await page.route("**/api/v2/rovers/perseverance", (r) => r.fulfill(json(per)));
  await page.route("**/api/v2/rovers/curiosity", (r) => r.fulfill(json(cur)));
  await page.route("**/api/v2/rovers/curiosity/radiation**", (r) =>
    r.fulfill(json(radiationResponse(radLong))),
  );
  await page.route("**/api/v2/rovers/*/weather**", (route) => {
    const url = new URL(route.request().url());
    const m = url.pathname.match(/\/rovers\/(perseverance|curiosity)\/weather(?:\/(\d+))?$/);
    if (!m)
      return route.fulfill(
        json({ error: { code: "not_found", message: "x", details: null } }, 404),
      );
    const id = m[1] as "perseverance" | "curiosity";
    const items = id === "perseverance" ? perLong : curLong;
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

const MAX_SOL = PER_NOW; // the right end of the track: the rover's current sol
const startHandle = (page: Page) => page.getByRole("slider", { name: /range start/i });
const endHandle = (page: Page) => page.getByRole("slider", { name: /range end/i });
const solsOnPage = (page: Page) =>
  page
    .getByRole("table")
    .getByRole("row")
    .evaluateAll((rows) =>
      rows.slice(1).map((r) => Number((r.firstElementChild?.textContent ?? "").replace(/\D/g, ""))),
    );

test.describe("range slider and table with the keyboard and the mouse", () => {
  test("AC-3 Tab reaches the handles; arrows, PageUp/PageDown, Home and End move them", async ({
    page,
  }) => {
    await mockApi(page);
    const w = watchPage(page);
    await page.goto("/data/weather");
    await expect(page.getByRole("table")).toBeVisible();
    await expect(page.getByLabel("From sol", { exact: true })).toHaveValue(
      String(PER_SOURCE_MAX - 668),
    );
    await startHandle(page).focus();
    await expect(startHandle(page)).toBeFocused();
    await page.keyboard.press("ArrowRight");
    await expect(page.getByLabel("From sol", { exact: true })).toHaveValue(
      String(PER_SOURCE_MAX - 667),
    );
    await page.keyboard.press("PageUp");
    await expect(startHandle(page)).toHaveAttribute("aria-valuenow", String(PER_SOURCE_MAX - 657));
    await page.keyboard.press("Home");
    await expect(page.getByLabel("From sol", { exact: true })).toHaveValue("3");
    // Tab goes from the start handle to the end handle
    await page.keyboard.press("Tab");
    await expect(endHandle(page)).toBeFocused();
    await page.keyboard.press("PageDown");
    await expect(page.getByLabel("To sol", { exact: true })).toHaveValue(
      String(PER_SOURCE_MAX - 10),
    );
    await page.keyboard.press("End");
    await expect(page.getByLabel("To sol", { exact: true })).toHaveValue(String(PER_NOW));
    expect(w.consoleErrors).toEqual([]);
  });

  test("AC-3 a handle can be dragged with the mouse, and a click on the track moves the nearest handle", async ({
    page,
  }) => {
    await mockApi(page);
    await page.goto("/data/weather");
    await expect(page.getByRole("table")).toBeVisible();
    const handle = startHandle(page);
    await handle.scrollIntoViewIfNeeded();
    const box = (await handle.boundingBox())!;
    const before = Number(await handle.getAttribute("aria-valuenow"));
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 - 60, box.y + box.height / 2, { steps: 6 });
    await page.mouse.up();
    expect(Number(await handle.getAttribute("aria-valuenow"))).toBeLessThan(before);
  });

  test("AC-3 a click on the track moves the nearest handle to that sol", async ({ page }) => {
    await mockApi(page);
    await page.goto("/data/weather");
    await expect(page.getByRole("table")).toBeVisible();
    const track = page.getByTestId("range-track");
    await track.scrollIntoViewIfNeeded();
    const t = (await track.boundingBox())!;
    const end = Number(await endHandle(page).getAttribute("aria-valuenow"));
    const start = Number(await startHandle(page).getAttribute("aria-valuenow"));
    // a point a little left of the start handle: the start handle is the nearest one
    const startX = t.x + ((start - 3) / (MAX_SOL - 3)) * t.width;
    await page.mouse.click(startX - 80, t.y + t.height / 2);
    const now = Number(await startHandle(page).getAttribute("aria-valuenow"));
    expect(now).toBeLessThan(start);
    expect(Number(await endHandle(page).getAttribute("aria-valuenow"))).toBe(end);
  });

  test("AC-4 'To sol' = 'From sol' + 3 is refused; a good value is applied with Enter", async ({
    page,
  }) => {
    await mockApi(page);
    await page.goto("/data/weather");
    await expect(page.getByRole("table")).toBeVisible();
    const to = page.getByLabel("To sol", { exact: true });
    await to.fill(String(PER_SOURCE_MAX - 668 + 3));
    await to.press("Enter");
    await expect(to).toHaveAttribute("aria-invalid", "true");
    await expect(page.getByRole("alert").filter({ hasText: /\S/ })).toBeVisible();
    await expect(endHandle(page)).toHaveAttribute("aria-valuenow", String(PER_SOURCE_MAX));
    await to.fill("1700");
    await to.press("Enter");
    await expect(endHandle(page)).toHaveAttribute("aria-valuenow", "1700");
    await expect(to).not.toHaveAttribute("aria-invalid", "true");
    // leaving the field applies a good value too
    const from = page.getByLabel("From sol", { exact: true });
    await from.fill("1500");
    await from.blur();
    await expect(startHandle(page)).toHaveAttribute("aria-valuenow", "1500");
  });

  test("AC-9 a click on the chart finds the sol in the table: the row is highlighted and has the focus", async ({
    page,
  }) => {
    await mockApi(page);
    await page.goto("/data/weather");
    await page
      .getByRole("group", { name: /range presets/i })
      .getByRole("button", { name: /Last 120 sols/ })
      .click();
    await expect(page.getByLabel("From sol", { exact: true })).toHaveValue("1740");
    const chart = page.getByRole("img", { name: /daily/i });
    await chart.scrollIntoViewIfNeeded();
    const box = (await chart.boundingBox())!;
    await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.5);
    const focused = page.locator("tbody tr:focus-within, tbody tr:focus").first();
    await expect(focused).toBeVisible();
    const sol = Number(
      ((await focused.locator("th, td").first().textContent()) ?? "").replace(/\D/g, ""),
    );
    expect(sol).toBeGreaterThanOrEqual(1760);
    expect(sol).toBeLessThanOrEqual(1840);
  });

  test("AC-12 Curiosity and its range are in the address; Photos and Back keep them", async ({
    page,
  }) => {
    await mockApi(page);
    await page.goto("/data/weather?rover=curiosity&from=4591&to=4710");
    await expect(page.getByRole("radio", { name: /Curiosity/ })).toBeChecked();
    await expect(page.getByLabel("From sol", { exact: true })).toHaveValue("4591");
    await page.reload();
    await expect(page.getByRole("radio", { name: /Curiosity/ })).toBeChecked();
    await expect(page.getByLabel("To sol", { exact: true })).toHaveValue("4710");
    await page
      .getByRole("table")
      .getByRole("link", { name: /photos/i })
      .first()
      .click();
    await expect(page).toHaveURL(/\/rovers\/curiosity\?sol=4710/);
    await page.goBack();
    await expect(page.getByRole("radio", { name: /Curiosity/ })).toBeChecked();
    await expect(page.getByLabel("From sol", { exact: true })).toHaveValue("4591");
    await expect(page.getByLabel("To sol", { exact: true })).toHaveValue("4710");
    // a range that misses the data falls back to the default
    await page.goto("/data/weather?rover=curiosity&from=1&to=2");
    await expect(page.getByLabel("To sol", { exact: true })).toHaveValue("4710");
    await expect(page.getByLabel("From sol", { exact: true })).toHaveValue("4042");
  });

  test("AC-12 the range is in the address, survives a reload and Back", async ({ page }) => {
    await mockApi(page);
    await page.goto("/data/weather");
    await expect(page.getByRole("table")).toBeVisible();
    await page
      .getByRole("group", { name: /range presets/i })
      .getByRole("button", { name: /Last 120 sols/ })
      .click();
    await expect(page).toHaveURL(/from=1740/);
    await expect(page).toHaveURL(/to=1859/);
    await page.reload();
    await expect(page.getByLabel("From sol", { exact: true })).toHaveValue("1740");
    await expect(page.getByLabel("To sol", { exact: true })).toHaveValue("1859");
    // away and back: the same range
    await page.goto("/");
    await page.goBack();
    await expect(page.getByLabel("From sol", { exact: true })).toHaveValue("1740");
    // a bad address gives the default range and no error
    await page.goto("/data/weather?from=abc&to=5");
    await expect(page.getByLabel("From sol", { exact: true })).toHaveValue(
      String(PER_SOURCE_MAX - 668),
    );
    await expect(page.getByRole("alert").filter({ hasText: /\S/ })).toHaveCount(0);
  });

  test("AC-8 'Jump to sol' with the keyboard: a far sol is found, focused, and its page is shown", async ({
    page,
  }) => {
    await mockApi(page);
    await page.goto("/data/weather");
    await page
      .getByRole("group", { name: /range presets/i })
      .getByRole("button", { name: /Last 120 sols/ })
      .click();
    const jump = page.getByLabel("Jump to sol");
    await jump.fill("1745");
    await jump.press("Enter");
    await expect.poll(() => solsOnPage(page)).toContain(1745);
    await expect(page.locator("tbody tr:focus-within").first()).toContainText(/1,?745/);
  });
});

for (const scheme of ["light", "dark"] as const) {
  test.describe(`AC-10 360 px, ${scheme} theme`, () => {
    test.use({ viewport: { width: 360, height: 800 }, colorScheme: scheme });

    test(`AC-10 /data/weather at 360 px (${scheme}): rows are cards with 'Sort by', no sideways scroll`, async ({
      page,
    }) => {
      await mockApi(page);
      const w = watchPage(page);
      await page.goto("/data/weather");
      await expect(page.getByRole("table")).toBeVisible();
      await settle(page);
      const { scrollWidth, innerWidth } = await horizontalOverflow(page);
      expect(scrollWidth, "page width").toBeLessThanOrEqual(innerWidth);
      // the sort control of the cards
      const sortBy = page.getByLabel("Sort by");
      await expect(sortBy).toBeVisible();
      const first = async () => solsOnPage(page).then((s) => s[0]);
      expect(await first()).toBe(PER_SOURCE_MAX);
      const options = await sortBy.locator("option").allTextContents();
      expect(options.length).toBeGreaterThan(1);
      await sortBy.selectOption({ index: 0 });
      await page
        .getByRole("button", { name: /ascending|descending/i })
        .first()
        .click();
      await expect.poll(first).not.toBe(PER_SOURCE_MAX);
      // every row fits the screen
      const rows = await page
        .getByRole("table")
        .getByRole("row")
        .evaluateAll((rs) =>
          rs.slice(1).map((r) => {
            const b = r.getBoundingClientRect();
            return { left: b.left, right: b.right };
          }),
        );
      for (const r of rows) {
        expect(r.left).toBeGreaterThanOrEqual(0);
        expect(r.right).toBeLessThanOrEqual(innerWidth);
      }
      // the chart fits too, and no area inside the page scrolls on its own and eats the wheel
      const box = (await page.getByRole("img", { name: /daily/i }).boundingBox())!;
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(innerWidth);
      const nested = await page.evaluate(
        () =>
          [...document.querySelectorAll<HTMLElement>("main *")].filter((el) => {
            const y = getComputedStyle(el).overflowY;
            const x = getComputedStyle(el).overflowX;
            return (
              ((y === "auto" || y === "scroll") && el.scrollHeight > el.clientHeight + 1) ||
              ((x === "auto" || x === "scroll") && el.scrollWidth > el.clientWidth + 1)
            );
          }).length,
      );
      expect(nested, "nested scroll areas").toBe(0);
      await page.screenshot({
        path: `test-results/sol-range-weather-360-${scheme}.png`,
        fullPage: true,
      });
      expect(w.consoleErrors).toEqual([]);
    });

    test(`AC-10 /data/radiation at 360 px (${scheme}): the same layout, credit visible`, async ({
      page,
    }) => {
      await mockApi(page);
      await page.goto("/data/radiation");
      await expect(page.getByRole("table")).toBeVisible();
      await expect(page.getByLabel("Sort by")).toBeVisible();
      await settle(page);
      const { scrollWidth, innerWidth } = await horizontalOverflow(page);
      expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
      await expect(page.getByRole("main")).toContainText("MSL RAD (SwRI)");
      await page.screenshot({
        path: `test-results/sol-range-radiation-360-${scheme}.png`,
        fullPage: true,
      });
    });
  });
}

test.describe("AC-10 desktop: the table keeps its columns and the sort buttons", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test("AC-10 column headers are sort buttons, 'Sort by' is not needed", async ({ page }) => {
    await mockApi(page);
    await page.goto("/data/weather");
    await expect(page.getByRole("table")).toBeVisible();
    const head = page.getByRole("columnheader", { name: /^Sol/ });
    await expect(head).toHaveAttribute("aria-sort", "descending");
    await head.getByRole("button").click();
    await expect(head).toHaveAttribute("aria-sort", "ascending");
    expect((await solsOnPage(page))[0]).toBe(PER_SOURCE_MAX - 668);
    const { scrollWidth, innerWidth } = await horizontalOverflow(page);
    expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
  });
});

test.describe("AC-11 radiation scale in a browser", () => {
  test("AC-11 Auto starts from zero on a long range and fits a short one; the toggles work", async ({
    page,
  }) => {
    await mockApi(page);
    await page.goto("/data/radiation");
    await expect(page.getByRole("table")).toBeVisible();
    const lowestTick = () =>
      page
        .getByRole("img", { name: /dose/i })
        .first()
        .evaluate((svg) =>
          Math.min(
            ...[...svg.querySelectorAll("text")]
              .map((t) => (t.textContent ?? "").replace(/,/g, "").trim())
              .filter((t) => /^-?\d+(\.\d+)?$/.test(t))
              .map(Number),
          ),
        );
    expect(await lowestTick()).toBe(0);
    await page
      .getByRole("group", { name: /range presets/i })
      .getByRole("button", { name: /Last 120 sols/ })
      .click();
    await expect.poll(lowestTick).toBeGreaterThan(0);
    const scale = page.getByLabel(/vertical scale/i);
    await scale.selectOption({ label: "From zero" }).catch(async () => {
      await page.getByRole("radio", { name: /from zero/i }).check();
    });
    await expect.poll(lowestTick).toBe(0);
  });
});
