/**
 * Stage 22 phase C fixtures: `GET /api/v2/rovers/curiosity/radiation/{sol}` (docs/API.md): the RadiationSol keys
 * plus `credit`, `citation`, `hours` (24 items or null) and `timing_caveat`. Pure data, no vitest imports.
 */
import { RAD_CITATION, RAD_CREDIT, radSol } from "./radiation";

/** The hour the tests inspect and the hourly values it has (see `radHours`). */
export const RAD_HOUR = 13;
export const RAD_AT_HOUR = { doseE: 7.4, doseB: 7.9, count: 7.6 };

interface RadHoursOpts {
  /** Hours without observations: coverage 0, observations 0, all values null. */
  missing?: number[];
}

/** 24 hours as the API sends them: dose rates in µGy/h (about 6-9), count rate in 1/s. */
export function radHours(opts: RadHoursOpts = {}): Record<string, unknown>[] {
  const gap = new Set(opts.missing ?? []);
  return Array.from({ length: 24 }, (_, h) => {
    if (gap.has(h)) {
      return {
        hour: h,
        coverage: 0,
        observations: 0,
        dose_e_ugy_h: null,
        dose_b_ugy_h: null,
        count_rate_b_per_s: null,
      };
    }
    const hit = h === RAD_HOUR;
    return {
      hour: h,
      coverage: 0.5,
      observations: 2,
      dose_e_ugy_h: hit ? RAD_AT_HOUR.doseE : 6.5 + (h % 5) / 10,
      dose_b_ugy_h: hit ? RAD_AT_HOUR.doseB : 6.9 + (h % 5) / 10,
      count_rate_b_per_s: hit ? RAD_AT_HOUR.count : 7 + (h % 5) / 10,
    };
  });
}

interface RadDetailOpts extends RadHoursOpts {
  /** `hours: null`: hours not computed for this sol. */
  noHours?: boolean;
  timingCaveat?: boolean;
}

/** The answer of `/radiation/{sol}`. The daily values of sol 344 match `radAround` of solreport.ts. */
export function radDetail(sol: number, opts: RadDetailOpts = {}) {
  const daily =
    sol === 344
      ? radSol(sol, {
          earth_date: "2013-07-15",
          dose_e_ugy_day: { mean: 218.7, min: 200, max: 240 },
          dose_b_ugy_day: { mean: 231.4, min: 210, max: 250 },
          count_rate_b_per_s: 9.437,
          coverage: 0.86,
        })
      : radSol(sol);
  return {
    rover: "curiosity",
    ...daily,
    timing_caveat: opts.timingCaveat ?? false,
    credit: RAD_CREDIT,
    citation: RAD_CITATION,
    hours: opts.noHours ? null : radHours({ missing: opts.missing }),
  };
}
