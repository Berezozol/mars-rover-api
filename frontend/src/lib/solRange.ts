// Range of sols shown by the data pages (stage 22): defaults, presets, clamping and the `?from=&to=` address.
import { replaceKeepingState } from "../router";
import { niceScale } from "./radiation";
import { niceTicks } from "./weather";

/** Length of a Mars year in sols (668.6, rounded up). */
export const MARS_YEAR_SOLS = 669;
/** The narrowest range: at least this many sols (`to - from + 1`). */
export const MIN_RANGE_SOLS = 6;

/** `[from, to]`, both sols included. */
export type SolRange = [number, number];

/** The newest published sol and the first sol of the data. */
export interface RangeBase {
  first: number;
  /** `source_max_sol`: the newest sol the archive has published. */
  sourceMax: number;
}

/** Limits of the sol axis: the first sol of the data and the newest sol of the rover (or of the data). */
export interface RangeBounds {
  first: number;
  max: number;
}

export type RangePreset = "year" | "zoom" | "all";
export const RANGE_PRESETS: { id: RangePreset; label: string }[] = [
  { id: "year", label: "Last Mars year (default)" },
  { id: "zoom", label: "Last 120 sols" },
  { id: "all", label: "Whole published data" },
];
const ZOOM_SOLS = 120;

const clamp = (v: number, lo: number, hi: number): number => Math.min(Math.max(v, lo), hi);

/** The last Mars year ending at the newest published sol (not at the rover's newest sol). */
export function defaultRange({ first, sourceMax }: RangeBase): SolRange {
  return clampRange([sourceMax - (MARS_YEAR_SOLS - 1), sourceMax], { first, max: sourceMax });
}

/** The range of a preset; all of them end at the newest published sol. */
export function presetRange(preset: RangePreset, base: RangeBase): SolRange {
  const { first, sourceMax } = base;
  if (preset === "all") return clampRange([first, sourceMax], { first, max: sourceMax });
  const n = preset === "zoom" ? ZOOM_SOLS : MARS_YEAR_SOLS;
  return clampRange([sourceMax - (n - 1), sourceMax], { first, max: sourceMax });
}

/**
 * Put a range inside `[first, max]`, keep `from <= to` and at least `MIN_RANGE_SOLS` sols.
 * When the bounds themselves are narrower than that, the whole bounds are returned.
 */
export function clampRange(range: SolRange, { first, max }: RangeBounds): SolRange {
  const lo = Math.min(first, max);
  const hi = Math.max(first, max);
  const gap = MIN_RANGE_SOLS - 1;
  if (hi - lo < gap) return [lo, hi];
  let [a, b] = range[0] <= range[1] ? range : ([range[1], range[0]] as SolRange);
  a = clamp(Math.round(a), lo, hi - gap);
  b = clamp(Math.round(b), a + gap, hi);
  return [a, b];
}

/** `?from=4591&to=4710` -> `[4591, 4710]`; anything wrong or incomplete -> `null` (use the default). */
export function parseRangeQuery(search: string, bounds?: RangeBounds): SolRange | null {
  const q = new URLSearchParams(search);
  const num = (key: string): number | null => {
    const s = q.get(key);
    if (s === null || !/^\d{1,9}$/.test(s.trim())) return null;
    return Number(s.trim());
  };
  const from = num("from");
  const to = num("to");
  if (from === null || to === null || from > to) return null;
  if (bounds && !rangeIntersects([from, to], bounds)) return null;
  return [from, to];
}

/** True when the range shares at least one sol with `[first, max]`. */
export function rangeIntersects(range: SolRange, { first, max }: RangeBounds): boolean {
  return range[0] <= max && range[1] >= first;
}

/** The address with `from` and `to` set (or removed for `null`); other parameters and the fragment stay. */
export function rangeQuery(range: SolRange | null, search: string): string {
  const q = new URLSearchParams(search);
  q.delete("from");
  q.delete("to");
  if (range) {
    q.set("from", String(range[0]));
    q.set("to", String(range[1]));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

/** The rover named by `?rover=`, or null. */
export function roverFromQuery(search: string): string | null {
  return new URLSearchParams(search).get("rover");
}

/**
 * Keep `?from=&to=` (and, when `rover` is given, `?rover=`) in the address without adding history
 * entries. `rover: null` removes the parameter, `undefined` leaves it alone.
 */
export function writeRangeQuery(range: SolRange | null, rover?: string | null): void {
  try {
    const { pathname, search, hash } = window.location;
    let q = rangeQuery(range, search);
    if (rover !== undefined) {
      const params = new URLSearchParams(q);
      if (rover === null) params.delete("rover");
      else params.set("rover", rover);
      const s = params.toString();
      q = s ? `?${s}` : "";
    }
    const next = `${pathname}${q}${hash}`;
    if (next !== `${pathname}${search}${hash}`) replaceKeepingState(next);
  } catch {
    // Some browsers limit how often the address may change; the range just stays unsaved then.
  }
}

/**
 * A coarse picture of the whole data for the slider track: `n` buckets over `[first, max]`,
 * each the mean of the values in it scaled to 0..1 (`null` for a bucket without values).
 */
export function overviewOf(
  points: [number, number][],
  { first, max }: RangeBounds,
  n = 240,
): (number | null)[] {
  const sum = new Array<number>(n).fill(0);
  const cnt = new Array<number>(n).fill(0);
  const span = Math.max(max - first, 1);
  for (const [sol, v] of points) {
    if (!Number.isFinite(v)) continue;
    const i = clamp(Math.floor(((sol - first) / span) * n), 0, n - 1);
    sum[i]! += v;
    cnt[i]! += 1;
  }
  const means = sum.map((s, i) => (cnt[i]! > 0 ? s / cnt[i]! : null));
  const vals = means.filter((v): v is number => v !== null);
  if (vals.length === 0) return means;
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  return means.map((v) => (v === null ? null : hi > lo ? (v - lo) / (hi - lo) : 0.5));
}

/** Vertical scale of the radiation chart: from zero for long ranges, otherwise fitted to the data. */
export type ScaleMode = "auto" | "zero" | "fit";
export const AUTO_ZERO_SOLS = 400;
export const zeroBasedScale = (mode: ScaleMode, range: SolRange): boolean =>
  mode === "zero" || (mode === "auto" && range[1] - range[0] + 1 >= AUTO_ZERO_SOLS);

/** Tick positions along the sol axis: both ends of the range and round values between them. */
export function rangeTicks(range: SolRange, count: number): number[] {
  const [x0, x1] = range;
  const keep = (x1 - x0) * 0.1;
  return [x0, ...niceTicks(x0, x1, count).filter((t) => t > x0 + keep && t < x1 - keep), x1];
}

/** A series needs at least this many points before its extreme values may be cut off the scale. */
export const OUTLIER_MIN_POINTS = 20;
const OUTLIER_FACTOR = 3;

/**
 * Bottom, top and ticks of a value axis: from zero, or fitted to the values of `points`
 * (`[sol, value]`). With at least 20 points, values above three times the median are outliers: when `clip` is set (default: unless `zero`), they are left out of the scale and
 * returned in `outliers` (one `[sol, value]` per sol, the highest value of it, by sol).
 */
export function valueScale(
  points: [number, number][],
  zero: boolean,
  clip: boolean = !zero,
): { bottom: number; top: number; ticks: number[]; outliers: [number, number][] } {
  const pts = points.filter(([, v]) => Number.isFinite(v));
  let outliers: [number, number][] = [];
  if (clip && pts.length >= OUTLIER_MIN_POINTS) {
    const sorted = pts.map(([, v]) => v).sort((a, b) => a - b);
    const mid = sorted.length >> 1;
    const p = sorted.length % 2 ? sorted[mid]! : (sorted[mid - 1]! + sorted[mid]!) / 2;
    if (p > 0) {
      const bySol = new Map<number, number>();
      for (const [sol, v] of pts) {
        if (v > OUTLIER_FACTOR * p) bySol.set(sol, Math.max(bySol.get(sol) ?? v, v));
      }
      outliers = [...bySol].sort((a, b) => a[0] - b[0]);
    }
  }
  const out = new Set(outliers.map(([sol]) => sol));
  const ok = pts.filter(([sol]) => !out.has(sol)).map(([, v]) => v);
  if (zero || ok.length === 0) {
    const s = niceScale(ok.length ? Math.max(...ok) : 0);
    return { bottom: 0, top: s.top, ticks: s.ticks, outliers };
  }
  let lo = Math.min(...ok);
  let hi = Math.max(...ok);
  if (hi === lo) {
    lo -= Math.abs(lo) * 0.05 || 1;
    hi += Math.abs(hi) * 0.05 || 1;
  }
  const ticks = niceTicks(lo, hi, 4);
  return { bottom: ticks[0] ?? lo, top: ticks[ticks.length - 1] ?? hi, ticks, outliers };
}

/** The sentence about sols off the scale (`""` for none); `fmtValue` and `unit` follow the chart. */
export function offScaleText(
  outliers: [number, number][],
  fmtValue: (v: number) => string,
  unit: string,
): string {
  if (outliers.length === 0) return "";
  let [sol, v] = outliers[0]!;
  for (const o of outliers) if (o[1] > v) [sol, v] = o;
  return outliers.length === 1
    ? `Sol ${sol}: ${fmtValue(v)} ${unit}, off scale`
    : `${outliers.length} sols off scale, highest sol ${sol}: ${fmtValue(v)} ${unit}`;
}
