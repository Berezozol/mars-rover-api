// Weather by sol (stage 17): labels, flag wording, scales and per-parameter reading of a WeatherSol.
import type { WeatherFlag, WeatherParameter, WeatherSol } from "../api/types";
import { lagMonths } from "./radiation";

export interface WeatherRover {
  id: string;
  name: string;
  instrument: string;
}

/** Rovers that have the weather endpoints. Which parameters each has comes from the response, not from here. */
export const WEATHER_ROVERS: WeatherRover[] = [
  { id: "perseverance", name: "Perseverance", instrument: "MEDA" },
  { id: "curiosity", name: "Curiosity", instrument: "REMS" },
];

// Display order, labels and decimals of the parameters we know; others get a label made from the key.
const ORDER = ["air_temp_c", "pressure_pa", "humidity_pct", "wind", "ground_temp_c", "uv_wm2"];
const LABEL: Record<string, string> = {
  air_temp_c: "Air temp",
  pressure_pa: "Pressure",
  humidity_pct: "Humidity",
  wind: "Wind",
  ground_temp_c: "Ground temp",
  uv_wm2: "UV",
};
const NAME: Record<string, string> = {
  air_temp_c: "air temperature",
  pressure_pa: "pressure",
  humidity_pct: "humidity",
  wind: "wind",
  ground_temp_c: "ground temperature",
  uv_wm2: "UV radiation",
};
const DIGITS: Record<string, number> = { uv_wm2: 2 };
const NOTE: Record<string, string> = {
  air_temp_c:
    "Band: lowest to highest valid reading of the sol. Line: mean of the hourly means, so uneven sampling does not skew it.",
  pressure_pa:
    "Pressure follows the Martian seasons (the whole atmosphere breathes in and out over the year); the scale starts near the data, not at zero.",
  humidity_pct:
    "Humidity is preliminary: the instrument teams still revise its calibration. Values above 100 % are impossible; they are shown as published and marked as an anomaly.",
  wind: "Line: mean wind speed (mean of hourly means). Dashed: gust, the 99th percentile of the sol (single sensor spikes are not used). Perseverance wind exists only for sols 36–313.",
  ground_temp_c:
    "Ground (brightness) temperature from the infrared sensor, valid readings only. Readings outside physical limits are left out and flagged.",
  uv_wm2:
    "Total UV-A/B/C flux. The band runs from 0 (night) to the daily maximum; the API gives only max and mean for UV.",
};
const ZERO_BASED = new Set(["humidity_pct", "wind", "uv_wm2"]);

const prettyKey = (key: string): string =>
  key.replace(/_(c|pa|pct|ms|wm2)$/, "").replace(/_/g, " ");

export const paramLabel = (key: string): string => {
  const s = LABEL[key] ?? prettyKey(key);
  return s.charAt(0).toUpperCase() + s.slice(1);
};
export const paramName = (key: string): string => NAME[key] ?? prettyKey(key);
export const paramDigits = (key: string): number => DIGITS[key] ?? 1;
export const paramNote = (key: string): string => NOTE[key] ?? "";
export const zeroBased = (key: string): boolean => ZERO_BASED.has(key);

/** Parameter keys of a rover in display order; keys we do not know come last, in the order of the header. */
export function paramKeys(parameters: Record<string, WeatherParameter>): string[] {
  const keys = Object.keys(parameters);
  return [...ORDER.filter((k) => keys.includes(k)), ...keys.filter((k) => !ORDER.includes(k))];
}

/** `measured` needs no mark; anything else (`preliminary`, `estimate`, future values) is shown as a badge. */
export const qualityBadge = (p: WeatherParameter | undefined): string | null =>
  p && p.quality !== "measured" ? p.quality : null;

// ---- numbers ----

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
export { isNum };

/** A number with a fixed number of decimals and a real minus sign; a dash for a missing value. */
export function fmtV(v: number | null | undefined, d: number): string {
  if (!isNum(v)) return "–";
  const s = Math.abs(v).toFixed(d);
  return (v < 0 && Number(s) !== 0 ? "−" : "") + s;
}

/** Round tick values from `lo` to `hi`, about `n` intervals. */
export function niceTicks(lo: number, hi: number, n: number): number[] {
  const span = hi - lo || 1;
  const raw = span / n;
  const p = 10 ** Math.floor(Math.log10(raw));
  const m = raw / p;
  const step = (m < 1.5 ? 1 : m < 3 ? 2 : m < 7 ? 5 : 10) * p;
  const a = Math.floor(lo / step + 1e-9) * step;
  const b = Math.ceil(hi / step - 1e-9) * step;
  const out: number[] = [];
  for (let v = a; v <= b + step * 1e-6; v += step) out.push(Number(v.toFixed(6)));
  return out;
}

const COMPASS = [
  "N",
  "NNE",
  "NE",
  "ENE",
  "E",
  "ESE",
  "SE",
  "SSE",
  "S",
  "SSW",
  "SW",
  "WSW",
  "W",
  "WNW",
  "NW",
  "NNW",
];
export const compass = (deg: number): string => COMPASS[Math.round(deg / 22.5) % 16] ?? "N";

// ---- reading a parameter of a sol ----

/** What the chart draws for one sol: an optional lower edge, an upper edge and the mean line. */
export interface Band {
  lo: number | null;
  hi: number;
  mean: number;
}

/** The parameter of a sol as an object of unknown shape: `undefined` = no such sensor, `null` = no data. */
export const rawOf = (sol: WeatherSol, key: string): unknown =>
  (sol as unknown as Record<string, unknown>)[key];

/** `null` when the sol has no value for the parameter (or the sensor does not exist). */
export function bandOf(sol: WeatherSol, key: string): Band | null {
  const v = rawOf(sol, key);
  if (!v || typeof v !== "object") return null;
  const o = v as Record<string, unknown>;
  if ("speed_mean_ms" in o) {
    if (!isNum(o.speed_mean_ms)) return null;
    return { lo: null, hi: isNum(o.gust_ms) ? o.gust_ms : o.speed_mean_ms, mean: o.speed_mean_ms };
  }
  if (!isNum(o.mean)) return null;
  if (isNum(o.min) && isNum(o.max)) return { lo: o.min, hi: o.max, mean: o.mean };
  if (isNum(o.max)) return { lo: key === "uv_wm2" ? 0 : null, hi: o.max, mean: o.mean };
  return { lo: null, hi: o.mean, mean: o.mean };
}

/** The upper edge of the wind chart is a gust line, not a band. */
export const hasGustLine = (key: string): boolean => key === "wind";

/** The key of the hourly values of a parameter in `GET /weather/{sol}`. */
export const hourKey = (key: string): string => (key === "wind" ? "wind_speed_ms" : key);

const HOUR_TITLE: Record<string, string> = {
  air_temp_c: "Air temperature",
  pressure_pa: "Pressure",
  humidity_pct: "Humidity",
  wind_speed_ms: "Wind speed",
  ground_temp_c: "Ground temperature",
  uv_wm2: "UV",
};
export const hourTitle = (hk: string): string => HOUR_TITLE[hk] ?? paramLabel(hk);

// ---- flags ----

const FLAG_SHORT: Record<string, string> = {
  partial_coverage: "partial coverage",
  humidity_over_100: "humidity above 100 %",
  out_of_range: "out of range",
};
export const FLAG_ORDER = ["partial_coverage", "humidity_over_100", "out_of_range"];

/** A short plain-words name of a flag; an unknown code is shown as the code itself. */
export const flagShort = (f: WeatherFlag): string => FLAG_SHORT[f.code] ?? f.code;

/** The CSS class suffix: unknown codes share one neutral look. */
export const flagClass = (f: WeatherFlag): string => (f.code in FLAG_SHORT ? f.code : "unknown");

export function flagWords(f: WeatherFlag): string {
  const n = f.count;
  if (f.code === "partial_coverage") {
    return "Measured in fewer than 18 of the 24 local hours. The mean is shown but may lean towards day or night.";
  }
  if (f.code === "humidity_over_100") {
    return `Humidity above 100 % in ${n ?? "some"} readings. That is physically impossible, so it is a sensor artefact; the values are kept as published.`;
  }
  if (f.code === "out_of_range") {
    const what = f.parameter ? paramName(f.parameter) : "a parameter";
    return `${n ?? "Some"} ${n === 1 ? "reading" : "readings"} of ${what} fell outside physical limits and ${n === 1 ? "was" : "were"} left out.`;
  }
  return `A note from the data source (code ${f.code}${f.parameter ? `, ${paramName(f.parameter)}` : ""}${n !== null ? `, ${n}` : ""}). Values are shown as published.`;
}

/** Flags that point at a problem with the values (everything except incomplete coverage). */
export const isAnomaly = (s: WeatherSol): boolean =>
  s.flags.some((f) => f.code !== "partial_coverage");
export const isPartial = (s: WeatherSol): boolean =>
  s.flags.some((f) => f.code === "partial_coverage");

// ---- how far the data is behind the rover ----

/** True while only the beginning of the mission is loaded: the whole-mission range would be mostly empty. */
export const isPartialLoad = (last: number, now: number): boolean => now > 0 && last / now < 0.5;

/** "N sols behind", with years once the gap is long. */
export function lagWords(sols: number): string {
  const n = `${sols} ${sols === 1 ? "sol" : "sols"}`;
  const months = lagMonths(sols);
  if (months >= 24) return `${n} (about ${Math.round(months / 12)} years) behind`;
  if (months >= 1) return `${n} (about ${months} ${months === 1 ? "month" : "months"}) behind`;
  return `${n} behind`;
}

/** The sol in `sols` (sorted ascending) nearest to `target`. */
export function nearestIndex(sols: number[], target: number): number {
  let lo = 0;
  let hi = sols.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if ((sols[mid] ?? 0) < target) lo = mid + 1;
    else hi = mid;
  }
  if (lo > 0 && Math.abs((sols[lo - 1] ?? 0) - target) <= Math.abs((sols[lo] ?? 0) - target)) {
    return lo - 1;
  }
  return lo;
}
