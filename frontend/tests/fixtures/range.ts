/**
 * Stage 22 phase A fixtures: long sol series for the range slider and the sol table.
 * Pure data (no vitest imports), so the Playwright specs can use it too.
 *
 * Numbers follow docs/proposals/sol-conditions.md section 2: Curiosity weather runs from sol 9 to
 * `source_max_sol` 4710 while `Rover.max_sol` is 5066 (the archive lags), which is the case of the
 * "Last 100 sols" defect.
 */
import { radSol } from "./radiation";
import { curSol, perSol, type WItem } from "./weather";

export const CUR_FIRST = 9;
export const CUR_SOURCE_MAX = 4710;
/** Perseverance: `Rover.max_sol` is 2031 (tests/helpers.ts); the archive stops at 1859. */
export const PER_FIRST = 3;
export const PER_SOURCE_MAX = 1859;
export const RAD_FIRST = 15;
export const RAD_SOURCE_MAX = 4843;

/** Sols between `first` and `last` inclusive, without the listed `[from, to]` gaps. */
export function solsBetween(first: number, last: number, gaps: [number, number][] = []): number[] {
  const out: number[] = [];
  for (let s = first; s <= last; s++) {
    if (!gaps.some(([a, b]) => s >= a && s <= b)) out.push(s);
  }
  return out;
}

/** An ISO date that moves with the sol (1 sol is 1.0275 days), so the date column sorts like the sol. */
export function dateOfSol(sol: number, landing = "2021-02-18"): string {
  const ms = Date.parse(`${landing}T00:00:00Z`) + Math.round(sol * 1.0275 * 86_400_000);
  return new Date(ms).toISOString().slice(0, 10);
}

/** The gap inside the last 120 sols of Perseverance: sols 1800-1805 have no data. */
export const PER_GAP: [number, number] = [1800, 1805];

export const perLong: WItem[] = solsBetween(PER_FIRST, PER_SOURCE_MAX, [PER_GAP]).map((s) =>
  perSol(s, {
    earth_date: dateOfSol(s),
    // values that differ from sol to sol, so every sortable column has an order of its own
    air_temp_c: { min: -90 + (s % 17), max: -20 + (s % 11), mean: -55 + (s % 7) },
    pressure_pa: { min: 700, max: 750, mean: 720 + (s % 13) },
  }),
);

export const CUR_GAP: [number, number] = [2331, 2352];
export const curLong: WItem[] = solsBetween(CUR_FIRST, CUR_SOURCE_MAX, [CUR_GAP]).map((s) =>
  curSol(s, {
    earth_date: dateOfSol(s, "2012-08-06"),
    air_temp_c: { min: -80 + (s % 17), max: -10 + (s % 11), mean: -45 + (s % 7) },
  }),
);

/** Curiosity RAD, sols 15-4843 with a gap; doses sit far from zero (about 150-250 uGy/day). */
export const RAD_GAP: [number, number] = [4800, 4805];
export const radLong = solsBetween(RAD_FIRST, RAD_SOURCE_MAX, [RAD_GAP]).map((s) => {
  const e = 190 + (s % 40);
  return radSol(s, {
    earth_date: dateOfSol(s, "2012-08-06"),
    dose_e_ugy_day: { mean: e, min: e - 20, max: e + 20 },
    dose_b_ugy_day: { mean: e + 8, min: e - 12, max: e + 28 },
    count_rate_b_per_s: 7 + (s % 9) / 10,
  });
});

/** The solar event of sol 4190: detector E reads about ten times the median (the real archive: 2342 uGy/day). */
export const SPIKE_SOL = 4190;
export const SPIKE_DOSE = 2342;
export const SPIKE_RATE = 70;

/** `radLong` with a dose spike at sol 4190 (detector E only; B and the count rate stay normal). */
export const radSpiked = radLong.map((x) =>
  x.sol === SPIKE_SOL
    ? { ...x, dose_e_ugy_day: { mean: SPIKE_DOSE, min: 2300, max: 2380 } }
    : x,
);

/** `radLong` with a count-rate spike at sol 4190 (the dose stays normal). */
export const radRateSpiked = radLong.map((x) =>
  x.sol === SPIKE_SOL ? { ...x, count_rate_b_per_s: SPIKE_RATE } : x,
);

/** `radLong` with three dose spikes: sols 4190, 4300 and 4301 (the highest is 4300). */
export const radManySpikes = radLong.map((x) =>
  x.sol === 4190
    ? { ...x, dose_e_ugy_day: { mean: 1500, min: 1400, max: 1600 } }
    : x.sol === 4300
      ? { ...x, dose_e_ugy_day: { mean: 2100, min: 2000, max: 2200 } }
      : x.sol === 4301
        ? { ...x, dose_e_ugy_day: { mean: 1800, min: 1700, max: 1900 } }
        : x,
);
