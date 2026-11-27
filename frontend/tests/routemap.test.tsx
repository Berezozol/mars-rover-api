import { fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { describe, expect, it } from "vitest";
import { App } from "../src/App";
import type { Traverse, Waypoint } from "../src/api/types";
import { fitView, prepare, project } from "../src/lib/mapgeo";
import { apiError, curiosity, detail, mockApi, page, perseverance, spirit } from "./helpers";

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

function traverse(sols: (number | null)[]): Traverse {
  const line = (sol: number | null, a: [number, number], b: [number, number]) => ({
    type: "Feature" as const,
    geometry: { type: "LineString" as const, coordinates: [a, b] },
    properties: { sol, length_m: 20 },
  });
  return {
    type: "FeatureCollection",
    rover: "perseverance",
    available: true,
    updated_at: "2026-10-28T15:05:03Z",
    source_max_sol: sols[2] ?? null,
    features: [
      line(sols[0] ?? null, [77.0, 18.0], [77.002, 18.001]),
      line(sols[1] ?? null, [77.002, 18.001], [77.003, 18.002]),
      line(sols[2] ?? null, [77.003, 18.002], [77.004, 18.004]),
    ],
  };
}

const location = (items = stops) => ({
  rover: "perseverance",
  available: true,
  updated_at: "2026-10-28T15:05:06Z",
  source_max_sol: items.length ? items[items.length - 1]!.sol : null,
  items,
});

function mapRoutes(over: Record<string, unknown> = {}) {
  return mockApi({
    "/api/v2/rovers": { items: [{ ...perseverance, max_sol: 50 }, curiosity, spirit] },
    "/api/v2/rovers/perseverance/traverse": traverse([20, 30, 40]),
    // `?latest=true` has its own tests below; here the strip stays out of the way.
    "/api/v2/rovers/perseverance/location": (url: URL) =>
      url.searchParams.get("latest") === "true" ? { ...location(), items: [] } : location(),
    ...over,
  });
}

function open(path = "/rovers/perseverance/map") {
  window.history.replaceState(null, "", path);
  return render(<App />);
}

describe("Route map page", () => {
  it("shows a loading state, then the summary, the map and the stop list", async () => {
    const m = mapRoutes();
    open();
    expect(await screen.findByText("Loading the route…")).toBeTruthy();
    expect(await screen.findByRole("heading", { name: "Perseverance route map" })).toBeTruthy();
    const summary = await screen.findByRole("region", { name: "Route summary" });
    expect(summary.textContent).toContain("sol 10 to 40");
    expect(summary.textContent).toContain("33.9 km"); // km only
    expect(summary.textContent).toContain("Sol 40");
    // the route and the stops come from the API only: no map tiles or other hosts
    for (const c of m.calls) expect(c.pathname.startsWith("/api/v2/")).toBe(true);
    expect(m.calls.filter((c) => c.pathname.endsWith("/traverse"))).toHaveLength(1);
    // all stops are reachable as a table (the non-graphic alternative)
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(1 + stops.length);
    expect(table.textContent).toContain("0.11 km");
    // one path for the route, one for the stops: no per-point elements
    const svg = screen.getByRole("img", { name: /route with 4 stops/ });
    expect(svg.querySelectorAll("path.route-base")).toHaveLength(1);
    expect(svg.querySelectorAll("path.stops-base")).toHaveLength(1);
  });

  it("selecting a stop shows 'Photos of sol N' and does not navigate by itself", async () => {
    mapRoutes();
    open();
    await screen.findByRole("table");
    expect(screen.getByText("Select a stop on the map or in the list.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Select stop: sol 20, site 2, drive 4" }));
    const link = await screen.findByRole("link", { name: "Photos of sol 20" });
    expect(link.getAttribute("href")).toBe("/rovers/perseverance?sol=20");
    expect(window.location.pathname).toBe("/rovers/perseverance/map"); // no navigation yet
    expect(screen.getByText("2 / 4")).toBeTruthy();
    fireEvent.click(link);
    await waitFor(() => expect(window.location.pathname).toBe("/rovers/perseverance"));
    expect(window.location.search).toBe("?sol=20");
  });

  it("is operable from the keyboard: arrows step through stops, Escape clears, Enter reaches the button", async () => {
    mapRoutes();
    open();
    await screen.findByRole("table");
    const map = screen.getByRole("group", { name: /Route map\. Use left and right/ });
    expect(map.getAttribute("tabindex")).toBe("0");
    map.focus();
    fireEvent.keyDown(map, { key: "ArrowLeft" }); // nothing selected: the newest stop
    await screen.findByRole("link", { name: "Photos of sol 40" });
    fireEvent.keyDown(map, { key: "ArrowLeft" });
    await screen.findByRole("link", { name: "Photos of sol 30" });
    fireEvent.keyDown(map, { key: "Home" });
    await screen.findByRole("link", { name: "Photos of sol 10" });
    fireEvent.keyDown(map, { key: "ArrowRight" });
    await screen.findByRole("link", { name: "Photos of sol 20" });
    expect(screen.getByText(/Sol 20, site 2, drive 4\./)).toBeTruthy(); // live region
    fireEvent.keyDown(map, { key: "Enter" });
    expect(document.activeElement?.textContent).toBe("Photos of sol 20");
    map.focus();
    fireEvent.keyDown(map, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("link", { name: /Photos of sol/ })).toBeNull());
    // zoom keys do not break the drawing
    fireEvent.keyDown(map, { key: "+" });
    fireEvent.keyDown(map, { key: "-" });
    expect(screen.getByRole("img", { name: /route with 4 stops/ })).toBeTruthy();
  });

  it("a click on the map selects the stop under the pointer, a click elsewhere does not", async () => {
    mapRoutes();
    open();
    await screen.findByRole("table");
    const svg = screen.getByRole("img", { name: /route with 4 stops/ });
    // jsdom has no layout: the map uses its default 600 x 440 area
    const data = prepare(traverse([20, 30, 40]), location())!;
    const view = fitView(data.bounds, 600, 440);
    const [x, y] = project(view, data.kx, stops[1]!.lon, stops[1]!.lat);
    fireEvent.pointerDown(svg, { clientX: x + 3, clientY: y + 2, pointerType: "mouse" });
    fireEvent.pointerUp(svg, { clientX: x + 3, clientY: y + 2, pointerType: "mouse" });
    expect(await screen.findByRole("link", { name: "Photos of sol 20" })).toBeTruthy();
    // far from any stop (mouse): the selection is cleared
    fireEvent.pointerDown(svg, { clientX: 5, clientY: 400, pointerType: "mouse" });
    fireEvent.pointerUp(svg, { clientX: 5, clientY: 400, pointerType: "mouse" });
    await waitFor(() => expect(screen.queryByRole("link", { name: /Photos of sol/ })).toBeNull());
  });

  it("with a sol range, stops outside it are still drawn and can be clicked", async () => {
    mapRoutes();
    open("/rovers/perseverance/map?from_sol=30&to_sol=40");
    await screen.findByRole("table");
    const svg = screen.getByRole("img", { name: /route with 4 stops/ });
    const data = prepare(traverse([20, 30, 40]), location())!;
    await new Promise((r) => setTimeout(r, 50)); // let the initial fit (a mount effect) happen first
    fireEvent.click(screen.getByRole("button", { name: "Whole route" }));
    expect(svg.querySelector("path.stops-base")).toBeTruthy();
    const w = stops[1]!; // sol 20: outside the 30-40 range
    const view = fitView(data.bounds, 600, 440);
    const [x, y] = project(view, data.kx, w.lon, w.lat);
    fireEvent.pointerDown(svg, { clientX: x, clientY: y, pointerType: "mouse" });
    fireEvent.pointerUp(svg, { clientX: x, clientY: y, pointerType: "mouse" });
    expect(await screen.findByRole("link", { name: "Photos of sol 20" })).toBeTruthy();
  });

  it("selects a stop from the address", async () => {
    mapRoutes();
    open("/rovers/perseverance/map?sol=30&site=3&drive=8");
    expect(await screen.findByRole("link", { name: "Photos of sol 30" })).toBeTruthy();
    expect(
      screen.getByRole("button", { name: /Select stop: sol 30/ }).getAttribute("aria-pressed"),
    ).toBe("true");
  });

  it("validates the sol range before changing anything, then highlights it", async () => {
    mapRoutes();
    open();
    await screen.findByRole("table");
    const from = screen.getByLabelText("From sol") as HTMLInputElement;
    const to = screen.getByLabelText("To sol") as HTMLInputElement;
    fireEvent.input(from, { target: { value: "30" } });
    fireEvent.input(to, { target: { value: "20" } });
    fireEvent.click(screen.getByRole("button", { name: "Highlight" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/must not be greater/);
    expect(window.location.search).toBe("");
    fireEvent.input(from, { target: { value: "20" } });
    fireEvent.input(to, { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: "Highlight" }));
    await waitFor(() => expect(window.location.search).toBe("?from_sol=20&to_sol=30"));
    expect(screen.getByText(/2 stops in range/)).toBeTruthy();
    const svg = screen.getByRole("img", { name: /route with 4 stops/ });
    expect(svg.querySelectorAll("path.route-hi")).toHaveLength(1);
    expect(screen.getByRole("table").textContent).toContain("Stops in the sol range");
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    await waitFor(() => expect(window.location.search).toBe(""));
  });

  it("explains a range without stops instead of failing", async () => {
    mapRoutes();
    open("/rovers/perseverance/map?from_sol=21&to_sol=29");
    expect(await screen.findByRole("heading", { name: "No stops in sols 21–29" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Clear range" }));
    await waitFor(() => expect(window.location.search).toBe(""));
  });

  it("says when position data ends before the last photo sol", async () => {
    mapRoutes();
    open();
    expect(await screen.findByText(/Position data ends at sol 40\./)).toBeTruthy();
    expect(screen.getByText(/Photos reach sol 50/)).toBeTruthy();
  });

  it("marks the range highlight as approximate when segments have no sol (Curiosity)", async () => {
    mockApi({
      "/api/v2/rovers": { items: [perseverance, curiosity] },
      "/api/v2/rovers/curiosity/traverse": { ...traverse([null, null, null]), rover: "curiosity" },
      "/api/v2/rovers/curiosity/location": { ...location(), rover: "curiosity" },
    });
    open("/rovers/curiosity/map?from_sol=20&to_sol=30");
    expect(await screen.findByText(/Approximate range highlight\./)).toBeTruthy();
  });

  it("shows 'no location data' for a rover without a source (available: false)", async () => {
    mockApi({
      "/api/v2/rovers": { items: [perseverance, spirit] },
      "/api/v2/rovers/spirit/traverse": {
        type: "FeatureCollection",
        rover: "spirit",
        available: false,
        updated_at: null,
        source_max_sol: null,
        features: [],
      },
      "/api/v2/rovers/spirit/location": {
        rover: "spirit",
        available: false,
        updated_at: null,
        source_max_sol: null,
        items: [],
      },
    });
    open("/rovers/spirit/map");
    expect(
      await screen.findByRole("heading", { name: "No location data for Spirit" }),
    ).toBeTruthy();
    expect(screen.getByRole("link", { name: "Browse Spirit photos" }).getAttribute("href")).toBe(
      "/rovers/spirit",
    );
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("available: true with no data yet says it is not loaded, without claims about other rovers", async () => {
    mapRoutes({
      "/api/v2/rovers/perseverance/traverse": {
        ...traverse([]),
        updated_at: null,
        source_max_sol: null,
        features: [],
      },
      "/api/v2/rovers/perseverance/location": {
        ...location([]),
        updated_at: null,
        source_max_sol: null,
      },
    });
    open();
    expect(
      await screen.findByRole("heading", { name: "Route data hasn’t been loaded yet" }),
    ).toBeTruthy();
    expect(screen.queryByText(/published for Curiosity and Perseverance only/)).toBeNull();
    expect(screen.getByRole("link", { name: "Browse Perseverance photos" })).toBeTruthy();
    expect(screen.getByText(/available: true, items: 0/)).toBeTruthy();
  });

  it("shows the 503 state with a retry", async () => {
    let fail = true;
    mapRoutes({
      "/api/v2/rovers/perseverance/location": () =>
        fail ? apiError(503, "db_unavailable", "down") : location(),
    });
    open();
    expect(await screen.findByText("Service temporarily unavailable")).toBeTruthy();
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("table")).toBeTruthy();
  });

  it("fetches the route once per rover: coming back to the page does not download it again", async () => {
    const m = mapRoutes();
    const first = open();
    await screen.findByRole("table");
    first.unmount();
    open();
    await screen.findByRole("table");
    expect(m.calls.filter((c) => c.pathname.endsWith("/traverse"))).toHaveLength(1);
    expect(m.calls.filter((c) => c.pathname.endsWith("/location"))).toHaveLength(1);
  });

  it("offers 'Route map' in the views of the rover (not the header) and in the rover tabs; no Ingenuity tab", async () => {
    mapRoutes();
    open();
    await screen.findByRole("table");
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).queryByRole("link", { name: "Route map" })).toBeNull();
    const views = screen.getByTestId("view-tabs");
    const link = within(views).getByRole("link", { name: "Route map" });
    expect(link.getAttribute("href")).toBe("/rovers/perseverance/map");
    expect(link.getAttribute("aria-current")).toBe("page");
    const tabs = screen.getByRole("navigation", { name: "Rover" });
    expect(within(tabs).getByRole("link", { name: "Curiosity" }).getAttribute("href")).toBe(
      "/rovers/curiosity/map",
    );
    expect(screen.queryByText(/Ingenuity/)).toBeNull();
  });
});

describe("Location block on the photo card", () => {
  const loc = {
    sol: 2020,
    site: 93,
    drive: 1840,
    lat: 18.4447,
    lon: 77.3951,
    elev_geoid: -2489.7,
    exact: false,
  };
  const routes = (d: ReturnType<typeof detail>) => ({
    "/api/v2/rovers": { items: [perseverance] },
    "/api/v2/photos/7": d,
    "/api/v2/rovers/perseverance/photos": page([], 0),
    "/api/v2/rovers/perseverance/traverse": traverse([20, 30, 40]),
  });

  it("approximate: badge, wording, coordinates and a link to the map centred on the stop", async () => {
    mockApi(routes(detail(7, { location: loc })));
    window.history.replaceState(null, "", "/photos/7");
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Location" })).toBeTruthy();
    expect(screen.getByText("Approximate")).toBeTruthy();
    expect(
      screen.getByText(/Taken while driving after the stop at site 93, drive 1840/),
    ).toBeTruthy();
    expect(screen.getByText(/18\.4447° N, 77\.3951° E/)).toBeTruthy();
    expect(screen.getByText(/−2,489\.7 m above areoid/)).toBeTruthy();
    expect(screen.getByText("Sol 2020 · site 93 · drive 1840")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Show on the route map" }).getAttribute("href")).toBe(
      "/rovers/perseverance/map?sol=2020&site=93&drive=1840",
    );
    const mini = await screen.findByRole("img", { name: /route with the position of this photo/ });
    expect(mini.querySelector("circle.loc-unc")).toBeTruthy(); // uncertainty circle
  });

  it("exact: badge 'Exact' without the uncertainty circle", async () => {
    mockApi(routes(detail(7, { location: { ...loc, exact: true } })));
    window.history.replaceState(null, "", "/photos/7");
    render(<App />);
    expect(await screen.findByText("Exact")).toBeTruthy();
    expect(screen.getByText("Position of the stop where this frame was taken.")).toBeTruthy();
    const mini = await screen.findByRole("img", { name: /route with the position of this photo/ });
    expect(mini.querySelector("circle.loc-unc")).toBeNull();
  });

  it("location: null hides the whole block, heading included", async () => {
    mockApi(routes(detail(7, { location: null })));
    window.history.replaceState(null, "", "/photos/7");
    render(<App />);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByRole("heading", { name: "Location" })).toBeNull();
  });

  it("still tells where the photo was taken when the route cannot be loaded", async () => {
    mockApi({
      ...routes(detail(7, { location: loc })),
      "/api/v2/rovers/perseverance/traverse": () => apiError(503, "db_unavailable", "down"),
    });
    window.history.replaceState(null, "", "/photos/7");
    render(<App />);
    expect(await screen.findByText("Sol 2020 · site 93 · drive 1840")).toBeTruthy();
    await waitFor(() =>
      expect(screen.queryByRole("img", { name: /route with the position/ })).toBeNull(),
    );
  });
});
