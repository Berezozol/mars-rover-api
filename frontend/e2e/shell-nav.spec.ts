import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type Route } from "@playwright/test";
import { horizontalOverflow } from "./helpers";

// Package 4 of the frontend review (docs/proposals/frontend-shell-nav.md): the header, the row of views of a rover,
// the narrow screen, the headings and focus after a link, the theme. Written against the specification, not against
// the implementation. The whole API is intercepted (page.route); images are replaced by a tiny SVG. Viewports come
// from the projects of playwright.config.ts (375 and 1280 px). Tests are named `shell AC-N`.

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
const SPI = rover("spirit", "Spirit", 2208, "complete");
const ROVERS = [PER, CUR, ING, SPI];

const SVG =
  "<svg xmlns='http://www.w3.org/2000/svg' width='4' height='3'><rect width='4' height='3' fill='#888'/></svg>";

const STOPS = [10, 20, 30, 40].map((sol, i) => ({
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

const photoDetail = (id: number) => ({
  id,
  rover: "curiosity",
  sol: 344,
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
  caption: null,
  received_at: "2026-11-07T03:41:09Z",
  site: null,
  drive: null,
  extra: {},
  location: null,
  local_time: null,
  in_sol: null,
});

const emptyList = { items: [], page: 1, per_page: 24, total: 0, nearest_sols: null };

/** Requests to /api/v2/photos/<id> (the page `/photos/abc` must not make one). */
async function mockApi(page: Page, seen: string[] = []) {
  // Every host but our own is an image host: answer with a picture so that nothing leaves the machine.
  await page.route(
    (url) => url.hostname !== "127.0.0.1" && url.hostname !== "localhost",
    (r) => r.fulfill({ status: 200, contentType: "image/svg+xml", body: SVG }),
  );
  await page.route("**/health/sync", (r) => json(r, { status: "ok", rovers: [] }));
  await page.route("**/api/v2/**", (route) => {
    const url = new URL(route.request().url());
    const p = url.pathname.replace("/api/v2", "");
    seen.push(p);
    let m: RegExpMatchArray | null;
    if (p === "/rovers") return json(route, { items: ROVERS });
    if (p === "/photos") return json(route, emptyList);
    if ((m = p.match(/^\/photos\/(\d+)$/))) return json(route, photoDetail(Number(m[1])));
    if ((m = p.match(/^\/rovers\/(\w+)$/))) {
      const r = ROVERS.find((x) => x.id === m![1]);
      return r
        ? json(route, r)
        : json(
            route,
            { error: { code: "not_found", message: "no such rover", details: null } },
            404,
          );
    }
    if ((m = p.match(/^\/rovers\/(\w+)\/photos(\/latest)?$/))) {
      return json(route, m[2] ? { sol: null, ...emptyList } : emptyList);
    }
    if (/\/cameras$/.test(p)) return json(route, { items: [] });
    if ((m = p.match(/^\/rovers\/(\w+)\/manifest$/))) {
      const r = ROVERS.find((x) => x.id === m![1]) ?? PER;
      return json(route, { rover: r, sols: [] });
    }
    if (p === "/rovers/perseverance/samples") {
      return json(route, {
        rover: "perseverance",
        source_url: "https://science.nasa.gov/mission/mars-2020-perseverance/mars-rock-samples/",
        source_updated_at: null,
        updated_at: null,
        items: [],
      });
    }
    if (p === "/rovers/ingenuity/flights") return json(route, { items: [] });
    if ((m = p.match(/^\/rovers\/(curiosity|perseverance)\/traverse$/))) {
      return json(route, {
        type: "FeatureCollection",
        rover: m[1],
        available: true,
        updated_at: "2026-10-28T15:05:03Z",
        source_max_sol: 40,
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
    if ((m = p.match(/^\/rovers\/(curiosity|perseverance)\/location$/))) {
      return json(route, {
        rover: m[1],
        available: true,
        updated_at: "2026-10-28T15:05:06Z",
        source_max_sol: 40,
        items: url.searchParams.get("latest") === "true" ? [] : STOPS,
      });
    }
    return json(
      route,
      { error: { code: "not_found", message: `no mock for ${p}`, details: null } },
      404,
    );
  });
}

const isMobile = () => test.info().project.name === "mobile";
const header = (page: Page) => page.locator("nav.nav[aria-label='Main']");
const tabs = (page: Page) => page.locator("nav[data-testid='view-tabs']");

const serious = async (page: Page, include?: string) => {
  const b = new AxeBuilder({ page });
  if (include) b.include(include);
  const res = await b.analyze();
  return res.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${v.impact} ${v.id}: ${v.help}`);
};

/** Text of the links and buttons a strip shows, in order (a closed menu's own list is not counted). */
const itemNames = (strip: ReturnType<typeof header>) =>
  strip.evaluate((el) =>
    [...el.querySelectorAll<HTMLElement>("a, button")]
      .filter((n) => !n.closest("[hidden]"))
      .map((n) => (n.textContent ?? "").trim()),
  );

test.describe("shell AC-1 the header", () => {
  test.beforeEach(async ({ page }) => mockApi(page));

  for (const url of [
    "/",
    "/search",
    "/status",
    "/data/weather",
    "/rovers/curiosity/map",
    "/no-such-page",
  ]) {
    test(`shell AC-1 ${url}: Rovers, Search, Data, Stats, Status, API docs and nothing else`, async ({
      page,
    }) => {
      await page.goto(url);
      await expect(header(page)).toBeVisible();
      const items = await itemNames(header(page));
      expect(items).toHaveLength(6);
      expect(items.map((t, i) => (i === 2 ? t.replace(/\s.*$/, "") : t))).toEqual([
        "Rovers",
        "Search",
        "Data",
        "Stats",
        "Status",
        "API docs",
      ]);
      for (const gone of ["Photos", "Timeline", "Route map", "Flights"]) {
        await expect(header(page).getByRole("link", { name: gone })).toHaveCount(0);
      }
    });
  }

  test("shell AC-1 Data is a menu button, not a link", async ({ page }) => {
    await page.goto("/");
    const data = header(page).getByRole("button", { name: /^Data/ });
    await expect(data).toHaveAttribute("aria-expanded", "false");
    await data.click();
    await expect(data).toHaveAttribute("aria-expanded", "true");
    await expect(header(page).getByRole("link", { name: "Weather" })).toBeVisible();
  });
});

test.describe("shell AC-1 the row of views on every page of a rover", () => {
  test.beforeEach(async ({ page }) => mockApi(page));

  const cases: [string, string[], [string, string][]][] = [
    [
      "curiosity",
      ["Photos", "Timeline", "Route map"],
      [
        ["/rovers/curiosity", "Photos"],
        ["/rovers/curiosity/timeline", "Timeline"],
        ["/rovers/curiosity/map", "Route map"],
      ],
    ],
    [
      "perseverance",
      ["Photos", "Timeline", "Route map", "Samples"],
      [
        ["/rovers/perseverance", "Photos"],
        ["/rovers/perseverance/timeline", "Timeline"],
        ["/rovers/perseverance/map", "Route map"],
        ["/rovers/perseverance/samples", "Samples"],
      ],
    ],
    [
      "ingenuity",
      ["Photos", "Timeline", "Flights"],
      [
        ["/rovers/ingenuity", "Photos"],
        ["/rovers/ingenuity/timeline", "Timeline"],
        ["/rovers/ingenuity/flights", "Flights"],
      ],
    ],
    [
      "spirit",
      ["Photos", "Timeline"],
      [
        ["/rovers/spirit", "Photos"],
        ["/rovers/spirit/timeline", "Timeline"],
      ],
    ],
  ];
  const PATH: Record<string, string> = {
    Photos: "",
    Timeline: "/timeline",
    "Route map": "/map",
    Samples: "/samples",
    Flights: "/flights",
  };

  for (const [id, labels, pages] of cases) {
    for (const [url, current] of pages) {
      test(`shell AC-1 ${url}: ${labels.join(", ")}; ${current} is current; every tab stays on ${id}`, async ({
        page,
      }) => {
        await page.goto(url);
        await expect(tabs(page)).toBeVisible();
        await expect(tabs(page)).toHaveCount(1);
        expect(await itemNames(tabs(page))).toEqual(labels);
        for (const label of labels) {
          const link = tabs(page).getByRole("link", { name: label });
          await expect(link).toHaveAttribute("href", `/rovers/${id}${PATH[label]}`);
          if (label === current) await expect(link).toHaveAttribute("aria-current", "page");
          else await expect(link).not.toHaveAttribute("aria-current", /page/);
        }
      });
    }
  }

  test("shell AC-1 a tab leads to the same rover's page (Perseverance map -> Timeline)", async ({
    page,
  }) => {
    await page.goto("/rovers/perseverance/map");
    await tabs(page).getByRole("link", { name: "Timeline" }).click();
    await expect(page).toHaveURL(/\/rovers\/perseverance\/timeline$/);
    await expect(tabs(page).getByRole("link", { name: "Timeline" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });
});

/** The strip scrolls sideways when its items do not fit; `data-overflow` says where more items wait. */
async function stripState(page: Page, selector: string) {
  return page.evaluate((sel) => {
    const el = document.querySelector<HTMLElement>(sel)!;
    return {
      scrollWidth: el.scrollWidth,
      clientWidth: el.clientWidth,
      scrollLeft: el.scrollLeft,
      attr: el.getAttribute("data-overflow"),
    };
  }, selector);
}

async function expectOverflowAttr(page: Page, selector: string) {
  const s = await stripState(page, selector);
  if (s.scrollWidth <= s.clientWidth + 1) {
    expect(["start", "end", "both"], "everything fits: no continuation mark").not.toContain(s.attr);
    return;
  }
  const expected = (st: typeof s) => {
    const start = st.scrollLeft > 1;
    const end = st.scrollLeft + st.clientWidth < st.scrollWidth - 1;
    return start && end ? "both" : start ? "start" : "end";
  };
  await expect.poll(async () => (await stripState(page, selector)).attr).toBe(expected(s));
  await page.evaluate((sel) => {
    const el = document.querySelector<HTMLElement>(sel)!;
    el.scrollLeft = el.scrollWidth;
  }, selector);
  await expect.poll(async () => (await stripState(page, selector)).attr).toBe("start");
  await page.evaluate((sel) => {
    document.querySelector<HTMLElement>(sel)!.scrollLeft = 0;
  }, selector);
  await expect.poll(async () => (await stripState(page, selector)).attr).toBe("end");
}

/** Tab through the strip: the outline of every focused item has to lie inside the strip, not cut off. */
async function expectFocusOutlineNotClipped(page: Page, selector: string) {
  let checked = 0;
  for (let i = 0; i < 40 && checked < 12; i++) {
    await page.keyboard.press("Tab");
    // let a smooth scroll to the focused item end before measuring
    await page.evaluate(
      (sel) =>
        new Promise<void>((resolve) => {
          const strip = document.querySelector<HTMLElement>(sel)!;
          let last = -1;
          let same = 0;
          const tick = () => {
            same = strip.scrollLeft === last ? same + 1 : 0;
            last = strip.scrollLeft;
            if (same >= 5) resolve();
            else requestAnimationFrame(tick);
          };
          tick();
        }),
      selector,
    );
    const r = await page.evaluate((sel) => {
      const strip = document.querySelector<HTMLElement>(sel)!;
      const el = document.activeElement as HTMLElement | null;
      if (!el || !strip.contains(el)) return null;
      const cs = getComputedStyle(el);
      const width = cs.outlineStyle === "none" ? 0 : parseFloat(cs.outlineWidth) || 0;
      const offset = parseFloat(cs.outlineOffset) || 0;
      const grow = width + offset;
      const clips = getComputedStyle(strip).overflow !== "visible";
      const b = el.getBoundingClientRect();
      const s = strip.getBoundingClientRect();
      return {
        label: (el.textContent ?? "").trim(),
        clips,
        width,
        top: b.top - grow - s.top,
        bottom: s.bottom - (b.bottom + grow),
        left: b.left - grow - s.left,
        right: s.right - (b.right + grow),
      };
    }, selector);
    if (!r) continue;
    checked++;
    if (!r.clips) continue; // nothing is cut off where the strip does not clip
    expect(r.width, `${r.label}: a visible focus outline`).toBeGreaterThan(0);
    expect(r.top, `${r.label}: outline top inside the strip`).toBeGreaterThanOrEqual(-0.5);
    expect(r.bottom, `${r.label}: outline bottom inside the strip`).toBeGreaterThanOrEqual(-0.5);
    expect(r.left, `${r.label}: outline left inside the strip`).toBeGreaterThanOrEqual(-0.5);
    expect(r.right, `${r.label}: outline right inside the strip`).toBeGreaterThanOrEqual(-0.5);
  }
  expect(checked, "Tab reached the strip").toBeGreaterThan(0);
}

test.describe("shell AC-2 the strips on a narrow screen", () => {
  test.beforeEach(async ({ page }) => mockApi(page));

  for (const url of ["/", "/search", "/stats", "/status", "/data/weather", "/data/radiation"]) {
    test(`shell AC-2 ${url}: the current item of the header is inside the strip without scrolling it by hand`, async ({
      page,
    }) => {
      await page.goto(url);
      const current = header(page).locator("[aria-current='page']:visible").first();
      await expect(current).toBeVisible();
      await expect
        .poll(async () => {
          const r = await current.evaluate((el) => {
            const b = el.getBoundingClientRect();
            const s = el.closest("nav")!.getBoundingClientRect();
            return { left: b.left - s.left, right: s.right - b.right };
          });
          return r.left >= -1 && r.right >= -1;
        })
        .toBe(true);
      const o = await horizontalOverflow(page);
      expect(o.scrollWidth, "the page itself does not scroll sideways").toBeLessThanOrEqual(
        o.innerWidth,
      );
    });
  }

  for (const url of [
    "/rovers/perseverance/samples",
    "/rovers/perseverance/map",
    "/rovers/ingenuity/flights",
  ]) {
    test(`shell AC-2 ${url}: the current tab is inside the row of views`, async ({ page }) => {
      await page.goto(url);
      const current = tabs(page).locator("[aria-current='page']");
      await expect(current).toBeVisible();
      await expect
        .poll(async () => {
          const r = await current.evaluate((el) => {
            const b = el.getBoundingClientRect();
            const s = el.closest("nav")!.getBoundingClientRect();
            return { left: b.left - s.left, right: s.right - b.right };
          });
          return r.left >= -1 && r.right >= -1;
        })
        .toBe(true);
    });
  }

  test("shell AC-2 /status: Status in the header is the current page", async ({ page }) => {
    await page.goto("/status");
    await expect(header(page).getByRole("link", { name: "Status" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  test("shell AC-2 data-overflow on the header follows the scroll (and says nothing when all fits)", async ({
    page,
  }) => {
    await page.goto("/");
    await expect(header(page)).toBeVisible();
    await expectOverflowAttr(page, "nav.nav[aria-label='Main']");
  });

  test("shell AC-2 data-overflow on the row of views follows the scroll (and says nothing when all fits)", async ({
    page,
  }) => {
    await page.goto("/rovers/perseverance/map");
    await expect(tabs(page)).toBeVisible();
    await expectOverflowAttr(page, "nav[data-testid='view-tabs']");
  });

  test("shell AC-2 the focus outline of the header items is not cut off", async ({ page }) => {
    await page.goto("/");
    await expect(header(page)).toBeVisible();
    await expectFocusOutlineNotClipped(page, "nav.nav[aria-label='Main']");
  });

  test("shell AC-2 the focus outline of the tabs is not cut off", async ({ page }) => {
    await page.goto("/rovers/perseverance/map");
    await expect(tabs(page)).toBeVisible();
    await expectFocusOutlineNotClipped(page, "nav[data-testid='view-tabs']");
  });

  test("shell AC-2 with a scrolled strip, an open Data menu has no mask and Weather can be clicked", async ({
    page,
  }) => {
    test.skip(!isMobile(), "the strip scrolls only on a phone");
    await page.goto("/");
    await expect(header(page)).toBeVisible();
    await page.evaluate(() => {
      document.querySelector<HTMLElement>("nav.nav[aria-label='Main']")!.scrollLeft = 40;
    });
    await expect(header(page)).toHaveAttribute("data-overflow", /^(start|end|both)$/);
    await header(page).getByRole("button", { name: /^Data/ }).click();
    const masks = await header(page).evaluate((el) => {
      const cs = getComputedStyle(el);
      return [cs.maskImage, cs.getPropertyValue("-webkit-mask-image")];
    });
    for (const m of masks) expect(["none", ""]).toContain(m);
    const weather = header(page).getByRole("link", { name: "Weather" });
    await expect(weather).toBeVisible();
    await weather.click();
    await expect(page).toHaveURL(/\/data\/weather/);
  });

  test("shell AC-2 the footer has the groups Project and API keys", async ({ page }) => {
    await page.goto("/no-such-page");
    await expect(page.getByTestId("footer-group-project")).toContainText("Project");
    await expect(page.getByTestId("footer-group-keys")).toContainText("API keys");
  });

  test("shell AC-2 every footer link is at least 44 px high on a phone", async ({ page }) => {
    test.skip(!isMobile(), "the 44 px target is specified for widths up to 640 px");
    await page.goto("/no-such-page");
    for (const id of ["footer-group-project", "footer-group-keys"]) {
      const links = page.getByTestId(id).getByRole("link");
      const n = await links.count();
      expect(n, `${id} has links`).toBeGreaterThanOrEqual(2);
      for (let i = 0; i < n; i++) {
        const h = (await links.nth(i).boundingBox())!.height;
        const label = await links.nth(i).textContent();
        expect(h, `${label}: height`).toBeGreaterThanOrEqual(43.5);
      }
    }
  });
});

test.describe("shell AC-3 headings and focus", () => {
  let seen: string[];
  test.beforeEach(async ({ page }) => {
    seen = [];
    await mockApi(page, seen);
  });

  for (const [url, title] of [
    ["/no-such-page", "Page not found"],
    ["/photos/abc", "Page not found"],
    ["/photos/-1", "Page not found"],
    ["/rovers/nosuch", "Rover not found"],
    ["/rovers/nosuch/timeline", "Rover not found"],
    ["/rovers/nosuch/map", "Rover not found"],
    ["/rovers/nosuch/samples", "Rover not found"],
    ["/rovers/nosuch/flights", "Rover not found"],
  ] as const) {
    test(`shell AC-3 ${url}: exactly one h1, "${title}"`, async ({ page }) => {
      await page.goto(url);
      await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();
      await expect(page.locator("h1")).toHaveCount(1);
    });
  }

  test("shell AC-3 /photos/abc and /photos/-1 never ask the API for a photo", async ({ page }) => {
    await page.goto("/photos/abc");
    await expect(page.getByRole("heading", { level: 1, name: "Page not found" })).toBeVisible();
    await page.goto("/photos/-1");
    await expect(page.getByRole("heading", { level: 1, name: "Page not found" })).toBeVisible();
    expect(seen.filter((p) => p.startsWith("/photos"))).toEqual([]);
  });

  test("shell AC-3 a link moves focus to the h1 of the new page", async ({ page }) => {
    await page.goto("/no-such-page");
    await header(page).getByRole("link", { name: "Search" }).click();
    const h1 = page.locator("main h1");
    await expect(h1).toHaveText("Search photos");
    await expect(h1).toBeFocused();
    await expect(h1).toHaveAttribute("tabindex", "-1");
  });

  test("shell AC-3 a tab of the row of views moves focus to the h1 as well", async ({ page }) => {
    await page.goto("/rovers/curiosity");
    await tabs(page).getByRole("link", { name: "Timeline" }).click();
    await expect(page).toHaveURL(/\/rovers\/curiosity\/timeline$/);
    const h1 = page.locator("main h1");
    await expect(h1).toBeVisible();
    await expect(h1).toBeFocused();
  });

  test("shell AC-3 Back does not move focus to the h1", async ({ page }) => {
    await page.goto("/no-such-page");
    await header(page).getByRole("link", { name: "Search" }).click();
    await expect(page.locator("main h1")).toBeFocused();
    await page.goBack();
    await expect(page).toHaveURL(/\/no-such-page$/);
    await expect(page.getByRole("heading", { level: 1, name: "Page not found" })).toBeVisible();
    await expect(page.locator("main h1")).not.toBeFocused();
  });
});

for (const scheme of ["light", "dark"] as const) {
  test(`shell AC-2 the strips show no scrollbar and the active tab underline is visible (${scheme})`, async ({
    page,
  }) => {
    test.skip(!isMobile(), "the strips scroll on a phone (375 x 812)");
    await mockApi(page);
    await page.emulateMedia({ colorScheme: scheme });
    await page.goto("/rovers/perseverance/map");
    await expect(tabs(page)).toBeVisible();
    const r = await page.evaluate(() => {
      const strips = [
        document.querySelector<HTMLElement>("nav.nav[aria-label='Main']")!,
        document.querySelector<HTMLElement>("nav[data-testid='view-tabs']")!,
      ];
      const probe = document.createElement("span");
      probe.style.color = "var(--accent)";
      document.body.appendChild(probe);
      const accent = getComputedStyle(probe).color;
      probe.remove();
      const active = strips[1]!.querySelector<HTMLElement>("[aria-current='page']")!;
      const acs = getComputedStyle(active);
      const header = document.querySelector(".site-header")!.getBoundingClientRect();
      const before = strips.map((s) => s.scrollLeft);
      strips.forEach((s) => (s.scrollLeft = s.scrollWidth));
      const after = strips.map((s) => s.scrollLeft);
      return {
        scrollbarWidth: strips.map((s) => getComputedStyle(s).scrollbarWidth),
        // the horizontal scrollbar's height: what is left once the borders are taken off
        barHeight: strips.map((s) => {
          const cs = getComputedStyle(s);
          return (
            s.offsetHeight -
            s.clientHeight -
            parseFloat(cs.borderTopWidth) -
            parseFloat(cs.borderBottomWidth)
          );
        }),
        navBottom: strips[0]!.getBoundingClientRect().bottom,
        headerBottom: header.bottom,
        accent,
        underlineColor: acs.borderBottomColor,
        underlineWidth: parseFloat(acs.borderBottomWidth),
        scrollable: strips.map(
          (s, i) => s.scrollWidth > s.clientWidth + 1 && after[i]! > before[i]!,
        ),
        fits: strips.map((s) => s.scrollWidth <= s.clientWidth + 1),
      };
    });
    expect(r.scrollbarWidth).toEqual(["none", "none"]);
    expect(r.barHeight).toEqual([0, 0]);
    expect(r.navBottom).toBeLessThanOrEqual(r.headerBottom + 0.5);
    expect(r.underlineColor).toBe(r.accent);
    expect(r.underlineWidth).toBeGreaterThanOrEqual(3);
    // still scrollable by hand wherever the items do not fit
    r.scrollable.forEach((ok, i) => expect(ok || r.fits[i], `strip ${i} scrolls`).toBe(true));
  });
}

const KEY = "sol-index-theme";
const luminance = (css: string) => {
  const m = css.match(/\d+(\.\d+)?/g)!.map(Number);
  const [r, g, b] = m as [number, number, number];
  return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
};
const background = (page: Page) =>
  page.evaluate(() => getComputedStyle(document.body).backgroundColor);
const themeAttr = (page: Page) =>
  page.evaluate(() => document.documentElement.getAttribute("data-theme"));
const stored = (page: Page) => page.evaluate((k) => localStorage.getItem(k), KEY);

test.describe("shell AC-4 the theme select", () => {
  test.beforeEach(async ({ page }) => mockApi(page));

  test("shell AC-4 is a select labelled Theme with Auto, Light, Dark; Auto by default", async ({
    page,
  }) => {
    await page.goto("/no-such-page");
    const select = page.getByLabel("Theme");
    await expect(select).toHaveAttribute("id", "theme-select");
    expect(await select.evaluate((el) => el.tagName)).toBe("SELECT");
    expect(await select.locator("option").allTextContents()).toEqual(["Auto", "Light", "Dark"]);
    await expect(select.locator("option:checked")).toHaveText("Auto");
    expect(await themeAttr(page)).toBeNull();
    expect(await stored(page)).toBeNull();
  });

  test("shell AC-4 Dark and Light set the page, the attribute and the key; Auto clears them", async ({
    page,
  }) => {
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto("/no-such-page");
    const select = page.getByLabel("Theme");
    await select.selectOption({ label: "Dark" });
    await expect.poll(() => themeAttr(page)).toBe("dark");
    expect(await stored(page)).toBe("dark");
    expect(luminance(await background(page))).toBeLessThan(0.35);
    await select.selectOption({ label: "Light" });
    await expect.poll(() => themeAttr(page)).toBe("light");
    expect(await stored(page)).toBe("light");
    expect(luminance(await background(page))).toBeGreaterThan(0.6);
    await select.selectOption({ label: "Auto" });
    await expect.poll(() => themeAttr(page)).toBeNull();
    expect(await stored(page)).toBeNull();
  });

  test("shell AC-4 under Auto a change of the system theme changes the page at once; the choice stays Auto", async ({
    page,
  }) => {
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto("/no-such-page");
    await expect.poll(async () => luminance(await background(page))).toBeGreaterThan(0.6);
    await page.emulateMedia({ colorScheme: "dark" });
    await expect.poll(async () => luminance(await background(page))).toBeLessThan(0.35);
    await expect(page.getByLabel("Theme").locator("option:checked")).toHaveText("Auto");
    await page.emulateMedia({ colorScheme: "light" });
    await expect.poll(async () => luminance(await background(page))).toBeGreaterThan(0.6);
    expect(await stored(page)).toBeNull();
  });

  test("shell AC-4 an explicit choice wins over the system theme", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto("/no-such-page");
    await page.getByLabel("Theme").selectOption({ label: "Dark" });
    await page.emulateMedia({ colorScheme: "light" });
    await page.emulateMedia({ colorScheme: "dark" });
    await page.emulateMedia({ colorScheme: "light" });
    expect(await themeAttr(page)).toBe("dark");
    expect(luminance(await background(page))).toBeLessThan(0.35);
  });

  test("shell AC-4 the choice survives a reload and the first frame is already in that theme (no flash)", async ({
    page,
  }) => {
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto("/no-such-page");
    await page.getByLabel("Theme").selectOption({ label: "Dark" });
    await expect.poll(() => themeAttr(page)).toBe("dark");
    // Record the page as it is at its first frame after the reload.
    await page.addInitScript(() => {
      requestAnimationFrame(() => {
        (window as unknown as { __first: unknown }).__first = {
          theme: document.documentElement.getAttribute("data-theme"),
          bg: document.body ? getComputedStyle(document.body).backgroundColor : null,
          rootBg: getComputedStyle(document.documentElement).backgroundColor,
        };
      });
    });
    await page.reload();
    await expect(page.getByLabel("Theme").locator("option:checked")).toHaveText("Dark");
    const first = (await page.evaluate(
      () => (window as unknown as { __first: unknown }).__first,
    )) as {
      theme: string | null;
      bg: string | null;
      rootBg: string;
    };
    expect(first.theme).toBe("dark");
    expect(luminance(first.bg ?? first.rootBg)).toBeLessThan(0.35);
  });

  test("shell AC-4 a choice made in another tab is applied in this one", async ({
    page,
    context,
  }) => {
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto("/no-such-page");
    const other = await context.newPage();
    await mockApi(other);
    await other.emulateMedia({ colorScheme: "light" });
    await other.goto("/no-such-page");
    await other.getByLabel("Theme").selectOption({ label: "Dark" });
    await expect.poll(() => themeAttr(page)).toBe("dark");
    await expect(page.getByLabel("Theme").locator("option:checked")).toHaveText("Dark");
    await other.getByLabel("Theme").selectOption({ label: "Auto" });
    await expect.poll(() => themeAttr(page)).toBeNull();
    await expect(page.getByLabel("Theme").locator("option:checked")).toHaveText("Auto");
    await other.close();
  });

  test("shell AC-4 the select can be used with the keyboard", async ({ page }) => {
    await page.goto("/no-such-page");
    const select = page.getByLabel("Theme");
    await select.focus();
    await page.keyboard.press("ArrowDown");
    await expect.poll(() => themeAttr(page)).toBe("light");
    await page.keyboard.press("ArrowDown");
    await expect.poll(() => themeAttr(page)).toBe("dark");
  });
});

for (const scheme of ["light", "dark"] as const) {
  test.describe(`shell axe, ${scheme} theme`, () => {
    test.beforeEach(async ({ page }) => {
      await mockApi(page);
      await page.emulateMedia({ colorScheme: scheme });
    });

    test(`shell AC-1 the header has no serious axe violations (${scheme})`, async ({ page }) => {
      await page.goto("/");
      await expect(header(page)).toBeVisible();
      expect(await serious(page, "header")).toEqual([]);
    });

    test(`shell AC-1 /rovers/perseverance/map has no serious axe violations or sideways scroll (${scheme})`, async ({
      page,
    }) => {
      await page.goto("/rovers/perseverance/map");
      await expect(tabs(page)).toBeVisible();
      await expect(page.getByRole("table")).toBeVisible();
      expect(await serious(page)).toEqual([]);
      const o = await horizontalOverflow(page);
      expect(o.scrollWidth).toBeLessThanOrEqual(o.innerWidth);
    });

    test(`shell AC-3 a placeholder page has no serious axe violations (${scheme})`, async ({
      page,
    }) => {
      await page.goto("/photos/abc");
      await expect(page.getByRole("heading", { level: 1, name: "Page not found" })).toBeVisible();
      expect(await serious(page)).toEqual([]);
    });
  });
}

// ----- shell-polish (docs/proposals/frontend-shell-nav-polish.md), AC-1..AC-4 -----
// Written against the specification. The 375 px tests run in the `mobile` project, the 1280 px ones in the other.

const SCHEMES = ["light", "dark"] as const;

/** The nav item that carries `aria-current` (links and the Data button, not the closed menu's list). */
const currentItemOf = (page: Page) =>
  page.locator(
    "nav.nav[aria-label='Main'] > a[aria-current], nav.nav[aria-label='Main'] .nav-menu-btn[aria-current]",
  );

/** Parses any CSS colour through a canvas into [r, g, b, a] (a in 0..1) so that colour spaces do not matter. */
const PARSE_COLOR = `(css) => {
  const c = document.createElement("canvas");
  c.width = c.height = 1;
  const x = c.getContext("2d", { willReadFrequently: true });
  x.clearRect(0, 0, 1, 1);
  x.fillStyle = css;
  x.fillRect(0, 0, 1, 1);
  const d = x.getImageData(0, 0, 1, 1).data;
  return [d[0], d[1], d[2], d[3] / 255];
}`;

/** The --accent colour and its contrast ratio against the header background (composited over the page). */
async function accentContrast(page: Page) {
  return page.evaluate(`(() => {
    const parse = ${PARSE_COLOR};
    const probe = document.createElement("span");
    probe.style.color = "var(--accent)";
    document.body.appendChild(probe);
    const accent = getComputedStyle(probe).color;
    probe.remove();
    const over = (top, under) => top.slice(0, 3).map((v, i) => v * top[3] + under[i] * (1 - top[3]));
    const pg = parse(getComputedStyle(document.body).backgroundColor);
    const headerBg = parse(getComputedStyle(document.querySelector(".site-header")).backgroundColor);
    const bg = over(headerBg, [pg[0], pg[1], pg[2]]);
    const a = parse(accent).slice(0, 3);
    const lum = (rgb) => {
      const [r, g, b] = rgb.map((v) => { const s = v / 255; return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4); });
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    const l1 = lum(a), l2 = lum(bg);
    return { accent, ratio: (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05) };
  })()`) as Promise<{ accent: string; ratio: number }>;
}

/** The computed indicator of an element: shadow offsets, the shadow colour as [r, g, b] and the fill. */
async function indicatorOf(el: ReturnType<Page["locator"]>) {
  return el.evaluate((node, parseSrc) => {
    const parse = (0, eval)(parseSrc) as (css: string) => number[];
    const cs = getComputedStyle(node);
    const shadow = cs.boxShadow;
    const off = shadow.match(/(-?\d+(?:\.\d+)?)px\s+(-?\d+(?:\.\d+)?)px/);
    const colour = shadow.match(/(rgba?\([^)]*\)|color\([^)]*\))/);
    const rgb = colour ? parse(colour[1]!) : null;
    return {
      shadow,
      x: off ? Number(off[1]) : null,
      y: off ? Number(off[2]) : null,
      inset: /\binset\b/.test(shadow),
      rgb: rgb ? rgb.slice(0, 3).map(Math.round) : null,
      background: cs.backgroundColor,
    };
  }, PARSE_COLOR);
}

const toRgb = (page: Page, css: string) =>
  page.evaluate(`(${PARSE_COLOR})(${JSON.stringify(css)}).slice(0, 3).map(Math.round)`) as Promise<
    number[]
  >;

const roverSwitch = (page: Page) => page.getByRole("navigation", { name: "Rover", exact: true });

test.describe("shell-polish AC-1 the four views fit on 375 px", () => {
  for (const scheme of SCHEMES) {
    for (const url of ["/rovers/perseverance/map", "/rovers/perseverance/samples"]) {
      test(`shell-polish AC-1 ${url} (${scheme}): the row of views needs no scrolling and shows all four tabs whole`, async ({
        page,
      }) => {
        await mockApi(page);
        await page.emulateMedia({ colorScheme: scheme });
        await page.goto(url);
        const row = page.locator("nav[data-testid='view-tabs']");
        await expect(row).toBeVisible();
        const r = await row.evaluate((el) => {
          const nav = el.getBoundingClientRect();
          return {
            scrollWidth: el.scrollWidth,
            clientWidth: el.clientWidth,
            overflowAttr: el.getAttribute("data-overflow"),
            tabs: [...el.querySelectorAll("a")].map((a) => {
              const b = a.getBoundingClientRect();
              return {
                label: (a.textContent ?? "").trim(),
                left: b.left - nav.left,
                right: nav.right - b.right,
              };
            }),
          };
        });
        expect(r.scrollWidth).toBeLessThanOrEqual(r.clientWidth);
        expect(r.overflowAttr).toBeNull();
        expect(r.tabs.map((t) => t.label)).toEqual(["Photos", "Timeline", "Route map", "Samples"]);
        for (const t of r.tabs) {
          expect(t.left, `${t.label} starts inside the row`).toBeGreaterThanOrEqual(-0.5);
          expect(t.right, `${t.label} ends inside the row`).toBeGreaterThanOrEqual(-0.5);
        }
        const o = await horizontalOverflow(page);
        expect(o.scrollWidth).toBeLessThanOrEqual(o.innerWidth);
      });
    }
  }
});

test.describe("shell-polish AC-2 the rover switcher on the map", () => {
  test.beforeEach(async ({ page }) => mockApi(page));

  for (const url of ["/rovers/perseverance/map", "/rovers/curiosity/map"]) {
    test(`shell-polish AC-2 ${url}: only Perseverance and Curiosity, every link leads to a map that exists`, async ({
      page,
    }) => {
      await page.goto(url);
      await expect(roverSwitch(page)).toBeVisible();
      expect(await itemNames(roverSwitch(page))).toEqual(["Perseverance", "Curiosity"]);
      const hrefs = await roverSwitch(page)
        .getByRole("link")
        .evaluateAll((as) => as.map((a) => a.getAttribute("href")));
      expect(hrefs).toEqual(["/rovers/perseverance/map", "/rovers/curiosity/map"]);
    });
  }

  test("shell-polish AC-2 a switcher link opens the map of the other rover", async ({ page }) => {
    await page.goto("/rovers/perseverance/map");
    await roverSwitch(page).getByRole("link", { name: "Curiosity" }).click();
    await expect(page).toHaveURL(/\/rovers\/curiosity\/map$/);
    await expect(roverSwitch(page).getByRole("link", { name: "Curiosity" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  test("shell-polish AC-2 Photos and Timeline still offer every rover", async ({ page }) => {
    for (const url of ["/rovers/perseverance", "/rovers/perseverance/timeline"]) {
      await page.goto(url);
      await expect(roverSwitch(page)).toBeVisible();
      expect(await itemNames(roverSwitch(page))).toEqual([
        "Perseverance",
        "Curiosity",
        "Ingenuity",
        "Spirit",
      ]);
    }
  });
});

test.describe("shell-polish AC-3 the Rovers item on the rover pages", () => {
  test.beforeEach(async ({ page }) => mockApi(page));

  for (const scheme of SCHEMES) {
    for (const url of [
      "/rovers/curiosity",
      "/rovers/curiosity/timeline",
      "/rovers/perseverance/map",
      "/rovers/perseverance/samples",
      "/rovers/ingenuity/flights",
      "/rovers/curiosity/sols/344/conditions",
    ]) {
      test(`shell-polish AC-3 ${url} (${scheme}): Rovers is current and looks current; Data is not`, async ({
        page,
      }) => {
        await page.emulateMedia({ colorScheme: scheme });
        await page.goto(url);
        const rovers = header(page).getByRole("link", { name: "Rovers", exact: true });
        const status = header(page).getByRole("link", { name: "Status", exact: true });
        const data = header(page).getByRole("button", { name: /^Data/ });
        await expect(rovers).toHaveAttribute("aria-current", "true");
        await expect(data).not.toHaveAttribute("aria-current", /.+/);
        const r = await indicatorOf(rovers);
        const s = await indicatorOf(status);
        const d = await indicatorOf(data);
        expect(r.shadow, "Rovers has an indicator").not.toBe("none");
        expect(s.shadow, "an ordinary item has none").toBe("none");
        expect(d.shadow, "Data has none here").toBe("none");
        expect(r.background, "Rovers has the 'current' fill").not.toBe(s.background);
      });
    }

    for (const url of ["/", "/data", "/data/weather"]) {
      test(`shell-polish AC-3 ${url} (${scheme}): ${url === "/" ? "Rovers" : "Data"} is the current item`, async ({
        page,
      }) => {
        await page.emulateMedia({ colorScheme: scheme });
        await page.goto(url);
        const rovers = header(page).getByRole("link", { name: "Rovers", exact: true });
        const data = header(page).getByRole("button", { name: /^Data/ });
        if (url === "/") {
          await expect(rovers).toHaveAttribute("aria-current", "page");
          await expect(data).not.toHaveAttribute("aria-current", /.+/);
        } else {
          await expect(data).toHaveAttribute("aria-current", /.+/);
          await expect(rovers).not.toHaveAttribute("aria-current", /.+/);
        }
      });
    }
  }
});

test.describe("shell-polish AC-4 the current item of the header has an indicator", () => {
  test.beforeEach(async ({ page }) => mockApi(page));

  for (const scheme of SCHEMES) {
    for (const url of ["/stats", "/data/weather", "/", "/rovers/perseverance/map"]) {
      test(`shell-polish AC-4 ${url} (${scheme}): a line of at least 2 px in --accent, contrast to the header at least 3:1`, async ({
        page,
      }) => {
        await page.emulateMedia({ colorScheme: scheme });
        await page.goto(url);
        const current = currentItemOf(page);
        await expect(current).toHaveCount(1);
        await expect(current).toBeVisible();
        const ind = await indicatorOf(current);
        expect(ind.inset, `inset shadow: ${ind.shadow}`).toBe(true);
        expect(ind.x, "no sideways offset").toBe(0);
        expect(ind.y, `a line at the bottom, at least 2 px: ${ind.shadow}`).toBeLessThanOrEqual(-2);
        const c = await accentContrast(page);
        expect(ind.rgb, "the line is --accent").toEqual(await toRgb(page, c.accent));
        expect(
          c.ratio,
          `--accent against the header background (${scheme})`,
        ).toBeGreaterThanOrEqual(3);
      });
    }

    test(`shell-polish AC-4 (${scheme}): the indicator does not change the size of the item`, async ({
      page,
    }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await page.goto("/stats");
      const withLine = await header(page)
        .getByRole("link", { name: "Stats", exact: true })
        .boundingBox();
      await page.goto("/status");
      const without = await header(page)
        .getByRole("link", { name: "Stats", exact: true })
        .boundingBox();
      expect(withLine!.width).toBeCloseTo(without!.width, 0);
      expect(withLine!.height).toBeCloseTo(without!.height, 0);
    });

    test(`shell-polish AC-4 (${scheme}): the header is not taller than 85 px on 375 px`, async ({
      page,
    }) => {
      test.skip(!isMobile(), "the height limit is specified for 375 px");
      await page.emulateMedia({ colorScheme: scheme });
      for (const url of ["/stats", "/data/weather", "/rovers/perseverance/map"]) {
        await page.goto(url);
        await expect(currentItemOf(page)).toBeVisible();
        const h = await page
          .locator(".site-header")
          .evaluate((el) => el.getBoundingClientRect().height);
        // 0.5 px of rounding room: the specification says "not more than the present 85 px"
        expect(h, `${url}: header height`).toBeLessThanOrEqual(85.5);
      }
    });

    test(`shell-polish AC-4 (${scheme}): no serious axe violations in the header with an indicator`, async ({
      page,
    }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await page.goto("/stats");
      await expect(currentItemOf(page)).toBeVisible();
      expect(await serious(page, "header")).toEqual([]);
    });
  }
});
