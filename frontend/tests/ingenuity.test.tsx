import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { App } from "../src/App";
import type { Flight, Traverse, Waypoint } from "../src/api/types";
import { stopFilter, stopLabel, stopPhotosHref } from "../src/lib/maplinks";
import { listContext, parseFrom } from "../src/pages/Photo";
import { apiError, detail, ingenuity, mockApi, page, perseverance, photo } from "./helpers";

const flights = (
  JSON.parse(readFileSync("tests/fixtures/ingenuity-flights.json", "utf-8")) as {
    items: Flight[];
  }
).items;
const flight9 = flights.find((f) => f.flight === 9)!;

function open(path: string) {
  window.history.replaceState(null, "", path);
  return render(<App />);
}

describe("Ingenuity on the home page", () => {
  it("shows a finished, imported card with the flight count and a flights button", async () => {
    mockApi({
      "/api/v2/rovers": { items: [perseverance, ingenuity] },
      "/api/v2/rovers/perseverance/photos/latest": { sol: null, ...page([]) },
      "/api/v2/rovers/ingenuity/flights": { items: flights },
    });
    open("/");
    const button = await screen.findByRole("link", { name: "5 flights" });
    expect(button.getAttribute("href")).toBe("/rovers/ingenuity/flights");
    const card = button.closest("article")!;
    expect(within(card).getByText("Mission complete")).toBeTruthy();
    expect(within(card).getByText("Imported")).toBeTruthy();
    expect(within(card).getByText(/one-off archive, not updated/)).toBeTruthy();
    expect(within(card).getByText("Flights").nextElementSibling?.textContent).toBe("5");
    expect(within(card).getByRole("link", { name: "Photos" }).getAttribute("href")).toBe(
      "/rovers/ingenuity",
    );
    expect(within(card).queryByRole("link", { name: "Route map" })).toBeNull();
  });

  it("still shows the card when the flight list fails", async () => {
    mockApi({
      "/api/v2/rovers": { items: [perseverance, ingenuity] },
      "/api/v2/rovers/perseverance/photos/latest": { sol: null, ...page([]) },
      "/api/v2/rovers/ingenuity/flights": apiError(503, "db_unavailable", "down"),
    });
    open("/");
    const card = (await screen.findByRole("heading", { name: "Ingenuity" })).closest("article")!;
    expect(within(card).getByRole("link", { name: "Flights" })).toBeTruthy();
  });
});

describe("Photo card of Ingenuity", () => {
  const heli = (over = {}) =>
    detail(500, {
      rover: "ingenuity",
      sol: 133,
      camera: { code: "HELI_RTE", name: "Helicopter Return-to-Earth Camera" },
      credit: "NASA/JPL-Caltech",
      site: null,
      drive: null,
      location: null,
      ...over,
    });

  it("shows the flight badge and block with details from /flights/{n}", async () => {
    const m = mockApi({
      "/api/v2/rovers": { items: [perseverance, ingenuity] },
      "/api/v2/photos/500": heli({ flight: 9 }),
      "/api/v2/rovers/ingenuity/flights/9": flight9,
      "/api/v2/rovers/ingenuity/photos": page([], 0),
    });
    open("/photos/500");
    const badge = (await screen.findAllByRole("link", { name: "Flight 9" }))[0]!;
    expect(badge.className).toContain("badge");
    const block = (await screen.findByRole("heading", { name: "Flight" })).closest("section")!;
    await waitFor(() => expect(block.textContent).toContain("Airfield E"));
    expect(block.textContent).toContain("Airfield F");
    expect(within(block).getByText(/625 m · max altitude 10 m/)).toBeTruthy();
    expect(within(block).getByText("2:46 (166.4 s)")).toBeTruthy();
    expect(within(block).getByText(/link is by sol only/)).toBeTruthy();
    expect(
      within(block)
        .getAllByRole("link", { name: "Flight 9" })
        .some((a) => a.getAttribute("href") === "/rovers/ingenuity/flights#flight-9"),
    ).toBe(true);
    expect(
      within(block).getByRole("link", { name: "All photos of sol 133" }).getAttribute("href"),
    ).toBe("/rovers/ingenuity?sol=133");
    expect(m.calls.some((c) => c.pathname === "/api/v2/rovers/ingenuity/flights/9")).toBe(true);
    expect(screen.queryByRole("heading", { name: "Location" })).toBeNull();
    expect(screen.getAllByText("NASA/JPL-Caltech").length).toBeGreaterThan(0); // credit stays
  });

  it("keeps the flight number when the details cannot be loaded", async () => {
    mockApi({
      "/api/v2/rovers": { items: [perseverance, ingenuity] },
      "/api/v2/photos/500": heli({ flight: 9 }),
      "/api/v2/rovers/ingenuity/flights/9": apiError(503, "db_unavailable", "down"),
      "/api/v2/rovers/ingenuity/photos": page([], 0),
    });
    open("/photos/500");
    const block = (await screen.findByRole("heading", { name: "Flight" })).closest("section")!;
    await waitFor(() => expect(within(block).queryByText(/Details/)).toBeNull());
    expect(within(block).getAllByRole("link", { name: "Flight 9" })).toHaveLength(1);
    expect(within(block).queryByText("Airfield E")).toBeNull();
  });

  it("says 'No flight on this sol' when flight is null", async () => {
    mockApi({
      "/api/v2/rovers": { items: [perseverance, ingenuity] },
      "/api/v2/photos/500": heli({ flight: null }),
      "/api/v2/rovers/ingenuity/photos": page([], 0),
    });
    open("/photos/500");
    expect(await screen.findByText(/No flight on this sol/)).toBeTruthy();
    expect(screen.queryByRole("link", { name: /^Flight \d+$/ })).toBeNull();
  });

  it("shows no flight block for other rovers", async () => {
    mockApi({
      "/api/v2/rovers": { items: [perseverance, ingenuity] },
      "/api/v2/photos/81234": detail(81234, { flight: null }),
      "/api/v2/rovers/perseverance/photos": page([], 0),
    });
    open("/photos/81234");
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByRole("heading", { name: "Flight" })).toBeNull();
    expect(screen.queryByText(/No flight on this sol/)).toBeNull();
  });
});

describe("Route map of Ingenuity", () => {
  it("shows the 'no location data' state for available: false", async () => {
    mockApi({
      "/api/v2/rovers": { items: [perseverance, ingenuity] },
      "/api/v2/rovers/ingenuity/traverse": {
        type: "FeatureCollection",
        rover: "ingenuity",
        available: false,
        updated_at: null,
        source_max_sol: null,
        features: [],
      } satisfies Traverse,
      "/api/v2/rovers/ingenuity/location": (url: URL) => {
        expect(url.searchParams.get("latest")).toBeNull(); // no latest strip without data
        return {
          rover: "ingenuity",
          available: false,
          updated_at: null,
          source_max_sol: null,
          items: [],
        };
      },
    });
    open("/rovers/ingenuity/map");
    expect(await screen.findByText("No location data for Ingenuity")).toBeTruthy();
    expect(screen.getByText(/The helicopter has no route of its own/)).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Latest position" })).toBeNull();
    expect(
      within(screen.getByRole("navigation", { name: "Views of Ingenuity" }))
        .getByRole("link", { name: "Flights" })
        .getAttribute("href"),
    ).toBe("/rovers/ingenuity/flights");
  });
});

describe("Stop photos recipe", () => {
  const stops = [
    { site: 93, drive: 0 },
    { site: 93, drive: 412 },
    { site: 94, drive: 10 },
    { site: 93, drive: 600 },
    { site: 95, drive: 0 },
  ];

  it("ends the range one drive before the next stop of the same site", () => {
    expect(stopFilter(stops, { site: 93, drive: 0 })).toEqual({
      site: 93,
      drive_from: 0,
      drive_to: 411,
    });
    expect(stopFilter(stops, { site: 93, drive: 412 })).toEqual({
      site: 93,
      drive_from: 412,
      drive_to: 599,
    });
  });

  it("leaves drive_to out for the last stop of a site", () => {
    expect(stopFilter(stops, { site: 93, drive: 600 })).toEqual({ site: 93, drive_from: 600 });
    expect(stopFilter(stops, { site: 94, drive: 10 })).toEqual({ site: 94, drive_from: 10 });
  });

  it("builds the photo browser address and the chip label", () => {
    expect(stopPhotosHref("perseverance", stops, { site: 93, drive: 412 })).toBe(
      "/rovers/perseverance?site=93&drive_from=412&drive_to=599",
    );
    expect(stopPhotosHref("perseverance", stops, { site: 93, drive: 600 })).toBe(
      "/rovers/perseverance?site=93&drive_from=600",
    );
    expect(stopLabel({ site: 93, drive_from: 412, drive_to: 599 })).toBe("Stop 93/412–599");
    expect(stopLabel({ site: 93, drive_from: 600 })).toBe("Stop 93/600+");
    expect(stopLabel({ site: 93, drive_from: 7, drive_to: 7 })).toBe("Stop 93/7");
  });

  it("treats a list of a stop like an Earth-date list for the photo card", () => {
    const from = "/rovers/perseverance?site=93&drive_from=412&drive_to=599";
    expect(parseFrom(from)?.query).toMatchObject({
      site: "93",
      drive_from: "412",
      drive_to: "599",
    });
    expect(listContext(from).byDate).toBe(true);
    // the range alone (without `site`) is passed on too: the API answers 422
    expect(parseFrom("/rovers/perseverance?drive_from=1")?.query.drive_from).toBe("1");
  });
});

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
  wp(20, 1, 4, 77.002, 18.001),
  wp(30, 2, 8, 77.003, 18.002),
  wp(40, 2, 12, 77.004, 18.004),
];
const location = (items: Waypoint[], max = 40) => ({
  rover: "perseverance",
  available: true,
  updated_at: "2026-10-28T15:05:06Z",
  source_max_sol: max,
  items,
});
const traverse: Traverse = {
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
      properties: { sol: 20, length_m: 20 },
    },
  ],
};

function mapRoutes(maxSol = 50) {
  return mockApi({
    "/api/v2/rovers": { items: [{ ...perseverance, max_sol: maxSol }, ingenuity] },
    "/api/v2/rovers/perseverance/traverse": traverse,
    "/api/v2/rovers/perseverance/location": (url: URL) =>
      url.searchParams.get("latest") === "true" ? location([stops[3]!]) : location(stops),
  });
}

describe("Route map: stop photos and the latest position", () => {
  it("adds 'Photos at this stop' with the contract recipe next to 'Photos of sol N'", async () => {
    mapRoutes();
    open("/rovers/perseverance/map");
    await screen.findByRole("table");
    fireEvent.click(screen.getByRole("button", { name: "Select stop: sol 10, site 1, drive 0" }));
    const panel = screen.getByRole("region", { name: "Selected stop" });
    expect(within(panel).getByRole("link", { name: "Photos of sol 10" })).toBeTruthy();
    // next stop of site 1 is drive 4: range 0..3
    expect(
      within(panel).getByRole("link", { name: "Photos at this stop" }).getAttribute("href"),
    ).toBe("/rovers/perseverance?site=1&drive_from=0&drive_to=3");
    // last stop of site 2: no drive_to
    fireEvent.click(screen.getByRole("button", { name: "Select stop: sol 40, site 2, drive 12" }));
    expect(
      within(panel).getByRole("link", { name: "Photos at this stop" }).getAttribute("href"),
    ).toBe("/rovers/perseverance?site=2&drive_from=12");
  });

  it("shows the latest position from ?latest=true with a note when photos are ahead", async () => {
    const m = mapRoutes(52);
    open("/rovers/perseverance/map");
    const strip = await screen.findByRole("region", { name: "Latest position" });
    expect(m.calls.some((c) => c.searchParams.get("latest") === "true")).toBe(true);
    expect(within(strip).getByText("Sol 40 · site 2, drive 12")).toBeTruthy();
    expect(within(strip).getByText(/Latest published stop; photos reach sol 52\./)).toBeTruthy();
    expect(within(strip).getByRole("link", { name: "Photos of sol 40" }).getAttribute("href")).toBe(
      "/rovers/perseverance?sol=40",
    );
    await screen.findByRole("table");
    fireEvent.click(within(strip).getByRole("button", { name: "Show on map" }));
    const panel = screen.getByRole("region", { name: "Selected stop" });
    expect(within(panel).getByText("2 / 12")).toBeTruthy();
  });

  it("hides the strip, without an error, when the latest request fails", async () => {
    mockApi({
      "/api/v2/rovers": { items: [{ ...perseverance, max_sol: 40 }] },
      "/api/v2/rovers/perseverance/traverse": traverse,
      "/api/v2/rovers/perseverance/location": (url: URL) =>
        url.searchParams.get("latest") === "true"
          ? apiError(503, "db_unavailable", "down")
          : location(stops),
    });
    open("/rovers/perseverance/map");
    await screen.findByRole("table");
    expect(screen.queryByRole("region", { name: "Latest position" })).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("links Flights from Perseverance's map", async () => {
    mapRoutes();
    open("/rovers/perseverance/map");
    await screen.findByRole("table");
    const tabs = screen.getByRole("navigation", { name: "Views of Perseverance" });
    expect(within(tabs).getByRole("link", { name: "Flights" }).getAttribute("href")).toBe(
      "/rovers/ingenuity/flights",
    );
  });
});

describe("Home to Flights", () => {
  it("fetches the flight list once for the card and the page, and uses 'flight' for one", async () => {
    const m = mockApi({
      "/api/v2/rovers": { items: [perseverance, ingenuity] },
      "/api/v2/rovers/perseverance/photos/latest": { sol: null, ...page([]) },
      "/api/v2/rovers/ingenuity/flights": { items: flights.slice(0, 1) },
    });
    open("/");
    fireEvent.click(await screen.findByRole("link", { name: "1 flight" }));
    await screen.findByRole("table");
    expect(m.calls.filter((c) => c.pathname === "/api/v2/rovers/ingenuity/flights")).toHaveLength(
      1,
    );
  });

  it("takes the photo card's flight from the loaded list (no /flights/{n} request)", async () => {
    const m = mockApi({
      "/api/v2/rovers": { items: [perseverance, ingenuity] },
      "/api/v2/rovers/ingenuity/flights": { items: flights },
      "/api/v2/photos/500": detail(500, {
        rover: "ingenuity",
        sol: 133,
        flight: 9,
        site: null,
        drive: null,
      }),
      "/api/v2/rovers/ingenuity/photos": page([], 0),
    });
    open("/rovers/ingenuity/flights");
    await screen.findByRole("table");
    open("/photos/500");
    const block = (await screen.findByRole("heading", { name: "Flight" })).closest("section")!;
    await waitFor(() => expect(block.textContent).toContain("Airfield E"));
    expect(m.calls.some((c) => c.pathname.endsWith("/flights/9"))).toBe(false);
    expect(screen.queryByText("Site / drive")).toBeNull();
  });
});

describe("Photo browser with a stop filter", () => {
  const cameras = { items: [{ code: "NAVCAM_LEFT", name: "Left Navcam", total_photos: 9 }] };
  function browser(items = [photo(1), photo(2, { sol: 2030 })], total = items.length) {
    return mockApi({
      "/api/v2/rovers": { items: [perseverance] },
      "/api/v2/rovers/perseverance/cameras": cameras,
      "/api/v2/rovers/perseverance/photos": page(items, total),
    });
  }

  it("passes site and the drive range, lists all sols and shows a clearable chip", async () => {
    const m = browser();
    open("/rovers/perseverance?site=93&drive_from=412&drive_to=599");
    const chip = await screen.findByRole("button", { name: "Stop 93/412–599: clear this filter" });
    await screen.findByRole("heading", { level: 2, name: "Stop 93/412–599" });
    await screen.findAllByRole("link", { name: /sol 2031/ });
    const q = m.last("/api/v2/rovers/perseverance/photos")!.searchParams;
    expect(q.get("site")).toBe("93");
    expect(q.get("drive_from")).toBe("412");
    expect(q.get("drive_to")).toBe("599");
    expect(q.get("sol")).toBeNull(); // not the latest sol: a stop spans sols
    // camera counts are mission-wide, not for one sol
    expect(
      m.calls
        .filter((c) => c.pathname.endsWith("/cameras"))
        .every((c) => !c.searchParams.get("sol")),
    ).toBe(true);
    // photo tiles carry the stop list as their context
    const tile = (await screen.findAllByRole("link", { name: /sol 2031/ }))[0]!;
    expect(decodeURIComponent(tile.getAttribute("href")!)).toContain(
      "from=/rovers/perseverance?site=93&drive_from=412&drive_to=599",
    );
    fireEvent.click(chip);
    await waitFor(() => expect(window.location.search).not.toContain("site="));
  });

  it("keeps the stop when another filter changes, and omits drive_to for the last stop", async () => {
    const m = browser();
    open("/rovers/perseverance?site=93&drive_from=600");
    await screen.findByRole("button", { name: "Stop 93/600+: clear this filter" });
    await screen.findAllByRole("link", { name: /sol 2031/ });
    expect(m.last("/api/v2/rovers/perseverance/photos")!.searchParams.has("drive_to")).toBe(false);
    fireEvent.click(await screen.findByLabelText("Full"));
    await waitFor(() => expect(window.location.search).toContain("sample_type=full"));
    expect(window.location.search).toContain("site=93&drive_from=600");
    await waitFor(() =>
      expect(m.last("/api/v2/rovers/perseverance/photos")!.searchParams.get("site")).toBe("93"),
    );
  });

  it("accepts a bare site or a drive_to only: chip, title and the parameters as typed", async () => {
    const m = browser();
    open("/rovers/perseverance?site=93&drive_to=10");
    const chip = await screen.findByRole("button", { name: "Stop 93/0–10: clear this filter" });
    expect(chip.getAttribute("title")).toBe("Photos with site 93, drive 0 to 10");
    await waitFor(() =>
      expect(m.last("/api/v2/rovers/perseverance/photos")?.searchParams.get("drive_to")).toBe("10"),
    );
    expect(m.last("/api/v2/rovers/perseverance/photos")!.searchParams.has("drive_from")).toBe(
      false,
    );
    cleanup();
    open("/rovers/perseverance?site=93");
    expect(await screen.findByRole("button", { name: "Stop 93: clear this filter" })).toBeTruthy();
    expect(await screen.findByRole("heading", { level: 2, name: "Stop 93" })).toBeTruthy();
  });

  it("sends an invalid stop as typed so the API's 422 is shown", async () => {
    mockApi({
      "/api/v2/rovers": { items: [perseverance] },
      "/api/v2/rovers/perseverance/cameras": cameras,
      "/api/v2/rovers/perseverance/photos": (url: URL) => {
        expect(url.searchParams.get("site")).toBe("abc");
        return apiError(422, "validation_error", "Invalid request", [
          { field: "site", message: "Input should be a valid integer" },
        ]);
      },
    });
    open("/rovers/perseverance?site=abc&drive_from=-1");
    expect((await screen.findAllByText(/valid integer/)).length).toBeGreaterThan(0);
  });

  it("offers a reset when a stop has no photos", async () => {
    browser([], 0);
    open("/rovers/perseverance?site=93&drive_from=412&drive_to=599");
    expect(await screen.findByText("No photos match these filters")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Reset filters" }));
    await waitFor(() => expect(window.location.search).not.toContain("site="));
  });

  it("walks the stop list from the photo card", async () => {
    const m = mockApi({
      "/api/v2/rovers": { items: [perseverance] },
      "/api/v2/photos/2": detail(2, {
        in_sol: { position: 1, total: 9, newer_id: null, older_id: 7 },
      }),
      "/api/v2/rovers/perseverance/photos": page([photo(1), photo(2), photo(3)], 3),
    });
    const from = "/rovers/perseverance?site=93&drive_from=412&drive_to=599";
    open(`/photos/2?from=${encodeURIComponent(from)}`);
    expect(await screen.findByText("2 of 3 in this list")).toBeTruthy();
    expect(m.last("/api/v2/rovers/perseverance/photos")!.searchParams.get("site")).toBe("93");
    expect(m.last("/api/v2/photos/2")!.searchParams.has("camera")).toBe(false);
  });
});
