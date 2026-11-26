import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page, type Route } from "@playwright/test";
import { horizontalOverflow } from "./helpers";
import { mockReportApi } from "./sol-report-mock";
import { radiationResponse } from "../tests/fixtures/radiation";
import { radDetail } from "../tests/fixtures/radhours";
import { radLong } from "../tests/fixtures/range";

// Package 3 of the frontend review (docs/proposals/frontend-url-state.md): Back brings the page back as the
// visitor left it (scroll position, filters, sort, selected stop, table page), and what is chosen lives in the
// address. The whole API is intercepted (page.route); images are replaced by a tiny SVG. Viewports come from the
// projects of playwright.config.ts (375 and 1280 px). Tests are named `url-state AC-N`.

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const rover = (id: string, name: string, max: number, status = "active") => ({
  id,
  name,
  mission: id === "curiosity" ? "msl" : "mars2020",
  launch_date: "2020-07-30",
  landing_date: "2021-02-18",
  end_date: null,
  status,
  max_sol: max,
  max_date: "2026-11-06",
  total_photos: 1000,
  cameras_count: 2,
  last_synced_at: "2026-11-17T06:00:12Z",
  data_source: "sync",
});
const PER = rover("perseverance", "Perseverance", 2031);
const CUR = rover("curiosity", "Curiosity", 5066);
const ING = rover("ingenuity", "Ingenuity", 1069, "complete");

const SVG =
  "<svg xmlns='http://www.w3.org/2000/svg' width='4' height='3'><rect width='4' height='3' fill='#888'/></svg>";

function photo(id: number, roverId: string, sol: number) {
  return {
    id,
    rover: roverId,
    sol,
    earth_date: "2026-11-06",
    taken_at: "2026-11-06T12:16:25Z",
    camera: { code: "NAVCAM_LEFT", name: "Navigation Camera - Left" },
    sample_type: "full",
    title: `Photo ${id}`,
    img_src: `https://img.example/${id}.png`,
    images: {
      small: `https://img.example/${id}_320.jpg`,
      medium: `https://img.example/${id}_800.jpg`,
      large: `https://img.example/${id}_1200.jpg`,
      full: `https://img.example/${id}.png`,
    },
    credit: "NASA/JPL-Caltech",
    source: { id: `SRC_${id}`, link: `https://source.example/${id}` },
    width: 1024,
    height: 1024,
  };
}
const photoList = (roverId: string, sol: number) =>
  Array.from({ length: 60 }, (_, i) => photo(1000 + i, roverId, sol));
const listPage = (items: unknown[]) => ({
  items,
  page: 1,
  per_page: items.length || 24,
  total: items.length,
  nearest_sols: null,
});

// 80 samples: tubes 1-60 are in the depot, the rest on the rover; every seventh is not a rock core.
const SAMPLES = Array.from({ length: 80 }, (_, i) => {
  const n = i + 1;
  const type = n % 7 === 0 ? "regolith" : "rock_core";
  return {
    number: n,
    name: `Sample ${n}`,
    feature_name: null,
    sampling_location: "Jezero",
    status: n <= 60 ? "depot" : "on_rover",
    current_location: n <= 60 ? "Sample Depot" : "On the rover",
    sample_type: type,
    sample_type_label: type === "rock_core" ? "Rock core" : "Regolith",
    rock_type: null,
    sealed_on: "2021-08-06",
    sol: 100 + n,
    height_cm: null,
    height_note: null,
    dossier_url: null,
    total_photos: 5,
    location: {
      sol: 100 + n,
      site: 6,
      drive: n,
      lat: 18.4,
      lon: 77.4,
      elev_geoid: -2500,
      match: "by_sol",
    },
  };
});

// 60 flights; the distance differs from flight to flight.
const FLIGHTS = Array.from({ length: 60 }, (_, i) => ({
  flight: i + 1,
  sol: 58 + i * 5,
  date: "2021-04-19",
  distance_m: ((i + 1) * 37) % 503,
  max_altitude_m: 10,
  max_groundspeed_mps: 5,
  duration_s: 120,
  from: "Airfield A",
  to: "Airfield B",
  total_photos: 100 + i,
}));

// Six stops of Perseverance, one sol apart in tens.
const STOPS = [10, 20, 30, 40, 50, 60].map((sol, i) => ({
  sol,
  site: i + 1,
  drive: i * 4,
  lon: 77.0 + i * 0.002,
  lat: 18.0 + i * 0.001,
  elev_geoid: -2489.7,
  easting: null,
  northing: null,
  roll: null,
  pitch: null,
  yaw: null,
  tilt: null,
  dist_m: 112.4,
  dist_total_m: 1000,
}));

const MANIFEST_SOLS = [
  { sol: 2031, earth_date: "2026-11-06", total_photos: 52, cameras: ["NAVCAM_LEFT"] },
  { sol: 2000, earth_date: "2026-10-06", total_photos: 300, cameras: ["NAVCAM_LEFT"] },
  { sol: 5, earth_date: "2021-02-23", total_photos: 900, cameras: ["NAVCAM_LEFT"] },
];

async function mockApi(page: Page) {
  // Every host but our own is an image host: answer with a picture so that nothing leaves the machine.
  await page.route(
    (url) => url.hostname !== "127.0.0.1" && url.hostname !== "localhost",
    (r) => r.fulfill({ status: 200, contentType: "image/svg+xml", body: SVG }),
  );
  await page.route("**/api/v2/**", (route) => {
    const url = new URL(route.request().url());
    const p = url.pathname.replace("/api/v2", "");
    let m: RegExpMatchArray | null;
    if (p === "/rovers") return json(route, { items: [PER, CUR, ING] });
    if (p === "/photos") return json(route, listPage(photoList("curiosity", 344)));
    if ((m = p.match(/^\/photos\/(\d+)$/))) {
      const id = Number(m[1]);
      return json(route, {
        ...photo(id, "curiosity", 344),
        caption: "A caption.",
        received_at: "2026-11-07T03:41:09Z",
        site: 112,
        drive: 1874,
        extra: {},
        location: null,
        local_time: null,
        in_sol: null,
      });
    }
    if ((m = p.match(/^\/rovers\/(\w+)$/))) {
      return json(route, [PER, CUR, ING].find((r) => r.id === m![1]) ?? PER);
    }
    if ((m = p.match(/^\/rovers\/(\w+)\/photos$/))) {
      return json(route, listPage(m[1] === "perseverance" ? [] : photoList(m[1]!, 344)));
    }
    if (/\/cameras$/.test(p)) return json(route, { items: [] });
    if (p === "/rovers/perseverance/samples") {
      return json(route, {
        rover: "perseverance",
        source_url: "https://science.nasa.gov/mission/mars-2020-perseverance/mars-rock-samples/",
        source_updated_at: "2026-06-20T16:45:46Z",
        updated_at: "2026-11-20T03:00:12Z",
        items: SAMPLES,
      });
    }
    if (p === "/rovers/ingenuity/flights") return json(route, { items: FLIGHTS });
    if (p === "/rovers/perseverance/traverse") {
      return json(route, {
        type: "FeatureCollection",
        rover: "perseverance",
        available: true,
        updated_at: "2026-10-28T15:05:03Z",
        source_max_sol: 60,
        features: STOPS.slice(1).map((s, i) => ({
          type: "Feature",
          geometry: {
            type: "LineString",
            coordinates: [
              [STOPS[i]!.lon, STOPS[i]!.lat],
              [s.lon, s.lat],
            ],
          },
          properties: { sol: s.sol, length_m: 20 },
        })),
      });
    }
    if (p === "/rovers/perseverance/location") {
      return json(route, {
        rover: "perseverance",
        available: true,
        updated_at: "2026-10-28T15:05:06Z",
        source_max_sol: 60,
        items: url.searchParams.get("latest") === "true" ? [] : STOPS,
      });
    }
    if (p === "/rovers/perseverance/manifest") {
      return json(route, { rover: PER, sols: MANIFEST_SOLS });
    }
    return json(
      route,
      { error: { code: "not_found", message: `no mock for ${p}`, details: null } },
      404,
    );
  });
}

const json2 = (body: unknown) => ({
  status: 200,
  contentType: "application/json",
  body: JSON.stringify(body),
});

const scrollY = (page: Page) => page.evaluate(() => window.scrollY);
const historyLength = (page: Page) => page.evaluate(() => window.history.length);

/** Scrolls `target` into the middle of the screen and returns the scroll position reached. */
async function scrollTo(page: Page, target: Locator): Promise<number> {
  await target.scrollIntoViewIfNeeded();
  await target.evaluate((el) => el.scrollIntoView({ block: "center" }));
  const y = await scrollY(page);
  expect(y, "the list is longer than one screen").toBeGreaterThan(300);
  return y;
}

/**
 * Clicks a link and returns the scroll position at the moment of the click (the click itself may scroll a
 * little, and late layout shifts may move the page), which is the position Back has to bring back.
 */
async function clickAndNote(page: Page, link: Locator): Promise<number> {
  await page.evaluate(() => {
    document.addEventListener(
      "click",
      () => ((window as unknown as { __clickY: number }).__clickY = window.scrollY),
      { capture: true, once: true },
    );
  });
  await link.click();
  return page.evaluate(() => (window as unknown as { __clickY: number }).__clickY);
}

/** A page opened by a link starts at the top (the check waits for the layout to move the page there). */
async function expectAtTop(page: Page) {
  await expect.poll(() => scrollY(page), { timeout: 5000 }).toBeLessThanOrEqual(1);
}

/** Back brings the scroll position back, within 50 px, once the page has its data. */
async function expectScrollNear(page: Page, y: number) {
  await expect
    .poll(async () => Math.abs((await scrollY(page)) - y), { timeout: 5000 })
    .toBeLessThanOrEqual(50);
}

const tiles = (page: Page) => page.locator("ul.photo-grid a[href^='/photos/']");

test.describe("url-state AC-1 Back restores the scroll position", () => {
  test.beforeEach(async ({ page }) => mockApi(page));

  for (const [name, url] of [
    ["Search", "/search?rover=curiosity&sol_min=300&sol_max=400"],
    ["Photos", "/rovers/curiosity?sol=344"],
  ] as const) {
    test(`url-state AC-1 ${name}: Back returns to the tile the visitor opened; a link still starts at the top`, async ({
      page,
    }) => {
      await page.goto(url);
      const tile = tiles(page).nth(40);
      await expect(tile).toBeAttached();
      await scrollTo(page, tile);
      const y = await clickAndNote(page, tile);
      await expect(page).toHaveURL(/\/photos\/\d+/);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expectAtTop(page);

      await page.goBack();
      await expect(tiles(page).first()).toBeAttached();
      await expectScrollNear(page, y);
      await expect(page).toHaveURL(
        (u) => u.pathname + u.search === new URL(url, u).pathname + new URL(url, u).search,
      );
    });
  }

  test("url-state AC-1 Samples: Back returns to the row the visitor left from", async ({
    page,
  }) => {
    await page.goto("/rovers/perseverance/samples");
    const row = page.locator("#sample-40");
    await expect(row).toBeAttached();
    await scrollTo(page, row);
    const y = await clickAndNote(page, row.getByRole("link", { name: "Show on route map" }));
    await expect(page).toHaveURL(/\/rovers\/perseverance\/map/);
    await expectAtTop(page);
    await page.goBack();
    await expect(page.locator("#sample-40")).toBeAttached();
    await expectScrollNear(page, y);
  });

  test("url-state AC-1 Flights: Back returns to the row the visitor left from", async ({
    page,
  }) => {
    await page.goto("/rovers/ingenuity/flights");
    const row = page.locator("#flight-45");
    await expect(row).toBeAttached();
    await scrollTo(page, row);
    const y = await clickAndNote(page, row.getByRole("link").first());
    await expect(page).toHaveURL(/\/rovers\/ingenuity\?sol=/);
    await expectAtTop(page);
    await page.goBack();
    await expect(page.locator("#flight-45")).toBeAttached();
    await expectScrollNear(page, y);
  });
});

test.describe("url-state AC-1 regressions around Back and Forward", () => {
  test.beforeEach(async ({ page }) => mockApi(page));

  test("url-state AC-1 Photos scrolled down, photo, Back, Forward: the photo opens at the top", async ({
    page,
  }) => {
    await page.goto("/rovers/curiosity?sol=344");
    const tile = tiles(page).nth(40);
    await expect(tile).toBeAttached();
    await scrollTo(page, tile);
    await tile.click();
    await expect(page).toHaveURL(/\/photos\/\d+/);
    await page.goBack();
    await expect(tiles(page).first()).toBeAttached();
    await page.goForward();
    await expect(page).toHaveURL(/\/photos\/\d+/);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect.poll(() => scrollY(page), { timeout: 5000 }).toBeLessThanOrEqual(50);
  });

  test("url-state AC-1 Search opened at the top: a photo scrolled down does not move the results on Back", async ({
    page,
  }) => {
    await page.goto("/search?rover=curiosity&sol_min=300&sol_max=400");
    await expect(tiles(page).first()).toBeAttached();
    expect(await scrollY(page)).toBeLessThanOrEqual(1);
    // a DOM click: Playwright's own click would scroll the page to the tile first
    await tiles(page)
      .first()
      .evaluate((el) => (el as HTMLElement).click());
    await expect(page).toHaveURL(/\/photos\/\d+/);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    // the photo card may be short: make it tall enough to scroll
    await page.evaluate(() => {
      const d = document.createElement("div");
      d.style.height = "4000px";
      d.setAttribute("data-spacer", "");
      document.body.appendChild(d);
      window.scrollTo(0, 1500);
    });
    expect(await scrollY(page)).toBeGreaterThan(500);
    await page.goBack();
    await expect(page).toHaveURL(/\/search\?/);
    await expect(tiles(page).first()).toBeAttached();
    await expect.poll(() => scrollY(page), { timeout: 5000 }).toBeLessThanOrEqual(1);
  });

  test("url-state AC-1 a page without a list: Back brings its position back", async ({ page }) => {
    await page.goto("/rovers/perseverance/timeline");
    await expect(page.getByLabel("From sol")).toBeVisible();
    // the Timeline is short: make it tall enough to scroll
    const y = await page.evaluate(() => {
      const d = document.createElement("div");
      d.style.height = "4000px";
      document.body.appendChild(d);
      window.scrollTo(0, 1500);
      return window.scrollY;
    });
    expect(y).toBeGreaterThan(500);
    expect(Math.abs((await scrollY(page)) - y)).toBeLessThanOrEqual(1);
    // a DOM click: Playwright's own click would scroll the page to the link first
    await page
      .getByRole("link", { name: "Route map" })
      .first()
      .evaluate((el) => (el as HTMLElement).click());
    await expect(page).toHaveURL(/\/rovers\/perseverance\/map/);
    await expect(page.getByRole("table")).toBeVisible();
    // on the other page, a different position
    await page.evaluate(() => window.scrollTo(0, 700));
    await page.goBack();
    await expect(page).toHaveURL(/\/timeline/);
    await expect(page.getByLabel("From sol")).toBeVisible();
    await expectScrollNear(page, y);
  });
});

test.describe("url-state AC-2 Search to a photo and back", () => {
  test.beforeEach(async ({ page }) => mockApi(page));
  const SEARCH = "/search?rover=curiosity&sol_min=300&sol_max=400";

  test("url-state AC-2 a photo opened from the search results links back to exactly that search", async ({
    page,
  }) => {
    await page.goto(SEARCH);
    await tiles(page).first().click();
    await expect(page).toHaveURL(/\/photos\/\d+\?from=/);
    const back = page.getByRole("link", { name: "← Back to search results" });
    await expect(back).toBeVisible();
    const href = await back.getAttribute("href");
    expect(new URL(href!, "http://local.invalid").pathname).toBe("/search");
    expect(Object.fromEntries(new URL(href!, "http://local.invalid").searchParams)).toMatchObject({
      rover: "curiosity",
      sol_min: "300",
      sol_max: "400",
    });
    await back.click();
    await expect(page).toHaveURL(
      (u) => u.pathname === "/search" && u.searchParams.get("sol_min") === "300",
    );
    await expect(page.getByRole("heading", { level: 1, name: "Search photos" })).toBeVisible();
  });

  for (const from of [
    "//evil.example/search",
    "https://evil.example/search",
    "javascript:alert(1)",
    "/stats",
  ]) {
    test(`url-state AC-2 no Back link for from=${from}`, async ({ page }) => {
      await page.goto(`/photos/1000?from=${encodeURIComponent(from)}`);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expect(page.getByTestId("back-to-search")).toHaveCount(0);
    });
  }

  test("url-state AC-2 the photo card with the Back link has no serious axe violations or sideways scroll", async ({
    page,
  }) => {
    await page.goto(`/photos/1000?from=${encodeURIComponent(SEARCH)}`);
    await expect(page.getByTestId("back-to-search")).toBeVisible();
    const bad = (await new AxeBuilder({ page }).analyze()).violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical",
    );
    expect(bad.map((v) => `${v.impact} ${v.id}: ${v.help}`)).toEqual([]);
    const o = await horizontalOverflow(page);
    expect(o.scrollWidth).toBeLessThanOrEqual(o.innerWidth);
  });
});

test.describe("url-state AC-4 Samples: filters survive a round trip", () => {
  test.beforeEach(async ({ page }) => mockApi(page));

  test("url-state AC-4 Depot + Rock core are in the address; 'Show on route map' then Back keeps filters and the row", async ({
    page,
  }) => {
    await page.goto("/rovers/perseverance/samples");
    const status = page.getByRole("group", { name: "Status" });
    const type = page.getByRole("group", { name: "Type" });
    await expect(status).toBeVisible();
    const before = await historyLength(page);
    await status.getByRole("button", { name: /^In depot/ }).click();
    await type.getByRole("button", { name: /^Rock core/ }).click();
    await expect(page).toHaveURL(
      (u) =>
        u.pathname === "/rovers/perseverance/samples" &&
        u.searchParams.get("status") === "depot" &&
        u.searchParams.get("type") === "rock_core",
    );
    expect(await historyLength(page), "filters replace the entry").toBe(before);

    const row = page.locator("#sample-25");
    await scrollTo(page, row);
    const y = await clickAndNote(page, row.getByRole("link", { name: "Show on route map" }));
    await expect(page).toHaveURL(/\/rovers\/perseverance\/map/);

    await page.goBack();
    await expect(page).toHaveURL(/status=depot/);
    await expect(page).toHaveURL(/type=rock_core/);
    await expect(status.getByRole("button", { name: /^In depot/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect(type.getByRole("button", { name: /^Rock core/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect(page.locator("#sample-25")).toBeInViewport();
    await expectScrollNear(page, y);
  });

  test("url-state AC-4 a shared link shows the same filters", async ({ page }) => {
    await page.goto("/rovers/perseverance/samples?status=depot&type=rock_core");
    await expect(page.getByRole("button", { name: /^In depot/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect(page.getByRole("button", { name: /^Rock core/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });
});

test.describe("url-state AC-5 Flights: the sort survives a round trip", () => {
  test.beforeEach(async ({ page }) => mockApi(page));

  test("url-state AC-5 sort by Distance is in the address; a row's photos then Back keeps sort and row", async ({
    page,
  }) => {
    await page.goto("/rovers/ingenuity/flights");
    await expect(page.getByRole("table")).toBeVisible();
    const before = await historyLength(page);
    await page.getByLabel("Sort by").selectOption("distance:desc");
    await expect(page).toHaveURL((u) => u.searchParams.get("sort") === "distance:desc");
    expect(await historyLength(page), "sorting replaces the entry").toBe(before);
    await expect(page.getByRole("columnheader", { name: /Distance/ })).toHaveAttribute(
      "aria-sort",
      "descending",
    );

    const row = page.getByRole("row").nth(30);
    const id = await row.getAttribute("id");
    expect(id).toMatch(/^flight-\d+$/);
    await scrollTo(page, row);
    const y = await clickAndNote(page, row.getByRole("link").first());
    await expect(page).toHaveURL(/\/rovers\/ingenuity\?sol=/);

    await page.goBack();
    await expect(page).toHaveURL((u) => u.searchParams.get("sort") === "distance:desc");
    await expect(page.getByRole("columnheader", { name: /Distance/ })).toHaveAttribute(
      "aria-sort",
      "descending",
    );
    await expect(page.locator(`#${id}`)).toBeInViewport();
    await expectScrollNear(page, y);
  });
});

test.describe("url-state AC-6 / AC-7 Route map", () => {
  test.beforeEach(async ({ page }) => mockApi(page));
  const select = (page: Page, sol: number, site: number, drive: number) =>
    page.getByRole("button", { name: `Select stop: sol ${sol}, site ${site}, drive ${drive}` });

  test("url-state AC-6 a selected stop is in the address without new entries; 'Photos at this stop' then Back keeps it", async ({
    page,
  }) => {
    await page.goto("/rovers/perseverance/map");
    await expect(page.getByRole("table")).toBeVisible();
    const before = await historyLength(page);
    await select(page, 30, 3, 8).click();
    await expect(page).toHaveURL(
      (u) =>
        u.searchParams.get("sol") === "30" &&
        u.searchParams.get("site") === "3" &&
        u.searchParams.get("drive") === "8",
    );
    await select(page, 40, 4, 12).click();
    await expect(page).toHaveURL(
      (u) =>
        u.searchParams.get("sol") === "40" &&
        u.searchParams.get("site") === "4" &&
        u.searchParams.get("drive") === "12",
    );
    expect(await historyLength(page), "choosing a stop replaces the entry").toBe(before);

    await page.getByRole("link", { name: "Photos at this stop" }).click();
    await expect(page).toHaveURL(/\/rovers\/perseverance\?/);
    await page.goBack();
    await expect(page).toHaveURL(
      (u) => u.pathname === "/rovers/perseverance/map" && u.searchParams.get("sol") === "40",
    );
    await expect(select(page, 40, 4, 12)).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByRole("link", { name: "Photos at this stop" })).toBeVisible();
    // the stop of the address is listed on the page the list shows
    await expect(select(page, 40, 4, 12)).toBeVisible();
  });

  test("url-state AC-7 a sol without a stop selects the last stop before it and says so (axe clean)", async ({
    page,
  }) => {
    await page.goto("/rovers/perseverance/map?sol=45");
    await expect(page.getByTestId("stop-fallback")).toHaveText(
      "No stop on sol 45 — showing the last stop before it, sol 40.",
    );
    await expect(select(page, 40, 4, 12)).toHaveAttribute("aria-pressed", "true");
    const bad = (await new AxeBuilder({ page }).analyze()).violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical",
    );
    expect(bad.map((v) => `${v.impact} ${v.id}: ${v.help}`)).toEqual([]);
    const o = await horizontalOverflow(page);
    expect(o.scrollWidth).toBeLessThanOrEqual(o.innerWidth);
  });

  test("url-state AC-7 a sol before the first stop selects nothing", async ({ page }) => {
    await page.goto("/rovers/perseverance/map?sol=5");
    await expect(page.getByTestId("stop-fallback")).toHaveText("No stops on or before sol 5.");
    await expect(page.getByRole("link", { name: /Photos of sol/ })).toHaveCount(0);
  });
});

test.describe("url-state AC-9 the sol table remembers page, sort and row", () => {
  test.beforeEach(async ({ page }) => {
    await mockReportApi(page);
    // A long radiation series (the report mock only has the sols around 344), so that the table has pages.
    await page.route("**/api/v2/rovers/curiosity/radiation**", (r) => {
      const url = new URL(r.request().url());
      const one = url.pathname.match(/\/radiation\/(\d+)$/);
      return r.fulfill(json2(one ? radDetail(Number(one[1])) : radiationResponse(radLong)));
    });
  });

  for (const [set, url] of [
    ["weather", "/data/weather?rover=curiosity"],
    ["radiation", "/data/radiation"],
  ] as const) {
    test(`url-state AC-9 ${set}: page 2, sorted by sol ascending: the sol report, then Back brings page, sort and the row`, async ({
      page,
    }) => {
      await page.goto(url);
      const table = page.getByRole("table");
      await expect(table).toBeVisible();
      // On a phone the headings are hidden and the sort is a select with a direction button.
      const phone = test.info().project.name === "mobile";
      const solHead = table.getByRole("columnheader", { name: /^Sol/ });
      const direction = page.getByRole("button", { name: /^Sort direction/ });
      const expectSolSort = (dir: "ascending" | "descending") =>
        phone
          ? expect(direction).toHaveAccessibleName(new RegExp(`Sort direction: ${dir}`))
          : expect(solHead).toHaveAttribute("aria-sort", dir);
      await expectSolSort("descending");

      await (phone ? direction : solHead.getByRole("button")).click();
      await expectSolSort("ascending");
      await page
        .getByRole("navigation", { name: "Table pages" })
        .getByRole("button", { name: /next/i })
        .click();
      await expect(page.getByText(/page 2 of/)).toBeVisible();

      const link = table.getByRole("link", { name: /^Sol report for sol \d+/ }).first();
      const label = (await link.getAttribute("aria-label"))!;
      const sol = Number(/sol (\d+)/.exec(label)![1]);
      await link.scrollIntoViewIfNeeded();
      await link.click();
      await expect(page).toHaveURL(new RegExp(`/rovers/curiosity/sols/${sol}/conditions`));

      await page.goBack();
      await expect(page.getByText(/page 2 of/)).toBeVisible();
      await expectSolSort("ascending");
      const hl = page.locator(
        "tr.hl, tr[aria-current]:not([aria-current='false']), tr[aria-selected='true'], tr[data-highlighted]",
      );
      await expect(hl).toHaveCount(1);
      await expect(hl).toContainText(String(sol));
      await expect(hl).toBeInViewport();
    });
  }

  test("url-state AC-9 a table of a fresh load starts as before: page 1, newest sol first", async ({
    page,
  }) => {
    await page.goto("/data/weather?rover=curiosity");
    const table = page.getByRole("table");
    await expect(table.getByRole("columnheader", { name: /^Sol/ })).toHaveAttribute(
      "aria-sort",
      "descending",
    );
    await expect(page.getByText(/page 1 of/)).toBeVisible();
    await expect(page.locator("tr.hl")).toHaveCount(0);
  });
  test("url-state AC-9 a table reached by an internal link starts as before, though the earlier visit saved a row", async ({
    page,
  }) => {
    await page.goto("/data/weather?rover=curiosity");
    await expect(page.getByRole("table")).toBeVisible();
    await page
      .getByRole("navigation", { name: "Table pages" })
      .getByRole("button", { name: /next/i })
      .click();
    await expect(page.getByText(/page 2 of/)).toBeVisible();
    const link = page
      .getByRole("table")
      .getByRole("link", { name: /^Sol report for sol \d+/ })
      .first();
    await link.click();
    await expect(page).toHaveURL(/\/sols\/\d+\/conditions/);
    // an internal link to the same table is a new visit, not Back
    await page.getByRole("button", { name: "Data" }).click();
    await page.getByRole("link", { name: "Weather", exact: true }).click();
    await expect(page).toHaveURL(/\/data\/weather/);
    await expect(page.getByText(/page 1 of/)).toBeVisible();
    await expect(page.locator("tr.hl")).toHaveCount(0);
  });
});

test.describe("url-state AC-10 Timeline range form", () => {
  test.beforeEach(async ({ page }) => mockApi(page));
  const START = "/rovers/perseverance/timeline?from_sol=1900&to_sol=2000";

  async function apply(page: Page, from: string, to: string) {
    await page.getByLabel("From sol").fill(from);
    await page.getByLabel("To sol").fill(to);
    await page.getByRole("button", { name: "Apply" }).click();
  }

  test("url-state AC-10 From above To: the address stays and the field names the problem", async ({
    page,
  }) => {
    await page.goto(START);
    await expect(page.getByLabel("From sol")).toBeVisible();
    const before = await historyLength(page);
    await apply(page, "1500", "100");
    await expect(page.getByText("“From” must not be greater than “To”.")).toBeVisible();
    await expect(page.getByLabel("From sol")).toHaveAttribute("aria-invalid", "true");
    await expect(page).toHaveURL((u) => u.search === "?from_sol=1900&to_sol=2000");
    expect(await historyLength(page)).toBe(before);
  });

  test("url-state AC-10 a sol above max_sol: 'Sols run 0–2031.' (the browser's own check must not swallow the form)", async ({
    page,
  }) => {
    await page.goto(START);
    await expect(page.getByLabel("From sol")).toBeVisible();
    await apply(page, "100", "3000");
    await expect(page.getByText("Sols run 0–2031.")).toBeVisible();
    await expect(page.getByLabel("To sol")).toHaveAttribute("aria-invalid", "true");
    await expect(page).toHaveURL((u) => u.search === "?from_sol=1900&to_sol=2000");
    await expect(page.getByLabel("To sol")).toHaveAttribute("max", "2031");
    await expect(page.getByLabel("To sol")).toHaveAttribute("min", "0");
  });

  test("url-state AC-10 the error state has no serious axe violations", async ({ page }) => {
    await page.goto(START);
    await expect(page.getByLabel("From sol")).toBeVisible();
    await apply(page, "1500", "100");
    await expect(page.getByText("“From” must not be greater than “To”.")).toBeVisible();
    const bad = (await new AxeBuilder({ page }).analyze()).violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical",
    );
    expect(bad.map((v) => `${v.impact} ${v.id}: ${v.help}`)).toEqual([]);
  });

  test("url-state AC-10 a valid range is a new entry; Back returns the old range and stays on the Timeline", async ({
    page,
  }) => {
    await page.goto(START);
    await expect(page.getByLabel("From sol")).toBeVisible();
    await apply(page, "100", "200");
    await expect(page).toHaveURL((u) => u.search === "?from_sol=100&to_sol=200");
    await page.goBack();
    await expect(page).toHaveURL(
      (u) =>
        u.pathname === "/rovers/perseverance/timeline" && u.search === "?from_sol=1900&to_sol=2000",
    );
    await expect(page.getByLabel("From sol")).toHaveValue("1900");
    await expect(page.getByLabel("To sol")).toHaveValue("2000");
  });
});
