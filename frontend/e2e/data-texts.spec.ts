import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { horizontalOverflow, settle } from "./helpers";
import { CUR_URL, json, mockReportApi } from "./sol-report-mock";
import { RAD_CREDIT } from "../tests/fixtures/radiation";
import { CUR_CREDIT, PER_CREDIT } from "../tests/fixtures/weather";

// docs/proposals/frontend-data-texts.md (revision 1, 2026-11-26) in a real browser: AC-1, AC-2/AC-3 (on 375 px),
// AC-4 (the number), AC-5, AC-7, AC-8, AC-9, AC-10. The whole API is intercepted (the sol report mock plus the routes
// below), so nothing depends on the server's data. Component-level checks of the same criteria are in tests/data-texts*.

const MOBILE = { width: 375, height: 812 };
const DESKTOP = { width: 1280, height: 800 };

const rover = (id: string, name: string, max: number) => ({
  id,
  name,
  mission: "x",
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
const ROVERS = [
  rover("perseverance", "Perseverance", 2031),
  rover("curiosity", "Curiosity", 4591),
  rover("ingenuity", "Ingenuity", 1003),
];

const apiPath = (p: Page, path: string, fn: (url: URL) => unknown, status = 200) =>
  p.route(
    (u) => u.pathname === path,
    (r) => r.fulfill(json(fn(new URL(r.request().url())), status)),
  );

const error422 = (field: string, message: string) => ({
  error: {
    code: "validation_error",
    message: "Invalid query parameters",
    details: [{ field: `query.${field}`, message }],
  },
});

async function mockExtra(page: Page): Promise<void> {
  await mockReportApi(page);
  await apiPath(page, "/api/v2/rovers", () => ({ items: ROVERS }));
  for (const r of ROVERS) await apiPath(page, `/api/v2/rovers/${r.id}`, () => r);
  await page.route("**/api/v2/rovers/*/cameras**", (route) => {
    const id = route.request().url().includes("/curiosity/") ? "curiosity" : "perseverance";
    const code = id === "curiosity" ? "CHEMCAM_RMI" : "NAVCAM_LEFT";
    return route.fulfill(json({ items: [{ code, name: code, total_photos: 1170 }] }));
  });
  await apiPath(page, "/api/v2/rovers/ingenuity/flights", () => ({
    items: Array.from({ length: 12 }, (_, i) => ({
      flight: i + 1,
      sol: 58 + i * 20,
      date: "2021-04-19",
      distance_m: 100 + i,
      max_altitude_m: 10,
      max_groundspeed_mps: 5.5,
      duration_s: 120.5,
      from: "Wright Brothers Field",
      to: "Airfield B",
      total_photos: 139 + i,
    })),
  }));
}

const group = (r: string, camera: string, total: number) => ({
  rover: r,
  camera,
  period: null,
  total_photos: total,
  sols_with_photos: 10,
  first_sol: 300,
  last_sol: 400,
  first_date: "2021-01-01",
  last_date: "2021-12-31",
});

async function mockStats(page: Page): Promise<void> {
  await apiPath(page, "/api/v2/photos/stats", (u) => {
    const by = u.searchParams.get("group_by") ?? "rover";
    const groups = [
      group("curiosity", "CHEMCAM_RMI", 1170),
      group("perseverance", "NAVCAM_LEFT", 60),
    ].map((g) =>
      by === "camera" ? g : { ...g, camera: null, period: by === "rover" ? null : "2021" },
    );
    return {
      group_by: by,
      total_photos: 1230,
      sols_with_photos: 20,
      first_date: "2012-08-06",
      last_date: "2026-11-06",
      groups,
    };
  });
}

// ---------------------------------------------------------------------------------------------------------------

const UNIT = /µ|W\/m²|\bPa\b|°C|m\/s/;

/** Visible elements whose own text has a unit but whose text-transform is uppercase (the element, ::before, ::after). */
async function shoutedUnits(page: Page): Promise<string[]> {
  return page.evaluate((src) => {
    const unit = new RegExp(src);
    const bad: string[] = [];
    const visible = (el: Element) => {
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && cs.display !== "none";
    };
    for (const el of document.body.querySelectorAll("*")) {
      if (!visible(el)) continue;
      const own = [...el.childNodes]
        .filter((n) => n.nodeType === Node.TEXT_NODE)
        .map((n) => n.textContent ?? "")
        .join("");
      const label = el.getAttribute("data-label") ?? "";
      if (unit.test(own) && getComputedStyle(el).textTransform === "uppercase") {
        bad.push(`${el.tagName.toLowerCase()}: ${own.trim().slice(0, 60)}`);
      }
      if (unit.test(label)) {
        for (const pseudo of ["::before", "::after"]) {
          const cs = getComputedStyle(el, pseudo);
          if (cs.content !== "none" && cs.textTransform === "uppercase") {
            bad.push(`${el.tagName.toLowerCase()}${pseudo}: ${label}`);
          }
        }
        if (getComputedStyle(el).textTransform === "uppercase") bad.push(`[data-label] ${label}`);
      }
    }
    return bad;
  }, UNIT.source);
}

test.describe("data-texts AC-1 units are not shouted", () => {
  const pages: [string, string][] = [
    ["radiation", "/data/radiation"],
    ["weather Perseverance", "/data/weather"],
    ["sol report", CUR_URL],
  ];
  for (const [name, url] of pages) {
    for (const [size, vp] of [
      ["375", MOBILE],
      ["1280", DESKTOP],
    ] as const) {
      test(`data-texts AC-1 ${name} at ${size} px has no uppercase unit`, async ({ page }) => {
        await page.setViewportSize(vp);
        await mockExtra(page);
        await page.goto(url);
        await expect(page.getByRole("main")).toBeVisible();
        await settle(page);
        expect(await shoutedUnits(page)).toEqual([]);
      });
    }
  }

  for (const [size, vp] of [
    ["375", MOBILE],
    ["1280", DESKTOP],
  ] as const) {
    test(`data-texts AC-1 weather Curiosity at ${size} px has no uppercase unit`, async ({
      page,
    }) => {
      await page.setViewportSize(vp);
      await mockExtra(page);
      await page.goto("/data/weather");
      await page.locator("label", { hasText: /^Curiosity / }).click();
      await settle(page);
      expect(await shoutedUnits(page)).toEqual([]);
    });
  }

  test("data-texts AC-1 the radiation card on 375 px shows the label 'E, µGy/day' as written", async ({
    page,
  }) => {
    await page.setViewportSize(MOBILE);
    await mockExtra(page);
    await page.goto("/data/radiation");
    await settle(page);
    const cell = page.locator('td[data-label="E, µGy/day"]').first();
    await expect(cell).toBeVisible();
    const t = await cell.evaluate((el) => ({
      own: getComputedStyle(el).textTransform,
      before: getComputedStyle(el, "::before").textTransform,
      content: getComputedStyle(el, "::before").content,
    }));
    expect(t.own).not.toBe("uppercase");
    expect(t.before).not.toBe("uppercase");
    expect(t.content).toContain("E, µGy/day");
  });
});

// ---------------------------------------------------------------------------------------------------------------

test.describe("data-texts AC-2/AC-3 the credit is near the data on 375 px", () => {
  const cases: [string, string, string, string?][] = [
    ["AC-2 radiation", "/data/radiation", RAD_CREDIT],
    ["AC-3 weather Perseverance", "/data/weather", PER_CREDIT],
    ["AC-3 weather Curiosity", "/data/weather", CUR_CREDIT, "Curiosity"],
  ];
  for (const [name, url, credit, pick] of cases) {
    test(`data-texts ${name}: the line under the charts is within one screen of the first chart`, async ({
      page,
    }) => {
      await page.setViewportSize(MOBILE);
      await mockExtra(page);
      await page.goto(url);
      if (pick) await page.locator("label", { hasText: new RegExp(`^${pick} `) }).click();
      const line = page.getByTestId("data-credit-chart");
      await expect(line).toContainText(`Credit: ${credit}`);
      await expect(page.getByTestId("data-credit-table")).toContainText(credit);
      const dy = await page.evaluate(() => {
        const svg = [...document.querySelectorAll("main svg[role='img']")][0]!;
        const el = document.querySelector('[data-testid="data-credit-chart"]')!;
        return el.getBoundingClientRect().top - svg.getBoundingClientRect().top;
      });
      expect(dy).toBeGreaterThan(0);
      expect(dy).toBeLessThanOrEqual(812);
    });
  }
});

// ---------------------------------------------------------------------------------------------------------------

test.describe("data-texts AC-4 the Search link of a camera row finds the number of the row", () => {
  const variants: [string, string, Record<string, string>][] = [
    ["sol period", "sol_min=300&sol_max=400", { sol_min: "300", sol_max: "400" }],
    [
      "date period",
      "date_min=2021-03-01&date_max=2021-06-30",
      { date_min: "2021-03-01", date_max: "2021-06-30" },
    ],
  ];
  for (const [name, query, expected] of variants) {
    test(`data-texts AC-4 ${name}: the count on /search equals the count in the Stats row`, async ({
      page,
    }) => {
      await mockExtra(page);
      await mockStats(page);
      // the archive answers with the row's number only for the filters of the page; any other filter gives a bigger one
      await apiPath(page, "/api/v2/photos", (u) => {
        const q = u.searchParams;
        const same =
          q.get("camera") === "CHEMCAM_RMI" &&
          q.get("sample_type") === "full" &&
          Object.entries(expected).every(([k, v]) => q.get(k) === v);
        return { items: [], page: 1, per_page: 24, total: same ? 1170 : 60235, nearest_sols: null };
      });
      await page.goto(`/stats?rovers=curiosity,perseverance&${query}&sample_type=full`);
      await page.getByTestId("stats-tab-camera").click();
      const row = page
        .getByTestId("stats-table")
        .getByRole("row")
        .filter({ hasText: "CHEMCAM_RMI" });
      await expect(row).toContainText("1,170");
      await row.getByRole("link", { name: /Search/ }).click();
      await expect(page).toHaveURL(/\/search\?/);
      await expect(page.getByTestId("search-results")).toContainText("1,170 photos");
    });
  }
});

// ---------------------------------------------------------------------------------------------------------------

test.describe("data-texts AC-5 the sol arrows of the photo page keep the filters", () => {
  test("data-texts AC-5 'Next sol' then 'Previous sol' keep camera, sample type and page size; the focus is on the title", async ({
    page,
  }) => {
    await mockExtra(page);
    await apiPath(page, "/api/v2/rovers/curiosity/photos", () => ({
      items: [],
      page: 1,
      per_page: 48,
      total: 0,
      nearest_sols: null,
    }));
    await page.goto("/rovers/curiosity?sol=344&camera=NAV_LEFT_B&sample_type=full&per_page=48");
    await page.getByRole("link", { name: "Next sol" }).click();
    await expect(page).toHaveURL(
      (u) =>
        u.pathname === "/rovers/curiosity" &&
        u.search === "?sol=345&camera=NAV_LEFT_B&sample_type=full&per_page=48",
    );
    await expect(page.locator("#result-title")).toBeFocused();
    await page.getByRole("link", { name: "Previous sol" }).click();
    await expect(page).toHaveURL(
      (u) => u.search === "?sol=344&camera=NAV_LEFT_B&sample_type=full&per_page=48",
    );
    await expect(page.locator("#result-title")).toBeFocused();
  });
});

// ---------------------------------------------------------------------------------------------------------------

test.describe("data-texts AC-7 the order of the bounds is told in words", () => {
  const TEXT = "“From sol” must not be greater than “To sol”";
  for (const [name, url, endpoint] of [
    ["Stats", "/stats?rovers=curiosity&sol_min=500&sol_max=100", "/api/v2/photos/stats"],
    ["Search", "/search?rovers=curiosity&sol_min=500&sol_max=100", "/api/v2/photos"],
  ] as const) {
    test(`data-texts AC-7 ${name}: the text and both fields marked invalid`, async ({ page }) => {
      await mockExtra(page);
      await page.route(
        (u) => u.pathname === endpoint,
        (r) =>
          r.fulfill(
            json(
              {
                error: {
                  code: "validation_error",
                  message: "Invalid query parameters",
                  details: [
                    {
                      field: "query.sol_min",
                      message: "sol_min must be less than or equal to sol_max",
                    },
                  ],
                },
              },
              422,
            ),
          ),
      );
      await page.goto(url);
      await expect(page.getByRole("alert")).toContainText(TEXT);
      // the backend sends one issue (for the low field); the text appears once, not as "From sol: “From sol” ..."
      const alertText = (await page.getByRole("alert").textContent()) ?? "";
      expect(alertText.split("must not be greater than")).toHaveLength(2);
      expect(alertText).not.toMatch(/From sol\W*:\s*“From sol”/);
      await expect(page.getByLabel("From sol")).toHaveAttribute("aria-invalid", "true");
      await expect(page.getByLabel("To sol")).toHaveAttribute("aria-invalid", "true");
    });
  }

  test("data-texts AC-7 the pydantic bound error keeps the 'whole number' text", async ({
    page,
  }) => {
    await mockExtra(page);
    await apiPath(
      page,
      "/api/v2/photos/stats",
      () => error422("sol_max", "Input should be less than or equal to 2147483647"),
      422,
    );
    await page.goto("/stats?rovers=curiosity&sol_max=99999999999");
    await expect(page.getByRole("alert")).toContainText(
      "must be a whole number from 0 to 2,147,483,647",
    );
  });
});

// ---------------------------------------------------------------------------------------------------------------

test.describe("data-texts AC-8 Stats by camera is not wider than the screen", () => {
  for (const [size, vp] of [
    ["375", MOBILE],
    ["1280", DESKTOP],
  ] as const) {
    test(`data-texts AC-8 ${size} px: no sideways scroll, the link column keeps the name 'Link'`, async ({
      page,
    }) => {
      await page.setViewportSize(vp);
      await mockExtra(page);
      await mockStats(page);
      await page.goto("/stats?group_by=camera&rovers=curiosity,perseverance");
      await page.getByTestId("stats-tab-camera").click();
      await expect(page.getByTestId("stats-table")).toContainText("CHEMCAM_RMI");
      await settle(page);
      const o = await horizontalOverflow(page);
      expect(o.scrollWidth).toBe(o.innerWidth);
      await expect(
        page.getByTestId("stats-table").getByRole("columnheader", { name: "Link" }),
      ).toHaveCount(1);
    });
  }
});

// ---------------------------------------------------------------------------------------------------------------

test.describe("data-texts AC-9 Flights is not wider than the screen", () => {
  for (const width of [800, 940]) {
    test(`data-texts AC-9 ${width} px: no sideways scroll and the Photos column is reachable`, async ({
      page,
    }) => {
      await page.setViewportSize({ width, height: 900 });
      await mockExtra(page);
      await page.goto("/rovers/ingenuity/flights");
      const region = page.getByRole("region", { name: "Ingenuity flights" });
      await expect(region).toBeVisible();
      await settle(page);
      const o = await horizontalOverflow(page);
      expect(o.scrollWidth).toBe(o.innerWidth);
      await expect(region).toHaveAttribute("tabindex", "0");
      const table = region.getByRole("table");
      if (await table.isVisible()) {
        const ok = await region.evaluate((el) => {
          el.scrollLeft = el.scrollWidth;
          const box = el.getBoundingClientRect();
          const cells = [...el.querySelectorAll("tbody tr")].map((tr) => {
            const tds = tr.querySelectorAll("td, th");
            return tds[tds.length - 1]!.getBoundingClientRect();
          });
          return (
            cells.length > 0 &&
            cells.every((c) => c.left >= box.left - 1 && c.right <= box.right + 1)
          );
        });
        expect(ok).toBe(true);
      }
    });
  }
});

// ---------------------------------------------------------------------------------------------------------------

test.describe("data-texts AC-10 the pinned hour survives the mouse", () => {
  const out = (page: Page) => page.locator("main output").first();
  const chart = (page: Page) => page.getByRole("img", { name: /air temperature/i });

  test.beforeEach(async ({ page }) => {
    await page.setViewportSize(DESKTOP);
    await mockReportApi(page);
    await page.goto(CUR_URL);
    await expect(chart(page)).toBeVisible();
  });

  test("data-texts AC-10 (a) the slider hour is back after the mouse passes over a chart", async ({
    page,
  }) => {
    await page.getByRole("slider", { name: /inspect hour/i }).fill("4");
    await expect(out(page)).toContainText("04:00");
    await chart(page).scrollIntoViewIfNeeded();
    const box = (await chart(page).boundingBox())!;
    await page.mouse.move(box.x + box.width * 0.8, box.y + box.height / 2);
    await expect(out(page)).not.toContainText("04:00");
    await page.mouse.move(5, 5);
    await expect(out(page)).toContainText("04:00");
    await expect(page.getByRole("button", { name: "Clear hour" })).toBeVisible();
  });

  test("data-texts AC-10 (b) with no hour the mouse leaving gives 'No hour selected'", async ({
    page,
  }) => {
    await chart(page).scrollIntoViewIfNeeded();
    const box = (await chart(page).boundingBox())!;
    await page.mouse.move(box.x + box.width * 0.6, box.y + box.height / 2);
    await expect(out(page)).toContainText(/\d\d:00/);
    await page.mouse.move(5, 5);
    await expect(out(page)).toHaveText("No hour selected");
    await expect(page.getByRole("button", { name: "Clear hour" })).toHaveCount(0);
  });

  test("data-texts AC-10 (c)(d) a click pins the hour, leaving keeps it, 'Clear hour' releases it", async ({
    page,
  }) => {
    await chart(page).scrollIntoViewIfNeeded();
    const box = (await chart(page).boundingBox())!;
    await page.mouse.click(box.x + box.width * 0.65, box.y + box.height / 2);
    const pinned = ((await out(page).textContent()) ?? "").match(/\d\d:00/)![0];
    await page.mouse.move(5, 5);
    await expect(out(page)).toContainText(pinned);
    await expect(page.getByRole("button", { name: "Clear hour" })).toBeVisible();
    await page.getByRole("button", { name: "Clear hour" }).click();
    await expect(out(page)).toHaveText("No hour selected");
  });
});

test("data-texts axe: the data pages have no serious violations after the change", async ({
  page,
}) => {
  await page.setViewportSize(MOBILE);
  await mockExtra(page);
  for (const url of ["/data/radiation", "/data/weather", "/rovers/ingenuity/flights"]) {
    await page.goto(url);
    await expect(page.getByRole("main")).toBeVisible();
    await settle(page);
    const res = await new AxeBuilder({ page }).analyze();
    expect(res.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual(
      [],
    );
  }
});
