/**
 * Stage 17 weather fixtures: shaped like `GET /api/v2/rovers/{rover}/weather` and `.../weather/{sol}`
 * in docs/API.md. Pure data (no vitest imports), so the Playwright specs can use it too.
 */

export const PER_CREDIT =
  "Rodriguez-Manfredi, J. A.; de la Torre Juarez, M. (MEDA, Mars 2020) / NASA PDS Atmospheres Node, urn:nasa:pds:mars2020_meda";
export const PER_SOURCE_URL = "https://atmos.nmsu.edu/PDS/data/PDS4/Mars2020/mars2020_meda/";
export const CUR_CREDIT =
  "Gomez-Elvira, J. et al. (REMS, MSL) / NASA PDS Atmospheres Node, msl_rems";
export const CUR_SOURCE_URL = "https://atmos.nmsu.edu/PDS/data/PDS4/MSL/msl_rems/";

/** `Rover.max_sol` of the rovers in `tests/helpers.ts` (the grey zone and the lag are counted against it). */
export const PER_NOW = 2031;
export const CUR_NOW = 5066;

export interface Flag {
  code: string;
  parameter: string | null;
  count: number | null;
}
export interface WItem {
  sol: number;
  flags: Flag[];
  hours_covered?: number;
  [key: string]: unknown;
}

const partial: Flag = { code: "partial_coverage", parameter: null, count: null };

export function perSol(sol: number, over: Record<string, unknown> = {}): WItem {
  return {
    sol,
    earth_date: "2021-06-01",
    ls: 52.4,
    coverage: 0.61,
    hours_covered: 24,
    air_temp_c: { min: -82.4, max: -18.9, mean: -53.7 },
    pressure_pa: { min: 708.3, max: 745.1, mean: 726.0 },
    humidity_pct: { min: 0.0, max: 14.6, mean: 2.3 },
    wind: { speed_mean_ms: 3.4, gust_ms: 11.8, direction_mode_deg: 112.5 },
    flags: [],
    source_version: { CAL_ATS: "P02", CAL_WS: "P03", DER_PS: "P01", DER_RHS: "P01" },
    ...over,
  };
}

/**
 * Perseverance: 100-102 normal, 103 is a gap, 104 normal, 105 partial coverage (14 of 24 hours),
 * 106 humidity above 100 %, 107 a reading left out (out_of_range), 108 wind is null.
 */
export const perItems: WItem[] = [
  perSol(100),
  perSol(101),
  perSol(102),
  perSol(104),
  perSol(105, {
    coverage: 0.3,
    hours_covered: 14,
    air_temp_c: { min: -70.1, max: -30.2, mean: -47.4 },
    flags: [partial],
  }),
  perSol(106, {
    humidity_pct: { min: 0.0, max: 137.5, mean: 20.1 },
    flags: [{ code: "humidity_over_100", parameter: "humidity_pct", count: 12 }],
  }),
  perSol(107, {
    flags: [{ code: "out_of_range", parameter: "humidity_pct", count: 1 }],
  }),
  perSol(108, { wind: null, air_temp_c: { min: -90.0, max: -20.0, mean: -61.3 } }),
];

/**
 * Curiosity: no wind key at all; humidity has no `min`; 13 has a reading left out of the ground
 * temperature, 14 is partial, 15 has UV null (the sensor exists, there is no value).
 */
export function curSol(sol: number, over: Record<string, unknown> = {}): WItem {
  return {
    sol,
    earth_date: "2012-08-20",
    ls: 150.1,
    coverage: 0.5,
    hours_covered: 24,
    air_temp_c: { min: -75.0, max: -5.0, mean: -45.2 },
    pressure_pa: { min: 700.0, max: 790.0, mean: 745.5 },
    humidity_pct: { max: 60.0, mean: 25.5 },
    ground_temp_c: { min: -88.0, max: 7.5, mean: -40.2 },
    uv_wm2: { max: 19.2, mean: 5.1 },
    flags: [],
    source_version: { RMD: "P1" },
    ...over,
  };
}
export const curItems: WItem[] = [
  curSol(10),
  curSol(11),
  curSol(13, {
    flags: [{ code: "out_of_range", parameter: "ground_temp_c", count: 1 }],
  }),
  curSol(14, { hours_covered: 12, coverage: 0.2, flags: [partial] }),
  curSol(15, { uv_wm2: null }),
];

export const PER_PARAMETERS = {
  air_temp_c: { unit: "°C", quality: "measured" },
  pressure_pa: { unit: "Pa", quality: "measured" },
  humidity_pct: { unit: "%", quality: "preliminary" },
  wind: { unit: "m/s", quality: "measured" },
};
export const CUR_PARAMETERS = {
  air_temp_c: { unit: "°C", quality: "measured" },
  pressure_pa: { unit: "Pa", quality: "measured" },
  humidity_pct: { unit: "%", quality: "preliminary" },
  ground_temp_c: { unit: "°C", quality: "measured" },
  uv_wm2: { unit: "W/m²", quality: "measured" },
};

export function weatherList(
  rover: "perseverance" | "curiosity",
  items: WItem[] = rover === "perseverance" ? perItems : curItems,
  over: Record<string, unknown> = {},
) {
  const per = rover === "perseverance";
  return {
    rover,
    credit: per ? PER_CREDIT : CUR_CREDIT,
    source_url: per ? PER_SOURCE_URL : CUR_SOURCE_URL,
    updated_at: items.length ? "2026-11-24T02:10:00Z" : null,
    source_max_sol: items.length ? Math.max(...items.map((i) => i.sol)) : null,
    parameters: per ? PER_PARAMETERS : CUR_PARAMETERS,
    items,
    ...over,
  };
}

/** `hours[]` of a sol: 24 elements, missing hours are null. Values differ by hour so charts are not flat. */
export function hoursFor(
  rover: "perseverance" | "curiosity",
  item: WItem,
  missing: number[] = [],
): Record<string, unknown>[] {
  const gap = new Set(
    missing.length
      ? missing
      : (item.hours_covered ?? 24) < 24
        ? Array.from({ length: 24 - (item.hours_covered ?? 24) }, (_, i) => i + 3)
        : [],
  );
  const noWind = rover === "perseverance" && item.wind === null;
  return Array.from({ length: 24 }, (_, h) => {
    const off = gap.has(h);
    const v = (x: number) => (off ? null : Math.round(x * 10) / 10);
    const base: Record<string, unknown> = {
      hour: h,
      coverage: off ? 0 : 0.5,
      air_temp_c: v(-70 + h),
      pressure_pa: v(730 + (h % 12)),
      humidity_pct: v(item.sol === 106 ? 120 + h / 2 : 5 + (h % 7)),
    };
    if (rover === "perseverance") {
      base.wind_speed_ms = noWind || off ? null : v(2 + (h % 5));
      base.wind_direction_deg = noWind || off ? null : 157.5;
    } else {
      base.ground_temp_c = v(-60 + h * 2);
      base.uv_wm2 = item.uv_wm2 === null ? null : v(Math.max(0, h - 6));
    }
    return base;
  });
}

export function weatherDetail(rover: "perseverance" | "curiosity", item: WItem) {
  const file = `data_calibrated_env/sol_0090_0179/sol_${item.sol}/WE__${item.sol}_CAL_ATS_P02.CSV`;
  return {
    rover,
    ...item,
    hours: hoursFor(rover, item),
    source: { files: [{ product: "CAL_ATS", version: "P02", file, link: `${PER_SOURCE_URL}${file}` }] },
  };
}
