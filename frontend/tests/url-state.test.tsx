// Package 3 of the frontend review (docs/proposals/frontend-url-state.md): what a visitor chose lives in the
// address or in the history entry, so Back and a shared link bring it back. Written against the proposal
// (sections 2, 3 and 7), not against the implementation. Scroll positions and real Back transitions
// are covered in frontend/e2e/url-state.spec.ts (jsdom has no layout).
import { fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { act } from "preact/test-utils";
import { useEffect } from "preact/hooks";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../src/App";
import type { Flight, Sample, Traverse, Waypoint } from "../src/api/types";
import { findStop } from "../src/lib/maplinks";
import { backToSearch } from "../src/pages/Photo";
import { navigate, readHistoryState, useQueryParam, writeHistoryState } from "../src/router";
import {
  curiosity,
  detail,
  ingenuity,
  manifest,
  mockApi,
  page,
  perseverance,
  photo,
  samplesResponse,
  spirit,
} from "./helpers";
import { curLong } from "./fixtures/range";
import { openWeatherRange, fromInput, toInput, rowFor } from "./range-helpers";
import { chip } from "./weather-helpers";

const NAV_EVENT = "sol-index:navigate";

/** The query of the current address as an object, so that the order of parameters does not matter. */
const query = () => Object.fromEntries(new URLSearchParams(window.location.search));

/** `jsdom` reports `window.scrollY` as 0 and has no layout: tests that need a position set it by hand. */
function setScrollY(y: number) {
  Object.defineProperty(window, "scrollY", { configurable: true, value: y });
}
afterEach(() => setScrollY(0));

const backTo = async (pathname: string) => {
  window.history.back();
  await waitFor(() => expect(window.location.pathname).toBe(pathname));
  await act(async () => {}); // let the page react to the pop before the assertions
};

describe("url-state AC-7 findStop", () => {
  const wp = (sol: number, site: number, drive: number): Waypoint => ({
    sol,
    site,
    drive,
    lon: 77,
    lat: 18,
    elev_geoid: null,
    easting: null,
    northing: null,
    roll: null,
    pitch: null,
    yaw: null,
    tilt: null,
    dist_m: null,
    dist_total_m: null,
  });
  const stops = [wp(10, 1, 0), wp(20, 2, 4), wp(30, 3, 8), wp(30, 3, 9), wp(40, 4, 12)];

  it("url-state AC-7 an exact site and drive, or the last stop of the sol, is exact", () => {
    expect(findStop(stops, 20, 2, 4)).toEqual({ index: 1, exact: true });
    expect(findStop(stops, 30, null, null)).toEqual({ index: 3, exact: true }); // last stop of sol 30
    expect(findStop(stops, null, 3, 8)).toEqual({ index: 2, exact: true });
    expect(findStop(stops, 20, 2, 5)).toEqual({ index: 1, exact: true }); // unknown drive: the sol's stop
  });

  it("url-state AC-7 a sol without a stop gives the last stop before it, marked as not exact", () => {
    expect(findStop(stops, 25, null, null)).toEqual({ index: 1, exact: false });
    expect(findStop(stops, 39, null, null)).toEqual({ index: 3, exact: false });
    expect(findStop(stops, 1500, null, null)).toEqual({ index: 4, exact: false });
  });

  it("url-state AC-7 an unknown site and drive follow the same rule by sol", () => {
    expect(findStop(stops, 1500, 60, 0)).toEqual({ index: 4, exact: false });
    expect(findStop(stops, 25, 60, 0)).toEqual({ index: 1, exact: false });
  });

  it("url-state AC-7 a sol before the first stop, or nothing to go by, gives null", () => {
    expect(findStop(stops, 5, null, null)).toBeNull();
    expect(findStop(stops, null, null, null)).toBeNull();
    expect(findStop([], 25, null, null)).toBeNull();
  });
});

describe("url-state AC-2 backToSearch", () => {
  it("url-state AC-2 returns the search address as it is", () => {
    expect(backToSearch("/search?rover=curiosity&sol_min=300&sol_max=400")).toBe(
      "/search?rover=curiosity&sol_min=300&sol_max=400",
    );
    expect(backToSearch("/search")).toBe("/search");
  });

  it.each([
    ["null", null],
    ["empty", ""],
    ["a list of rover photos", "/rovers/curiosity?sol=344"],
    ["a protocol-relative address", "//evil.example/search"],
    ["an absolute address", "https://evil.example/search?x=1"],
    ["javascript:", "javascript:alert(1)"],
    ["a backslash trick", "/\\evil.example"],
    ["a leading space", " /search?x=1"],
    ["another page of the interface", "/stats"],
  ])("url-state AC-2 gives no link for %s", (_name, from) => {
    expect(backToSearch(from)).toBeNull();
  });
});

describe("url-state AC-2 Search tiles and the Back link on the photo card", () => {
  const cams = ["NAVCAM_LEFT", "MAST_LEFT"].map((c) => ({ code: c, name: c, total_photos: 10 }));

  it("url-state AC-2 a tile of the search results carries the search address as `from`", async () => {
    mockApi({
      "/api/v2/rovers": { items: [perseverance, curiosity, spirit, ingenuity] },
      "/api/v2/rovers/curiosity/cameras": { items: cams },
      "/api/v2/photos": page(
        [photo(10, { rover: "curiosity" }), photo(9, { rover: "curiosity" })],
        2,
      ),
    });
    const here = "/search?rover=curiosity&sol_min=300&sol_max=400";
    window.history.replaceState(null, "", here);
    render(<App />);
    const results = await screen.findByTestId("search-results");
    const tile = await waitFor(() => {
      const a = results.querySelector<HTMLAnchorElement>("a[href^='/photos/10']");
      expect(a).not.toBeNull();
      return a!;
    });
    const from = new URL(tile.getAttribute("href")!, "http://local.invalid").searchParams.get(
      "from",
    );
    expect(from).not.toBeNull();
    expect(from!.startsWith("/search?")).toBe(true);
    const want = Object.fromEntries(new URLSearchParams(here.split("?")[1]));
    expect(Object.fromEntries(new URLSearchParams(from!.split("?")[1]))).toMatchObject(want);
  });

  function openPhoto(from: string | null) {
    mockApi({
      "/api/v2/rovers": { items: [perseverance] },
      "/api/v2/photos/2": detail(2, {
        in_sol: { position: 2, total: 5, newer_id: 3, older_id: 1 },
      }),
      "/api/v2/rovers/perseverance/photos": page([photo(2)], 1),
    });
    const url = from === null ? "/photos/2" : "/photos/2?from=" + encodeURIComponent(from);
    window.history.replaceState(null, "", url);
    render(<App />);
  }

  it("url-state AC-2 the card links back to exactly the search address", async () => {
    const from = "/search?rover=curiosity&sol_min=300&sol_max=400&page=2";
    openPhoto(from);
    const link = await screen.findByTestId("back-to-search");
    expect(link.textContent).toBe("← Back to search results");
    expect(link.getAttribute("href")).toBe(from);
    expect(screen.getByRole("link", { name: "← Back to search results" })).toBe(link);
  });

  it.each([
    ["no from", null],
    ["a list of rover photos", "/rovers/perseverance?sol=2031"],
    ["a protocol-relative address", "//evil.example/search"],
    ["an absolute address", "https://evil.example/search"],
    ["javascript:", "javascript:alert(1)"],
  ])("url-state AC-2 no Back link for %s", async (_name, from) => {
    openPhoto(from);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByTestId("back-to-search")).toBeNull();
    expect(screen.queryByText(/Back to search results/)).toBeNull();
    for (const a of screen.getAllByRole("link")) {
      expect(a.getAttribute("href") ?? "").not.toMatch(/^(\/\/|https?:\/\/evil|javascript:)/);
    }
  });
});

describe("url-state AC-3 the 'Sol N' crumb remembers the list page", () => {
  function openPhoto(from: string) {
    mockApi({
      "/api/v2/rovers": { items: [perseverance, curiosity] },
      "/api/v2/photos/2": detail(2, {
        sol: 344,
        in_sol: { position: 2, total: 50, newer_id: 3, older_id: 1 },
      }),
      "/api/v2/rovers/perseverance/photos": page([photo(2)], 1),
      "/api/v2/rovers/curiosity/photos": page([photo(2)], 1),
    });
    window.history.replaceState(null, "", "/photos/2?from=" + encodeURIComponent(from));
    render(<App />);
  }
  const crumb = async () =>
    (await screen.findByRole("link", { name: "Sol 344" })).getAttribute("href");

  it("url-state AC-3 the same rover and sol: the crumb leads to the list page the visitor came from", async () => {
    openPhoto("/rovers/perseverance?sol=344&page=3");
    expect(await crumb()).toBe("/rovers/perseverance?sol=344&page=3");
  });

  it("url-state AC-3 the camera and sample type of the list are not carried to the crumb", async () => {
    openPhoto("/rovers/perseverance?sol=344&camera=NAVCAM_LEFT&sample_type=full&page=3");
    expect(await crumb()).toBe("/rovers/perseverance?sol=344&page=3");
  });

  it("url-state AC-3 another sol in `from`: the crumb is as before", async () => {
    openPhoto("/rovers/perseverance?sol=100&page=3");
    expect(await crumb()).toBe("/rovers/perseverance?sol=344");
  });

  it("url-state AC-3 another rover in `from`: the crumb is as before", async () => {
    openPhoto("/rovers/curiosity?sol=344&page=3");
    expect(await crumb()).toBe("/rovers/perseverance?sol=344");
  });
});

describe("url-state router: navigation kind, history.state and scroll", () => {
  it("url-state AC-1 a link navigation is announced as push, Back as pop", async () => {
    const kinds: string[] = [];
    const on = (e: Event) => kinds.push((e as CustomEvent<{ kind: string }>).detail.kind);
    window.addEventListener(NAV_EVENT, on);
    try {
      window.history.replaceState(null, "", "/no/one");
      navigate("/no/two");
      expect(kinds).toEqual(["push"]);
      window.history.back();
      await waitFor(() => expect(kinds).toEqual(["push", "pop"]));
    } finally {
      window.removeEventListener(NAV_EVENT, on);
    }
  });

  it("url-state AC-1 a push saves the scroll position in the entry the visitor leaves", async () => {
    window.history.replaceState(null, "", "/no/one");
    setScrollY(420);
    navigate("/no/two");
    setScrollY(0);
    await backTo("/no/one");
    expect(window.history.state).toMatchObject({ scroll: 420 });
  });

  it("url-state AC-1 replace keeps the other fields of history.state", () => {
    window.history.replaceState({ keep: 1 }, "", "/no/one");
    navigate("/no/one?x=1", { replace: true });
    expect(window.history.state).toMatchObject({ keep: 1 });
    expect(window.location.search).toBe("?x=1");
  });

  it("url-state AC-9 readHistoryState / writeHistoryState keep a value per entry, without touching others", async () => {
    window.history.replaceState({ other: "x" }, "", "/no/one");
    expect(readHistoryState("solTable:weather")).toBeFalsy();
    writeHistoryState("solTable:weather", { page: 2, sort: "sol:asc", hl: 120 });
    expect(readHistoryState("solTable:weather")).toEqual({ page: 2, sort: "sol:asc", hl: 120 });
    expect(window.history.state).toMatchObject({ other: "x" });
    navigate("/no/two"); // a new entry starts without it
    expect(readHistoryState("solTable:weather")).toBeFalsy();
    await backTo("/no/one"); // and the old entry keeps it
    expect(readHistoryState("solTable:weather")).toEqual({ page: 2, sort: "sol:asc", hl: 120 });
  });

  it("url-state AC-1 the layout scrolls to the top after a link but not after Back", async () => {
    mockApi({});
    window.history.replaceState(null, "", "/no/one");
    render(<App />);
    await screen.findByRole("heading", { name: "Page not found" });
    const scrollTo = vi.mocked(window.scrollTo);
    scrollTo.mockClear();
    setScrollY(300);

    act(() => navigate("/no/two"));
    await waitFor(() => expect(scrollTo).toHaveBeenCalledWith(0, 0));

    scrollTo.mockClear();
    await backTo("/no/one");
    expect(scrollTo).not.toHaveBeenCalledWith(0, 0);
  });
});

describe("url-state useQueryParam", () => {
  function Probe() {
    const [v, set] = useQueryParam<"a" | "b">(
      "mode",
      (raw) => (raw === "a" || raw === "b" ? raw : undefined),
      "a",
    );
    useEffect(() => {
      (window as unknown as { setMode: typeof set }).setMode = set;
    });
    return <output aria-label="mode">{v}</output>;
  }
  const setMode = (v: "a" | "b") =>
    (window as unknown as { setMode: (v: "a" | "b") => void }).setMode(v);

  it("url-state AC-8 reads the value, writes it with replace and drops the default from the address", async () => {
    window.history.replaceState(null, "", "/probe?mode=b&keep=1");
    render(<Probe />);
    expect(screen.getByLabelText("mode").textContent).toBe("b");
    const before = window.history.length;
    act(() => setMode("a"));
    await waitFor(() => expect(query()).toEqual({ keep: "1" }));
    expect(screen.getByLabelText("mode").textContent).toBe("a");
    act(() => setMode("b"));
    await waitFor(() => expect(query()).toEqual({ keep: "1", mode: "b" }));
    expect(window.history.length).toBe(before); // no new entries
  });

  it("url-state AC-8 an unknown value gives the fallback and is cleaned out of the address", async () => {
    window.history.replaceState(null, "", "/probe?mode=zzz&keep=1");
    render(<Probe />);
    expect(screen.getByLabelText("mode").textContent).toBe("a");
    await waitFor(() => expect(query()).toEqual({ keep: "1" }));
  });
});

// ---------------------------------------------------------------------------------------------------------------
// AC-4 Samples

const samplesFixture = JSON.parse(readFileSync("tests/fixtures/samples.json", "utf-8")) as {
  items: Sample[];
};
const sampleItems = samplesFixture.items;

function openSamples(path: string) {
  mockApi({
    "/api/v2/rovers": { items: [perseverance, curiosity] },
    "/api/v2/rovers/perseverance/samples": samplesResponse(sampleItems),
    "/api/v2/rovers/perseverance/cameras": { items: [] },
  });
  window.history.replaceState(null, "", path);
  return render(<App />);
}
const sampleRows = () =>
  screen
    .getAllByRole("row")
    .slice(1)
    .map((r) => Number(r.id.replace("sample-", "")));
const samplesWith = (status: string | null, type: string | null) =>
  sampleItems
    .filter((x) => (!status || x.status === status) && (!type || x.sample_type === type))
    .map((x) => x.number);

describe("url-state AC-4 Samples filters in the address", () => {
  it("url-state AC-4 choosing Depot and Rock core writes status and type, without new history entries", async () => {
    openSamples("/rovers/perseverance/samples");
    await screen.findByRole("table");
    const before = window.history.length;
    fireEvent.click(screen.getByRole("button", { name: /In depot/ }));
    await waitFor(() => expect(query()).toEqual({ status: "depot" }));
    fireEvent.click(screen.getByRole("button", { name: /Rock core/ }));
    await waitFor(() => expect(query()).toEqual({ status: "depot", type: "rock_core" }));
    expect(window.location.pathname).toBe("/rovers/perseverance/samples");
    expect(window.history.length).toBe(before);
    expect(sampleRows()).toEqual(samplesWith("depot", "rock_core"));
  });

  it("url-state AC-4 a direct visit shows the same filters", async () => {
    openSamples("/rovers/perseverance/samples?status=depot&type=rock_core");
    await screen.findByRole("table");
    expect(screen.getByRole("button", { name: /In depot/ }).getAttribute("aria-pressed")).toBe(
      "true",
    );
    expect(screen.getByRole("button", { name: /Rock core/ }).getAttribute("aria-pressed")).toBe(
      "true",
    );
    expect(sampleRows()).toEqual(samplesWith("depot", "rock_core"));
    expect(
      screen.getByText(
        `Showing ${samplesWith("depot", "rock_core").length} of ${sampleItems.length} samples`,
      ),
    ).toBeTruthy();
  });

  it("url-state AC-4 an unknown value means All and is cleaned out of the address", async () => {
    openSamples("/rovers/perseverance/samples?status=foo&type=bar&keep=1");
    await screen.findByRole("table");
    const statusGroup = screen.getByRole("group", { name: "Status" });
    const typeGroup = screen.getByRole("group", { name: "Type" });
    expect(
      within(statusGroup).getByRole("button", { name: /^All/ }).getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      within(typeGroup).getByRole("button", { name: /^All/ }).getAttribute("aria-pressed"),
    ).toBe("true");
    expect(sampleRows()).toEqual(sampleItems.map((x) => x.number));
    await waitFor(() => expect(query()).toEqual({ keep: "1" }));
  });

  it("url-state AC-4 'All' takes the parameter out of the address again", async () => {
    openSamples("/rovers/perseverance/samples?status=depot");
    await screen.findByRole("table");
    const statusGroup = screen.getByRole("group", { name: "Status" });
    fireEvent.click(within(statusGroup).getByRole("button", { name: /^All/ }));
    await waitFor(() => expect(window.location.search).toBe(""));
  });
});

// ---------------------------------------------------------------------------------------------------------------
// AC-5 Flights

const flightsFixture = JSON.parse(
  readFileSync("tests/fixtures/ingenuity-flights.json", "utf-8"),
) as {
  items: Flight[];
};

function openFlights(path: string) {
  mockApi({
    "/api/v2/rovers": { items: [perseverance, ingenuity] },
    "/api/v2/rovers/ingenuity/flights": flightsFixture,
  });
  window.history.replaceState(null, "", path);
  return render(<App />);
}
const flightOrder = () =>
  screen
    .getAllByRole("row")
    .slice(1)
    .map((r) => Number(r.id.replace("flight-", "")));
const header = (name: RegExp) => screen.getByRole("columnheader", { name });

describe("url-state AC-5 Flights sort in the address", () => {
  it("url-state AC-5 sorting by Distance writes sort=distance:desc, without a new history entry", async () => {
    openFlights("/rovers/ingenuity/flights");
    await screen.findByRole("table");
    const before = window.history.length;
    fireEvent.click(within(header(/Distance/)).getByRole("button"));
    await waitFor(() => expect(query()).toEqual({ sort: "distance:desc" }));
    expect(window.history.length).toBe(before);
    expect(header(/Distance/).getAttribute("aria-sort")).toBe("descending");
  });

  it("url-state AC-5 a direct visit gives the same sort, in the headings and in the select", async () => {
    openFlights("/rovers/ingenuity/flights?sort=distance:desc");
    await screen.findByRole("table");
    expect(header(/Distance/).getAttribute("aria-sort")).toBe("descending");
    expect(flightOrder()).toEqual([25, 68, 9, 1, 59]);
    expect((screen.getByLabelText("Sort by") as HTMLSelectElement).value).toBe("distance:desc");
  });

  it("url-state AC-5 the select writes the sort to the address too", async () => {
    openFlights("/rovers/ingenuity/flights");
    await screen.findByRole("table");
    fireEvent.change(screen.getByLabelText("Sort by"), { target: { value: "photos:desc" } });
    await waitFor(() => expect(query()).toEqual({ sort: "photos:desc" }));
    expect(header(/Photos/).getAttribute("aria-sort")).toBe("descending");
  });

  it.each(["distance", "distance:sideways", "bogus:desc", ":"])(
    "url-state AC-5 an invalid sort (%s) means the default order and leaves the address",
    async (bad) => {
      openFlights(`/rovers/ingenuity/flights?sort=${bad}`);
      await screen.findByRole("table");
      expect(header(/^Flight$/).getAttribute("aria-sort")).toBe("ascending");
      expect(flightOrder()).toEqual([1, 9, 25, 59, 68]);
      await waitFor(() => expect(window.location.search).toBe(""));
    },
  );

  it("url-state AC-5 returning to the default sort takes the parameter out of the address", async () => {
    openFlights("/rovers/ingenuity/flights?sort=flight:desc");
    await screen.findByRole("table");
    expect(flightOrder()).toEqual([68, 59, 25, 9, 1]);
    fireEvent.change(screen.getByLabelText("Sort by"), { target: { value: "flight:asc" } });
    await waitFor(() => expect(window.location.search).toBe(""));
  });
});

// ---------------------------------------------------------------------------------------------------------------
// AC-6, AC-7 Route map

describe("url-state route map", () => {
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
  const line = (sol: number, a: [number, number], b: [number, number]) => ({
    type: "Feature" as const,
    geometry: { type: "LineString" as const, coordinates: [a, b] },
    properties: { sol, length_m: 20 },
  });
  const route: Traverse = {
    type: "FeatureCollection",
    rover: "perseverance",
    available: true,
    updated_at: "2026-10-28T15:05:03Z",
    source_max_sol: 40,
    features: [
      line(20, [77.0, 18.0], [77.002, 18.001]),
      line(30, [77.002, 18.001], [77.003, 18.002]),
      line(40, [77.003, 18.002], [77.004, 18.004]),
    ],
  };
  const location = {
    rover: "perseverance",
    available: true,
    updated_at: "2026-10-28T15:05:06Z",
    source_max_sol: 40,
    items: stops,
  };

  function openMap(path: string) {
    mockApi({
      "/api/v2/rovers": { items: [{ ...perseverance, max_sol: 50 }, curiosity, spirit] },
      "/api/v2/rovers/perseverance/traverse": route,
      "/api/v2/rovers/perseverance/location": (url: URL) =>
        url.searchParams.get("latest") === "true" ? { ...location, items: [] } : location,
    });
    window.history.replaceState(null, "", path);
    return render(<App />);
  }

  it("url-state AC-6 selecting a stop writes sol, site and drive, without a new history entry", async () => {
    openMap("/rovers/perseverance/map");
    await screen.findByRole("table");
    const before = window.history.length;
    fireEvent.click(screen.getByRole("button", { name: "Select stop: sol 20, site 2, drive 4" }));
    await waitFor(() => expect(query()).toMatchObject({ sol: "20", site: "2", drive: "4" }));
    fireEvent.click(screen.getByRole("button", { name: "Select stop: sol 30, site 3, drive 8" }));
    await waitFor(() => expect(query()).toMatchObject({ sol: "30", site: "3", drive: "8" }));
    expect(window.location.pathname).toBe("/rovers/perseverance/map");
    expect(window.history.length).toBe(before);
  });

  it("url-state AC-6 keyboard selection writes the address as well", async () => {
    openMap("/rovers/perseverance/map");
    await screen.findByRole("table");
    const map = screen.getByRole("group", { name: /Route map\. Use left and right/ });
    map.focus();
    fireEvent.keyDown(map, { key: "ArrowLeft" }); // nothing selected: the newest stop
    await waitFor(() => expect(query()).toMatchObject({ sol: "40", site: "4", drive: "12" }));
  });

  it("url-state AC-6 an address with a stop selects it (and 'Photos at this stop' is there)", async () => {
    openMap("/rovers/perseverance/map?sol=30&site=3&drive=8");
    expect(await screen.findByRole("link", { name: "Photos at this stop" })).toBeTruthy();
    expect(
      screen.getByRole("button", { name: /Select stop: sol 30/ }).getAttribute("aria-pressed"),
    ).toBe("true");
    expect(screen.queryByTestId("stop-fallback")).toBeNull();
  });

  it("url-state AC-7 a sol without a stop selects the last stop before it and says so", async () => {
    openMap("/rovers/perseverance/map?sol=25");
    expect(await screen.findByRole("link", { name: "Photos of sol 20" })).toBeTruthy();
    expect(screen.getByTestId("stop-fallback").textContent).toBe(
      "No stop on sol 25 — showing the last stop before it, sol 20.",
    );
    expect(
      screen.getByRole("button", { name: /Select stop: sol 20/ }).getAttribute("aria-pressed"),
    ).toBe("true");
  });

  it("url-state AC-7 a sol before the first stop selects nothing", async () => {
    openMap("/rovers/perseverance/map?sol=5");
    await screen.findByRole("table");
    expect(screen.getByTestId("stop-fallback").textContent).toBe("No stops on or before sol 5.");
    expect(screen.queryByRole("link", { name: /Photos of sol/ })).toBeNull();
    for (const b of screen.getAllByRole("button", { name: /Select stop/ })) {
      expect(b.getAttribute("aria-pressed")).not.toBe("true");
    }
  });

  it("url-state AC-7 a site and drive that do not exist follow the rule by sol", async () => {
    openMap("/rovers/perseverance/map?sol=1500&site=60&drive=0");
    expect(await screen.findByRole("link", { name: "Photos of sol 40" })).toBeTruthy();
    expect(screen.getByTestId("stop-fallback").textContent).toBe(
      "No stop on sol 1500 — showing the last stop before it, sol 40.",
    );
  });

  it("url-state AC-7 an exact stop shows no fallback note", async () => {
    openMap("/rovers/perseverance/map?sol=20");
    expect(await screen.findByRole("link", { name: "Photos of sol 20" })).toBeTruthy();
    expect(screen.queryByTestId("stop-fallback")).toBeNull();
  });
});

// ---------------------------------------------------------------------------------------------------------------
// AC-8 Weather parameter

describe("url-state AC-8 weather parameter in the address", () => {
  // The parameter key is the one of the API header (`pressure_pa`); the proposal writes `pressure` as a short form.
  const PATH = "/data/weather?rover=curiosity&param=pressure_pa&from=300&to=400";

  it("url-state AC-8 a direct visit selects the parameter and the range", async () => {
    openWeatherRange({ cur: curLong }, PATH);
    const panel = await screen.findByRole("main");
    await within(panel).findByRole("table");
    expect(chip(panel, /^Pressure/).getAttribute("aria-pressed")).toBe("true");
    expect(chip(panel, /^Air temp/).getAttribute("aria-pressed")).toBe("false");
    await waitFor(() => expect([fromInput().value, toInput().value]).toEqual(["300", "400"]));
  });

  it("url-state AC-8 choosing a chip updates `param` without a new history entry and keeps the range", async () => {
    openWeatherRange({ cur: curLong }, PATH);
    const panel = await screen.findByRole("main");
    await within(panel).findByRole("table");
    const before = window.history.length;
    fireEvent.click(chip(panel, /^Humidity/));
    await waitFor(() => expect(query().param).toBe("humidity_pct"));
    expect(query()).toMatchObject({ rover: "curiosity", from: "300", to: "400" });
    expect(window.history.length).toBe(before);
    expect(chip(panel, /^Humidity/).getAttribute("aria-pressed")).toBe("true");
  });

  it("url-state AC-8 an unknown parameter means the first one and is removed from the address", async () => {
    openWeatherRange({ cur: curLong }, "/data/weather?rover=curiosity&param=bogus&from=300&to=400");
    const panel = await screen.findByRole("main");
    await within(panel).findByRole("table");
    expect(chip(panel, /^Air temp/).getAttribute("aria-pressed")).toBe("true");
    await waitFor(() => expect(query().param).toBeUndefined());
    expect(query()).toMatchObject({ rover: "curiosity", from: "300", to: "400" });
  });

  it("url-state AC-8 the first parameter is the default and does not stay in the address", async () => {
    openWeatherRange({ cur: curLong }, PATH);
    const panel = await screen.findByRole("main");
    await within(panel).findByRole("table");
    fireEvent.click(chip(panel, /^Air temp/));
    await waitFor(() => expect(query().param).toBeUndefined());
  });
});

// ---------------------------------------------------------------------------------------------------------------
// AC-10 Timeline

describe("url-state AC-10 Timeline checks the range before it changes the address", () => {
  const sols = [
    { sol: 2031, earth_date: "2026-11-06", total_photos: 52, cameras: ["NAVCAM_LEFT"] },
    { sol: 2000, earth_date: "2026-10-06", total_photos: 300, cameras: ["MCZ_LEFT"] },
    { sol: 5, earth_date: "2021-02-23", total_photos: 900, cameras: ["NAVCAM_LEFT"] },
  ];
  const START = "/rovers/perseverance/timeline?from_sol=1900&to_sol=2000";

  async function setup(path = START) {
    mockApi({
      "/api/v2/rovers": { items: [perseverance] },
      "/api/v2/rovers/perseverance/manifest": manifest(perseverance, sols),
    });
    window.history.replaceState(null, "", path);
    render(<App />);
    await screen.findByText("Sols with photos");
    await act(async () => {}); // the mount effects of the form must run before the visitor types
  }
  const from = () => screen.getByLabelText("From sol") as HTMLInputElement;
  const to = () => screen.getByLabelText("To sol") as HTMLInputElement;
  // The first edit right after the page appears is overwritten once by the form syncing with the address
  // (observed in jsdom), so the value is typed twice; the second one sticks.
  const type = (el: HTMLInputElement, value: string) => {
    fireEvent.input(el, { target: { value } });
    fireEvent.input(el, { target: { value } });
    expect(el.value).toBe(value);
  };
  const apply = async (f: string, t: string) => {
    type(from(), f);
    type(to(), t);
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
  };

  it("url-state AC-10 both fields have min=0 and max=<max_sol>", async () => {
    await setup();
    for (const el of [from(), to()]) {
      expect(el.getAttribute("min")).toBe("0");
      expect(el.getAttribute("max")).toBe("2031");
    }
  });

  it("url-state AC-10 From greater than To: the address stays, the field says why", async () => {
    await setup();
    const before = window.history.length;
    await apply("1500", "100");
    expect(await screen.findByText(/“From” must not be greater than “To”\./)).toBeTruthy();
    expect(window.location.search).toBe("?from_sol=1900&to_sol=2000");
    expect(window.history.length).toBe(before);
    expect([from(), to()].some((el) => el.getAttribute("aria-invalid") === "true")).toBe(true);
  });

  it.each([
    ["From", "2500", "2600"],
    ["To", "100", "3000"],
  ])(
    "url-state AC-10 %s above max_sol: names the allowed sols and marks that field",
    async (which, f, t) => {
      await setup();
      await apply(f, t);
      expect(await screen.findByText(/Sols run 0–2031\./)).toBeTruthy();
      expect(window.location.search).toBe("?from_sol=1900&to_sol=2000");
      const bad = which === "From" ? from() : to();
      expect(bad.getAttribute("aria-invalid")).toBe("true");
    },
  );

  it("url-state AC-10 a fraction is refused and the address stays", async () => {
    await setup();
    await apply("10.5", "100");
    await waitFor(() =>
      expect([from(), to()].some((el) => el.getAttribute("aria-invalid") === "true")).toBe(true),
    );
    expect(window.location.search).toBe("?from_sol=1900&to_sol=2000");
  });

  it("url-state AC-10 a valid range is a new history entry and Back brings the old range", async () => {
    await setup();
    await apply("100", "200");
    await waitFor(() => expect(window.location.search).toBe("?from_sol=100&to_sol=200"));
    expect([from(), to()].some((el) => el.getAttribute("aria-invalid") === "true")).toBe(false);

    window.history.back();
    await waitFor(() => expect(window.location.search).toBe("?from_sol=1900&to_sol=2000"));
    expect(window.location.pathname).toBe("/rovers/perseverance/timeline");
    await waitFor(() => expect([from().value, to().value]).toEqual(["1900", "2000"]));
  });

  it("url-state AC-10 Apply with the range that is already shown adds no history entry", async () => {
    await setup();
    // cut off any "forward" entries left by earlier tests, so that the length is a fair measure
    window.history.pushState(window.history.state, "", window.location.href);
    const before = window.history.length;
    await apply("1900", "2000");
    await act(async () => {});
    expect(window.location.search).toBe("?from_sol=1900&to_sol=2000");
    expect(window.history.length).toBe(before);
  });

  it("url-state AC-10 a hand-made address with a wrong range still says 'Invalid range'", async () => {
    mockApi({
      "/api/v2/rovers": { items: [perseverance] },
      "/api/v2/rovers/perseverance/manifest": manifest(perseverance, sols),
    });
    window.history.replaceState(null, "", "/rovers/perseverance/timeline?from_sol=1500&to_sol=100");
    render(<App />);
    expect(await screen.findByText("Invalid range")).toBeTruthy();
  });
});

describe("url-state AC-9 a row link only marks the row for a plain click", () => {
  const savedHl = () =>
    (readHistoryState("solTable:weather:perseverance") as { hl?: number | null } | undefined)?.hl ??
    null;
  const linkOfFirstRow = async () => {
    openWeatherRange({
      over: {
        // the sol report asks for the latest position
        "/api/v2/rovers/perseverance/location": {
          rover: "perseverance",
          available: false,
          updated_at: null,
          source_max_sol: null,
          items: [],
        },
      },
    });
    await screen.findByRole("table");
    const row = screen.getAllByRole("row")[1]!;
    return within(row).getByRole("link", { name: /^Sol report for sol \d+/ });
  };
  // jsdom cannot navigate: swallow the default action after the page's own handlers have run.
  const swallow = (e: Event) => e.preventDefault();
  afterEach(() => document.removeEventListener("click", swallow));

  it("url-state AC-9 ctrl-click and meta-click do not mark the row", async () => {
    const link = await linkOfFirstRow();
    document.addEventListener("click", swallow);
    fireEvent.click(link, { ctrlKey: true });
    fireEvent.click(link, { metaKey: true });
    await act(async () => {});
    expect(savedHl()).toBeNull();
    expect(window.location.pathname).toBe("/data/weather");
  });

  it("url-state AC-9 a middle click does not mark the row", async () => {
    const link = await linkOfFirstRow();
    document.addEventListener("click", swallow);
    fireEvent.click(link, { button: 1 });
    fireEvent(link, new MouseEvent("auxclick", { button: 1, bubbles: true, cancelable: true }));
    await act(async () => {});
    expect(savedHl()).toBeNull();
    expect(window.location.pathname).toBe("/data/weather");
  });

  it("url-state AC-9 a plain click marks the row of the opened sol (control)", async () => {
    const link = await linkOfFirstRow();
    const sol = Number(/sol (\d+)/.exec(link.getAttribute("aria-label")!)![1]);
    fireEvent.click(link);
    await waitFor(() => expect(window.location.pathname).toMatch(/\/sols\/\d+\/conditions$/));
    await backTo("/data/weather");
    await screen.findByRole("table");
    expect(rowFor(sol)).toBeTruthy();
    await waitFor(() => expect(savedHl()).toBe(sol));
  });
});
