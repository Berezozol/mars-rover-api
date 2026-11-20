import { render, screen } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import type { LocationResponse, Traverse } from "../src/api/types";
import { App } from "../src/App";
import { detail, mockApi, page } from "./helpers";
import { fitView, pathData, prepare, project } from "../src/lib/mapgeo";

// Decimated real responses of the public deployment, derived by scripts/make-fixtures.mjs.
const read = <T,>(name: string): T =>
  JSON.parse(readFileSync(`tests/fixtures/${name}`, "utf-8")) as T;
const traverse = read<Traverse>("perseverance-traverse.json");
const location = read<LocationResponse>("perseverance-location.json");

describe("Perseverance with real data", () => {
  const data = prepare(traverse, location)!;
  const view = fitView(data.bounds, 619, 440);

  it("the initial fit puts every route vertex and every stop inside the drawing area", () => {
    const { lons, lats } = data.route;
    for (let i = 0; i < lons.length; i++) {
      const [x, y] = project(view, data.kx, lons[i]!, lats[i]!);
      expect(x).toBeGreaterThanOrEqual(0);
      expect(x).toBeLessThanOrEqual(619);
      expect(y).toBeGreaterThanOrEqual(0);
      expect(y).toBeLessThanOrEqual(440);
    }
    for (const w of data.stops) {
      const [x, y] = project(view, data.kx, w.lon, w.lat);
      expect(x).toBeGreaterThan(0);
      expect(x).toBeLessThan(619);
      expect(y).toBeGreaterThan(0);
      expect(y).toBeLessThan(440);
    }
  });

  it("the latest stop and the last route vertex are the same screen point", () => {
    const last = data.stops[data.stops.length - 1]!;
    const { lons, lats } = data.route;
    const end = lons.length - 1;
    const a = project(view, data.kx, last.lon, last.lat);
    const b = project(view, data.kx, lons[end]!, lats[end]!);
    expect(Math.hypot(a[0] - b[0], a[1] - b[1])).toBeLessThan(2);
  });

  it("the drawn route path spans the whole drawing area, not a corner", () => {
    const d = pathData(data.route, data.route.segs, view, data.kx);
    const xs = [...d.matchAll(/[ML]([-\d.]+) ([-\d.]+)/g)].map((m) => Number(m[1]));
    expect(Math.min(...xs)).toBeLessThan(619 * 0.2);
    expect(Math.max(...xs)).toBeGreaterThan(619 * 0.8);
    expect(d.length).toBeLessThan(150_000);
  });
});

describe("Route map page with real data", () => {
  it("draws the route across the area and puts the latest-stop marker on the route end", async () => {
    const rovers = read<{ items: unknown[] }>("rovers.json");
    mockApi({
      "/api/v2/rovers": rovers,
      "/api/v2/rovers/perseverance/traverse": traverse,
      "/api/v2/rovers/perseverance/location": location,
    });
    window.history.replaceState(null, "", "/rovers/perseverance/map");
    render(<App />);
    const svg = await screen.findByRole("img", { name: /route with \d+ stops/ });
    const d = svg.querySelector("path.route-base")!.getAttribute("d")!;
    const pts = [...d.matchAll(/[ML]([-\d.]+) ([-\d.]+)/g)].map((m) => [
      Number(m[1]),
      Number(m[2]),
    ]);
    const xs = pts.map((p) => p[0]!);
    const ys = pts.map((p) => p[1]!);
    expect(Math.min(...xs)).toBeGreaterThanOrEqual(0);
    expect(Math.max(...xs)).toBeLessThanOrEqual(600);
    expect(Math.min(...ys)).toBeGreaterThanOrEqual(0);
    expect(Math.max(...ys)).toBeLessThanOrEqual(440);
    expect(Math.max(...xs) - Math.min(...xs)).toBeGreaterThan(300);
    // marker: translate(x y) of the latest stop equals the last drawn route vertex (within 2 px)
    const g = svg.querySelector("rect.latest-mark")!.parentElement!.getAttribute("transform")!;
    const [mx, my] = g
      .match(/translate\(([-\d.]+) ([-\d.]+)\)/)!
      .slice(1)
      .map(Number) as [number, number];
    const end = pts[pts.length - 1]!;
    expect(Math.hypot(mx - end[0]!, my - end[1]!)).toBeLessThan(2);
    // the stops path is drawn too
    expect(svg.querySelector("path.stops-base")!.getAttribute("d")!.length).toBeGreaterThan(1000);
  });
});

describe("Curiosity with real data", () => {
  it("Curiosity: route and stops are drawn, marker on the route end", async () => {
    mockApi({
      "/api/v2/rovers": read("rovers.json"),
      "/api/v2/rovers/curiosity/traverse": read("curiosity-traverse.json"),
      "/api/v2/rovers/curiosity/location": read("curiosity-location.json"),
    });
    window.history.replaceState(null, "", "/rovers/curiosity/map");
    render(<App />);
    const svg = await screen.findByRole("img", { name: /route with \d+ stops/ });
    const d = svg.querySelector("path.route-base")!.getAttribute("d")!;
    const pts = [...d.matchAll(/[ML]([-\d.]+) ([-\d.]+)/g)].map((m) => [
      Number(m[1]),
      Number(m[2]),
    ]);
    const xs = pts.map((p) => p[0]!);
    expect(Math.min(...xs)).toBeGreaterThanOrEqual(0);
    expect(Math.max(...xs)).toBeLessThanOrEqual(600);
    expect(Math.max(...xs) - Math.min(...xs)).toBeGreaterThan(100);
    expect(svg.querySelector("path.stops-base")!.getAttribute("d")!.length).toBeGreaterThan(1000);
    expect(svg.querySelector("rect.latest-mark")).toBeTruthy();
  });
});

describe("Photo card with real Perseverance data", () => {
  it("the mini-map puts the stop on the real route", async () => {
    const stop = location.items[Math.floor(location.items.length / 2)]!;
    mockApi({
      "/api/v2/rovers": read("rovers.json"),
      "/api/v2/photos/7": detail(7, {
        location: {
          sol: stop.sol,
          site: stop.site,
          drive: stop.drive,
          lat: stop.lat,
          lon: stop.lon,
          elev_geoid: stop.elev_geoid,
          exact: true,
        },
      }),
      "/api/v2/rovers/perseverance/photos": page([], 0),
      "/api/v2/rovers/perseverance/traverse": traverse,
    });
    window.history.replaceState(null, "", "/photos/7");
    render(<App />);
    const mini = await screen.findByRole("img", { name: /route with the position of this photo/ });
    const d = mini.querySelector("path.loc-route")!.getAttribute("d")!;
    expect(d.length).toBeGreaterThan(1000);
    const c = mini.querySelector("circle.loc-pt")!;
    expect(Number(c.getAttribute("cx"))).toBeGreaterThan(0);
    expect(Number(c.getAttribute("cx"))).toBeLessThan(300);
    expect(Number(c.getAttribute("cy"))).toBeLessThan(190);
  });
});
