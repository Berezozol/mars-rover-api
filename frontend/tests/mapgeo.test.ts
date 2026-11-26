import { describe, expect, it } from "vitest";
import type { LocationResponse, Traverse, Waypoint } from "../src/api/types";
import {
  boundsOf,
  centerOn,
  fitView,
  flattenTraverse,
  highlightRuns,
  km,
  latLabel,
  MAX_PPU,
  lonLabel,
  metres,
  minPpu,
  nearestStop,
  niceStep,
  pathData,
  prepare,
  project,
  rangeError,
  scaleBar,
  scaleLabel,
  stopsInRange,
  unproject,
  zoomAt,
} from "../src/lib/mapgeo";
import { thumbRatio } from "../src/components/Thumb";
import { findStop, mapHref } from "../src/lib/maplinks";
import { miniMapGeometry } from "../src/components/LocationBlock";

const wp = (sol: number, site: number, drive: number, lon: number, lat: number): Waypoint => ({
  sol,
  site,
  drive,
  lon,
  lat,
  elev_geoid: -2000,
  easting: null,
  northing: null,
  roll: null,
  pitch: null,
  yaw: null,
  tilt: null,
  dist_m: 10,
  dist_total_m: 1500,
});

const feature = (sol: number | null, coords: [number, number][]) => ({
  type: "Feature" as const,
  geometry: { type: "LineString" as const, coordinates: coords },
  properties: { sol, length_m: 10 },
});

export const traverse = (sols: (number | null)[] = [1, 2, 3]): Traverse => ({
  type: "FeatureCollection",
  rover: "perseverance",
  available: true,
  updated_at: "2026-10-28T15:05:03Z",
  source_max_sol: 3,
  features: [
    feature(sols[0] ?? null, [
      [77.0, 18.0],
      [77.001, 18.001],
    ]),
    feature(sols[1] ?? null, [
      [77.001, 18.001],
      [77.002, 18.001],
      [77.003, 18.002],
    ]),
    feature(sols[2] ?? null, [
      [77.003, 18.002],
      [77.004, 18.004],
    ]),
  ],
});

const stops = [wp(1, 1, 0, 77.0, 18.0), wp(2, 1, 5, 77.003, 18.002), wp(3, 1, 9, 77.004, 18.004)];
const location = (items: Waypoint[]): LocationResponse => ({
  rover: "perseverance",
  available: true,
  updated_at: "2026-10-28T15:05:06Z",
  source_max_sol: 3,
  items,
});

describe("flattenTraverse", () => {
  it("stores vertices in flat arrays and keeps segments as index ranges", () => {
    const r = flattenTraverse(traverse());
    expect(r.lons).toHaveLength(7);
    expect(r.segs).toEqual([
      { sol: 1, from: 0, to: 2 },
      { sol: 2, from: 2, to: 5 },
      { sol: 3, from: 5, to: 7 },
    ]);
    expect(r.lats[3]).toBe(18.001);
  });

  it("skips empty features and keeps null sols", () => {
    const t = traverse([null, null, null]);
    t.features.push(feature(5, []));
    const r = flattenTraverse(t);
    expect(r.segs.map((s) => s.sol)).toEqual([null, null, null]);
  });
});

describe("prepare / bounds / projection", () => {
  it("bounds cover the route and the stops; kx is cos(mean latitude)", () => {
    const d = prepare(traverse(), location(stops))!;
    const meanLat = (18.0 + 18.004) / 2;
    expect(d.kx).toBeCloseTo(Math.cos((meanLat * Math.PI) / 180), 10);
    expect(d.bounds.minX).toBeCloseTo(77.0 * d.kx, 10);
    expect(d.bounds.maxX).toBeCloseTo(77.004 * d.kx, 10);
    expect(d.bounds.minY).toBeCloseTo(-18.004, 10);
    expect(d.bounds.maxY).toBeCloseTo(-18.0, 10);
  });

  it("returns null without any geometry and bounds of stops are null for none", () => {
    const empty: Traverse = { ...traverse(), features: [] };
    expect(prepare(empty, location([]))).toBeNull();
    expect(boundsOf([], 1)).toBeNull();
  });

  it("fitView puts every point of the bounds inside the area with a margin", () => {
    const d = prepare(traverse(), location(stops))!;
    const v = fitView(d.bounds, 600, 440);
    for (const w of stops) {
      const [x, y] = project(v, d.kx, w.lon, w.lat);
      expect(x).toBeGreaterThan(0);
      expect(x).toBeLessThan(600);
      expect(y).toBeGreaterThan(0);
      expect(y).toBeLessThan(440);
    }
    // north is up: a larger latitude has a smaller y
    expect(project(v, d.kx, 77, 18.004)[1]).toBeLessThan(project(v, d.kx, 77, 18.0)[1]);
    // the centre of the bounds is the centre of the area
    const cx = (d.bounds.minX + d.bounds.maxX) / 2 / d.kx;
    const cy = -(d.bounds.minY + d.bounds.maxY) / 2;
    const [px, py] = project(v, d.kx, cx, cy);
    expect(px).toBeCloseTo(300, 6);
    expect(py).toBeCloseTo(220, 6);
  });

  it("unproject is the inverse of project", () => {
    const v = fitView({ minX: 70, maxX: 71, minY: -19, maxY: -18 }, 500, 300);
    const kx = 0.95;
    const [x, y] = project(v, kx, 70.4, 18.3);
    const back = unproject(v, kx, x, y);
    expect(back.lon).toBeCloseTo(70.4, 9);
    expect(back.lat).toBeCloseTo(18.3, 9);
  });

  it("a degree of latitude and of longitude (corrected by cos) have the same scale on screen", () => {
    const v = fitView({ minX: 0, maxX: 1, minY: -1, maxY: 0 }, 500, 500);
    const kx = Math.cos((60 * Math.PI) / 180);
    const [x0, y0] = project(v, kx, 10, 60);
    const [x1] = project(v, kx, 11, 60);
    const [, y1] = project(v, kx, 10, 59);
    expect(Math.abs(x1 - x0)).toBeCloseTo(Math.abs(y1 - y0) * kx, 6);
  });

  it("zoomAt keeps the point under the pointer fixed and respects the limits", () => {
    const v = fitView({ minX: 70, maxX: 71, minY: -19, maxY: -18 }, 600, 400);
    const z = zoomAt(v, 2, 100, 50, 1);
    const before = unproject(v, 1, 100, 50);
    const after = unproject(z, 1, 100, 50);
    expect(after.lon).toBeCloseTo(before.lon, 9);
    expect(after.lat).toBeCloseTo(before.lat, 9);
    expect(z.ppu).toBeCloseTo(v.ppu * 2, 6);
    expect(zoomAt(v, 0.0001, 0, 0, v.ppu * 0.5).ppu).toBeCloseTo(v.ppu * 0.5, 9);
    expect(zoomAt(v, 1e9, 0, 0, 1).ppu).toBe(MAX_PPU);
  });

  it("centerOn moves the centre to a position; minPpu fits the whole route with room", () => {
    const d = prepare(traverse(), location(stops))!;
    const v = centerOn(fitView(d.bounds, 600, 440), d.kx, 77.003, 18.002, 5e6);
    const [x, y] = project(v, d.kx, 77.003, 18.002);
    expect(x).toBeCloseTo(300, 6);
    expect(y).toBeCloseTo(220, 6);
    expect(v.ppu).toBe(5e6);
    // the zoom-out limit shows the whole route with the same margin as the fit
    expect(minPpu(d.bounds, 600, 440)).toBeCloseTo(fitView(d.bounds, 600, 440).ppu, 6);
  });
});

describe("pathData", () => {
  const dense = (n: number): Traverse => ({
    ...traverse(),
    features: [
      feature(
        null,
        Array.from(
          { length: n },
          (_, i) => [77 + i * 1e-6, 18 + (i % 2) * 1e-6] as [number, number],
        ),
      ),
    ],
  });

  it("draws one subpath per connected piece", () => {
    const d = prepare(traverse(), location(stops))!;
    const v = fitView(d.bounds, 600, 440);
    const path = pathData(d.route, d.route.segs, v, d.kx);
    expect((path.match(/M/g) ?? []).length).toBe(3);
    expect((path.match(/L/g) ?? []).length).toBe(4);
  });

  it("merges vertices closer than a pixel: a dense route stays small zoomed out", () => {
    const t = dense(30000);
    const d = prepare(t, location([]))!;
    const v = fitView(d.bounds, 600, 440);
    const path = pathData(d.route, d.route.segs, v, d.kx);
    expect((path.match(/L/g) ?? []).length).toBeLessThan(1500);
    expect(path.startsWith("M")).toBe(true);
  });

  it("drops segments that cannot touch the visible area, keeps those crossing it", () => {
    const d = prepare(traverse(), location(stops))!;
    const whole = fitView(d.bounds, 600, 440);
    // zoom far into the start: only the first segment is near
    const near = centerOn(whole, d.kx, 77.0, 18.0, whole.ppu * 40);
    const path = pathData(d.route, d.route.segs, near, d.kx);
    expect((path.match(/M/g) ?? []).length).toBeLessThan(3);
    // a long segment with both ends off screen but crossing the view is kept
    const t: Traverse = {
      ...traverse(),
      features: [
        feature(1, [
          [76.0, 18.0],
          [78.0, 18.0],
        ]),
      ],
    };
    const r = flattenTraverse(t);
    const v = fitView({ minX: 77 * 0.95, maxX: 77.001 * 0.95, minY: -18.001, maxY: -18 }, 600, 440);
    expect(pathData(r, r.segs, v, 0.95)).toContain("L");
  });

  it("draws nothing for an empty run list", () => {
    const d = prepare(traverse(), location(stops))!;
    expect(pathData(d.route, [], fitView(d.bounds, 600, 440), d.kx)).toBe("");
  });
});

describe("stops: range, nearest, highlight", () => {
  it("stopsInRange keeps list order and the index in the full list", () => {
    expect(stopsInRange(stops, 2, 3).map((o) => o.i)).toEqual([1, 2]);
    expect(stopsInRange(stops, null, 1).map((o) => o.w.sol)).toEqual([1]);
    expect(stopsInRange(stops, 5, null)).toEqual([]);
    expect(stopsInRange(stops, null, null)).toHaveLength(3);
  });

  it("nearestStop picks the closest within the radius, else null", () => {
    const d = prepare(traverse(), location(stops))!;
    const v = fitView(d.bounds, 600, 440);
    const pool = stopsInRange(stops, null, null);
    const [x, y] = project(v, d.kx, stops[1]!.lon, stops[1]!.lat);
    expect(nearestStop(pool, v, d.kx, x + 3, y - 2)).toBe(1);
    expect(nearestStop(pool, v, d.kx, x + 200, y + 200)).toBeNull();
    // only stops of the pool are candidates
    expect(nearestStop(stopsInRange(stops, 1, 1), v, d.kx, x, y)).toBeNull();
  });

  it("highlights the segments of the sol range exactly when segments carry sols", () => {
    const d = prepare(traverse(), location(stops))!;
    const h = highlightRuns(d, 2, 3, stopsInRange(stops, 2, 3));
    expect(h.approximate).toBe(false);
    expect(h.runs.map((r) => r.from)).toEqual([2, 5]);
    expect(highlightRuns(d, null, null, []).runs).toEqual([]);
  });

  it("approximates by snapping to route vertices when segments have no sol (Curiosity)", () => {
    const d = prepare(traverse([null, null, null]), location(stops))!;
    const h = highlightRuns(d, 2, 3, stopsInRange(stops, 2, 3));
    expect(h.approximate).toBe(true);
    // from the vertex at stop 2 (index 4, same position as 3) to the vertex of stop 3 (index 6)
    expect(h.runs.length).toBeGreaterThan(0);
    for (const r of h.runs) {
      expect(r.from).toBeGreaterThanOrEqual(3);
      expect(r.to).toBeLessThanOrEqual(7);
    }
    expect(highlightRuns(d, 9, 9, []).runs).toEqual([]);
  });
});

describe("grid, scale and labels", () => {
  it("niceStep grows as the zoom falls and scaleBar never exceeds its width", () => {
    expect(niceStep(1e6, 1, 90)).toBeLessThan(niceStep(1e4, 1, 90));
    for (const ppu of [1e3, 1e4, 1e5, 1e6, MAX_PPU]) {
      const b = scaleBar(ppu, 130);
      expect(b.px).toBeLessThanOrEqual(130 + 1e-9);
    }
    expect(scaleBar(1e5).km).toBeGreaterThan(scaleBar(MAX_PPU).km);
  });

  it("short scale bars also give metres", () => {
    expect(scaleLabel(0.002)).toBe("0.002 km (2 m)");
    expect(scaleLabel(0.005)).toBe("0.005 km (5 m)");
    expect(scaleLabel(0.01)).toBe("0.01 km");
    expect(scaleLabel(2)).toBe("2 km");
  });

  it("distances are kilometres only", () => {
    expect(km(33874.9)).toBe("33.9 km");
    expect(km(112.4)).toBe("0.11 km");
    expect(km(null)).toBe("—");
    expect(km(1234567)).toBe("1,234.6 km");
    expect(km(0)).toBe("0.00 km");
  });

  it("formats coordinates and elevation", () => {
    expect(latLabel(18.4447)).toBe("18.4447° N");
    expect(latLabel(-3.5)).toBe("3.5000° S");
    expect(lonLabel(77.3951)).toBe("77.3951° E");
    expect(lonLabel(-12)).toBe("12.0000° W");
    expect(metres(-2489.7)).toBe("−2,489.7 m");
    expect(metres(null)).toBe("—");
  });

  it("validates a sol range before any request", () => {
    expect(rangeError("", "")).toBeNull();
    expect(rangeError("10", "20")).toBeNull();
    expect(rangeError("20", "10")).toMatch(/must not be greater/);
    expect(rangeError("-1", "")).toMatch(/whole numbers/);
    expect(rangeError("1.5", "")).toMatch(/whole numbers/);
  });
});

describe("stop links", () => {
  it("findStop matches site and drive first, then the last stop of the sol", () => {
    expect(findStop(stops, 2, 1, 5)).toEqual({ index: 1, exact: true });
    expect(findStop(stops, 3, null, null)).toEqual({ index: 2, exact: true });
    // url-state AC-7: a sol without a stop gives the last stop before it, not "nothing"
    expect(findStop(stops, 99, null, null)).toEqual({ index: 2, exact: false });
    expect(findStop(stops, 0, null, null)).toBeNull(); // before the first stop
    expect(findStop(stops, null, 1, 9)).toEqual({ index: 2, exact: true });
    expect(findStop(stops, 2, 1, 6)).toEqual({ index: 1, exact: true }); // unknown drive: falls back to the sol
  });

  it("mapHref builds the address of the map centred on a stop", () => {
    expect(mapHref("perseverance", { stop: { sol: 2020, site: 93, drive: 1840 } })).toBe(
      "/rovers/perseverance/map?sol=2020&site=93&drive=1840",
    );
    expect(mapHref("curiosity", { from: 10, to: null })).toBe("/rovers/curiosity/map?from_sol=10");
    expect(mapHref("curiosity")).toBe("/rovers/curiosity/map");
  });
});

describe("mini-map and thumbnails", () => {
  it("places the photo position inside 300 x 190 and grows the uncertainty with zoom", () => {
    const g = miniMapGeometry(traverse(), { lat: 18.002, lon: 77.003 });
    expect(g.x).toBeGreaterThan(0);
    expect(g.x).toBeLessThan(300);
    expect(g.y).toBeGreaterThan(0);
    expect(g.y).toBeLessThan(190);
    expect(g.d.startsWith("M")).toBe(true);
    expect(g.uncertaintyPx).toBeGreaterThan(0);
    // a point far from the route stretches the map: the uncertainty circle shrinks
    const far = miniMapGeometry(traverse(), { lat: 18.5, lon: 77.5 });
    expect(far.uncertaintyPx).toBeLessThan(g.uncertaintyPx);
  });

  it("thumbRatio uses width/height within a sane range, 4:3 when unknown", () => {
    expect(thumbRatio(1648, 1200)).toBeCloseTo(1648 / 1200, 10);
    expect(thumbRatio(null, null)).toBeCloseTo(4 / 3, 10);
    expect(thumbRatio(undefined, 100)).toBeCloseTo(4 / 3, 10);
    expect(thumbRatio(5000, 100)).toBe(2.5);
    expect(thumbRatio(100, 5000)).toBe(0.5);
  });
});
