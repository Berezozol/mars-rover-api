/** Stage 21 RAD fixtures: shaped like `GET /api/v2/rovers/curiosity/radiation` in docs/API.md. */

export const RAD_CREDIT =
  "MSL RAD (SwRI) / NASA PDS Planetary Plasma Interactions Node, MSL-M-RAD-3-RDR-V1.0";
export const RAD_CITATION =
  "J. Peterson, S. Rafkin, C. Zeitlin, B. Ehresmann, E. Weigle, S. Jeffers, and D.M. Hassler, MSL MARS RADIATION ASSESSMENT DETECTOR RDR V1.0, MSL-M-RAD-3-RDR-V1.0, NASA Planetary Data System, 2013.";
export const RAD_SOURCE_URL = "https://pds-ppi.igpp.ucla.edu/data/MSL-M-RAD-3-RDR-V1.0/";

export function radSol(sol: number, over: Record<string, unknown> = {}) {
  const file = `DATA/SOL_01649_01772/RAD_RDR_2017_085_16_28_${sol}_V00.TXT`;
  return {
    sol,
    earth_date: "2017-03-26",
    dose_b_ugy_day: { mean: 226.51, min: 205.3, max: 248.92 },
    dose_e_ugy_day: { mean: 212.08, min: 190.44, max: 236.15 },
    count_rate_b_per_s: 9.437,
    coverage: 0.86,
    observations: { used: 84, dropped_qc: 5, dropped_low_rate: 0, dropped_invalid: 0 },
    timing_caveat: false,
    source: { file, link: `${RAD_SOURCE_URL}${file}` },
    ...over,
  };
}

/** Sols 10, 11 and 13 (12 is a gap); the three newest rows are the whole table. */
export const radItems = [
  radSol(10, {
    dose_e_ugy_day: { mean: 150.11, min: 140, max: 160 },
    dose_b_ugy_day: { mean: 158.22, min: 150, max: 170 },
    count_rate_b_per_s: 7.85,
    coverage: 0.31,
  }),
  radSol(11, {
    earth_date: "2012-08-16",
    dose_e_ugy_day: { mean: 171.5, min: 160, max: 180 },
    dose_b_ugy_day: { mean: 180.25, min: 170, max: 190 },
    count_rate_b_per_s: 8.5,
    coverage: 0.45,
  }),
  radSol(13, {
    earth_date: "2012-08-18",
    dose_e_ugy_day: { mean: 160.4, min: 150, max: 170 },
    dose_b_ugy_day: { mean: 167.9, min: 160, max: 175 },
    count_rate_b_per_s: 9.437,
    coverage: 0.86,
  }),
];

export function radiationResponse(items: unknown[] = radItems, over: Record<string, unknown> = {}) {
  const last = items.length ? (items[items.length - 1] as { sol: number }).sol : null;
  return {
    rover: "curiosity",
    credit: RAD_CREDIT,
    citation: RAD_CITATION,
    source_url: RAD_SOURCE_URL,
    updated_at: items.length ? "2026-12-01T01:42:10Z" : null,
    source_max_sol: last,
    source_release: items.length ? "2026-08-03" : null,
    items,
    ...over,
  };
}
