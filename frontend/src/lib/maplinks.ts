import type { Waypoint } from "../api/types";
import { qs } from "../router";

/** The stop a link points at: same site and drive, else the last stop of the sol. */
export function findStop(
  stops: Waypoint[],
  sol: number | null,
  site: number | null,
  drive: number | null,
): number | null {
  if (site !== null && drive !== null) {
    const i = stops.findIndex(
      (w) => w.site === site && w.drive === drive && (sol === null || w.sol === sol),
    );
    if (i >= 0) return i;
  }
  if (sol === null) return null;
  for (let i = stops.length - 1; i >= 0; i--) if (stops[i]!.sol === sol) return i;
  return null;
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
