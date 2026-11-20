import { api } from "../api/client";
import type { Sample, SampleKind, SampleMatch, SampleStatus } from "../api/types";
import { qs } from "../router";

export const SAMPLES_ROVER = "perseverance";
export const TUBE_CAMERA = "CACHECAM";
/** The page counts as "behind the mission" when its last sealed sol is this many sols short of the rover. */
export const LAG_SOLS = 100;

export const STATUS_LABEL: Record<SampleStatus, string> = {
  on_rover: "On rover",
  depot: "In depot",
};
export const STATUS_CLASS: Record<SampleStatus, string> = {
  on_rover: "badge-active",
  depot: "badge-complete",
};
export const KIND_LABEL: Record<SampleKind, string> = {
  rock_core: "Rock core",
  regolith: "Regolith",
  atmospheric: "Atmospheric",
};

export const MATCH_TEXT: Record<SampleMatch, string> = {
  exact: "Sealed at this stop.",
  nearest_before: "Sealed at the same site, a little further on than this stop.",
  by_sol: "Nearest published stop to the sealing sol; the rover may have moved since.",
};

export interface Counts {
  on_rover: number;
  depot: number;
  rock_core: number;
  regolith: number;
  atmospheric: number;
}

/** Counts by status and type; rows with an unknown (null) value are in no bucket. */
export function countSamples(items: Sample[]): Counts {
  const c: Counts = { on_rover: 0, depot: 0, rock_core: 0, regolith: 0, atmospheric: 0 };
  for (const x of items) {
    if (x.status) c[x.status]++;
    if (x.sample_type) c[x.sample_type]++;
  }
  return c;
}

export interface Summary {
  count: number;
  firstSol: number | null;
  lastSol: number | null;
  /** Numbers of samples with no sol yet (`TBD` on the page). */
  tbd: number[];
  counts: Counts;
}

export function summarize(items: Sample[]): Summary {
  const sols = items.flatMap((x) => (x.sol === null ? [] : [x.sol]));
  return {
    count: items.length,
    firstSol: sols.length ? Math.min(...sols) : null,
    lastSol: sols.length ? Math.max(...sols) : null,
    tbd: items.filter((x) => x.sol === null).map((x) => x.number),
    counts: countSamples(items),
  };
}

export type StatusFilter = "all" | SampleStatus;
export type TypeFilter = "all" | SampleKind;

export function filterSamples(items: Sample[], status: StatusFilter, type: TypeFilter): Sample[] {
  return items.filter(
    (x) => (status === "all" || x.status === status) && (type === "all" || x.sample_type === type),
  );
}

/** The last sealed sol when the page is far behind the rover's latest sol; null when it is not. */
export function lag(items: Sample[], maxSol: number | null): { last: number; max: number } | null {
  const { lastSol } = summarize(items);
  if (lastSol === null || maxSol === null || maxSol - lastSol <= LAG_SOLS) return null;
  return { last: lastSol, max: maxSol };
}

/** Height as published: centimetres, else the page's own note, else null (shown as a dash). */
export function heightText(x: Sample): string | null {
  if (x.height_cm !== null) return `${x.height_cm} cm`;
  return x.height_note;
}

export const samplesHref = (n?: number): string =>
  `/rovers/${SAMPLES_ROVER}/samples${n !== undefined ? `#sample-${n}` : ""}`;
export const solPhotosHref = (sol: number): string => `/rovers/${SAMPLES_ROVER}${qs({ sol })}`;
export const tubePhotosHref = (sol: number): string =>
  `/rovers/${SAMPLES_ROVER}${qs({ sol, camera: TUBE_CAMERA })}`;

/** Samples that share one stop on the map. A group is approximate when no sample in it is placed exactly. */
export interface StopGroup {
  key: string;
  sol: number;
  site: number;
  drive: number;
  lat: number;
  lon: number;
  items: Sample[];
  approximate: boolean;
}

export const stopKey = (sol: number, site: number, drive: number): string =>
  `${sol}/${site}/${drive}`;

export function groupByStop(items: Sample[]): StopGroup[] {
  const groups = new Map<string, StopGroup>();
  for (const x of items) {
    const l = x.location;
    if (!l) continue;
    const key = stopKey(l.sol, l.site, l.drive);
    let g = groups.get(key);
    if (!g) {
      g = {
        key,
        sol: l.sol,
        site: l.site,
        drive: l.drive,
        lat: l.lat,
        lon: l.lon,
        items: [],
        approximate: true,
      };
      groups.set(key, g);
    }
    g.items.push(x);
    if (l.match !== "by_sol") g.approximate = false;
  }
  return [...groups.values()];
}

/**
 * Does CACHECAM have photos on this sol? `true`/`false`, or `null` when that cannot be told (request failed);
 * the tube link is then left out. One small cached request per sol, a few at a time.
 */
export async function hasTubePhotos(sol: number): Promise<boolean | null> {
  try {
    const cams = await api.camerasQueued(SAMPLES_ROVER, sol);
    return cams.some((c) => c.code === TUBE_CAMERA && c.total_photos > 0);
  } catch {
    return null;
  }
}
