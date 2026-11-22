// Logic of the "Photos near a point" mode of the route map, without a DOM.
import type { SearchQuery, Waypoint } from "../api/types";
import { M_PER_DEG } from "./mapgeo";

/** Rovers whose photos can be searched by place (API.md: `near` works for these two only). */
export const NEAR_ROVERS = ["curiosity", "perseverance"];
export const supportsNear = (rover: string): boolean => NEAR_ROVERS.includes(rover);

export const R_MIN = 0.01;
export const R_MAX = 50;
export const R_DEFAULT = 1;
export const NEAR_PER_PAGE = [24, 48, 96];

/** Mean radius of Mars, km (the map and the API use the same figure for distances). */
const MARS_KM = 3389.5;

// ---------- numbers ----------

/** A coordinate as typed or sent: at most 4 decimals (about 6 m), no trailing zeros. */
export const fmtCoord = (n: number): string => String(Number(n.toFixed(4)));

/** A radius the API accepts (digits, at most 10 decimals, no sign or exponent): `.5` becomes `0.5`. */
export const normRadius = (r: string | number): string => String(Number(Number(r).toFixed(6)));

export const fmtRadius = (r: number): string => String(Number(r.toPrecision(3)));

const NUM = /^[+-]?(\d+\.?\d*|\.\d+)$/;

/** A decimal number from text; null for anything else (empty, letters, `1e3`, `NaN`). */
export function parseNumber(text: string): number | null {
  const t = text.trim().replace(",", ".");
  if (!NUM.test(t)) return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

export type Parsed<T> = { ok: true; value: T } | { ok: false; error: string };

export function parseRadius(text: string): Parsed<number> {
  const n = parseNumber(text);
  if (n === null) return { ok: false, error: "Radius must be a number of kilometres." };
  if (n < R_MIN || n > R_MAX) {
    return { ok: false, error: `Radius must be from ${R_MIN} to ${R_MAX} km.` };
  }
  return { ok: true, value: n };
}

export interface Point {
  lat: number;
  lon: number;
}

export function parseLat(text: string): Parsed<number> {
  const n = parseNumber(text);
  if (n === null) return { ok: false, error: "Latitude must be a number from −90 to 90." };
  if (n < -90 || n > 90) return { ok: false, error: "Latitude must be from −90 to 90 degrees." };
  return { ok: true, value: n };
}

export function parseLon(text: string): Parsed<number> {
  const n = parseNumber(text);
  if (n === null) return { ok: false, error: "Longitude must be a number from −180 to 180." };
  if (n < -180 || n > 180) {
    return { ok: false, error: "Longitude must be from −180 to 180 degrees (east is positive)." };
  }
  return { ok: true, value: n };
}

/** `LAT,LON` of the address. */
export function parseNear(value: string): Parsed<Point> {
  const parts = value.split(",");
  if (parts.length !== 2) return { ok: false, error: "The search point must look like LAT,LON." };
  const lat = parseLat(parts[0]!);
  if (!lat.ok) return lat;
  const lon = parseLon(parts[1]!);
  if (!lon.ok) return lon;
  return { ok: true, value: { lat: lat.value, lon: lon.value } };
}

export const nearValue = (p: Point): string => `${fmtCoord(p.lat)},${fmtCoord(p.lon)}`;

// ---------- the radius slider is logarithmic: 0.01 km at 0, 50 km at 100 ----------

const SPAN = Math.log(R_MAX / R_MIN);
export const radiusToSlider = (r: number): number =>
  Math.round((100 * Math.log(Math.min(R_MAX, Math.max(R_MIN, r)) / R_MIN)) / SPAN);
export function sliderToRadius(v: number): number {
  const r = R_MIN * Math.exp((SPAN * v) / 100);
  // Round to 2-3 significant digits: slider steps should give readable radii.
  return Math.min(R_MAX, Math.max(R_MIN, Number(r.toPrecision(2))));
}

// ---------- geometry ----------

/** Distance in km between two points on a sphere of Mars' radius (haversine). */
export function distKm(a: Point, b: Point): number {
  const rad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * rad;
  const dLon = (b.lon - a.lon) * rad;
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * MARS_KM * Math.asin(Math.min(1, Math.sqrt(h)));
}

/** Radius in degrees of latitude, for drawing the circle. */
export const radiusDeg = (km: number): number => (km * 1000) / M_PER_DEG;

export interface StopHit {
  w: Waypoint;
  i: number;
  d: number;
}

/** Stops within `radius` km of the point, in route order, each with its distance. */
export function stopsWithin(stops: Waypoint[], p: Point, radius: number): StopHit[] {
  const out: StopHit[] = [];
  stops.forEach((w, i) => {
    const d = distKm(p, w);
    if (d <= radius + 1e-9) out.push({ w, i, d });
  });
  return out;
}

export function nearestStopTo(stops: Waypoint[], p: Point): StopHit | null {
  let best: StopHit | null = null;
  stops.forEach((w, i) => {
    const d = distKm(p, w);
    if (!best || d < best.d) best = { w, i, d };
  });
  return best;
}

// ---------- the address ----------

export interface NearState {
  /** The `near` parameter is present (even when empty): the map is in "near" mode. */
  active: boolean;
  point: Point | null;
  /** A problem with `near` in the address (e.g. a hand-made link). */
  pointError: string | null;
  radiusText: string;
  radius: number | null;
  radiusError: string | null;
  camera: string;
  solMin: string;
  solMax: string;
  sort: "-sol" | "sol";
  perPage: number;
  page: number;
}

export function parseNearState(search: string): NearState {
  const q = new URLSearchParams(search);
  const near = q.get("near");
  let point: Point | null = null;
  let pointError: string | null = null;
  if (near !== null && near.trim() !== "") {
    const p = parseNear(near);
    if (p.ok) point = p.value;
    else pointError = p.error;
  }
  const radiusText = q.get("radius_km") ?? String(R_DEFAULT);
  const r = parseRadius(radiusText);
  const per = Number(q.get("per_page"));
  const page = Number(q.get("page"));
  return {
    active: near !== null,
    point,
    pointError,
    radiusText,
    radius: r.ok ? r.value : null,
    radiusError: r.ok ? null : r.error,
    camera: (q.get("camera") ?? "").trim().toUpperCase(),
    solMin: q.get("sol_min") ?? "",
    solMax: q.get("sol_max") ?? "",
    sort: q.get("sort") === "sol" ? "sol" : "-sol",
    perPage: NEAR_PER_PAGE.includes(per) ? per : NEAR_PER_PAGE[0]!,
    // The API refuses page × per_page above 10 000: an address beyond it opens the last browsable page.
    page:
      Number.isInteger(page) && page >= 1
        ? Math.min(
            page,
            Math.floor(10_000 / (NEAR_PER_PAGE.includes(per) ? per : NEAR_PER_PAGE[0]!)),
          )
        : 1,
  };
}

/** The query for `GET /api/v2/photos` near a point; null while the point or the radius is missing or wrong. */
export function nearQuery(rover: string, s: NearState): SearchQuery | null {
  if (!s.point || s.radius === null) return null;
  const q: SearchQuery = {
    rovers: rover,
    near: nearValue(s.point),
    radius_km: normRadius(s.radius),
    sort: s.sort,
    page: s.page,
    per_page: s.perPage,
  };
  if (s.camera) q.camera = s.camera;
  if (s.solMin !== "") q.sol_min = s.solMin;
  if (s.solMax !== "") q.sol_max = s.solMax;
  return q;
}

/** Address of the map in "near" mode. The radius is always written, so a link says what it means. */
export function nearHref(
  rover: string,
  s: {
    point?: Point | null;
    radius?: string | number;
    camera?: string;
    solMin?: string;
    solMax?: string;
    sort?: string;
    perPage?: number;
    page?: number;
  } = {},
): string {
  const p = new URLSearchParams();
  p.set("near", s.point ? nearValue(s.point) : "");
  // With a point the radius is always written, so a link says what it means; without one only a custom radius is kept.
  if (s.point || (s.radius !== undefined && normRadius(s.radius) !== String(R_DEFAULT))) {
    p.set("radius_km", normRadius(s.radius ?? R_DEFAULT));
  }
  if (s.camera) p.set("camera", s.camera);
  if (s.solMin) p.set("sol_min", s.solMin);
  if (s.solMax) p.set("sol_max", s.solMax);
  if (s.sort && s.sort !== "-sol") p.set("sort", s.sort);
  if (s.perPage && s.perPage !== NEAR_PER_PAGE[0]) p.set("per_page", String(s.perPage));
  if (s.page && s.page > 1) p.set("page", String(s.page));
  return `/rovers/${rover}/map?${p.toString().replace(/%2C/g, ",")}`;
}

/** "May be slow": near together with a camera is a heavy search (API.md, "Перегрузка"). */
export const isHeavyNear = (s: { camera: string }): boolean => s.camera !== "";
