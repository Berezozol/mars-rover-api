import type { RadiationSol } from "../api/types";

export const RAD_ROVER = "curiosity";
/** Length of one sol in Earth days. */
const SOL_DAYS = 1.0274912;
const DAYS_PER_MONTH = 30.44;

/** A top value and evenly spaced tick values from 0 for an axis that starts at zero (at most five intervals). */
export function niceScale(max: number): { top: number; ticks: number[] } {
  if (!(max > 0)) return { top: 1, ticks: [0, 1] };
  const mag = 10 ** Math.floor(Math.log10(max));
  let step = mag;
  for (const k of [0.1, 0.2, 0.5, 1, 2, 5, 10]) {
    step = mag * k;
    if (Math.ceil(max / step - 1e-9) <= 5) break;
  }
  const n = Math.max(1, Math.ceil(max / step - 1e-9));
  const top = Number((n * step).toPrecision(12));
  return {
    top,
    ticks: Array.from({ length: n + 1 }, (_, i) => Number((i * step).toPrecision(12))),
  };
}

/** Round tick positions along the sol axis, from 0, about `count` of them. */
export function solTicks(xmax: number, count: number): number[] {
  const raw = xmax / Math.max(count, 1);
  const mag = 10 ** Math.floor(Math.log10(Math.max(raw, 1)));
  const step = ([1, 2, 5, 10].map((k) => k * mag).find((s) => s >= raw) ?? raw) || 1;
  const out: number[] = [];
  for (let t = 0; t <= xmax; t += step) out.push(t);
  return out;
}

export const lagMonths = (sols: number): number => Math.round((sols * SOL_DAYS) / DAYS_PER_MONTH);

/** "N sols (about 8 months) behind"; short lags are given in sols only. */
export function lagText(sols: number): string {
  const months = lagMonths(sols);
  const n = `${sols} ${sols === 1 ? "sol" : "sols"}`;
  if (months < 1) return `${n} behind`;
  return `${n} (about ${months} ${months === 1 ? "month" : "months"}) behind`;
}

const num = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
/** The API promises numbers; a missing one is shown as a dash instead of breaking the page. */
export const dose = (v: number | null | undefined): string => (num(v) ? v.toFixed(1) : "—");
export const rate = (v: number | null | undefined): string => (num(v) ? v.toFixed(2) : "—");
export const cover = (v: number | null | undefined): string => (num(v) ? v.toFixed(2) : "—");
export { num };

export interface RadiationView {
  first: number;
  last: number;
  bySol: Map<number, RadiationSol>;
  maxDose: number;
  maxRate: number;
}

export function viewOf(items: RadiationSol[]): RadiationView {
  const bySol = new Map<number, RadiationSol>();
  let maxDose = 0;
  let maxRate = 0;
  for (const x of items) {
    bySol.set(x.sol, x);
    const e = x.dose_e_ugy_day?.mean;
    const b = x.dose_b_ugy_day?.mean;
    if (num(e)) maxDose = Math.max(maxDose, e);
    if (num(b)) maxDose = Math.max(maxDose, b);
    if (num(x.count_rate_b_per_s)) maxRate = Math.max(maxRate, x.count_rate_b_per_s);
  }
  return {
    first: items[0]!.sol,
    last: items[items.length - 1]!.sol,
    bySol,
    maxDose,
    maxRate,
  };
}
