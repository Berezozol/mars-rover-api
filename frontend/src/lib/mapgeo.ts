// Geometry of the route map: flat route data, projection, view fitting and compact SVG paths.
// Pure functions only (no DOM), so they are unit-tested. No basemap: coordinates go straight to SVG.
import type { LocationResponse, Traverse, Waypoint } from "../api/types";

/** Metres per degree of latitude on Mars (R = 3389.5 km). */
export const M_PER_DEG = 59160;

/** Highest zoom: about 3 cm per pixel. */
export const MAX_PPU = 2e6;

/** Route as flat arrays (no per-point objects): segment `s` covers vertices `[from, to)`. */
export interface Route {
  lons: number[];
  lats: number[];
  segs: { sol: number | null; from: number; to: number }[];
}

export function flattenTraverse(fc: Traverse): Route {
  const lons: number[] = [];
  const lats: number[] = [];
  const segs: Route["segs"] = [];
  for (const f of fc.features ?? []) {
    const coords = f?.geometry?.coordinates;
    if (!Array.isArray(coords) || coords.length === 0) continue;
    const from = lons.length;
    for (const c of coords) {
      lons.push(c[0]);
      lats.push(c[1]);
    }
    const sol = f.properties?.sol;
    segs.push({ sol: typeof sol === "number" ? sol : null, from, to: lons.length });
  }
  return { lons, lats, segs };
}

/** Bounds in projected units (X = lon * kx, Y = -lat), plus the longitude scale `kx`. */
export interface Bounds {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
}

export interface Prepared {
  route: Route;
  stops: Waypoint[];
  kx: number;
  bounds: Bounds;
}

export function prepare(traverse: Traverse, location: LocationResponse): Prepared | null {
  const route = flattenTraverse(traverse);
  const stops = location.items;
  let minLon = Infinity;
  let maxLon = -Infinity;
  let minLat = Infinity;
  let maxLat = -Infinity;
  const add = (lon: number, lat: number) => {
    if (lon < minLon) minLon = lon;
    if (lon > maxLon) maxLon = lon;
    if (lat < minLat) minLat = lat;
    if (lat > maxLat) maxLat = lat;
  };
  for (let i = 0; i < route.lons.length; i++) add(route.lons[i]!, route.lats[i]!);
  for (const w of stops) add(w.lon, w.lat);
  if (!Number.isFinite(minLon)) return null;
  const kx = Math.cos((((minLat + maxLat) / 2) * Math.PI) / 180);
  return {
    route,
    stops,
    kx,
    bounds: { minX: minLon * kx, maxX: maxLon * kx, minY: -maxLat, maxY: -minLat },
  };
}

export function boundsOf(stops: Waypoint[], kx: number): Bounds | null {
  if (!stops.length) return null;
  const b: Bounds = { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity };
  for (const w of stops) {
    b.minX = Math.min(b.minX, w.lon * kx);
    b.maxX = Math.max(b.maxX, w.lon * kx);
    b.minY = Math.min(b.minY, -w.lat);
    b.maxY = Math.max(b.maxY, -w.lat);
  }
  return b;
}

/** Pixel view: centre in projected units, pixels per unit, and the size of the drawing area. */
export interface View {
  cx: number;
  cy: number;
  ppu: number;
  W: number;
  H: number;
}

export function project(v: View, kx: number, lon: number, lat: number): [number, number] {
  return [(lon * kx - v.cx) * v.ppu + v.W / 2, (-lat - v.cy) * v.ppu + v.H / 2];
}

export function unproject(v: View, kx: number, x: number, y: number): { lon: number; lat: number } {
  return {
    lon: ((x - v.W / 2) / v.ppu + v.cx) / kx,
    lat: -((y - v.H / 2) / v.ppu + v.cy),
  };
}

/** The view that shows `b` with `pad` (fraction) of margin on every side. */
export function fitView(b: Bounds, W: number, H: number, pad = 0.12): View {
  const dx = Math.max(b.maxX - b.minX, 1e-5);
  const dy = Math.max(b.maxY - b.minY, 1e-5);
  return {
    W,
    H,
    ppu: Math.min(W / (dx * (1 + pad * 2)), H / (dy * (1 + pad * 2))),
    cx: (b.minX + b.maxX) / 2,
    cy: (b.minY + b.maxY) / 2,
  };
}

/** The view centred on a point at a given pixels-per-unit (for "show this stop"). */
export function centerOn(v: View, kx: number, lon: number, lat: number, ppu = v.ppu): View {
  return { ...v, ppu, cx: lon * kx, cy: -lat };
}

/** Lowest allowed zoom: the whole route fits with a margin. */
export function minPpu(b: Bounds, W: number, H: number): number {
  return Math.min(
    W / (Math.max(b.maxX - b.minX, 1e-5) * 1.24),
    H / (Math.max(b.maxY - b.minY, 1e-5) * 1.24),
  );
}

/** Zoom by factor `f` around pixel (ax, ay), keeping the point under it fixed. */
export function zoomAt(v: View, f: number, ax: number, ay: number, lo: number, hi = MAX_PPU): View {
  const wx = (ax - v.W / 2) / v.ppu + v.cx;
  const wy = (ay - v.H / 2) / v.ppu + v.cy;
  const ppu = Math.min(Math.max(v.ppu * f, lo), hi);
  return { ...v, ppu, cx: wx - (ax - v.W / 2) / ppu, cy: wy - (ay - v.H / 2) / ppu };
}

const fixed = (n: number) => n.toFixed(1);

/**
 * One compact SVG path for several runs of route vertices. Segments that cannot touch the visible
 * area are skipped and vertices closer than ~0.6 px to the previous drawn one are merged, so a
 * 27 000-vertex route stays a small string when zoomed out (and cheap when zoomed in).
 */
export function pathData(
  route: Route,
  runs: { from: number; to: number }[],
  v: View,
  kx: number,
  margin = 24,
): string {
  const { lons, lats } = route;
  const lo = -margin;
  const hiX = v.W + margin;
  const hiY = v.H + margin;
  let d = "";
  for (const run of runs) {
    let px = 0;
    let py = 0;
    let lastX = NaN;
    let lastY = NaN;
    let drawnPrev = false; // the previous vertex is the end of the open subpath
    for (let i = run.from; i < run.to; i++) {
      const x = (lons[i]! * kx - v.cx) * v.ppu + v.W / 2;
      const y = (-lats[i]! - v.cy) * v.ppu + v.H / 2;
      if (i > run.from) {
        const visible = !(
          (x < lo && px < lo) ||
          (x > hiX && px > hiX) ||
          (y < lo && py < lo) ||
          (y > hiY && py > hiY)
        );
        if (visible) {
          if (!drawnPrev) {
            d += `M${fixed(px)} ${fixed(py)}`;
            lastX = px;
            lastY = py;
          }
          if (Math.abs(x - lastX) + Math.abs(y - lastY) >= 0.6) {
            d += `L${fixed(x)} ${fixed(y)}`;
            lastX = x;
            lastY = y;
          }
          drawnPrev = true;
        } else {
          drawnPrev = false;
        }
      }
      px = x;
      py = y;
    }
  }
  return d;
}

/** Stop dots as zero-length subpaths (round caps make them dots); only those inside the view. */
export function dotsPath(stops: Waypoint[], v: View, kx: number): string {
  let d = "";
  for (const w of stops) {
    const [x, y] = project(v, kx, w.lon, w.lat);
    if (x > -10 && x < v.W + 10 && y > -10 && y < v.H + 10) d += `M${fixed(x)} ${fixed(y)}h0`;
  }
  return d;
}

/** Stops of a sol range with their index in the full list (list order is the movement order). */
export function stopsInRange(
  stops: Waypoint[],
  from: number | null,
  to: number | null,
): { w: Waypoint; i: number }[] {
  const f = from ?? -Infinity;
  const t = to ?? Infinity;
  const out: { w: Waypoint; i: number }[] = [];
  stops.forEach((w, i) => {
    if (w.sol >= f && w.sol <= t) out.push({ w, i });
  });
  return out;
}

/** The index of the stop closest to a pixel, within `radius` px, or null. */
export function nearestStop(
  pool: { w: Waypoint; i: number }[],
  v: View,
  kx: number,
  mx: number,
  my: number,
  radius = 14,
): number | null {
  let best: number | null = null;
  let bd = radius * radius;
  for (const o of pool) {
    const [x, y] = project(v, kx, o.w.lon, o.w.lat);
    const e = (x - mx) ** 2 + (y - my) ** 2;
    if (e <= bd) {
      bd = e;
      best = o.i;
    }
  }
  return best;
}

/**
 * Route runs to highlight for a sol range. With sols on segments (Perseverance) it is exact. When
 * segments have no sol (Curiosity) the first and last stop of the range are snapped to the nearest
 * route vertices and the part between them is highlighted: an approximation.
 */
export function highlightRuns(
  data: Prepared,
  from: number | null,
  to: number | null,
  inRange: { w: Waypoint; i: number }[],
): { runs: { from: number; to: number }[]; approximate: boolean } {
  if (from === null && to === null) return { runs: [], approximate: false };
  const { route } = data;
  const hasSols = route.segs.some((s) => s.sol !== null);
  if (hasSols) {
    const f = from ?? -Infinity;
    const t = to ?? Infinity;
    return {
      runs: route.segs.filter((s) => s.sol !== null && s.sol >= f && s.sol <= t),
      approximate: false,
    };
  }
  if (!inRange.length || !route.lons.length) return { runs: [], approximate: true };
  const nearest = (w: Waypoint) => {
    let bi = 0;
    let bd = Infinity;
    for (let i = 0; i < route.lons.length; i++) {
      const e = (route.lons[i]! - w.lon) ** 2 + (route.lats[i]! - w.lat) ** 2;
      if (e < bd) {
        bd = e;
        bi = i;
      }
    }
    return bi;
  };
  let a = nearest(inRange[0]!.w);
  let b = nearest(inRange[inRange.length - 1]!.w);
  if (a > b) [a, b] = [b, a];
  // Vertices of different segments are not joined: highlight each segment's slice separately.
  const runs: { from: number; to: number }[] = [];
  for (const s of route.segs) {
    const from_ = Math.max(s.from, a);
    const to_ = Math.min(s.to, b + 1);
    if (to_ - from_ >= 2) runs.push({ from: from_, to: to_ });
  }
  return { runs, approximate: true };
}

/** Grid step in degrees giving at least `minPx` between lines. */
export function niceStep(ppu: number, kx: number, minPx: number): number {
  const steps = [
    0.00002, 0.00005, 0.0001, 0.0002, 0.0005, 0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1,
  ];
  const pxPerDeg = ppu * Math.min(1, kx);
  return steps.find((s) => s * pxPerDeg >= minPx) ?? steps[steps.length - 1]!;
}

const BAR_KM = [0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10];

/** The longest round scale bar (km) that is at most `maxPx` wide, and its width in px. */
export function scaleBar(ppu: number, maxPx = 130): { km: number; px: number } {
  const mPerPx = M_PER_DEG / ppu;
  let km = BAR_KM[0]!;
  for (const t of BAR_KM) if ((t * 1000) / mPerPx <= maxPx) km = t;
  return { km, px: (km * 1000) / mPerPx };
}

/** Scale bar text in km; very short bars also give metres: `0.002 km (2 m)`. */
export function scaleLabel(km: number): string {
  return km < 0.01 ? `${km} km (${Math.round(km * 1000)} m)` : `${km} km`;
}

// ---------- formatting (distances in km only) ----------

const num = (n: number, d: number) =>
  n.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });

/** Metres as kilometres: `33.9 km`, `0.11 km`; null as an em dash. */
export function km(m: number | null | undefined): string {
  if (m === null || m === undefined) return "—";
  const k = m / 1000;
  return `${num(k, Math.abs(k) >= 10 ? 1 : 2)} km`;
}

export const latLabel = (v: number): string => `${Math.abs(v).toFixed(4)}° ${v >= 0 ? "N" : "S"}`;
export const lonLabel = (v: number): string => `${Math.abs(v).toFixed(4)}° ${v >= 0 ? "E" : "W"}`;

/** Elevation with a real minus sign. */
export const metres = (v: number | null | undefined): string =>
  v === null || v === undefined ? "—" : `${num(v, 1).replace("-", "−")} m`;

/** Validate a sol range typed by the visitor; returns an error text or null. */
export function rangeError(from: string, to: string): string | null {
  const parse = (s: string) => (s.trim() === "" ? null : Number(s));
  const f = parse(from);
  const t = parse(to);
  const bad = (n: number | null) => n !== null && (!Number.isInteger(n) || n < 0);
  if (bad(f) || bad(t)) return "Sols must be whole numbers, 0 or greater.";
  if (f !== null && t !== null && f > t) return "“From sol” must not be greater than “To sol”.";
  return null;
}
