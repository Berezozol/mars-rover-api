import { fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { App } from "../src/App";
import { navigate } from "../src/router";
import type { Sample, SampleLocation, Waypoint } from "../src/api/types";
import {
  countSamples,
  filterSamples,
  groupByStop,
  heightText,
  lag,
  summarize,
} from "../src/lib/samples";
import {
  apiError,
  curiosity,
  detail,
  mockApi,
  page,
  perseverance,
  photo,
  samplesResponse,
} from "./helpers";

// Real rows (1, 2, 3, 8, 9, 17, 25, 26, 29, 30) from the saved NASA page; photo counts and `match` are made up.
const fixture = JSON.parse(readFileSync("tests/fixtures/samples.json", "utf-8")) as {
  items: Sample[];
};
const items = fixture.items;
const byNumber = (n: number) => items.find((x) => x.number === n)!;

const CAMERAS = "/api/v2/rovers/perseverance/cameras";

/** `/cameras?sol=S`: CACHECAM has photos on every sol but 377 (sample 9). */
function cameras(url: URL) {
  const sol = Number(url.searchParams.get("sol"));
  const list = [{ code: "SUPERCAM_RMI", name: "SuperCam RMI", total_photos: 40 }];
  if (sol !== 377)
    list.push({ code: "CACHECAM", name: "Sample Caching System Camera", total_photos: 12 });
  return { items: list };
}

function routes(over: Record<string, unknown> = {}) {
  return mockApi({
    "/api/v2/rovers": { items: [perseverance, curiosity] },
    "/api/v2/rovers/perseverance/samples": samplesResponse(items),
    [CAMERAS]: cameras,
    ...over,
  });
}

function open(path = "/rovers/perseverance/samples") {
  window.history.replaceState(null, "", path);
  return render(<App />);
}

const rowNumbers = () =>
  screen
    .getAllByRole("row")
    .slice(1)
    .map((r) => Number(r.id.replace("sample-", "")));

describe("sample helpers", () => {
  it("counts by status and type and sums up the range", () => {
    expect(countSamples(items)).toEqual({
      on_rover: 7,
      depot: 3,
      rock_core: 8,
      regolith: 1,
      atmospheric: 1,
    });
    const s = summarize(items);
    expect([s.count, s.firstSol, s.lastSol, s.tbd]).toEqual([10, 164, 1401, [29, 30]]);
    expect(summarize([])).toMatchObject({ count: 0, firstSol: null, lastSol: null });
  });

  it("filters by status and type; null values only match 'all'", () => {
    expect(filterSamples(items, "depot", "all").map((x) => x.number)).toEqual([1, 2, 9]);
    expect(filterSamples(items, "on_rover", "regolith").map((x) => x.number)).toEqual([17]);
    const odd = { ...byNumber(3), status: null, sample_type: null } as Sample;
    expect(filterSamples([odd], "all", "all")).toHaveLength(1);
    expect(filterSamples([odd], "depot", "all")).toHaveLength(0);
    expect(filterSamples([odd], "all", "rock_core")).toHaveLength(0);
  });

  it("flags the page as behind only beyond 100 sols", () => {
    expect(lag(items, 2031)).toEqual({ last: 1401, max: 2031 });
    expect(lag(items, 1501)).toBeNull(); // exactly 100 behind
    expect(lag(items, 1502)).toEqual({ last: 1401, max: 1502 });
    expect(lag(items, null)).toBeNull();
    expect(lag([byNumber(29)], 2031)).toBeNull(); // no sealed sol at all
  });

  it("shows the height as published", () => {
    expect(heightText(byNumber(25))).toBe("6.2 cm");
    expect(heightText(byNumber(29))).toBe("Est. 5-7 cm (2-2.8 in)");
    expect(heightText(byNumber(1))).toBeNull();
  });

  it("groups samples that share a stop; a group is approximate only if every placement is by_sol", () => {
    const g = groupByStop(items);
    expect(g.map((x) => x.items.map((s) => s.number))).toEqual([
      [1],
      [2, 3],
      [8, 9],
      [17],
      [25],
      [26],
    ]);
    const two = g.find((x) => x.items.length === 2)!;
    expect(two.approximate).toBe(false); // nearest_before + by_sol
    expect(g.find((x) => x.items[0]!.number === 1)!.approximate).toBe(true);
    expect(g.find((x) => x.items[0]!.number === 8)!.approximate).toBe(false);
  });
});

describe("Samples page", () => {
  it("shows the source, the lag strip, tiles and all rows with the text as published", async () => {
    routes();
    open();
    expect(await screen.findByRole("heading", { level: 1, name: /rock samples/ })).toBeTruthy();
    const src = await screen.findByRole("link", { name: /NASA, Mars Rock Samples page/ });
    expect(src.getAttribute("href")).toBe(
      "https://science.nasa.gov/mission/mars-2020-perseverance/mars-rock-samples/",
    );
    expect(screen.getByText("2026-06-20")).toBeTruthy();
    expect(screen.getByText(/may lag the mission/)).toBeTruthy();
    const banner = document.getElementById("lag-banner")!;
    expect(banner.textContent).toMatch(
      /last sealed sample was sealed on sol 1401; Perseverance is now at sol 2031/,
    );
    expect(banner.textContent).toMatch(/Samples 29 and 30 have no date or sol on the page yet/);
    const tiles = screen.getByLabelText("Sample summary");
    expect(within(tiles).getByText("sol 164 – 1401 · 2 without date yet")).toBeTruthy();
    expect(rowNumbers()).toEqual([1, 2, 3, 8, 9, 17, 25, 26, 29, 30]);
    // names are not normalised
    expect(screen.getAllByText(/Ha'ahóni \(aka "Hahonih"\)/).length).toBeGreaterThan(0);
    expect(screen.getByText("Est. 5-7 cm (2-2.8 in)")).toBeTruthy();
    expect(screen.getByText("6.2 cm")).toBeTruthy();
    expect(screen.getAllByText("No dossier yet").length).toBeGreaterThan(0);
    expect(screen.getAllByText("TBD")).toHaveLength(2);
    expect(screen.getAllByText("Photos: sol TBD")).toHaveLength(2);
    expect(
      screen
        .getByRole("link", { name: /155 photos of sol 1215 \(sample 25\)/ })
        .getAttribute("href"),
    ).toBe("/rovers/perseverance?sol=1215");
    expect(screen.getByText("Showing 10 of 10 samples")).toBeTruthy();
  });

  it("hides the lag strip when the page is close to the rover", async () => {
    routes({ "/api/v2/rovers": { items: [{ ...perseverance, max_sol: 1450 }] } });
    open();
    await screen.findByRole("table");
    expect(document.getElementById("lag-banner")?.textContent).not.toMatch(/may be behind/);
  });

  it("filters on the client by status and type and can clear the filters", async () => {
    const m = routes();
    open();
    await screen.findByRole("table");
    fireEvent.click(screen.getByRole("button", { name: /In depot/ }));
    expect(rowNumbers()).toEqual([1, 2, 9]);
    expect(screen.getByText("Showing 3 of 10 samples")).toBeTruthy();
    expect(screen.getByRole("button", { name: /In depot/ }).getAttribute("aria-pressed")).toBe(
      "true",
    );
    fireEvent.click(screen.getByRole("button", { name: /Regolith/ }));
    expect(screen.getByText("No samples match these filters")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(rowNumbers()).toHaveLength(10);
    fireEvent.click(screen.getByRole("button", { name: /Rock core/ }));
    fireEvent.click(screen.getByRole("button", { name: /On rover/ }));
    expect(rowNumbers()).toEqual([3, 8, 25, 26, 29, 30]);
    // no new samples request for filtering
    expect(m.calls.filter((c) => c.pathname.endsWith("/samples"))).toHaveLength(1);
  });

  it("offers 'Photos of the tube' only where CACHECAM has photos that sol", async () => {
    routes();
    open();
    await screen.findByRole("table");
    expect(
      await screen.findByRole("link", { name: /Photos of the tube: sample 25, sol 1215/ }),
    ).toBeTruthy();
    expect(
      screen.getByRole("link", { name: /Photos of the tube: sample 25/ }).getAttribute("href"),
    ).toBe("/rovers/perseverance?sol=1215&camera=CACHECAM");
    // sample 9 (sol 377) has no CACHECAM photos: only the sol link
    await waitFor(() =>
      expect(screen.getAllByRole("link", { name: /Photos of the tube/ })).toHaveLength(7),
    );
    expect(
      screen.queryByRole("link", { name: /Photos of the tube: sample 9, sol 377/ }),
    ).toBeNull();
    expect(screen.getByRole("link", { name: /229 photos of sol 377/ })).toBeTruthy();
  });

  it("asks one cached question per sol and never more than three at a time", async () => {
    let active = 0;
    let peak = 0;
    const m = routes({
      [CAMERAS]: async (url: URL) => {
        active++;
        peak = Math.max(peak, active);
        await new Promise((r) => setTimeout(r, 5));
        active--;
        return cameras(url);
      },
    });
    open();
    await screen.findByRole("table");
    await waitFor(() =>
      expect(screen.getAllByRole("link", { name: /Photos of the tube/ })).toHaveLength(7),
    );
    const asked = m.calls
      .filter((c) => c.pathname === CAMERAS)
      .map((c) => c.searchParams.get("sol"));
    expect(new Set(asked).size).toBe(asked.length); // one request per sol
    expect(asked).toHaveLength(8); // 8 distinct sealed sols, none for the TBD rows
    expect(peak).toBeLessThanOrEqual(3);
  });

  it("asks nothing about tubes until a row comes near the screen", async () => {
    const watchers: ((e: { isIntersecting: boolean }[]) => void)[] = [];
    vi.stubGlobal(
      "IntersectionObserver",
      class {
        constructor(cb: (e: { isIntersecting: boolean }[]) => void) {
          watchers.push(cb);
        }
        observe() {}
        disconnect() {}
      },
    );
    const m = routes();
    open();
    await screen.findByRole("table");
    // every sealed row has registered with the observer (effects run after paint, so wait for it)
    await waitFor(() => expect(watchers).toHaveLength(8), { timeout: 3000 });
    expect(m.calls.filter((c) => c.pathname === CAMERAS)).toHaveLength(0);
    watchers.forEach((cb) => cb([{ isIntersecting: true }]));
    await waitFor(() => expect(m.calls.filter((c) => c.pathname === CAMERAS)).toHaveLength(8), {
      timeout: 3000,
    });
  });

  it("leaves the tube link out when the cameras request fails", async () => {
    const m = routes({ [CAMERAS]: apiError(503, "db_unavailable", "Database is unavailable") });
    open();
    await screen.findByRole("table");
    await waitFor(() => expect(m.calls.filter((c) => c.pathname === CAMERAS)).toHaveLength(8));
    await new Promise((r) => setTimeout(r, 20)); // let the failed answers reach the links
    await waitFor(() =>
      expect(screen.getAllByText(/Photos of sol 1215/).length).toBeGreaterThan(0),
    );
    expect(screen.queryByRole("link", { name: /Photos of the tube/ })).toBeNull();
  });

  it("opens #sample-N, resetting filters that hide the row, and highlights it", async () => {
    routes();
    open("/rovers/perseverance/samples#sample-9");
    await screen.findByRole("table");
    await waitFor(() => expect(document.getElementById("sample-9")?.className).toBe("target"));
    // a filter that hides the row is dropped when the address points at it
    fireEvent.click(screen.getByRole("button", { name: /On rover/ }));
    expect(document.getElementById("sample-9")).toBeNull();
    navigate("/rovers/perseverance/samples#sample-9"); // the same address again, as a link click
    await waitFor(() => expect(document.getElementById("sample-9")).toBeTruthy());
    expect(rowNumbers()).toHaveLength(10);
  });

  it("links a placed sample to the route map", async () => {
    routes();
    open();
    await screen.findByRole("table");
    const row = document.getElementById("sample-25")!;
    const link = within(row).getByRole("link", { name: "Show on route map" });
    const loc = byNumber(25).location!;
    expect(link.getAttribute("href")).toBe(
      `/rovers/perseverance/map?sol=${loc.sol}&site=${loc.site}&drive=${loc.drive}&from_sol=${loc.sol}&to_sol=${loc.sol}`,
    );
    expect(
      within(document.getElementById("sample-30")!).queryByRole("link", {
        name: "Show on route map",
      }),
    ).toBeNull();
  });

  it("shows the empty state when nothing is loaded yet", async () => {
    routes({ "/api/v2/rovers/perseverance/samples": samplesResponse([]) });
    open();
    expect(await screen.findByText("No samples loaded yet")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Open NASA’s sample page/ })).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("shows a loading state, then the 503 state with a retry that works", async () => {
    let n = 0;
    routes({
      "/api/v2/rovers/perseverance/samples": () => {
        n++;
        return n === 1
          ? apiError(503, "db_unavailable", "Database is unavailable")
          : samplesResponse(items);
      },
    });
    open();
    expect((await screen.findByRole("alert")).textContent).toMatch(/temporarily unavailable/i);
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("table")).toBeTruthy();
  });

  it("says that other rovers have no sample list", async () => {
    routes();
    open("/rovers/curiosity/samples");
    expect(await screen.findByText("Curiosity has no sample list")).toBeTruthy();
  });
});

describe("Samples entry points", () => {
  it("replaces 'Samples' in the main navigation with 'Data'; the Perseverance card keeps its Samples button", async () => {
    routes({
      "/api/v2/rovers/perseverance/photos/latest": { sol: 2031, ...page([], 0) },
    });
    window.history.replaceState(null, "", "/");
    render(<App />);
    const nav = await screen.findByRole("navigation", { name: "Main" });
    await waitFor(() => expect(within(nav).getByRole("button", { name: /^Data/ })).toBeTruthy());
    expect(within(nav).queryByRole("link", { name: "Samples" })).toBeNull();
    // the button on the Perseverance card stays
    const links = screen.getAllByRole("link", { name: "Samples" });
    expect(links).toHaveLength(1);
    expect(links[0]!.getAttribute("href")).toBe("/rovers/perseverance/samples");
    expect(nav.contains(links[0]!)).toBe(false);
  });

  it("has no Samples item without Perseverance", async () => {
    mockApi({
      "/api/v2/rovers": { items: [curiosity] },
      "/api/v2/rovers/curiosity/photos/latest": { sol: 5066, ...page([], 0) },
    });
    window.history.replaceState(null, "", "/");
    render(<App />);
    const nav = await screen.findByRole("navigation", { name: "Main" });
    await screen.findByRole("link", { name: /Latest sol 5066/ });
    expect(within(nav).queryByRole("link", { name: "Samples" })).toBeNull();
  });
});

// ----- route map -----
const wp = (sol: number, site: number, drive: number, lon: number, lat: number): Waypoint => ({
  sol,
  site,
  drive,
  lon,
  lat,
  elev_geoid: -2489.7,
  easting: null,
  northing: null,
  roll: null,
  pitch: null,
  yaw: null,
  tilt: null,
  dist_m: 112.4,
  dist_total_m: 33874.9,
});
const stops = [
  wp(10, 1, 0, 77.0, 18.0),
  wp(20, 2, 4, 77.002, 18.001),
  wp(30, 3, 8, 77.003, 18.002),
  wp(40, 4, 12, 77.004, 18.004),
];
const at = (w: Waypoint, match: SampleLocation["match"]): SampleLocation => ({
  sol: w.sol,
  site: w.site,
  drive: w.drive,
  lat: w.lat,
  lon: w.lon,
  elev_geoid: w.elev_geoid,
  match,
});
const withLoc = (n: number, loc: SampleLocation | null): Sample => ({
  ...byNumber(n),
  location: loc,
});
// 25 exact at sol 20; 2 and 3 share sol 30 (3 is by_sol); 1 alone at sol 10 (by_sol, approximate); 29 not placed.
const placed = [
  withLoc(1, at(stops[0]!, "by_sol")),
  withLoc(25, at(stops[1]!, "exact")),
  withLoc(2, at(stops[2]!, "nearest_before")),
  withLoc(3, at(stops[2]!, "by_sol")),
  withLoc(29, null),
];

function mapRoutes(list: Sample[] | null = placed) {
  return mockApi({
    "/api/v2/rovers": { items: [{ ...perseverance, max_sol: 50 }] },
    "/api/v2/rovers/perseverance/traverse": {
      type: "FeatureCollection",
      rover: "perseverance",
      available: true,
      updated_at: "2026-10-28T15:05:03Z",
      source_max_sol: 40,
      features: [
        {
          type: "Feature",
          geometry: {
            type: "LineString",
            coordinates: [
              [77.0, 18.0],
              [77.004, 18.004],
            ],
          },
          properties: { sol: 40, length_m: 20 },
        },
      ],
    },
    "/api/v2/rovers/perseverance/location": (url: URL) => ({
      rover: "perseverance",
      available: true,
      updated_at: "2026-10-28T15:05:06Z",
      source_max_sol: 40,
      items: url.searchParams.get("latest") === "true" ? [] : stops,
    }),
    "/api/v2/rovers/perseverance/samples":
      list === null
        ? apiError(503, "db_unavailable", "Database is unavailable")
        : samplesResponse(list),
    [CAMERAS]: cameras,
  });
}

describe("Route map: samples", () => {
  it("draws one triangle per stop with a count, approximate ones dashed, and a legend", async () => {
    mapRoutes();
    open("/rovers/perseverance/map");
    await screen.findByRole("table");
    const svg = screen.getByRole("img", { name: /route with 4 stops/ });
    const marks = [...svg.querySelectorAll("g.smp-g")];
    expect(marks.map((g) => g.getAttribute("data-stop"))).toEqual(["10/1/0", "20/2/4", "30/3/8"]);
    const cls = marks.map((g) => g.querySelector("path")!.getAttribute("class"));
    expect(cls).toEqual(["smp smp-apx", "smp", "smp"]); // sol 30 mixes nearest_before and by_sol
    expect(marks[2]!.textContent).toBe("2");
    expect(marks[0]!.textContent).toBe("");
    const legend = screen.getByRole("group", { name: "Legend" });
    expect(within(legend).getByText("Sample sealed here")).toBeTruthy();
    expect(within(legend).getByText("Sample, approximate stop")).toBeTruthy();
    expect(screen.getByText(/▲ 4 samples shown at 3 stops; 1 not placed/)).toBeTruthy();
    const tabs = screen.getByRole("navigation", { name: "Views of Perseverance" });
    expect(within(tabs).getByRole("link", { name: "Samples" })).toBeTruthy();
  });

  it("toggles the markers", async () => {
    mapRoutes();
    open("/rovers/perseverance/map");
    await screen.findByRole("table");
    const svg = screen.getByRole("img", { name: /route with 4 stops/ });
    fireEvent.click(screen.getByRole("checkbox", { name: "Samples" }));
    expect(svg.querySelectorAll("g.smp-g")).toHaveLength(0);
    expect(screen.queryByText("Sample sealed here")).toBeNull();
    fireEvent.click(screen.getByRole("checkbox", { name: "Samples" }));
    expect(svg.querySelectorAll("g.smp-g")).toHaveLength(3);
  });

  it("lists the samples of the selected stop with the right caveat for each placement", async () => {
    mapRoutes();
    open("/rovers/perseverance/map?sol=30&site=3&drive=8");
    expect(await screen.findByText("2 samples sealed here")).toBeTruthy();
    const link = screen.getByRole("link", { name: `Sample 2 — ${byNumber(2).name}` });
    expect(link.getAttribute("href")).toBe("/rovers/perseverance/samples#sample-2");
    expect(
      screen.getByText(/Sealed at the same site, a little further on than this stop\./),
    ).toBeTruthy();
    expect(
      screen.getByText(
        /Nearest published stop to the sealing sol; the rover may have moved since\. Stop of sol 30\./,
      ),
    ).toBeTruthy();
    // sample 3 (sol 196) has CACHECAM; the tube link is there
    expect(
      await screen.findByRole("link", { name: /Photos of the tube: sample 3, sol 196/ }),
    ).toBeTruthy();
    // the stop list marks stops that have samples
    expect(
      screen.getByRole("button", { name: /Select stop: sol 30.*2 sample\(s\) sealed here/ }),
    ).toBeTruthy();
  });

  it("says 'Sealed at this stop' for an exact placement and shows nothing for a stop without samples", async () => {
    mapRoutes();
    open("/rovers/perseverance/map?sol=20&site=2&drive=4");
    expect(await screen.findByRole("heading", { name: "Sample sealed here" })).toBeTruthy();
    expect(screen.getByText(/^Sealed at this stop\./)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Select stop: sol 40/ }));
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Sample sealed here" })).toBeNull(),
    );
  });

  it("works without markers when the sample list fails", async () => {
    mapRoutes(null);
    open("/rovers/perseverance/map");
    await screen.findByRole("table");
    expect(document.querySelectorAll("g.smp-g")).toHaveLength(0);
    expect(screen.queryByRole("checkbox", { name: "Samples" })).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

// ----- photo card -----
describe("Photo card: sample block", () => {
  function photoRoutes(over: Record<string, unknown>, id = 700) {
    const m = mockApi({
      "/api/v2/rovers": { items: [perseverance] },
      "/api/v2/rovers/perseverance/samples": samplesResponse(items),
      "/api/v2/rovers/perseverance/photos": page([photo(id)], 1),
      [CAMERAS]: cameras,
      ...over,
    });
    window.history.replaceState(null, "", `/photos/${id}`);
    render(<App />);
    return m;
  }

  it("names the sample from the cached list and links to it", async () => {
    photoRoutes({ "/api/v2/photos/700": detail(700, { sol: 1215, samples: [25] }) });
    const link = await screen.findByRole("link", { name: /Sample 25 — Sapphire Canyon/ });
    expect(link.getAttribute("href")).toBe("/rovers/perseverance/samples#sample-25");
    expect(screen.getByRole("heading", { level: 2, name: "Sample" })).toBeTruthy();
    expect(
      screen.getByText(
        /Sealed on this sol \(2024-07-21\), feature Cheyava Falls, Neretva Vallis\./,
      ),
    ).toBeTruthy();
    expect(screen.getByText(/Linked by sol only/)).toBeTruthy();
    expect(screen.getByText("On rover")).toBeTruthy();
    expect(
      await screen.findByRole("link", { name: /^Photos of the tube \(CACHECAM\)/ }),
    ).toBeTruthy();
    expect(screen.getByRole("link", { name: "Photos of sol 1215" }).getAttribute("href")).toBe(
      "/rovers/perseverance?sol=1215",
    );
    expect(screen.getByRole("link", { name: "All samples" })).toBeTruthy();
  });

  it("offers the sol link only when CACHECAM has no photos that sol", async () => {
    const m = photoRoutes({ "/api/v2/photos/701": detail(701, { sol: 377, samples: [9] }) }, 701);
    await screen.findByRole("link", { name: /Sample 9 — Ats/ });
    await screen.findByRole("link", { name: "Photos of sol 377" });
    await waitFor(() =>
      expect(
        m.calls.some((c) => c.pathname === CAMERAS && c.searchParams.get("sol") === "377"),
      ).toBe(true),
    );
    await new Promise((r) => setTimeout(r, 20)); // the answer has been handled
    expect(screen.queryByRole("link", { name: /Photos of the tube/ })).toBeNull();
  });

  it("falls back to the number when the sample list cannot be loaded", async () => {
    photoRoutes(
      {
        "/api/v2/photos/702": detail(702, { sol: 1215, samples: [25] }),
        "/api/v2/rovers/perseverance/samples": apiError(503, "db_unavailable", "x"),
      },
      702,
    );
    expect(await screen.findByRole("link", { name: "Sample 25" })).toBeTruthy();
  });

  it("renders no block when the server sends no samples field at all", async () => {
    const d = detail(704);
    delete (d as { samples?: number[] }).samples;
    photoRoutes({ "/api/v2/photos/704": d }, 704);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByRole("heading", { level: 2, name: /^Samples?$/ })).toBeNull();
  });

  it("renders no block, not even a heading, without samples", async () => {
    photoRoutes({ "/api/v2/photos/703": detail(703, { samples: [] }) }, 703);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByRole("heading", { level: 2, name: /^Samples?$/ })).toBeNull();
    // and an older server without the field behaves the same
    expect(screen.queryByText(/Linked by sol only/)).toBeNull();
  });
});
