// What the sol report draws: one chart definition per parameter, read from `GET /weather/{sol}`.
import type { WeatherHour, WeatherSolDetail } from "../api/types";
import { isNum } from "./weather";

export type HourValues = (number | null)[];

export interface ChartSeries {
  name: string;
  values: HourValues;
  /** Drawn dashed (gust, detector B). */
  dashed?: boolean;
}

export interface ChartDef {
  id: string;
  title: string;
  unit: string;
  digits: number;
  /** The value axis starts at zero. */
  zero?: boolean;
  series: ChartSeries[];
  /** Lowest and highest value of each hour (`_min` / `_max`); `null` for an hour without readings. */
  hourBand?: ({ lo: number; hi: number } | null)[];
  /** Fallback band: lowest and highest value of the whole sol. */
  solBand?: [number, number];
  /** A horizontal reference line for the whole sol (UV maximum, wind gust of the sol). */
  refLine?: { value: number; label: string };
  /** Wind direction of each hour, degrees. */
  direction?: HourValues;
  quality?: "preliminary" | "estimate";
  /** One line of numbers for the whole sol. */
  summary: string;
  /** The sensor exists but this sol has no data at all. */
  noData?: string;
}

const pad2 = (h: number) => String(h).padStart(2, "0");
export const hourLabel = (h: number): string => `${pad2(h)}:00–${pad2((h + 1) % 24)}:00`;

/** Value of the key in each of the 24 hours (`null` for a missing hour or value). */
function column(hours: WeatherHour[], key: string): HourValues {
  const out: HourValues = Array.from({ length: 24 }, () => null);
  for (const h of hours) {
    const v = (h as unknown as Record<string, unknown>)[key];
    if (Number.isInteger(h.hour) && h.hour >= 0 && h.hour < 24 && isNum(v)) out[h.hour] = v;
  }
  return out;
}

const hasKey = (hours: WeatherHour[], key: string): boolean =>
  hours.some((h) => (h as unknown as Record<string, unknown>)[key] !== undefined);

function bandOf(hours: WeatherHour[], key: string): ChartDef["hourBand"] {
  if (!hasKey(hours, `${key}_min`) || !hasKey(hours, `${key}_max`)) return undefined;
  const lo = column(hours, `${key}_min`);
  const hi = column(hours, `${key}_max`);
  const band = lo.map((l, i) => (l !== null && hi[i] !== null ? { lo: l, hi: hi[i]! } : null));
  return band.some((b) => b !== null) ? band : undefined;
}

const fmtNum = (v: number, d: number): string =>
  `${v < 0 && Number(Math.abs(v).toFixed(d)) !== 0 ? "−" : ""}${Math.abs(v).toFixed(d)}`;

interface Spec {
  key: string;
  id: string;
  title: string;
  unit: string;
  digits: number;
  zero?: boolean;
}

const SPECS: Spec[] = [
  { key: "air_temp_c", id: "air", title: "Air temperature", unit: "°C", digits: 1 },
  { key: "ground_temp_c", id: "ground", title: "Ground temperature", unit: "°C", digits: 1 },
  { key: "pressure_pa", id: "pressure", title: "Pressure", unit: "Pa", digits: 1 },
  {
    key: "humidity_pct",
    id: "humidity",
    title: "Relative humidity",
    unit: "%",
    digits: 1,
    zero: true,
  },
  {
    key: "uv_wm2",
    id: "uv",
    title: "Ultraviolet flux (UV-A+B+C)",
    unit: "W/m²",
    digits: 2,
    zero: true,
  },
];

/** Sol-level numbers of a parameter as `[min, mean, max]` (any may be missing). */
function solStat(sol: WeatherSolDetail, key: string) {
  const v = (sol as unknown as Record<string, unknown>)[key];
  if (!v || typeof v !== "object") return null;
  const o = v as { min?: unknown; max?: unknown; mean?: unknown };
  return {
    min: isNum(o.min) ? o.min : null,
    max: isNum(o.max) ? o.max : null,
    mean: isNum(o.mean) ? o.mean : null,
  };
}

/** One chart per parameter the rover has; the order follows the mockups (air, ground, pressure, humidity, UV / wind). */
export function chartDefs(sol: WeatherSolDetail): ChartDef[] {
  const hours = sol.hours ?? [];
  const out: ChartDef[] = [];
  for (const s of SPECS) {
    if (!hasKey(hours, s.key) && (sol as unknown as Record<string, unknown>)[s.key] === undefined) {
      continue; // the rover has no such sensor
    }
    const values = column(hours, s.key);
    const stat = solStat(sol, s.key);
    const d = s.digits;
    const parts: string[] = [];
    if (stat?.min !== null && stat?.min !== undefined) parts.push(`min ${fmtNum(stat.min, d)}`);
    if (stat?.mean !== null && stat?.mean !== undefined) parts.push(`mean ${fmtNum(stat.mean, d)}`);
    if (stat?.max !== null && stat?.max !== undefined) parts.push(`max ${fmtNum(stat.max, d)}`);
    const def: ChartDef = {
      id: s.id,
      title: s.title,
      unit: s.unit,
      digits: d,
      zero: s.zero,
      series: [{ name: "hourly mean", values }],
      summary: parts.length ? `Sol: ${parts.join(" · ")}` : "No sol summary",
    };
    if (s.key === "humidity_pct") def.quality = "preliminary";
    if (s.key === "ground_temp_c" && sol.rover === "perseverance") def.quality = "estimate";
    const band = bandOf(hours, s.key);
    if (band) def.hourBand = band;
    else if (stat && stat.min !== null && stat.max !== null) def.solBand = [stat.min, stat.max];
    if (s.key === "uv_wm2" && !band && stat?.max != null) {
      def.refLine = { value: stat.max, label: "sol max" };
    }
    if (!values.some((v) => v !== null) && !band) def.summary = "No readings on this sol";
    out.push(def);
  }

  if (hasKey(hours, "wind_speed_ms") || sol.wind !== undefined) {
    const speed = column(hours, "wind_speed_ms");
    const dir = column(hours, "wind_direction_deg");
    const w = sol.wind;
    const def: ChartDef = {
      id: "wind",
      title: "Wind speed",
      unit: "m/s",
      digits: 1,
      zero: true,
      series: [{ name: "mean", values: speed }],
      summary: w
        ? `Sol: mean ${fmtNum(w.speed_mean_ms ?? 0, 1)} · gust ${w.gust_ms === null ? "–" : fmtNum(w.gust_ms, 1)}${
            w.direction_mode_deg === null
              ? ""
              : ` · prevailing direction ${fmtNum(w.direction_mode_deg, 0)}°`
          }`
        : "No wind data",
      direction: dir,
    };
    if (hasKey(hours, "wind_gust_ms")) {
      def.series.push({ name: "gust", values: column(hours, "wind_gust_ms"), dashed: true });
    } else if (w && isNum(w.gust_ms)) {
      def.refLine = { value: w.gust_ms, label: "sol gust" };
    }
    if (!speed.some((v) => v !== null)) {
      def.noData = `No wind data for sol ${sol.sol}. The wind sensor has data only on sols 36–313.`;
    }
    out.push(def);
  }
  return out;
}

/** The values of every chart at one hour, for the readout and the screen-reader announcement. */
export function readoutOf(def: ChartDef, hour: number): string {
  const parts = def.series.map((s) => {
    const v = s.values[hour];
    const name = def.series.length > 1 ? `${s.name} ` : "";
    return v === null || v === undefined
      ? `${name}no reading`
      : `${name}${fmtNum(v, def.digits)} ${def.unit}`;
  });
  const band = def.hourBand?.[hour];
  if (band) parts.push(`range ${fmtNum(band.lo, def.digits)} to ${fmtNum(band.hi, def.digits)}`);
  const dir = def.direction?.[hour];
  if (dir !== null && dir !== undefined) parts.push(`direction ${fmtNum(dir, 0)}°`);
  return parts.join(", ");
}
