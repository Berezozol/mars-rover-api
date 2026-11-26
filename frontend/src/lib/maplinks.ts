import type { Waypoint } from "../api/types";
import { qs } from "../router";

/** A stop found for a link; `exact: false` when the stop of the asked sol was missing and an earlier one stands in. */
export interface FoundStop {
  index: number;
  exact: boolean;
}

/**
 * The stop a link points at: same site and drive (and sol, when given); otherwise the last stop of
 * the sol, or, when the sol has none, the last stop before it (`exact: false`). `null` when nothing fits.
 */
export function findStop(
  stops: Waypoint[],
  sol: number | null,
  site: number | null,
  drive: number | null,
): FoundStop | null {
  if (site !== null && drive !== null) {
    const i = stops.findIndex(
      (w) => w.site === site && w.drive === drive && (sol === null || w.sol === sol),
    );
    if (i >= 0) return { index: i, exact: true };
  }
  if (sol === null) return null;
  let best = -1;
  for (let i = 0; i < stops.length; i++) {
    if (stops[i]!.sol <= sol && (best < 0 || stops[i]!.sol >= stops[best]!.sol)) best = i;
  }
  return best < 0 ? null : { index: best, exact: stops[best]!.sol === sol };
}

/** Address of the map for a rover, optionally centred on a stop or with a sol range highlighted. */
export function mapHref(
  rover: string,
  o: {
    stop?: { sol: number; site: number; drive: number };
    from?: number | null;
    to?: number | null;
  } = {},
): string {
  return `/rovers/${rover}/map${qs({
    sol: o.stop?.sol,
    site: o.stop?.site,
    drive: o.stop?.drive,
    from_sol: o.from,
    to_sol: o.to,
  })}`;
}

/** The filters of the photo browser for the photos of a stop (contract recipe, API.md `/photos`). */
export interface StopFilter {
  /** Strings are hand-made addresses, sent as-is so that the API explains what is wrong (422). */
  site: number | string;
  drive_from?: number | string;
  /** Absent for the last stop of a site: everything after it belongs to it. */
  drive_to?: number | string;
}

/**
 * "As in the photo's `location`": on the stop and on the way to the next one. `drive_to` is the
 * drive of the next stop of the same site minus 1; the last stop of a site has no `drive_to`.
 */
export function stopFilter(
  stops: { site: number; drive: number }[],
  stop: { site: number; drive: number },
): StopFilter {
  let next: number | null = null;
  for (const w of stops) {
    if (w.site === stop.site && w.drive > stop.drive && (next === null || w.drive < next)) {
      next = w.drive;
    }
  }
  return {
    site: stop.site,
    drive_from: stop.drive,
    ...(next !== null ? { drive_to: next - 1 } : {}),
  };
}

/** Address of the photo browser limited to a stop. */
export function stopPhotosHref(
  rover: string,
  stops: { site: number; drive: number }[],
  stop: { site: number; drive: number },
): string {
  const f = stopFilter(stops, stop);
  return `/rovers/${rover}${qs({ site: f.site, drive_from: f.drive_from, drive_to: f.drive_to })}`;
}

/** Short label of a stop filter: `Stop 93/412`, `Stop 93/412–517` or `Stop 93/412+`. */
export function stopLabel(f: StopFilter): string {
  if (f.drive_from === undefined && f.drive_to === undefined) return `Stop ${f.site}`;
  const base = `Stop ${f.site}/${f.drive_from ?? 0}`;
  if (f.drive_to === undefined) return `${base}+`;
  return String(f.drive_to) === String(f.drive_from ?? 0) ? base : `${base}–${f.drive_to}`;
}
