import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { CUR_NOW, PER_NOW, perSol, weatherList, type WItem } from "../tests/fixtures/weather";
import { radiationResponse } from "../tests/fixtures/radiation";
import { radDetail } from "../tests/fixtures/radhours";
import {
  PER_SOURCE_MAX,
  SPIKE_DOSE,
  SPIKE_SOL,
  perLong,
  radLong,
  radManySpikes,
  radSpiked,
} from "../tests/fixtures/range";
import { latestLocation, radAround, reportDetail } from "../tests/fixtures/solreport";
import { horizontalOverflow, settle, watchPage } from "./helpers";

// Small fixes 2026-11-26 (docs/proposals/small-fixes-2026-11-26.md) in a real browser: AC-3 (the credit of the
// sol report comes from `/weather/{sol}`), AC-4 (a spike is left off the radiation scale and named), AC-8 (the old
// "Inspect a sol" inspector is gone, the chart click and the flag pills stay). The whole API is intercepted.

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

// Perseverance with one flagged sol (humidity above 100 %) inside the newest 50 rows of the table.
const FLAGGED = 1850;
const perFlagged: WItem[] = perLong.map((i) =>
  i.sol === FLAGGED
    ? perSol(FLAGGED, {
        earth_date: i.earth_date,
        humidity_pct: { min: 0, max: 137.5, mean: 20.1 },
        flags: [{ code: "humidity_over_100", parameter: "humidity_pct", count: 12 }],
      })
    : i,
);

interface Mock {
  /** Every request of the page to the API, as `pathname + search`. */
  api: string[];
}

async function mockApi(page: Page, radiation: unknown[] = radLong): Promise<Mock> {
  const mock: Mock = { api: [] };
  page.on("request", (r) => {
    const u = new URL(r.url());
    if (u.pathname.startsWith("/api/")) mock.api.push(u.pathname + u.search);
  });
  await page.route("**/api/v2/rovers", (r) => r.fulfill(json({ items: [PER, CUR] })));
  await page.route("**/api/v2/rovers/perseverance", (r) => r.fulfill(json(PER)));
  await page.route("**/api/v2/rovers/curiosity", (r) => r.fulfill(json(CUR)));
  await page.route("**/api/v2/rovers/*/location**", (r) => {
    const id = r.request().url().includes("/curiosity/") ? "curiosity" : "perseverance";
    return r.fulfill(json(latestLocation(id)));
  });
  await page.route("**/api/v2/rovers/curiosity/radiation**", (r) => {
    const url = new URL(r.request().url());
    if (/\/radiation\/\d+$/.test(url.pathname)) {
      return r.fulfill(json(radDetail(Number(url.pathname.split("/").pop()))));
    }
    const lo = Number(url.searchParams.get("sol_min") ?? 0);
    const hi = Number(url.searchParams.get("sol_max") ?? 1e9);
    const items = url.searchParams.has("sol_min")
      ? radAround.filter((x) => x.sol >= lo && x.sol <= hi)
      : radiation;
    return r.fulfill(json(radiationResponse(items)));
  });
  await page.route("**/api/v2/rovers/*/weather**", (route) => {
    const url = new URL(route.request().url());
    const m = url.pathname.match(/\/rovers\/(perseverance|curiosity)\/weather(?:\/(\d+))?$/);
    if (!m) {
      return route.fulfill(
        json({ error: { code: "not_found", message: "x", details: null } }, 404),
      );
    }
    const id = m[1] as "perseverance" | "curiosity";
    if (m[2] === undefined) {
      return route.fulfill(json(weatherList(id, id === "perseverance" ? perFlagged : [])));
    }
    return route.fulfill(json(reportDetail(id, Number(m[2]), { ls: 356.7 })));
  });
  return mock;
}

const solOfFocusedRow = async (page: Page) => {
  const row = page.locator("tbody tr:focus-within, tbody tr:focus").first();
  await expect(row).toBeVisible();
  return Number(((await row.locator("th, td").first().textContent()) ?? "").replace(/\D/g, ""));
};

test.describe("AC-3 the credit of the sol report comes from /weather/{sol}", () => {
  for (const [id, name, sol, who] of [
    ["curiosity", "Curiosity", 344, "Gomez-Elvira"],
    ["perseverance", "Perseverance", 100, "Rodriguez-Manfredi"],
  ] as const) {
    test(`AC-3 ${name}: the credit line is shown and no request asks for the list of /weather`, async ({
      page,
    }) => {
      const mock = await mockApi(page);
      const watch = watchPage(page);
      await page.goto(`/rovers/${id}/sols/${sol}/conditions`);
      await expect(
        page.getByRole("heading", { level: 1, name: new RegExp(`${name}.*sol ${sol}\\b`, "i") }),
      ).toBeVisible();
      await expect(page.getByRole("main")).toContainText(who);
      await expect(page.getByRole("main")).toContainText(/Credit:/);
      await settle(page);
      const weatherCalls = mock.api.filter((u) => u.includes(`/rovers/${id}/weather`));
      expect(weatherCalls).toEqual([`/api/v2/rovers/${id}/weather/${sol}`]);
      expect(watch.consoleErrors).toEqual([]);
      expect(watch.badResponses).toEqual([]);
    });
  }
});

test.describe("AC-4 a spike is left off the radiation scale", () => {
  const RANGE = "/data/radiation?from=4000&to=4300";
  // the numbers on the vertical axis: the numeric labels above the row of sol numbers
  const yAxis = (page: Page) =>
    page
      .getByRole("img", { name: /dose/i })
      .first()
      .evaluate((svg) => {
        const labels = [...svg.querySelectorAll("text")]
          .map((t) => ({
            y: Number(t.getAttribute("y")),
            n: (t.textContent ?? "").replace(/,/g, "").trim(),
          }))
          .filter((t) => /^-?\d+(\.\d+)?$/.test(t.n));
        const bottom = Math.max(...labels.map((t) => t.y));
        return labels.filter((t) => t.y < bottom).map((t) => Number(t.n));
      });

  test("AC-4 the label names the sol and the value, the chart name says the same, the scale ignores the spike", async ({
    page,
  }) => {
    await mockApi(page, radSpiked);
    const watch = watchPage(page);
    await page.goto(RANGE);
    await expect(page.getByRole("table")).toBeVisible();
    const label = page.getByText(/Sol 4190: 2,?342(\.\d)? µGy\/day, off scale/);
    await expect(label).toBeVisible();
    await expect(
      page.getByRole("img", { name: /Sol 4190: 2,?342(\.\d)? µGy\/day, off scale/ }),
    ).toHaveCount(1);
    const top = Math.max(...(await yAxis(page)));
    expect(top).toBeLessThan(1000);
    expect(top).toBeGreaterThan(200);
    // the label stays inside the chart (360 px and 1280 px alike)
    const chart = (await page.getByRole("img", { name: /dose/i }).first().boundingBox())!;
    const text = (await label.boundingBox())!;
    expect(text.x).toBeGreaterThanOrEqual(chart.x - 1);
    expect(text.x + text.width).toBeLessThanOrEqual(chart.x + chart.width + 1);
    const { scrollWidth, innerWidth } = await horizontalOverflow(page);
    expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
    expect(watch.consoleErrors).toEqual([]);
  });

  test("AC-5 'From zero' shows the whole spike and drops the label", async ({ page }) => {
    await mockApi(page, radSpiked);
    await page.goto(RANGE);
    await expect(page.getByText(/off scale/)).toBeVisible();
    await page.getByLabel(/vertical scale/i).selectOption({ label: "From zero" });
    await expect
      .poll(async () => Math.max(...(await yAxis(page))))
      .toBeGreaterThanOrEqual(SPIKE_DOSE);
    await expect(page.getByText(/off scale/)).toHaveCount(0);
  });

  test("AC-6 a range without the spike has no label", async ({ page }) => {
    await mockApi(page, radSpiked);
    await page.goto("/data/radiation?from=1000&to=1300");
    await expect(page.getByRole("table")).toBeVisible();
    await expect(page.getByText(/off scale/)).toHaveCount(0);
  });

  test("AC-4 the spike sol is still in the table with its true value", async ({ page }) => {
    await mockApi(page, radSpiked);
    await page.goto(RANGE);
    const row = page
      .getByRole("row")
      .filter({ hasText: String(SPIKE_SOL) })
      .first();
    await page.getByLabel("Jump to sol").fill(String(SPIKE_SOL));
    await page.getByLabel("Jump to sol").press("Enter");
    await expect(row).toContainText(/2342(\.0)?/);
  });
});

test.describe("AC-8 the old inspector is gone", () => {
  for (const path of ["/data/weather", "/data/radiation"]) {
    test(`AC-8 ${path}: no 'Inspect a sol' slider and no readout; the range handles stay`, async ({
      page,
    }) => {
      await mockApi(page);
      const watch = watchPage(page);
      await page.goto(path);
      await expect(page.getByRole("table")).toBeVisible();
      await expect(
        page.getByRole("slider", { name: /inspect a sol|next loaded sol/i }),
      ).toHaveCount(0);
      await expect(page.getByText(/Inspect a sol/i)).toHaveCount(0);
      await expect(page.getByRole("button", { name: /anomaly/i })).toHaveCount(0);
      await expect(page.getByRole("slider")).toHaveCount(2);
      await expect(page.getByRole("slider", { name: /range start/i })).toBeVisible();
      expect(watch.consoleErrors).toEqual([]);
    });

    test(`AC-8 ${path}: a click on the chart finds the sol in the table (the row has the focus)`, async ({
      page,
    }) => {
      await mockApi(page);
      await page.goto(path);
      await expect(page.getByRole("table")).toBeVisible();
      await page
        .getByRole("group", { name: /range presets/i })
        .getByRole("button", { name: /Last 120 sols/ })
        .click();
      const chart = page.getByRole("img", { name: /daily/i }).first();
      await chart.scrollIntoViewIfNeeded();
      const box = (await chart.boundingBox())!;
      await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.5);
      const sol = await solOfFocusedRow(page);
      const hi = path.includes("weather") ? PER_SOURCE_MAX : 4843;
      expect(sol).toBeGreaterThanOrEqual(hi - 119);
      expect(sol).toBeLessThanOrEqual(hi);
    });
  }

  test("AC-8 /data/weather: the flag pill of a flagged sol is in its table row", async ({
    page,
  }) => {
    await mockApi(page);
    await page.goto("/data/weather");
    const row = page
      .getByRole("row")
      .filter({ hasText: String(FLAGGED) })
      .first();
    await expect(row).toContainText(/humidity above 100/i);
  });

  test("AC-8 /data/weather: a row opens the sol report", async ({ page }) => {
    await mockApi(page);
    await page.goto("/data/weather");
    const row = page
      .getByRole("row")
      .filter({ hasText: String(FLAGGED) })
      .first();
    await row.getByRole("link", { name: /sol report/i }).click();
    await expect(page).toHaveURL(new RegExp(`/rovers/perseverance/sols/${FLAGGED}/conditions$`));
  });

  test("AC-8 the flag pill is still on the sol report of a flagged sol", async ({ page }) => {
    await mockApi(page);
    await page.route("**/api/v2/rovers/perseverance/weather/100", (r) =>
      r.fulfill(
        json(
          reportDetail("perseverance", 100, {
            flags: [{ code: "humidity_over_100", parameter: "humidity_pct", count: 12 }],
          }),
        ),
      ),
    );
    await page.goto("/rovers/perseverance/sols/100/conditions");
    await expect(
      page.getByRole("heading", { level: 1, name: /Perseverance.*sol 100(?!\d)/i }),
    ).toBeVisible();
    await expect(page.getByRole("main")).toContainText(/humidity above 100/i);
  });
});

for (const scheme of ["light", "dark"] as const) {
  test.describe(`AC-9 axe on the data pages, ${scheme} theme`, () => {
    test.use({ colorScheme: scheme });

    for (const [name, path] of [
      ["weather", "/data/weather"],
      ["radiation with a spike", "/data/radiation?from=4000&to=4300"],
    ] as const) {
      test(`AC-9 ${name} (${scheme}): no serious or critical axe violation`, async ({ page }) => {
        await mockApi(page, radSpiked);
        await page.goto(path);
        await expect(page.getByRole("table")).toBeVisible();
        await settle(page);
        const result = await new AxeBuilder({ page }).analyze();
        const bad = result.violations.filter(
          (v) => v.impact === "serious" || v.impact === "critical",
        );
        expect(
          bad.map(
            (v) =>
              `${v.impact} ${v.id}: ${v.help} -> ${v.nodes.map((n) => n.html.slice(0, 160)).join(" | ")}`,
          ),
        ).toEqual([]);
      });
    }
  });
}

test.describe("AC-4 at 360 px: the notes and the pager fit the screen", () => {
  test.use({ viewport: { width: 360, height: 800 } });

  for (const [name, items] of [
    ["one outlier", radSpiked],
    ["several outliers", radManySpikes],
  ] as const) {
    test(`AC-4 360 px, ${name}: the note is inside the viewport and the page does not scroll sideways`, async ({
      page,
    }) => {
      await mockApi(page, items);
      await page.goto("/data/radiation?from=4100&to=4400");
      await expect(page.getByRole("table")).toBeVisible();
      const note = page.getByTestId("off-scale-note").first();
      await expect(note).toBeVisible();
      await expect(note).toContainText(/off scale/);
      const box = (await note.boundingBox())!;
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(360);
      const { scrollWidth, innerWidth } = await horizontalOverflow(page);
      expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
    });
  }

  test("AC-4 360 px: the table pager wraps inside the viewport", async ({ page }) => {
    await mockApi(page, radSpiked);
    await page.goto("/data/radiation?from=4000&to=4300");
    const pager = page.getByRole("navigation", { name: /pagination|table pages/i });
    await expect(pager).toBeVisible();
    await expect(pager.getByRole("button", { name: /next page/i })).toBeVisible();
    for (const b of await pager.getByRole("button").all()) {
      const box = (await b.boundingBox())!;
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(360);
    }
    const { scrollWidth, innerWidth } = await horizontalOverflow(page);
    expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
  });
});
