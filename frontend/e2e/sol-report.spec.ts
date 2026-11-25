import { expect, test, type Page } from "@playwright/test";
import { CUR_NOW, PER_NOW, weatherList } from "../tests/fixtures/weather";
import { radiationResponse } from "../tests/fixtures/radiation";
import { curLong, perLong } from "../tests/fixtures/range";
import { latestLocation, radAround, reportDetail } from "../tests/fixtures/solreport";
import { horizontalOverflow, settle, watchPage } from "./helpers";

// Stage 22 phase B in a real browser (docs/proposals/sol-conditions.md): the sol report (AC-25 ... AC-33)
// and the way table -> Sol report -> Photos. The whole API is intercepted, so the numbers are fixed.

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
const PER = rover("perseverance", "Perseverance", PER_NOW);
const CUR = rover("curiosity", "Curiosity", CUR_NOW);

const json = (body: unknown, status = 200) => ({
  status,
  contentType: "application/json",
  body: JSON.stringify(body),
});
const err = (status: number, code: string, message: string) =>
  json({ error: { code, message, details: null } }, status);

interface Mock {
  /** Calls to `/weather/{sol}` by sol. */
  detailCalls: number[];
  /** Photo list requests (the last step of the flow). */
  photoRequests: string[];
}

/**
 * The whole API of the report. `detail` can replace the answer for `/weather/{sol}`: it gets the rover, the sol
 * and the number of the call (from 1), and returns a route result.
 */
async function mockApi(
  page: Page,
  detail?: (
    id: "perseverance" | "curiosity",
    sol: number,
    call: number,
  ) => Promise<ReturnType<typeof json>> | ReturnType<typeof json>,
): Promise<Mock> {
  const mock: Mock = { detailCalls: [], photoRequests: [] };
  await page.route("**/api/v2/rovers", (r) => r.fulfill(json({ items: [PER, CUR] })));
  await page.route("**/api/v2/rovers/perseverance", (r) => r.fulfill(json(PER)));
  await page.route("**/api/v2/rovers/curiosity", (r) => r.fulfill(json(CUR)));
  await page.route("**/api/v2/rovers/*/location**", (r) => {
    const id = r.request().url().includes("/curiosity/") ? "curiosity" : "perseverance";
    return r.fulfill(json(latestLocation(id)));
  });
  await page.route("**/api/v2/rovers/curiosity/radiation**", (r) => {
    const url = new URL(r.request().url());
    const lo = Number(url.searchParams.get("sol_min") ?? 0);
    const hi = Number(url.searchParams.get("sol_max") ?? 1e9);
    // the table of phase A asks for the whole history, the report for a range around its sol
    const items = url.searchParams.has("sol_min")
      ? radAround.filter((x) => x.sol >= lo && x.sol <= hi)
      : radAround;
    return r.fulfill(json(radiationResponse(items)));
  });
  await page.route("**/api/v2/rovers/*/weather**", async (route) => {
    const url = new URL(route.request().url());
    const m = url.pathname.match(/\/rovers\/(perseverance|curiosity)\/weather(?:\/(\d+))?$/);
    if (!m) return route.fulfill(err(404, "not_found", "x"));
    const id = m[1] as "perseverance" | "curiosity";
    if (m[2] === undefined) {
      return route.fulfill(json(weatherList(id, id === "perseverance" ? perLong : curLong)));
    }
    const sol = Number(m[2]);
    mock.detailCalls.push(sol);
    if (detail) return route.fulfill(await detail(id, sol, mock.detailCalls.length));
    return route.fulfill(json(reportDetail(id, sol, { ls: id === "curiosity" ? 356.7 : 52.4 })));
  });
  // the Photos page, the last step of the flow
  await page.route("**/api/v2/rovers/*/photos**", (r) => {
    mock.photoRequests.push(r.request().url());
    return r.fulfill(
      json({
        items: [],
        page: 1,
        per_page: 24,
        total: 0,
        nearest_sols: { before: null, after: null },
      }),
    );
  });
  await page.route("**/api/v2/rovers/*/cameras**", (r) => r.fulfill(json({ items: [] })));
  await page.route("**/api/v2/rovers/*/manifest**", (r) => {
    const id = r.request().url().includes("/curiosity/") ? CUR : PER;
    return r.fulfill(json({ rover: id, sols: [] }));
  });
  return mock;
}

const CUR_URL = "/rovers/curiosity/sols/344/conditions";
const PER_URL = "/rovers/perseverance/sols/100/conditions";
const heading = (page: Page, name: string, sol: number) =>
  page.getByRole("heading", { level: 1, name: new RegExp(`${name}.*sol ${sol}\\b`, "i") });
/**
 * The hourly svg charts of the report: their name says "hourly" (the strip of hour coverage is an img too, but
 * not an svg, and the radiation trend of Curiosity is a chart of sols, not of hours).
 */
const charts = (page: Page) => page.locator("main svg[role='img'][aria-label*='hourly' i]");

test.describe("table -> Sol report -> Photos", () => {
  test("AC-31 a row of the weather table opens the sol report, whose button leads to the photos, and Back returns", async ({
    page,
  }) => {
    const mock = await mockApi(page);
    const watch = watchPage(page);
    await page.goto("/data/weather?rover=curiosity&from=4591&to=4710");
    await expect(page.getByRole("table")).toBeVisible();
    const link = page.getByRole("link", { name: /sol report/i }).first();
    await expect(link).toHaveAttribute("href", "/rovers/curiosity/sols/4710/conditions");
    await link.click();

    await expect(page).toHaveURL(/\/rovers\/curiosity\/sols\/4710\/conditions$/);
    await expect(heading(page, "Curiosity", 4710)).toBeVisible();
    await expect(charts(page).first()).toBeVisible();
    expect(mock.detailCalls).toContain(4710);
    await settle(page);
    expect(watch.consoleErrors).toEqual([]);
    expect(watch.badResponses).toEqual([]);

    await page.getByRole("link", { name: /photos of this sol/i }).click();
    await expect(page).toHaveURL(/\/rovers\/curiosity\?sol=4710/);
    await expect.poll(() => mock.photoRequests.some((u) => /sol=4710/.test(u))).toBe(true);

    // Back: the report, and once more: the table with the range of the address
    await page.goBack();
    await expect(heading(page, "Curiosity", 4710)).toBeVisible();
    await page.goBack();
    await expect(page.getByLabel("From sol", { exact: true })).toHaveValue("4591");
    await expect(page.getByLabel("To sol", { exact: true })).toHaveValue("4710");
  });

  test("AC-30 the neighbouring sols: forward, back, and the address follows", async ({ page }) => {
    await mockApi(page);
    await page.goto(CUR_URL);
    await expect(heading(page, "Curiosity", 344)).toBeVisible();
    await page.getByRole("link", { name: /Sol 345/ }).click();
    await expect(page).toHaveURL(/\/sols\/345\/conditions$/);
    await expect(heading(page, "Curiosity", 345)).toBeVisible();
    await page.getByRole("link", { name: /Sol 344/ }).click();
    await expect(heading(page, "Curiosity", 344)).toBeVisible();
    await page.goBack();
    await expect(heading(page, "Curiosity", 345)).toBeVisible();
  });

  test("AC-25 a reload of the report address opens the same report (the server serves the page)", async ({
    page,
  }) => {
    await mockApi(page);
    await page.goto(PER_URL);
    await expect(heading(page, "Perseverance", 100)).toBeVisible();
    await page.reload();
    await expect(heading(page, "Perseverance", 100)).toBeVisible();
    await page.goto("/rovers/curiosity/sols/abc/conditions");
    await expect(page.getByRole("heading", { name: /not found/i })).toBeVisible();
  });
});

test.describe("AC-29 the shared hour cursor with the pointer", () => {
  test("AC-29 hover (desktop) or touch (phone) on a chart moves the hour slider and the cursor of the other charts", async ({
    page,
  }, info) => {
    await mockApi(page);
    await page.goto(CUR_URL);
    await expect(heading(page, "Curiosity", 344)).toBeVisible();
    const slider = page.getByLabel(/inspect hour/i);
    const first = charts(page).first();
    const other = charts(page).nth(1);
    await first.scrollIntoViewIfNeeded();
    const box = (await first.boundingBox())!;
    const point = (fraction: number) => ({ x: box.width * fraction, y: box.height / 2 });
    const pointTo = async (fraction: number) => {
      if (info.project.name === "mobile") await first.tap({ position: point(fraction) });
      else await first.hover({ position: point(fraction) });
    };

    const otherBefore = await other.evaluate((el) => el.outerHTML);
    await pointTo(0.25);
    const early = Number(await slider.inputValue());
    const otherEarly = await other.evaluate((el) => el.outerHTML);
    expect(otherEarly, "the cursor appears in the other chart").not.toBe(otherBefore);
    await pointTo(0.75);
    const late = Number(await slider.inputValue());
    expect(late, "further right means a later hour").toBeGreaterThan(early + 6);
    expect(await other.evaluate((el) => el.outerHTML)).not.toBe(otherEarly);
    // the announcement to a screen reader is for the slider and the keyboard (next test), not for every hover
  });

  test("AC-29 the slider works with the keyboard", async ({ page }) => {
    await mockApi(page);
    await page.goto(CUR_URL);
    await expect(heading(page, "Curiosity", 344)).toBeVisible();
    const slider = page.getByLabel(/inspect hour/i);
    await slider.focus();
    await page.keyboard.press("Home");
    await expect(slider).toHaveValue("0");
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("ArrowRight");
    await expect(slider).toHaveValue("2");
    await expect(page.locator("[aria-live='polite']").filter({ hasText: /02:00/ })).toHaveCount(1);
  });
});

const NARROW = { width: 360, height: 800 };

for (const scheme of ["light", "dark"] as const) {
  test.describe(`AC-33 360 px, ${scheme} theme`, () => {
    test.use({ viewport: NARROW, colorScheme: scheme });

    for (const [name, url, who, sol] of [
      ["Curiosity", CUR_URL, "Curiosity", 344],
      ["Perseverance", PER_URL, "Perseverance", 100],
    ] as const) {
      test(`AC-33 ${name} at 360 px (${scheme}): one column of charts, no sideways scroll, no scroll area that eats the wheel`, async ({
        page,
      }) => {
        await mockApi(page);
        const watch = watchPage(page);
        await page.goto(url);
        await expect(heading(page, who, sol)).toBeVisible();
        await expect(charts(page).first()).toBeVisible();
        await settle(page);

        const { scrollWidth, innerWidth } = await horizontalOverflow(page);
        expect(scrollWidth, "page width").toBeLessThanOrEqual(innerWidth);

        // one column: the charts share the left edge and follow each other down the page
        const boxes = await charts(page).evaluateAll((svgs) =>
          svgs.map((s) => {
            const b = s.getBoundingClientRect();
            return { x: b.x, y: b.y + window.scrollY, w: b.width, right: b.right };
          }),
        );
        expect(boxes.length).toBeGreaterThanOrEqual(4);
        for (const b of boxes) {
          expect(b.x).toBeGreaterThanOrEqual(0);
          expect(b.right).toBeLessThanOrEqual(innerWidth);
          expect(Math.abs(b.x - boxes[0]!.x)).toBeLessThanOrEqual(2);
        }
        const tops = boxes.map((b) => b.y);
        expect(tops, "charts go down the page").toEqual([...tops].sort((a, b) => a - b));
        expect(new Set(tops.map((t) => Math.round(t))).size).toBe(tops.length);

        // nothing inside the page scrolls up and down on its own (a scroll area eats the wheel)
        const nested = await page.evaluate(
          () =>
            [...document.querySelectorAll<HTMLElement>("main *")].filter((el) => {
              const y = getComputedStyle(el).overflowY;
              return (y === "auto" || y === "scroll") && el.scrollHeight > el.clientHeight + 1;
            }).length,
        );
        expect(nested, "nested vertical scroll areas").toBe(0);

        // the theme is the one asked for: a dark page has a dark background and light text
        const colours = await page.evaluate(() => {
          const lum = (css: string) => {
            const m = css
              .match(/\d+(\.\d+)?/g)!
              .slice(0, 3)
              .map(Number);
            return (0.2126 * m[0]! + 0.7152 * m[1]! + 0.0722 * m[2]!) / 255;
          };
          return {
            bg: lum(getComputedStyle(document.body).backgroundColor),
            fg: lum(getComputedStyle(document.body).color),
          };
        });
        if (scheme === "dark") expect(colours.bg).toBeLessThan(colours.fg);
        else expect(colours.bg).toBeGreaterThan(colours.fg);

        await page.screenshot({
          path: `test-results/sol-report-${name.toLowerCase()}-360-${scheme}.png`,
          fullPage: true,
        });
        expect(watch.consoleErrors).toEqual([]);
      });
    }

    test(`AC-33 loading (${scheme}): a skeleton while the sol loads, then the report`, async ({
      page,
    }) => {
      let release: () => void = () => {};
      const gate = new Promise<void>((res) => (release = res));
      await mockApi(page, async (id, sol) => {
        await gate;
        return json(reportDetail(id, sol, { ls: 356.7 }));
      });
      await page.goto(CUR_URL);
      await expect(page.getByRole("status", { name: /loading/i })).toBeVisible();
      await expect(charts(page)).toHaveCount(0);
      const { scrollWidth, innerWidth } = await horizontalOverflow(page);
      expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
      await page.screenshot({ path: `test-results/sol-report-loading-360-${scheme}.png` });
      release();
      await expect(heading(page, "Curiosity", 344)).toBeVisible();
      await expect(charts(page).first()).toBeVisible();
    });

    test(`AC-33 503 (${scheme}): a readable error with Retry, and Retry brings the report`, async ({
      page,
    }) => {
      await mockApi(page, (id, sol, call) =>
        call === 1
          ? err(503, "db_unavailable", "Database is unavailable")
          : json(reportDetail(id, sol, { ls: 356.7 })),
      );
      await page.goto(CUR_URL);
      const alert = page.getByRole("alert");
      await expect(alert).toBeVisible();
      const retry = page.getByRole("button", { name: /retry|try again/i });
      await expect(retry).toBeVisible();
      const box = (await retry.boundingBox())!;
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(NARROW.width);
      const { scrollWidth, innerWidth } = await horizontalOverflow(page);
      expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
      await page.screenshot({ path: `test-results/sol-report-503-360-${scheme}.png` });
      await retry.click();
      await expect(heading(page, "Curiosity", 344)).toBeVisible();
      await expect(charts(page).first()).toBeVisible();
      await expect(page.getByRole("alert")).toHaveCount(0);
    });
  });
}

test.describe("AC-33 760 px: two columns of charts", () => {
  test.use({ viewport: { width: 760, height: 900 } });

  test("AC-33 at 760 px the charts stand two in a row, and the page does not scroll sideways", async ({
    page,
  }) => {
    await mockApi(page);
    await page.goto(CUR_URL);
    await expect(heading(page, "Curiosity", 344)).toBeVisible();
    await expect(charts(page).first()).toBeVisible();
    await settle(page);
    const boxes = await charts(page).evaluateAll((svgs) =>
      svgs.map((s) => {
        const b = s.getBoundingClientRect();
        return { x: Math.round(b.x), y: Math.round(b.y + window.scrollY) };
      }),
    );
    const columns = new Set(boxes.map((b) => b.x));
    expect(columns.size, "two columns of charts").toBe(2);
    const rows = new Map<number, number>();
    for (const b of boxes) rows.set(b.y, (rows.get(b.y) ?? 0) + 1);
    expect(
      [...rows.values()].some((n) => n === 2),
      "two charts in one row",
    ).toBe(true);
    const { scrollWidth, innerWidth } = await horizontalOverflow(page);
    expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
  });
});

test.describe("AC-33 desktop", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test("AC-33 the report of Perseverance at 1280 px has the wind chart, no radiation, the credit", async ({
    page,
  }) => {
    await mockApi(page);
    const watch = watchPage(page);
    await page.goto(PER_URL);
    await expect(heading(page, "Perseverance", 100)).toBeVisible();
    await expect(page.getByRole("img", { name: /wind/i }).first()).toBeVisible();
    await expect(page.getByRole("heading", { name: /radiation/i })).toHaveCount(0);
    await expect(page.getByRole("main")).toContainText("Rodriguez-Manfredi");
    const { scrollWidth, innerWidth } = await horizontalOverflow(page);
    expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
    await settle(page);
    expect(watch.consoleErrors).toEqual([]);
    expect(watch.badResponses).toEqual([]);
  });

  test("AC-32 the report of Curiosity at 1280 px has the radiation block with the daily values of the sol", async ({
    page,
  }) => {
    await mockApi(page);
    await page.goto(CUR_URL);
    await expect(heading(page, "Curiosity", 344)).toBeVisible();
    await expect(page.getByRole("heading", { name: /radiation/i })).toBeVisible();
    await expect(page.getByRole("main")).toContainText("231.4");
    await expect(page.getByRole("main")).toContainText("MSL RAD (SwRI)");
  });
});

// AC-33: no accessibility violation of level serious or higher. This needs `@axe-core/playwright`, which is not
// a dependency yet (a request to frontend-dev is in the report); until it is installed these tests are skipped.
async function loadAxe(): Promise<null | (new (opts: { page: Page }) => AxeRunner)> {
  const name = "@axe-core/playwright";
  try {
    const mod = (await import(/* @vite-ignore */ name)) as {
      default?: unknown;
      AxeBuilder?: unknown;
    };
    return (mod.AxeBuilder ?? mod.default) as new (opts: { page: Page }) => AxeRunner;
  } catch {
    return null;
  }
}
interface AxeRunner {
  analyze: () => Promise<{ violations: { id: string; impact?: string | null; help: string }[] }>;
}

for (const scheme of ["light", "dark"] as const) {
  test.describe(`AC-33 axe, ${scheme} theme`, () => {
    test.use({ colorScheme: scheme });

    for (const [name, url, who, sol] of [
      ["Curiosity", CUR_URL, "Curiosity", 344],
      ["Perseverance", PER_URL, "Perseverance", 100],
    ] as const) {
      test(`AC-33 ${name} report (${scheme}): no serious or critical axe violation`, async ({
        page,
      }) => {
        const Axe = await loadAxe();
        test.skip(Axe === null, "needs the dev dependency @axe-core/playwright");
        await mockApi(page);
        await page.goto(url);
        await expect(heading(page, who, sol)).toBeVisible();
        await expect(charts(page).first()).toBeVisible();
        await settle(page);
        const result = await new Axe!({ page }).analyze();
        const bad = result.violations.filter(
          (v) => v.impact === "serious" || v.impact === "critical",
        );
        expect(bad.map((v) => `${v.impact} ${v.id}: ${v.help}`)).toEqual([]);
      });
    }
  });
}
