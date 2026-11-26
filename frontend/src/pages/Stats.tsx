import { useMemo, useRef, useState } from "preact/hooks";
import type { JSX } from "preact";
import { api, photoStats, requestLine } from "../api/client";
import { periodInvalidIds } from "../api/errors";
import type { Camera, Rover, StatsGroup, StatsResponse } from "../api/types";
import { BarChart, type Bar } from "../components/BarChart";
import {
  CameraPicker,
  RoverChips,
  toggleRoverDraft,
  useCameraLists,
  WhenFields,
} from "../components/FilterParts";
import { Pager } from "../components/Pager";
import { ErrorAlert, PageSkeleton } from "../components/States";
import { fmt, statusLabel, visibleRovers } from "../lib/format";
import { useAsync, useMedia, useRovers, useTitle, type Async } from "../lib/hooks";
import { defaultFilters, searchHref } from "../lib/search";
import {
  activeStatsCount,
  fillPeriods,
  MAX_STATS_CAMERAS,
  parseStats,
  STATS_TABS,
  statsQuery,
  TABLE_ROWS,
  toStatsString,
  validateStats,
  type StatsFilters,
  type StatsProblem,
  type StatsTab,
} from "../lib/stats";
import { Link, navigate, useLocation } from "../router";

export function Stats() {
  const [rovers, reload] = useRovers();
  useTitle("Stats — Sol Index");
  if (rovers.status === "loading") return <PageSkeleton label="Loading rovers…" />;
  if (rovers.status === "error") return <ErrorAlert error={rovers.error} onRetry={reload} />;
  return <StatsPage rovers={visibleRovers(rovers.data)} />;
}

const pct = (part: number, whole: number): string => {
  if (!whole) return "0%";
  const v = (100 * part) / whole;
  return `${v.toFixed(v < 10 ? 1 : 0)}%`;
};

function StatsPage({ rovers }: { rovers: Rover[] }) {
  const { pathname, search } = useLocation();
  const allIds = useMemo(() => rovers.map((r) => r.id), [rovers]);
  const idsKey = allIds.join(",");
  const nameOf = (id: string) => rovers.find((r) => r.id === id)?.name ?? id;

  const applied = useMemo<StatsFilters>(() => {
    const f = parseStats(search);
    return { ...f, rovers: f.rovers.filter((r) => allIds.includes(r)) };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, idsKey]);
  const params = new URLSearchParams(search);
  const view: "chart" | "table" = params.get("view") === "table" ? "table" : "chart";
  const pageText = params.get("page") ?? "";
  const tablePage = /^\d+$/.test(pageText) ? Math.max(1, Number(pageText)) : 1;

  const explicit = (f: StatsFilters): StatsFilters => ({
    ...f,
    rovers: f.rovers.length ? f.rovers : allIds,
  });
  const [draft, setDraft] = useState<StatsFilters>(() => explicit(applied));
  const [problem, setProblem] = useState<StatsProblem | null>(null);
  // The filters of the address (without the tab, the view and the table page) decide when the form is refilled.
  const filterKey = toStatsString({ ...applied, tab: "month" }, allIds);
  const [seen, setSeen] = useState(filterKey);
  if (seen !== filterKey) {
    setSeen(filterKey);
    setDraft(explicit(applied));
    setProblem(null);
  }
  const set = (patch: Partial<StatsFilters>) => setDraft((d) => ({ ...d, ...patch }));

  const wide = useMedia("(min-width: 760px)");
  const [open, setOpen] = useState(wide);
  const [pickerOpen, setPickerOpen] = useState(true);
  const chosenRovers = rovers.filter((r) => draft.rovers.includes(r.id));
  const toggleRover = (id: string) => setDraft(toggleRoverDraft(draft, id, cams.lists, setDraft));
  const toggleCamera = (code: string) =>
    setDraft((d) =>
      d.cameras.includes(code)
        ? { ...d, cameras: d.cameras.filter((c) => c !== code) }
        : d.cameras.length >= MAX_STATS_CAMERAS
          ? d
          : { ...d, cameras: [...d.cameras, code] },
    );

  // Two requests: totals by rover (summary and cards), then the chosen tab.
  const [top, retryTop] = useAsync(
    (signal) => photoStats(statsQuery(applied, "rover", allIds), signal),
    [filterKey, idsKey],
  );
  const [tab, retryTab] = useAsync(
    (signal) => photoStats(statsQuery(applied, applied.tab, allIds), signal),
    [filterKey, idsKey, applied.tab],
  );
  // The rover cards link to Search with the chosen cameras that belong to each rover, so with cameras
  // chosen their lists are loaded before the cards appear (one small request per rover, queued).
  const [cardCams] = useAsync<Record<string, Camera[]> | null>(async () => {
    if (top.status !== "ok") return null;
    if (applied.cameras.length === 0) return {};
    const out: Record<string, Camera[]> = {};
    await Promise.all(
      top.data.groups.map((g) =>
        api.cameras(g.rover).then(
          (items) => {
            out[g.rover] = items;
          },
          () => {},
        ),
      ),
    );
    return out;
  }, [top.status, filterKey, idsKey]);
  const cardsReady =
    applied.cameras.length === 0 ||
    (top.status === "ok" && cardCams.status === "ok" && cardCams.data !== null);
  const cams = useCameraLists(
    draft.rovers,
    pickerOpen && top.status !== "loading" && tab.status !== "loading",
  );

  const onSubmit = (e: Event) => {
    e.preventDefault();
    const found = validateStats(draft);
    setProblem(found);
    if (found) {
      setOpen(true);
      requestAnimationFrame(() => document.getElementById(found.ids[0]!)?.focus());
      return;
    }
    if (!wide) setOpen(false);
    const q = new URLSearchParams(toStatsString({ ...draft, tab: applied.tab }, allIds));
    if (view === "table") q.set("view", "table");
    const qsText = q.toString().replace(/%2C/g, ",");
    const url = `/stats${qsText ? `?${qsText}` : ""}`;
    if (url === `${pathname}${search}`) {
      retryTop();
      retryTab();
    } else navigate(url);
  };
  const reset = () => {
    setProblem(null);
    if (`${pathname}${search}` === "/stats") {
      setDraft(explicit({ ...applied, ...parseStats(""), tab: applied.tab }));
      retryTop();
      retryTab();
    } else navigate("/stats");
  };

  /** Address of this page with the tab, the view and the table page changed. */
  const hrefFor = (patch: { tab?: StatsTab; view?: string; page?: number }) => {
    const p = new URLSearchParams(
      toStatsString({ ...applied, tab: patch.tab ?? applied.tab }, allIds),
    );
    // The Chart / Table choice survives a change of tab.
    if ((patch.view ?? view) === "table") p.set("view", "table");
    if (patch.page && patch.page > 1) p.set("page", String(patch.page));
    const s = p.toString().replace(/%2C/g, ",");
    return `/stats${s ? `?${s}` : ""}`;
  };

  const count = activeStatsCount(draft, rovers.length);
  const topQuery = statsQuery(applied, "rover", allIds) as Record<string, string | number>;
  const tabQuery = statsQuery(applied, applied.tab, allIds) as Record<string, string | number>;

  return (
    <>
      <div class="page-head">
        <h1>Stats</h1>
        <span class="small muted">How many photos each rover has sent</span>
      </div>
      <p class="lede muted">
        How many photos each rover has sent, by month, year or camera. Counts come from the same
        sync summaries as the Timeline, so they are instant.
      </p>

      <form
        class="card filters-card"
        noValidate
        aria-label="Stats filters"
        data-testid="stats-form"
        onSubmit={onSubmit}
      >
        <div class="filters-bar">
          <button
            class="btn btn-sm filters-toggle"
            type="button"
            aria-expanded={open}
            aria-controls="stats-filters-panel"
            onClick={() => setOpen(!open)}
          >
            <span>{open ? "Hide filters" : "Filters"}</span>
            {count > 0 && <span class="badge badge-warn no-dot">{count} active</span>}
          </button>
          <div class="summary-text">{summaryOf(draft, nameOf, rovers.length)}</div>
          <button class="btn btn-sm" type="button" onClick={reset}>
            Reset
          </button>
        </div>
        <div class="filters-panel" id="stats-filters-panel" data-collapsed={String(!open)}>
          <fieldset class="fieldset">
            <legend>Rovers</legend>
            <RoverChips rovers={rovers} selected={draft.rovers} onToggle={toggleRover} />
            <span class="hint">At least one.</span>
          </fieldset>
          <fieldset class="fieldset">
            <legend>Period</legend>
            <WhenFields
              idPrefix="stats"
              mode={draft.when}
              solMin={draft.solMin}
              solMax={draft.solMax}
              dateMin={draft.dateMin}
              dateMax={draft.dateMax}
              onMode={(when) =>
                set(
                  when === "sol"
                    ? { when, dateMin: "", dateMax: "" }
                    : { when, solMin: "", solMax: "" },
                )
              }
              onChange={(patch) => set(patch)}
              invalid={[
                ...(problem?.ids ?? []),
                ...periodInvalidIds("stats", top.status === "error" ? top.error : null),
              ]}
              error={problem?.text}
              hint={
                draft.when === "date"
                  ? "Dates here are the day of each sol’s first photo, not of every single photo. Photos from a sol that crosses midnight UTC count on the day it started, so totals can differ slightly from Search."
                  : "Sols are counted separately for each rover."
              }
            />
          </fieldset>
          <fieldset class="fieldset wide">
            <legend>Cameras</legend>
            <CameraPicker
              idPrefix="stats"
              rovers={chosenRovers}
              selected={draft.cameras}
              max={MAX_STATS_CAMERAS}
              open={pickerOpen}
              onOpen={setPickerOpen}
              onToggle={toggleCamera}
              onClear={() => set({ cameras: [] })}
              lists={cams.lists}
              failed={cams.failed}
              onRetry={cams.retry}
            />
            <label class="check">
              <input
                type="checkbox"
                checked={draft.full}
                onChange={(e) => set({ full: e.currentTarget.checked })}
              />{" "}
              Full frames only
            </label>
          </fieldset>
          <div class="wide actions">
            <button class="btn btn-primary" type="submit" data-testid="stats-submit">
              Show stats
            </button>
          </div>
        </div>
      </form>

      <section aria-label="Stats results" aria-busy={top.status === "loading"}>
        {top.status === "loading" && <Loading />}
        {top.status === "error" && (
          <ErrorAlert
            error={top.error}
            onRetry={retryTop}
            title="We couldn’t load these stats"
            actions={
              <button class="btn btn-sm" type="button" onClick={reset}>
                Clear the filters
              </button>
            }
          />
        )}
        {top.status === "ok" && top.data.total_photos === 0 && (
          <div class="card empty" data-testid="stats-empty">
            <h3>No photos match these filters</h3>
            <ul class="reasons">
              {emptyReasons(applied, rovers).map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
            <p class="xsmall muted">
              The API answers with <code>total_photos: 0</code> and <code>groups: []</code>.
            </p>
            <div class="row">
              <button class="btn btn-sm" type="button" onClick={reset}>
                Reset filters
              </button>
            </div>
          </div>
        )}
        {top.status === "ok" && top.data.total_photos > 0 && !cardsReady && <Loading />}
        {top.status === "ok" && top.data.total_photos > 0 && cardsReady && (
          <>
            <Summary
              data={top.data}
              rovers={rovers}
              applied={applied}
              lists={(cardCams.status === "ok" && cardCams.data) || cams.lists}
            />
            <Breakdown
              applied={applied}
              rovers={rovers}
              result={tab}
              retry={retryTab}
              view={view}
              tablePage={tablePage}
              hrefFor={hrefFor}
              onTab={(t) => navigate(hrefFor({ tab: t }), { replace: true })}
              onView={(v) => navigate(hrefFor({ view: v }), { replace: true })}
            />
          </>
        )}
      </section>
      <p class="api-line">
        API: <code>{requestLine("/photos/stats", topQuery)}</code>
        {" · "}
        <code>{requestLine("/photos/stats", tabQuery)}</code>
      </p>
    </>
  );
}

function Loading() {
  return (
    <div role="status" aria-live="polite">
      <span class="visually-hidden">Loading stats…</span>
      <div class="cards" aria-hidden="true">
        {[0, 1, 2].map((i) => (
          <div key={i} class="card rcard">
            <div class="skeleton" style={{ height: 20, width: "50%" }} />
            <div class="skeleton" style={{ height: 36, width: "70%" }} />
            <div class="skeleton" style={{ height: 14 }} />
            <div class="skeleton" style={{ height: 14, width: "80%" }} />
          </div>
        ))}
      </div>
    </div>
  );
}

function summaryOf(f: StatsFilters, names: (id: string) => string, all: number): string {
  const parts = [
    f.rovers.length === 0 || f.rovers.length >= all
      ? "All rovers"
      : f.rovers.map(names).join(" + "),
  ];
  if (f.when === "sol") {
    parts.push(
      f.solMin === "" && f.solMax === ""
        ? "all sols"
        : `sols ${f.solMin || "first"}–${f.solMax || "last"}`,
    );
  } else {
    parts.push(
      f.dateMin === "" && f.dateMax === ""
        ? "all dates"
        : `${f.dateMin || "start"} → ${f.dateMax || "now"}`,
    );
  }
  parts.push(
    f.cameras.length
      ? `${f.cameras.length} ${f.cameras.length === 1 ? "camera" : "cameras"}`
      : "all cameras",
  );
  if (f.full) parts.push("full frames only");
  return parts.join(" · ");
}

function emptyReasons(f: StatsFilters, rovers: Rover[]): string[] {
  const out: string[] = [];
  if (f.when === "sol" && (f.solMin !== "" || f.solMax !== "")) {
    out.push(
      `The sol range may start after a rover’s latest sol: ${rovers.map((r) => `${r.name} ${r.max_sol}`).join(", ")}.`,
    );
  }
  if (f.when === "date") out.push("The dates may fall before landing or after the latest photo.");
  if (f.cameras.length) {
    out.push(
      "None of the chosen cameras belongs to the chosen rovers, or they took no photos in this period.",
    );
  }
  return out.length ? out : ["Try a wider period."];
}

/** Totals for the selection and one card per rover. */
function Summary({
  data,
  rovers,
  applied,
  lists,
}: {
  data: StatsResponse;
  rovers: Rover[];
  applied: StatsFilters;
  lists: Record<string, Camera[]>;
}) {
  return (
    <div data-testid="stats-summary">
      <div class="card overall" role="group" aria-label="Totals for the selection">
        <div>
          <div class="k">Photos</div>
          <div class="v">{fmt(data.total_photos)}</div>
        </div>
        <div>
          <div class="k">
            Sols with photos <span class="visually-hidden">(summed over rovers)</span>
          </div>
          <div class="v">{fmt(data.sols_with_photos)}</div>
        </div>
        <div>
          <div class="k">First photo</div>
          <div class="v sm">{data.first_date ?? "—"}</div>
        </div>
        <div>
          <div class="k">Latest photo</div>
          <div class="v sm">{data.last_date ?? "—"}</div>
        </div>
        {data.groups.length > 1 && (
          <div class="xsmall muted" style={{ flexBasis: "100%" }}>
            Sols of different rovers are counted separately and added up: they are not calendar
            days.
          </div>
        )}
      </div>
      <div class="cards">
        {data.groups.map((g) => {
          const r = rovers.find((x) => x.id === g.rover);
          const name = r?.name ?? g.rover;
          return (
            <article key={g.rover} class="card rcard" aria-labelledby={`rc-${g.rover}`}>
              <header>
                <h3 id={`rc-${g.rover}`}>{name}</h3>
                {r && (
                  <span
                    class={`badge ${r.status === "active" ? "badge-active" : "badge-complete"}`}
                  >
                    {statusLabel(r.status)}
                  </span>
                )}
              </header>
              <div class="big">
                {fmt(g.total_photos)} <small>photos</small>
              </div>
              <dl class="stats">
                <div>
                  <dt>Sols with photos</dt>
                  <dd>{fmt(g.sols_with_photos)}</dd>
                </div>
                <div>
                  <dt>Share of selection</dt>
                  <dd>{pct(g.total_photos, data.total_photos)}</dd>
                </div>
                <div>
                  <dt>First</dt>
                  <dd>
                    sol {fmt(g.first_sol)} · {g.first_date}
                  </dd>
                </div>
                <div>
                  <dt>Last</dt>
                  <dd>
                    sol {fmt(g.last_sol)} · {g.last_date}
                  </dd>
                </div>
              </dl>
              <div>
                <Link
                  class="btn btn-sm"
                  href={searchLinkFor(g.rover, applied, lists[g.rover], data.groups.length === 1)}
                  aria-label={`Search ${name} photos`}
                >
                  Search these photos
                </Link>
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}

/**
 * `/search` for a rover with the filters of the page: its own cameras only (the API refuses a
 * camera that belongs to no chosen rover), the period and "full frames only".
 */
function searchLinkFor(
  rover: string,
  f: StatsFilters,
  known: Camera[] | undefined,
  alone: boolean,
): string {
  const cameras = known
    ? f.cameras.filter((c) => known.some((k) => k.code === c))
    : alone
      ? f.cameras
      : [];
  return searchHref({
    ...defaultFilters(),
    rovers: [rover],
    cameras,
    when: f.when,
    solMin: f.solMin,
    solMax: f.solMax,
    dateMin: f.dateMin,
    dateMax: f.dateMax,
    sampleType: f.full ? "full" : "",
  });
}

interface BreakdownProps {
  applied: StatsFilters;
  rovers: Rover[];
  result: Async<StatsResponse>;
  retry: () => void;
  view: "chart" | "table";
  tablePage: number;
  hrefFor: (patch: { tab?: StatsTab; view?: string; page?: number }) => string;
  onTab: (t: StatsTab) => void;
  onView: (v: "chart" | "table") => void;
}

function Breakdown(p: BreakdownProps) {
  const tabs = STATS_TABS;
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const onKey = (e: JSX.TargetedKeyboardEvent<HTMLButtonElement>, i: number) => {
    const k = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!k) return;
    e.preventDefault();
    const next = (i + k + tabs.length) % tabs.length;
    refs.current[next]?.focus();
    p.onTab(tabs[next]!.id);
  };
  const periodic = p.applied.tab !== "camera";
  return (
    <div>
      <div class="tabs" role="tablist" aria-label="Break down by">
        {tabs.map((t, i) => (
          <button
            key={t.id}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="tab"
            id={`stats-tab-${t.id}`}
            aria-controls="stats-tabpanel"
            aria-selected={p.applied.tab === t.id}
            tabIndex={p.applied.tab === t.id ? 0 : -1}
            data-testid={`stats-tab-${t.id}`}
            onClick={() => p.onTab(t.id)}
            onKeyDown={(e) => onKey(e, i)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div
        id="stats-tabpanel"
        role="tabpanel"
        aria-labelledby={`stats-tab-${p.applied.tab}`}
        tabIndex={-1}
      >
        {periodic && (
          <div class="panel-tools">
            <div class="segmented" role="radiogroup" aria-label="Show as">
              <input
                type="radio"
                name="stats-view"
                id="stats-view-chart"
                checked={p.view === "chart"}
                onChange={() => p.onView("chart")}
              />
              <label for="stats-view-chart">Chart</label>
              <input
                type="radio"
                name="stats-view"
                id="stats-view-table"
                checked={p.view === "table"}
                data-testid="stats-table-toggle"
                onChange={() => p.onView("table")}
              />
              <label for="stats-view-table">Table</label>
            </div>
            <span class="xsmall muted">
              Each rover has its own scale. The table has the same numbers.
            </span>
          </div>
        )}
        {p.result.status === "loading" && (
          <div class="card panel" role="status">
            <div class="skeleton" style={{ height: 200 }} />
            <span class="visually-hidden">Loading…</span>
          </div>
        )}
        {p.result.status === "error" && (
          <ErrorAlert
            error={p.result.error}
            onRetry={p.retry}
            title="We couldn’t load this breakdown"
          />
        )}
        {p.result.status === "ok" && p.result.data.groups.length === 0 && (
          <div class="card empty" data-testid="stats-empty">
            <h3>No photos match these filters</h3>
          </div>
        )}
        {p.result.status === "ok" && p.result.data.groups.length > 0 && (
          <>
            {p.applied.tab === "camera" ? (
              <CameraTable groups={p.result.data.groups} rovers={p.rovers} applied={p.applied} />
            ) : p.view === "table" ? (
              <PeriodTable
                groups={p.result.data.groups}
                rovers={p.rovers}
                unit={p.applied.tab}
                page={p.tablePage}
                hrefFor={(n) => p.hrefFor({ view: "table", page: n })}
              />
            ) : (
              <PeriodCharts groups={p.result.data.groups} rovers={p.rovers} unit={p.applied.tab} />
            )}
          </>
        )}
      </div>
    </div>
  );
}

/** One chart per rover, each with its own scale; months without photos are drawn as zero. */
function PeriodCharts({
  groups,
  rovers,
  unit,
}: {
  groups: StatsGroup[];
  rovers: Rover[];
  unit: "month" | "year";
}) {
  const filled = fillPeriods(groups);
  const order = [...new Set(filled.map((g) => g.rover))];
  return (
    <>
      {order.map((id) => {
        const rows = filled.filter((g) => g.rover === id);
        const real = groups.filter((g) => g.rover === id);
        const name = rovers.find((r) => r.id === id)?.name ?? id;
        const bars: Bar[] = rows.map((g) => ({
          period: g.period as string,
          photos: g.total_photos,
          sols: g.total_photos > 0 ? g.sols_with_photos : null,
        }));
        const peak = Math.max(...bars.map((b) => b.photos));
        const title = `${name}: photos per ${unit}`;
        return (
          <section key={id} class="card st-chart-card" aria-labelledby={`ct-${id}`}>
            <h3 id={`ct-${id}`}>{title}</h3>
            <p class="xsmall muted" style={{ margin: 0 }}>
              {real.length} {unit}
              {real.length === 1 ? "" : "s"} with photos · scale 0–{fmt(peak)}+
            </p>
            <BarChart id={`chart-${id}`} title={title} unit={unit} bars={bars} />
          </section>
        );
      })}
    </>
  );
}

function PeriodTable({
  groups,
  rovers,
  unit,
  page,
  hrefFor,
}: {
  groups: StatsGroup[];
  rovers: Rover[];
  unit: "month" | "year";
  page: number;
  hrefFor: (n: number) => string;
}) {
  const pages = Math.max(1, Math.ceil(groups.length / TABLE_ROWS));
  const at = Math.min(page, pages);
  const slice = groups.slice((at - 1) * TABLE_ROWS, at * TABLE_ROWS);
  return (
    <>
      <div class="card panel table-wrap">
        <table class="data" data-testid="stats-table">
          <caption>
            Photos per {unit}, rover by rover, oldest first ({fmt(groups.length)} rows; periods
            without photos are not listed)
          </caption>
          <thead>
            <tr>
              <th scope="col">Rover</th>
              <th scope="col">{unit === "month" ? "Month" : "Year"}</th>
              <th scope="col" class="num">
                Photos
              </th>
              <th scope="col" class="num">
                Sols with photos
              </th>
              <th scope="col">First – last date</th>
            </tr>
          </thead>
          <tbody>
            {slice.map((g) => (
              <tr key={`${g.rover}-${g.period}`}>
                <th scope="row">{rovers.find((r) => r.id === g.rover)?.name ?? g.rover}</th>
                <td>{g.period}</td>
                <td class="num">{fmt(g.total_photos)}</td>
                <td class="num">{fmt(g.sols_with_photos)}</td>
                <td>
                  {g.first_date} – {g.last_date}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pager page={at} pages={pages} hrefFor={hrefFor} />
    </>
  );
}

type CamSort = "api" | "desc" | "asc";

function CameraTable({
  groups,
  rovers,
  applied,
}: {
  groups: StatsGroup[];
  rovers: Rover[];
  applied: StatsFilters;
}) {
  const [sort, setSort] = useState<CamSort>("api");
  const total: Record<string, number> = {};
  const most: Record<string, number> = {};
  for (const g of groups) {
    total[g.rover] = (total[g.rover] ?? 0) + g.total_photos;
    most[g.rover] = Math.max(most[g.rover] ?? 0, g.total_photos);
  }
  const rows =
    sort === "api"
      ? groups
      : [...groups].sort((a, b) =>
          sort === "desc" ? b.total_photos - a.total_photos : a.total_photos - b.total_photos,
        );
  return (
    <div class="card panel table-wrap">
      <table class="data" data-testid="stats-table">
        <caption>
          Photos per camera ({fmt(groups.length)} cameras). Bars are relative to the busiest camera
          of the same rover.
        </caption>
        <thead>
          <tr>
            <th scope="col">Rover</th>
            <th scope="col">Camera</th>
            <th
              scope="col"
              class="num"
              aria-sort={sort === "api" ? "none" : sort === "desc" ? "descending" : "ascending"}
            >
              <button
                type="button"
                onClick={() => setSort(sort === "api" ? "desc" : sort === "desc" ? "asc" : "api")}
              >
                Photos{sort === "desc" ? " ▼" : sort === "asc" ? " ▲" : ""}
              </button>
            </th>
            <th scope="col" class="bar-cell">
              Share of rover
            </th>
            <th scope="col" class="num">
              Sols
            </th>
            <th scope="col">First – last sol</th>
            <th scope="col">
              <span class="visually-hidden">Link</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((g) => {
            const name = rovers.find((r) => r.id === g.rover)?.name ?? g.rover;
            const share = (100 * g.total_photos) / (total[g.rover] || 1);
            return (
              <tr key={`${g.rover}-${g.camera}`}>
                <td>{name}</td>
                <td>
                  <span class="cam-code">{g.camera}</span>
                </td>
                <td class="num">{fmt(g.total_photos)}</td>
                <td class="bar-cell share">
                  <span class="bar-track" aria-hidden="true">
                    <span
                      class="bar"
                      style={{
                        width: `${((100 * g.total_photos) / (most[g.rover] || 1)).toFixed(1)}%`,
                      }}
                    />
                  </span>
                  <span class="xsmall muted">
                    {share < 1 ? "<1" : share.toFixed(share < 10 ? 1 : 0)}%
                  </span>
                </td>
                <td class="num">{fmt(g.sols_with_photos)}</td>
                <td>
                  {fmt(g.first_sol)} – {fmt(g.last_sol)}
                </td>
                <td>
                  {g.camera && (
                    <Link
                      href={searchLinkFor(
                        g.rover,
                        { ...applied, cameras: [g.camera] },
                        undefined,
                        true,
                      )}
                      aria-label={`Search ${g.camera} photos of ${name}`}
                    >
                      Search
                    </Link>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
