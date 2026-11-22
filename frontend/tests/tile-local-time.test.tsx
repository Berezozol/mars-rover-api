// Follow-up: local solar time on Search tiles (`local_time`) and the nearest stop on "near" tiles (`near_stop`).
// Written against docs/API.md. Newer fields are attached through a loose cast so the tests also run against
// an older `Photo` type.
import { render, screen, waitFor, within } from "@testing-library/preact";
import { describe, expect, it } from "vitest";
import { App } from "../src/App";
import curiosityLocation from "./fixtures/curiosity-location.json";
import curiosityTraverse from "./fixtures/curiosity-traverse.json";
import { curiosity, ingenuity, mockApi, page, perseverance, photo, spirit } from "./helpers";

const PHOTOS = "/api/v2/photos";
const CREDIT = "NASA/JPL-Caltech/LANL/CNES/IRAP";
const NEAR = "/rovers/curiosity/map?near=-4.7331,137.3985&radius_km=2";

type Extra = {
  local_time?: { time: string; kind: "lmst" | "ltst"; planet: "mars" } | null;
  near_stop?: {
    sol: number;
    site: number;
    drive: number;
    lat: number;
    lon: number;
    distance_km: number;
  } | null;
};
const withExtra = (id: number, extra: Extra) =>
  ({ ...photo(id), ...extra }) as unknown as ReturnType<typeof photo>;

const LMST = { time: "17:40:12", kind: "lmst", planet: "mars" } as const;
const LTST = { time: "06:05:59", kind: "ltst", planet: "mars" } as const;
const STOP = { sol: 45, site: 93, drive: 1840, lat: -4.59, lon: 137.44, distance_km: 0.42 };

function setupSearch(items: ReturnType<typeof photo>[]) {
  mockApi({
    "/api/v2/rovers": { items: [perseverance, curiosity, spirit, ingenuity] },
    "/api/v2/rovers/perseverance/cameras": { items: [] },
    "/api/v2/rovers/curiosity/cameras": { items: [] },
    "/api/v2/rovers/spirit/cameras": { items: [] },
    "/api/v2/rovers/ingenuity/cameras": { items: [] },
    [PHOTOS]: page(items, items.length),
  });
  window.history.replaceState(null, "", "/search");
  render(<App />);
}

function setupNear(items: ReturnType<typeof photo>[]) {
  mockApi({
    "/api/v2/rovers": { items: [perseverance, curiosity, spirit, ingenuity] },
    "/api/v2/rovers/curiosity/traverse": curiosityTraverse,
    "/api/v2/rovers/curiosity/location": curiosityLocation,
    "/api/v2/rovers/curiosity/cameras": { items: [] },
    [PHOTOS]: page(items, items.length),
  });
  window.history.replaceState(null, "", NEAR);
  render(<App />);
}

describe("Search tiles: local solar time", () => {
  it("shows HH:MM and the upper-case kind, with an accessible mention of Mars", async () => {
    setupSearch([withExtra(1, { local_time: LMST }), withExtra(2, { local_time: LTST })]);
    const results = await screen.findByTestId("search-results");
    const stamps = await within(results).findAllByTestId("tile-local-time");
    expect(stamps).toHaveLength(2);
    expect(stamps[0]!.textContent).toMatch(/17:40\s+LMST/);
    expect(stamps[1]!.textContent).toMatch(/06:05\s+LTST/);
    // seconds are not shown
    expect(stamps[0]!.textContent).not.toContain("17:40:12");
    for (const s of stamps) {
      const text = `${s.getAttribute("title") ?? ""} ${s.getAttribute("aria-label") ?? ""} ${s.textContent}`;
      expect(text).toMatch(/mars/i);
    }
  });

  it("is absent when local_time is null or missing (older server); credit stays on every tile", async () => {
    setupSearch([
      withExtra(1, { local_time: LMST }),
      withExtra(2, { local_time: null }),
      photo(3), // field missing altogether
    ]);
    const results = await screen.findByTestId("search-results");
    await waitFor(() => expect(within(results).getAllByText(CREDIT)).toHaveLength(3));
    expect(within(results).getAllByTestId("tile-local-time")).toHaveLength(1);
  });

  it("no tile shows a time when every local_time is null", async () => {
    setupSearch([withExtra(1, { local_time: null }), withExtra(2, { local_time: null })]);
    const results = await screen.findByTestId("search-results");
    await waitFor(() => expect(within(results).getAllByText(CREDIT)).toHaveLength(2));
    expect(screen.queryByTestId("tile-local-time")).toBeNull();
  });

  it("a normal Search response without near_stop shows no near-stop line", async () => {
    setupSearch([withExtra(1, { local_time: LMST })]);
    const results = await screen.findByTestId("search-results");
    await within(results).findByText(CREDIT);
    expect(screen.queryByTestId("tile-near-stop")).toBeNull();
  });
});

describe("Near tiles: nearest stop", () => {
  it("each tile shows site, drive and distance; credit stays", async () => {
    setupNear([
      withExtra(1, { near_stop: STOP }),
      withExtra(2, { near_stop: { ...STOP, site: 94, drive: 12, distance_km: 1.5 } }),
    ]);
    const results = await screen.findByTestId("near-results");
    const stops = await within(results).findAllByTestId("tile-near-stop");
    expect(stops).toHaveLength(2);
    expect(stops[0]!.textContent).toMatch(/site 93/i);
    expect(stops[0]!.textContent).toMatch(/drive 1840/i);
    // the brief says "e.g. 0.42 km"; the implementation may format sub-kilometre distances in metres
    expect(stops[0]!.textContent).toMatch(/0\.42 km|420 m\b/);
    expect(stops[1]!.textContent).toMatch(/site 94/i);
    expect(stops[1]!.textContent).toMatch(/drive 12\b/i);
    expect(stops[1]!.textContent).toMatch(/1\.5\d? km/);
    expect(within(results).getAllByText(CREDIT)).toHaveLength(2);
  });

  it("a tile whose near_stop is missing or null renders without the line and does not break", async () => {
    setupNear([withExtra(1, { near_stop: STOP }), withExtra(2, { near_stop: null }), photo(3)]);
    const results = await screen.findByTestId("near-results");
    await waitFor(() => expect(within(results).getAllByText(CREDIT)).toHaveLength(3));
    expect(within(results).getAllByTestId("tile-near-stop")).toHaveLength(1);
  });

  it("local time also shows on near tiles when present", async () => {
    setupNear([withExtra(1, { near_stop: STOP, local_time: LMST })]);
    const results = await screen.findByTestId("near-results");
    const t = await within(results).findByTestId("tile-local-time");
    expect(t.textContent).toMatch(/17:40\s+LMST/);
  });
});
