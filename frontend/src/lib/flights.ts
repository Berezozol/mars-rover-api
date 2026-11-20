// Pure helpers for the Ingenuity flight log: summary tiles, sorting, records, formatting.
import type { Flight } from "../api/types";

export interface FlightSummary {
  count: number;
  firstSol: number;
  lastSol: number;
  airSeconds: number;
  distanceM: number;
  top: Flight;
}

/** Totals for the tiles; null for an empty list. All 72 flights arrive in one response. */
export function summarize(items: Flight[]): FlightSummary | null {
  if (!items.length) return null;
  const sols = items.map((f) => f.sol);
  return {
    count: items.length,
    firstSol: Math.min(...sols),
    lastSol: Math.max(...sols),
    airSeconds: items.reduce((a, f) => a + f.duration_s, 0),
    distanceM: items.reduce((a, f) => a + f.distance_m, 0),
    top: items.reduce((a, f) => (f.max_altitude_m > a.max_altitude_m ? f : a)),
  };
}

/** 166.4 -> `2:46`. */
export function mmss(seconds: number): string {
  const total = Math.round(seconds);
  const m = Math.floor(total / 60);
  return `${m}:${String(total - m * 60).padStart(2, "0")}`;
}

/** Whole seconds split into hours, minutes and seconds. */
export function hms(seconds: number): { h: number; m: number; s: number } {
  const total = Math.round(seconds);
  return { h: Math.floor(total / 3600), m: Math.floor((total % 3600) / 60), s: total % 60 };
}

export type SortKey = "flight" | "sol" | "date" | "distance" | "alt" | "speed" | "dur" | "photos";
export type SortDir = "asc" | "desc";

export const SORT_VALUE: Record<SortKey, (f: Flight) => number | string> = {
  flight: (f) => f.flight,
  sol: (f) => f.sol,
  date: (f) => f.date,
  distance: (f) => f.distance_m,
  alt: (f) => f.max_altitude_m,
  speed: (f) => f.max_groundspeed_mps,
  dur: (f) => f.duration_s,
  photos: (f) => f.total_photos,
};

/** The direction a heading starts with: numbers and dates by "most" first, except the keys of time. */
export function defaultDir(key: SortKey): SortDir {
  return key === "flight" || key === "sol" || key === "date" ? "asc" : "desc";
}

/** A sorted copy; ties fall back to the flight number so the order is stable. */
export function sortFlights(items: Flight[], key: SortKey, dir: SortDir): Flight[] {
  const get = SORT_VALUE[key];
  const sign = dir === "asc" ? 1 : -1;
  return [...items].sort((a, b) => {
    const x = get(a);
    const y = get(b);
    const c = x < y ? -1 : x > y ? 1 : 0;
    return c * sign || a.flight - b.flight;
  });
}

export type RecordTag = "highest" | "longest" | "fastest";

/** Which records each flight holds (altitude, distance, speed). Zero maxima are not records. */
export function recordsOf(items: Flight[]): Map<number, RecordTag[]> {
  const out = new Map<number, RecordTag[]>();
  if (!items.length) return out;
  const alt = Math.max(...items.map((f) => f.max_altitude_m));
  const dist = Math.max(...items.map((f) => f.distance_m));
  const speed = Math.max(...items.map((f) => f.max_groundspeed_mps));
  for (const f of items) {
    const tags: RecordTag[] = [];
    if (alt > 0 && f.max_altitude_m === alt) tags.push("highest");
    if (dist > 0 && f.distance_m === dist) tags.push("longest");
    if (speed > 0 && f.max_groundspeed_mps === speed) tags.push("fastest");
    if (tags.length) out.set(f.flight, tags);
  }
  return out;
}
