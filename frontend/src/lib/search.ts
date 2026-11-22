// Logic of the Search page without a DOM: address <-> filters <-> API query, validation, hints.
import type { SearchQuery } from "../api/types";

/** `page × per_page` must not exceed this (API.md, `GET /api/v2/photos`). */
export const MAX_BROWSABLE = 10_000;
/** Most camera codes in one query. */
export const MAX_CAMERAS = 10;
export const PER_PAGE_CHOICES = [24, 48, 96];
export const DEFAULT_PER_PAGE = 24;
/** Chosen by the owner: newest on Mars first (the server default `-sol` mixes eras across rovers). */
export const DEFAULT_SORT = "-taken_at";
const MAX_INT = 2_147_483_647;

export const SORT_CHOICES: { value: string; label: string }[] = [
  { value: "-taken_at", label: "Newest on Mars (latest taken first)" },
  { value: "taken_at", label: "Oldest on Mars (earliest taken first)" },
  { value: "-sol", label: "Latest sol first (per rover)" },
  { value: "sol", label: "Earliest sol first (per rover)" },
];
const SORT_VALUES = SORT_CHOICES.map((s) => s.value);

export type WhenMode = "sol" | "date";
export type TimeMode = "any" | "golden" | "custom";

export interface SearchFilters {
  /** Chosen rover slugs; empty means "all" (the `rovers` parameter is not sent). */
  rovers: string[];
  cameras: string[];
  when: WhenMode;
  solMin: string;
  solMax: string;
  dateMin: string;
  dateMax: string;
  sampleType: string;
  sort: string;
  time: TimeMode;
  timeMin: string;
  timeMax: string;
  minWidth: string;
  minHeight: string;
  perPage: number;
  page: number;
}

export function defaultFilters(): SearchFilters {
  return {
    rovers: [],
    cameras: [],
    when: "sol",
    solMin: "",
    solMax: "",
    dateMin: "",
    dateMax: "",
    sampleType: "",
    sort: DEFAULT_SORT,
    time: "any",
    timeMin: "",
    timeMax: "",
    minWidth: "",
    minHeight: "",
    perPage: DEFAULT_PER_PAGE,
    page: 1,
  };
}

const uniq = (list: string[]) => [...new Set(list)];
const csv = (v: string | null) =>
  (v ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
const posInt = (v: string | null) => (v !== null && /^\d+$/.test(v) ? Number(v) : undefined);

/** Filters from an address (`?rovers=…&camera=…`); the address uses the names of the API parameters. */
export function parseSearch(search: string): SearchFilters {
  const q = new URLSearchParams(search);
  const f = defaultFilters();
  f.rovers = uniq(csv(q.get("rovers")).map((r) => r.toLowerCase()));
  f.cameras = uniq(csv(q.get("camera")).map((c) => c.toUpperCase())).slice(0, MAX_CAMERAS);

  const sol = q.get("sol");
  f.solMin = q.get("sol_min") ?? sol ?? "";
  f.solMax = q.get("sol_max") ?? sol ?? "";
  const day = q.get("earth_date");
  f.dateMin = q.get("date_min") ?? day ?? "";
  f.dateMax = q.get("date_max") ?? day ?? "";
  const hasSol = f.solMin !== "" || f.solMax !== "";
  f.when = !hasSol && (f.dateMin !== "" || f.dateMax !== "") ? "date" : "sol";
  if (f.when === "sol") {
    f.dateMin = "";
    f.dateMax = "";
  } else {
    f.solMin = "";
    f.solMax = "";
  }

  f.sampleType = q.get("sample_type") ?? "";
  const sort = q.get("sort");
  f.sort = sort !== null && SORT_VALUES.includes(sort) ? sort : DEFAULT_SORT;

  if (q.get("golden_hour") === "true" || q.get("golden_hour") === "1") {
    f.time = "golden";
  } else if (q.has("local_time_min") || q.has("local_time_max")) {
    f.time = "custom";
    f.timeMin = q.get("local_time_min") ?? "";
    f.timeMax = q.get("local_time_max") ?? "";
  }
  f.minWidth = q.get("min_width") ?? "";
  f.minHeight = q.get("min_height") ?? "";

  const per = posInt(q.get("per_page"));
  f.perPage = per !== undefined && per >= 1 && per <= 100 ? per : DEFAULT_PER_PAGE;
  const page = posInt(q.get("page"));
  f.page = page !== undefined && page >= 1 ? page : 1;
  return f;
}

/** True when the same value stands in both ends: one sol (a light query) rather than a range. */
export const isSingleSol = (f: SearchFilters): boolean =>
  f.when === "sol" && f.solMin !== "" && f.solMin === f.solMax;
export const isSingleDate = (f: SearchFilters): boolean =>
  f.when === "date" && f.dateMin !== "" && f.dateMin === f.dateMax;

/** Chosen rovers; `all` is the list the API knows. Everything chosen (or nothing) is "all". */
function rovers(f: SearchFilters, all: string[]): string[] {
  const chosen = f.rovers.filter((r) => all.length === 0 || all.includes(r));
  return chosen.length === 0 || (all.length > 0 && all.every((r) => chosen.includes(r)))
    ? []
    : chosen;
}

type Pairs = Record<string, string | number>;

function filterPairs(f: SearchFilters, all: string[]): Pairs {
  const p: Pairs = {};
  const chosen = rovers(f, all);
  if (chosen.length) p.rovers = chosen.join(",");
  if (f.when === "sol") {
    if (isSingleSol(f)) p.sol = f.solMin;
    else {
      if (f.solMin !== "") p.sol_min = f.solMin;
      if (f.solMax !== "") p.sol_max = f.solMax;
    }
  } else if (isSingleDate(f)) {
    p.earth_date = f.dateMin;
  } else {
    if (f.dateMin) p.date_min = f.dateMin;
    if (f.dateMax) p.date_max = f.dateMax;
  }
  if (f.cameras.length) p.camera = f.cameras.join(",");
  if (f.sampleType) p.sample_type = f.sampleType;
  if (f.time === "golden") p.golden_hour = "true";
  if (f.time === "custom") {
    if (f.timeMin) p.local_time_min = f.timeMin;
    if (f.timeMax) p.local_time_max = f.timeMax;
  }
  if (f.minWidth !== "") p.min_width = f.minWidth;
  if (f.minHeight !== "") p.min_height = f.minHeight;
  return p;
}

/** The query sent to `GET /api/v2/photos`: always with the sort, the page and the page size. */
export function toQuery(f: SearchFilters, all: string[] = []): SearchQuery {
  return { ...filterPairs(f, all), sort: f.sort, page: f.page, per_page: f.perPage } as SearchQuery;
}

/** The address of the page for these filters; defaults are left out. */
export function toSearchString(f: SearchFilters, all: string[] = []): string {
  const p: Pairs = filterPairs(f, all);
  if (f.sort !== DEFAULT_SORT) p.sort = f.sort;
  if (f.page > 1) p.page = f.page;
  if (f.perPage !== DEFAULT_PER_PAGE) p.per_page = f.perPage;
  const s = new URLSearchParams(Object.entries(p).map(([k, v]) => [k, String(v)])).toString();
  return s ? `?${s.replace(/%2C/g, ",").replace(/%3A/g, ":")}` : "";
}

export const searchHref = (f: SearchFilters, all: string[] = []): string =>
  `/search${toSearchString(f, all)}`;

/** `/search` address for one rover and camera, as linked from the stats table. */
export function searchLink(rover: string, camera?: string | null): string {
  const f = { ...defaultFilters(), rovers: [rover], cameras: camera ? [camera] : [] };
  return searchHref(f);
}

// ---------- validation ----------

export interface FieldProblem {
  /** Which message slot the text belongs to. */
  key: "when" | "time" | "size";
  /** `id`s of the inputs to mark invalid; the first gets the focus. */
  ids: string[];
  text: string;
}

const isInt = (v: string) => /^\d+$/.test(v) && Number(v) <= MAX_INT;

export function validateFilters(f: SearchFilters): FieldProblem[] {
  const out: FieldProblem[] = [];
  if (f.when === "sol") {
    if ((f.solMin !== "" && !isInt(f.solMin)) || (f.solMax !== "" && !isInt(f.solMax))) {
      out.push({
        key: "when",
        ids: ["search-sol-min", "search-sol-max"],
        text: "Sols must be whole numbers from 0 to 2,147,483,647.",
      });
    } else if (f.solMin !== "" && f.solMax !== "" && Number(f.solMin) > Number(f.solMax)) {
      out.push({
        key: "when",
        ids: ["search-sol-min", "search-sol-max"],
        text: "“From sol” must not be greater than “To sol”.",
      });
    }
  } else if (f.dateMin && f.dateMax && f.dateMin > f.dateMax) {
    out.push({
      key: "when",
      ids: ["search-date-min", "search-date-max"],
      text: "“From date” must not be after “To date”.",
    });
  }
  if (f.time === "custom") {
    if (f.timeMin && f.timeMax && f.timeMin === f.timeMax) {
      out.push({
        key: "time",
        ids: ["search-time-min", "search-time-max"],
        text: "Start and end of the time window must differ. To cover the whole day, choose “Any time”.",
      });
    } else if (!f.timeMin && !f.timeMax) {
      out.push({
        key: "time",
        ids: ["search-time-min"],
        text: "Enter at least one end of the time window, or choose “Any time”.",
      });
    }
  }
  for (const [value, id] of [
    [f.minWidth, "search-min-width"],
    [f.minHeight, "search-min-height"],
  ] as const) {
    if (value !== "" && (!/^\d+$/.test(value) || Number(value) < 1 || Number(value) > 32_767)) {
      out.push({
        key: "size",
        ids: [id],
        text: "Frame size must be a whole number from 1 to 32,767.",
      });
      break;
    }
  }
  return out;
}

/** Local time window that runs through midnight (`min` after `max`): a note for the visitor. */
export const wrapsMidnight = (f: SearchFilters): boolean =>
  f.time === "custom" && !!f.timeMin && !!f.timeMax && f.timeMin > f.timeMax;

// ---------- hints ----------

/**
 * "May be slow": a simplified copy of the heavy-search rules of API.md ("Перегрузка"). The API has
 * no flag for it, so this can drift; it only decides whether to show a warning.
 */
export function isHeavy(f: SearchFilters): boolean {
  if (isSingleSol(f) || isSingleDate(f)) return false;
  const dateRange = f.when === "date" && (f.dateMin !== "" || f.dateMax !== "");
  const filtered = f.cameras.length > 0 || f.sampleType !== "";
  return (
    dateRange ||
    f.time !== "any" ||
    f.minWidth !== "" ||
    f.minHeight !== "" ||
    (f.sampleType !== "" && f.sampleType !== "full") ||
    (filtered && f.sort.endsWith("taken_at"))
  );
}

/** A camera without a sol or a date: the owner asked for a hint to pick a sol (it makes the query fast). */
export function needsSolHint(f: SearchFilters): boolean {
  if (f.cameras.length === 0) return false;
  return f.when === "sol"
    ? f.solMin === "" && f.solMax === ""
    : f.dateMin === "" && f.dateMax === "";
}

/** Number of pages the Pager may show: the API refuses to go past 10,000 photos. */
export function pageLimit(total: number, perPage: number): { pages: number; capped: boolean } {
  const wanted = Math.max(1, Math.ceil(total / perPage));
  const cap = Math.max(1, Math.floor(MAX_BROWSABLE / perPage));
  return { pages: Math.min(wanted, cap), capped: wanted > cap };
}

/** Number of filters that differ from the defaults (shown on the phone "Filters" button). */
export function activeCount(f: SearchFilters, allRovers: number): number {
  return [
    f.rovers.length > 0 && f.rovers.length < allRovers,
    f.solMin !== "" || f.solMax !== "" || f.dateMin !== "" || f.dateMax !== "",
    f.cameras.length > 0,
    f.time !== "any",
    f.minWidth !== "" || f.minHeight !== "",
    f.sampleType !== "",
    f.sort !== DEFAULT_SORT,
  ].filter(Boolean).length;
}

const SORT_WORDS: Record<string, string> = {
  "-taken_at": "newest on Mars",
  taken_at: "oldest on Mars",
  "-sol": "latest sol first",
  sol: "earliest sol first",
};

/** One line that sums up the filters, for the collapsed phone panel. */
export function summaryText(f: SearchFilters, names: (id: string) => string, all: number): string {
  const parts: string[] = [];
  parts.push(
    f.rovers.length === 0 || f.rovers.length >= all
      ? "All rovers"
      : f.rovers.map(names).join(" + "),
  );
  if (f.when === "sol") {
    if (f.solMin === "" && f.solMax === "") parts.push("any sol");
    else if (isSingleSol(f)) parts.push(`sol ${f.solMin}`);
    else parts.push(`sols ${f.solMin || "first"}–${f.solMax || "last"}`);
  } else if (f.dateMin === "" && f.dateMax === "") parts.push("any date");
  else parts.push(`${f.dateMin || "start"} → ${f.dateMax || "now"}`);
  if (f.cameras.length === 0) parts.push("all cameras");
  else parts.push(f.cameras.length === 1 ? f.cameras[0]! : `${f.cameras.length} cameras`);
  if (f.time === "golden") parts.push("golden hour");
  if (f.time === "custom") parts.push(`local ${f.timeMin || "00:00"}–${f.timeMax || "24:00"}`);
  if (f.minWidth !== "" || f.minHeight !== "") {
    parts.push(`≥ ${f.minWidth || "·"}×${f.minHeight || "·"} px`);
  }
  if (f.sampleType) parts.push(`${f.sampleType} frames`);
  parts.push(SORT_WORDS[f.sort] ?? f.sort);
  return parts.join(" · ");
}

/** Likely reasons for an empty result, from the active filters (the API does not say which one emptied it). */
export function emptyReasons(
  f: SearchFilters,
  chosen: { name: string; max_sol: number | null }[],
  hasCuriosity: boolean,
): string[] {
  const out: string[] = [];
  if (f.time !== "any") {
    out.push(
      hasCuriosity
        ? "Time-of-day filters skip photos without a local time (about half of Curiosity’s)."
        : "Time-of-day filters skip photos without a local time.",
    );
  }
  if (f.minWidth !== "" || f.minHeight !== "") {
    out.push(
      "Size filters skip photos with no known frame size (all of Curiosity’s), and frames smaller than asked.",
    );
  }
  if (f.when === "sol" && (f.solMin !== "" || f.solMax !== "")) {
    out.push(
      `The sol range may lie beyond a rover’s latest sol: ${chosen
        .filter((r) => r.max_sol !== null)
        .map((r) => `${r.name} ${r.max_sol}`)
        .join(", ")}.`,
    );
  }
  if (f.cameras.length > 0) {
    out.push(
      "The chosen cameras may not have taken photos in this range, or belong to other rovers.",
    );
  }
  if (f.sampleType) out.push(`Only “${f.sampleType}” frames are included.`);
  return out.length ? out : ["Try a wider sol or date range, or fewer cameras."];
}

/** The filters without the time-of-day and frame-size conditions (the "drop heavy filters" shortcut). */
export function withoutHeavyFilters(f: SearchFilters): SearchFilters {
  return { ...f, time: "any", timeMin: "", timeMax: "", minWidth: "", minHeight: "", page: 1 };
}
