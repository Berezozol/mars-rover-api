// Logic of the Stats page without a DOM: address <-> filters <-> API query, zero-filled periods, chart scale.
import type { StatsGroup, StatsQuery } from "../api/types";

export const MAX_STATS_CAMERAS = 10;
export const TABLE_ROWS = 12;
const MAX_INT = 2_147_483_647;

export type StatsTab = "month" | "year" | "camera";
export const STATS_TABS: { id: StatsTab; label: string }[] = [
  { id: "month", label: "By month" },
  { id: "year", label: "By year" },
  { id: "camera", label: "By camera" },
];
export const DEFAULT_TAB: StatsTab = "month";

export interface StatsFilters {
  /** Chosen rovers; empty means all (the `rovers` parameter is not sent). */
  rovers: string[];
  cameras: string[];
  when: "sol" | "date";
  solMin: string;
  solMax: string;
  dateMin: string;
  dateMax: string;
  /** "Full frames only": `sample_type=full`. */
  full: boolean;
  tab: StatsTab;
}

export function defaultStatsFilters(): StatsFilters {
  return {
    rovers: [],
    cameras: [],
    when: "sol",
    solMin: "",
    solMax: "",
    dateMin: "",
    dateMax: "",
    full: false,
    tab: DEFAULT_TAB,
  };
}

const csv = (v: string | null) =>
  (v ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

/** Filters from an address; the names are those of the API parameters. */
export function parseStats(search: string): StatsFilters {
  const q = new URLSearchParams(search);
  const f = defaultStatsFilters();
  f.rovers = [...new Set(csv(q.get("rovers")).map((r) => r.toLowerCase()))];
  f.cameras = [...new Set(csv(q.get("camera")).map((c) => c.toUpperCase()))].slice(
    0,
    MAX_STATS_CAMERAS,
  );
  f.solMin = q.get("sol_min") ?? "";
  f.solMax = q.get("sol_max") ?? "";
  f.dateMin = q.get("date_min") ?? "";
  f.dateMax = q.get("date_max") ?? "";
  const hasSol = f.solMin !== "" || f.solMax !== "";
  // Sols and dates cannot be combined: the sols win, as in the form.
  if (!hasSol && (f.dateMin !== "" || f.dateMax !== "")) f.when = "date";
  else {
    f.dateMin = "";
    f.dateMax = "";
  }
  f.full = q.get("sample_type") === "full";
  const by = q.get("group_by");
  if (by === "month" || by === "year" || by === "camera") f.tab = by;
  return f;
}

/** The query for one `group_by`. */
export function statsQuery(
  f: StatsFilters,
  groupBy: "rover" | StatsTab,
  all: string[] = [],
): StatsQuery {
  const q: StatsQuery = {};
  const chosen = f.rovers.filter((r) => all.length === 0 || all.includes(r));
  if (chosen.length && !(all.length > 0 && all.every((r) => chosen.includes(r)))) {
    q.rovers = chosen.join(",");
  }
  if (f.cameras.length) q.camera = f.cameras.join(",");
  if (f.when === "sol") {
    if (f.solMin !== "") q.sol_min = f.solMin;
    if (f.solMax !== "") q.sol_max = f.solMax;
  } else {
    if (f.dateMin) q.date_min = f.dateMin;
    if (f.dateMax) q.date_max = f.dateMax;
  }
  if (f.full) q.sample_type = "full";
  q.group_by = groupBy;
  return q;
}

/** The address of the page: the filters and the tab (the default tab is left out). */
export function toStatsString(f: StatsFilters, all: string[] = []): string {
  const { group_by: _drop, ...rest } = statsQuery(f, "rover", all);
  void _drop;
  const pairs = Object.entries(rest).map(([k, v]) => [k, String(v)] as [string, string]);
  if (f.tab !== DEFAULT_TAB) pairs.push(["group_by", f.tab]);
  const s = new URLSearchParams(pairs).toString().replace(/%2C/g, ",");
  return s ? `?${s}` : "";
}

export interface StatsProblem {
  ids: string[];
  text: string;
}

export function validateStats(f: StatsFilters): StatsProblem | null {
  const isInt = (v: string) => /^\d+$/.test(v) && Number(v) <= MAX_INT;
  if (f.when === "sol") {
    if ((f.solMin !== "" && !isInt(f.solMin)) || (f.solMax !== "" && !isInt(f.solMax))) {
      return {
        ids: ["stats-sol-min", "stats-sol-max"],
        text: "Sols must be whole numbers from 0 to 2,147,483,647.",
      };
    }
    if (f.solMin !== "" && f.solMax !== "" && Number(f.solMin) > Number(f.solMax)) {
      return {
        ids: ["stats-sol-min", "stats-sol-max"],
        text: "“From sol” must not be greater than “To sol”.",
      };
    }
  } else if (f.dateMin && f.dateMax && f.dateMin > f.dateMax) {
    return {
      ids: ["stats-date-min", "stats-date-max"],
      text: "“From date” must not be after “To date”.",
    };
  }
  return null;
}

export function activeStatsCount(f: StatsFilters, allRovers: number): number {
  return [
    f.rovers.length > 0 && f.rovers.length < allRovers,
    f.solMin !== "" || f.solMax !== "" || f.dateMin !== "" || f.dateMax !== "",
    f.cameras.length > 0,
    f.full,
  ].filter(Boolean).length;
}

// ---------- periods ----------

/** Next period after `p`: `2021-12` -> `2022-01`, `2021` -> `2022`. */
function nextPeriod(p: string): string {
  if (p.length === 4) return String(Number(p) + 1);
  const y = Number(p.slice(0, 4));
  const m = Number(p.slice(5, 7));
  return m >= 12 ? `${y + 1}-01` : `${y}-${String(m + 1).padStart(2, "0")}`;
}

/** Every period from `first` to `last`, inclusive; both are `YYYY` or both `YYYY-MM`. */
export function periodSpan(first: string, last: string): string[] {
  const out: string[] = [];
  let p = first;
  // The bound keeps a malformed value from looping for ever.
  for (let i = 0; p <= last && i < 20_000; i++) {
    out.push(p);
    p = nextPeriod(p);
  }
  return out;
}

type Periodic = { period: string | null };

/**
 * The API leaves out periods without photos. For a continuous time axis the client adds them with
 * zero counts: from the first to the last period of the same rover (a rover is never padded
 * beyond its own first and last period). Accepts the `groups` of a `group_by=month|year`
 * response (any order of rovers; each rover is filled on its own) or a plain list of period
 * strings, and returns the same kind of list. Rows that were added have `total_photos: 0`.
 */
export function fillPeriods(periods: string[]): string[];
export function fillPeriods<T extends Periodic>(groups: T[]): (T | StatsGroup)[];
export function fillPeriods<T extends Periodic>(
  items: (T | string)[],
): (T | StatsGroup | string)[] {
  if (items.length === 0) return [];
  if (typeof items[0] === "string") {
    const list = (items as string[]).filter(Boolean).sort();
    return list.length ? periodSpan(list[0]!, list[list.length - 1]!) : [];
  }
  const groups = items as T[];
  // Keep the order in which rovers first appear.
  const order: string[] = [];
  const byRover = new Map<string, T[]>();
  for (const g of groups) {
    const rover = String((g as unknown as { rover?: string }).rover ?? "");
    if (!byRover.has(rover)) {
      byRover.set(rover, []);
      order.push(rover);
    }
    byRover.get(rover)!.push(g);
  }
  const out: (T | StatsGroup)[] = [];
  for (const rover of order) {
    const rows = byRover
      .get(rover)!
      .filter((g) => g.period)
      .sort((a, b) => (a.period as string).localeCompare(b.period as string));
    if (rows.length === 0) continue;
    const have = new Map(rows.map((g) => [g.period as string, g]));
    for (const p of periodSpan(
      rows[0]!.period as string,
      rows[rows.length - 1]!.period as string,
    )) {
      out.push(
        have.get(p) ??
          ({
            rover,
            camera: null,
            period: p,
            total_photos: 0,
            sols_with_photos: 0,
            first_sol: 0,
            last_sol: 0,
            first_date: "",
            last_date: "",
          } satisfies StatsGroup),
      );
    }
  }
  return out;
}

// ---------- chart scale ----------

/** A round upper bound for the axis: 1, 2, 4, 8 or 10 times a power of ten. */
export function niceMax(max: number): number {
  if (max <= 0) return 1;
  const mag = 10 ** Math.floor(Math.log10(max));
  return [1, 2, 4, 8, 10].map((x) => x * mag).find((x) => x >= max) ?? max;
}

/** Axis label: `1.5k`, `2M`. */
export function axisLabel(v: number): string {
  if (v >= 1e6) return `${trim(v / 1e6)}M`;
  if (v >= 1e3) return `${trim(v / 1e3)}k`;
  return String(v);
}
const trim = (n: number) => String(Number(n.toFixed(2)));

/** Label under a bar: the year (months show it only in January). */
export function periodLabel(period: string): string {
  return period.slice(0, 4);
}

/** Month `2021-03` as `Mar 2021`; a year stays as is. */
export function periodName(period: string): string {
  if (period.length !== 7) return period;
  const names = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
  ];
  return `${names[Number(period.slice(5, 7)) - 1] ?? period.slice(5, 7)} ${period.slice(0, 4)}`;
}
