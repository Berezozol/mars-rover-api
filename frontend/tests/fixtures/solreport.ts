/**
 * Stage 22 phase B fixtures: `GET /api/v2/rovers/{rover}/weather/{sol}` with the hourly `_min`/`_max` and
 * `wind_gust_ms` keys of docs/API.md, plus the neighbours the report page may ask for (location, radiation).
 * Pure data (no vitest imports), so the Playwright specs can use it too.
 */
import { CUR_CREDIT, CUR_SOURCE_URL, PER_CREDIT, PER_SOURCE_URL, curSol, perSol } from "./weather";
import { radSol } from "./radiation";

export type ReportRover = "perseverance" | "curiosity";

/** The sol of the Curiosity example of the spec (docs/proposals/sol-conditions.md, section 1). */
export const CUR_SOL = 344;
/** A Perseverance sol inside the wind sensor window (sols 36-313). */
export const PER_SOL = 100;

/** The hour every "Inspect hour" test looks at, and the values the fixtures give that hour. */
export const HOUR = 13;
export const CUR_AT_HOUR = {
  air: -38,
  pressure: 853,
  ground: -34,
  uv: 7,
};
export const PER_AT_HOUR = {
  air: -54,
  pressure: 731,
  windSpeed: 5.3,
  windGust: 9.7,
  windDirection: 157.5,
};

const r1 = (x: number) => Math.round(x * 10) / 10;

interface HoursOpts {
  /** Hours without any measurement: every value of the hour is null and `coverage` is 0. */
  missing?: number[];
  /** Leave out all `_min`/`_max`/`wind_gust_ms` keys, as an answer from before the re-parse does. */
  extremes?: boolean;
  /** Perseverance outside sols 36-313: the three wind keys are null. */
  noWind?: boolean;
}

/** 24 hours as the API sends them. Values grow with the hour, so a chart is not flat. */
export function reportHours(rover: ReportRover, opts: HoursOpts = {}): Record<string, unknown>[] {
  const gap = new Set(opts.missing ?? []);
  const extremes = opts.extremes ?? true;
  return Array.from({ length: 24 }, (_, h) => {
    const off = gap.has(h);
    const out: Record<string, unknown> = { hour: h, coverage: off ? 0 : 0.5 };
    const put = (key: string, mean: number, spread: number) => {
      out[key] = off ? null : r1(mean);
      if (extremes) {
        out[`${key}_min`] = off ? null : r1(mean - spread);
        out[`${key}_max`] = off ? null : r1(mean + spread);
      }
    };
    if (rover === "curiosity") {
      put("air_temp_c", -77 + 3 * h, 2); // hour 13 -> -38
      put("pressure_pa", 840 + h, 1.5); // hour 13 -> 853
      put("humidity_pct", 5 + (h % 7), 1);
      put("ground_temp_c", -60 + 2 * h, 3); // hour 13 -> -34
      put("uv_wm2", Math.max(0, h - 6), 0.5); // hour 13 -> 7
    } else {
      put("air_temp_c", -80 + 2 * h, 2); // hour 13 -> -54
      put("pressure_pa", 718 + h, 1.5); // hour 13 -> 731
      put("humidity_pct", 5 + (h % 7), 1);
      put("ground_temp_c", -70 + 2 * h, 3);
      const wind = off || opts.noWind;
      out.wind_speed_ms = wind ? null : h === HOUR ? 5.3 : r1(2 + (h % 5));
      if (extremes) out.wind_gust_ms = wind ? null : h === HOUR ? 9.7 : r1(6 + (h % 5));
      out.wind_direction_deg = wind ? null : 157.5;
    }
    return out;
  });
}

interface DetailOpts extends HoursOpts {
  ls?: number | null;
  coverage?: number;
  hoursCovered?: number;
  flags?: { code: string; parameter: string | null; count: number | null }[];
  earthDate?: string;
}

/** The answer of `/weather/{sol}`: the WeatherSol keys of the list plus `hours` and `source`. */
export function reportDetail(rover: ReportRover, sol: number, opts: DetailOpts = {}) {
  const per = rover === "perseverance";
  const base = per
    ? perSol(sol, { earth_date: opts.earthDate ?? "2021-06-01" })
    : curSol(sol, { earth_date: opts.earthDate ?? "2013-07-15" });
  const item = {
    ...base,
    ...("ls" in opts ? { ls: opts.ls } : {}),
    ...(opts.coverage !== undefined ? { coverage: opts.coverage } : {}),
    ...(opts.hoursCovered !== undefined ? { hours_covered: opts.hoursCovered } : {}),
    ...(opts.flags ? { flags: opts.flags } : {}),
    // outside the wind window the API has no wind at all: `wind: null` and null hours
    ...(per && (sol < 36 || sol > 313) ? { wind: null } : {}),
  };
  const file = per
    ? `data_calibrated_env/sol_0090_0179/sol_${String(sol).padStart(4, "0")}/WE__${sol}_CAL_ATS_P02.CSV`
    : `data_calibrated/sol_0300_0399/sol_${sol}/REMS_${sol}.TAB`;
  const root = per ? PER_SOURCE_URL : CUR_SOURCE_URL;
  const missing =
    opts.missing ??
    (opts.hoursCovered !== undefined && opts.hoursCovered < 24
      ? Array.from({ length: 24 - opts.hoursCovered }, (_, i) => i + 3)
      : []);
  return {
    rover,
    // `credit` is in the answer of `/weather/{sol}` (docs/API.md, 2026-11-26): the report reads it from here
    credit: CREDIT[rover],
    ...item,
    hours: reportHours(rover, {
      missing,
      extremes: opts.extremes,
      noWind: per && (sol < 36 || sol > 313),
    }),
    source: {
      files: [{ product: per ? "CAL_ATS" : "RMD", version: "P02", file, link: `${root}${file}` }],
    },
  };
}

export const CREDIT = { perseverance: PER_CREDIT, curiosity: CUR_CREDIT };

/** `GET /rovers/{rover}/location?latest=true`: one waypoint, the latitude is what the report needs. */
export function latestLocation(rover: ReportRover) {
  const per = rover === "perseverance";
  return {
    rover,
    available: true,
    updated_at: "2026-11-19T23:33:42Z",
    source_max_sol: per ? 2031 : 5073,
    items: [
      {
        sol: per ? 2031 : 5073,
        site: 112,
        drive: 1874,
        lat: per ? 18.4 : -4.6,
        lon: per ? 77.4 : 137.4,
        elev_geoid: null,
        easting: null,
        northing: null,
        roll: null,
        pitch: null,
        yaw: null,
        tilt: null,
        dist_m: null,
        dist_total_m: null,
      },
    ],
  };
}

/** Daily RAD rows for sols 300-400 around `CUR_SOL`; the sol of the report has its own numbers. */
export const radAround = Array.from({ length: 101 }, (_, i) => {
  const sol = 300 + i;
  return sol === CUR_SOL
    ? radSol(sol, {
        earth_date: "2013-07-15",
        dose_e_ugy_day: { mean: 218.7, min: 200, max: 240 },
        dose_b_ugy_day: { mean: 231.4, min: 210, max: 250 },
        count_rate_b_per_s: 9.437,
        coverage: 0.86,
      })
    : radSol(sol, {
        dose_e_ugy_day: { mean: 190 + (sol % 20), min: 180, max: 210 },
        dose_b_ugy_day: { mean: 200 + (sol % 20), min: 190, max: 220 },
        count_rate_b_per_s: 8 + (sol % 7) / 10,
      });
});
