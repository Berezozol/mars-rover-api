import { useEffect, useState } from "preact/hooks";
import { api } from "../api/client";
import type { Flight, Rover } from "../api/types";
import { ViewTabs } from "../components/ViewTabs";
import { EmptyState, ErrorAlert, PageSkeleton } from "../components/States";
import { fmt, statusLabel } from "../lib/format";
import {
  defaultDir,
  hms,
  mmss,
  recordsOf,
  sortFlights,
  summarize,
  type SortDir,
  type SortKey,
} from "../lib/flights";
import { useAsync, useRovers, useTitle } from "../lib/hooks";
import { Link, useQueryParam, useRestoreScroll } from "../router";

interface Column {
  key: SortKey | "route";
  label: string;
  num: boolean;
}

const COLUMNS: Column[] = [
  { key: "flight", label: "Flight", num: false },
  { key: "sol", label: "Sol", num: true },
  { key: "date", label: "Date (PT)", num: false },
  { key: "distance", label: "Distance", num: true },
  { key: "alt", label: "Max altitude", num: true },
  { key: "speed", label: "Max speed", num: true },
  { key: "dur", label: "Duration", num: true },
  { key: "route", label: "From → to", num: false },
  { key: "photos", label: "Photos", num: true },
];

const SORT_OPTIONS: { value: string; label: string }[] = [
  { value: "flight:asc", label: "Flight number, first to last" },
  { value: "flight:desc", label: "Flight number, last to first" },
  { value: "distance:desc", label: "Distance, longest first" },
  { value: "alt:desc", label: "Max altitude, highest first" },
  { value: "speed:desc", label: "Max speed, fastest first" },
  { value: "dur:desc", label: "Duration, longest first" },
  { value: "photos:desc", label: "Photos, most first" },
];

export function Flights({ roverId }: { roverId: string }) {
  const [rovers, reload] = useRovers();
  useTitle(`Flights — ${roverId} — Sol Index`);
  if (rovers.status === "loading") return <PageSkeleton label="Loading rover…" />;
  if (rovers.status === "error") return <ErrorAlert error={rovers.error} onRetry={reload} />;
  const rover = rovers.data.find((r) => r.id === roverId);
  const back = (
    <Link class="btn btn-sm" href="/">
      All rovers
    </Link>
  );
  if (!rover) {
    return (
      <EmptyState level={1} title="Rover not found" actions={back}>
        We don’t know a rover called “{roverId}”.
      </EmptyState>
    );
  }
  if (rover.id !== "ingenuity") {
    return (
      <EmptyState
        level={1}
        title={`${rover.name} has no flights`}
        actions={
          <>
            <Link class="btn btn-sm" href="/rovers/ingenuity/flights">
              Ingenuity flights
            </Link>
            {back}
          </>
        }
      >
        Only the Ingenuity helicopter has a flight log.
      </EmptyState>
    );
  }
  return <FlightsView rover={rover} />;
}

const DEFAULT_SORT = "flight:asc";
const SORT_KEYS: SortKey[] = ["flight", "sol", "date", "distance", "alt", "speed", "dur", "photos"];

/** `distance:desc` -> itself; anything else -> `undefined` (the default order). */
function parseSort(raw: string): string | undefined {
  const [k, d, ...rest] = raw.split(":");
  return rest.length === 0 && SORT_KEYS.includes(k as SortKey) && (d === "asc" || d === "desc")
    ? raw
    : undefined;
}

function FlightsView({ rover }: { rover: Rover }) {
  const [state, retry] = useAsync(() => api.flights(rover.id), [rover.id]);
  const [sort, setSort] = useQueryParam<string>("sort", parseSort, DEFAULT_SORT);
  const [key, dir] = sort.split(":") as [SortKey, SortDir];
  const [target, setTarget] = useState<number | null>(null);
  const items = state.status === "ok" ? state.data : null;
  useRestoreScroll(state.status === "ok");

  // `#flight-N` anchors: the rows appear after loading, so the browser can't scroll to them itself.
  useEffect(() => {
    if (!items) return;
    const m = /^#flight-(\d+)$/.exec(window.location.hash);
    if (!m) return;
    setTarget(Number(m[1]));
    document.getElementById(`flight-${m[1]}`)?.scrollIntoView?.({ block: "center" });
  }, [items]);

  const sortBy = (k: SortKey) => {
    if (k === key) setSort(`${k}:${dir === "asc" ? "desc" : "asc"}`);
    else setSort(`${k}:${defaultDir(k)}`);
  };
  const selectValue = `${key}:${dir}`;
  const selectKnown = SORT_OPTIONS.some((o) => o.value === selectValue);

  const summary = items ? summarize(items) : null;
  const first = items?.[0];
  const last = items?.[items.length - 1];

  return (
    <>
      <nav class="crumbs" aria-label="Breadcrumb">
        <Link href="/">Rovers</Link> › <Link href={`/rovers/${rover.id}`}>{rover.name}</Link> ›{" "}
        <span aria-current="page">Flights</span>
      </nav>

      <ViewTabs rover={rover.id} name={rover.name} current="flights" />

      <div class="row" style={{ alignItems: "center", marginBottom: "var(--s2)" }}>
        <h1 style={{ margin: 0 }}>{rover.name} flights</h1>
        <span class={`badge ${rover.status === "active" ? "badge-active" : "badge-complete"}`}>
          {rover.status === "complete" ? "Complete" : statusLabel(rover.status)}
        </span>
        {rover.data_source === "archive" && <span class="badge no-dot">Imported</span>}
      </div>
      {first && last ? (
        <p class="muted" style={{ maxWidth: "70ch" }}>
          The Mars helicopter flew {items!.length} times between sol {first.sol} and sol {last.sol}{" "}
          ({first.date} to {last.date}, Pacific time). Sols are Perseverance sols. Open a flight’s
          photos to see everything taken that sol.
        </p>
      ) : (
        <p class="muted" style={{ maxWidth: "70ch" }}>
          The Mars helicopter’s flight log. Sols are Perseverance sols.
        </p>
      )}

      <Tiles summary={summary} loading={state.status === "loading"} />

      <section aria-live="polite" aria-busy={state.status === "loading"}>
        {state.status === "loading" && (
          <div class="card panel" role="status" aria-label="Loading flights">
            <span class="visually-hidden">Loading flights…</span>
            {Array.from({ length: 8 }, (_, i) => (
              <div
                key={i}
                class="skeleton"
                style={{ height: 34, marginBottom: 10 }}
                aria-hidden="true"
              />
            ))}
          </div>
        )}
        {state.status === "error" && (
          <ErrorAlert error={state.error} onRetry={retry} title="Flights could not be loaded" />
        )}
        {items && items.length === 0 && (
          <EmptyState
            title="No flights yet"
            actions={
              <Link class="btn btn-sm" href={`/rovers/${rover.id}`}>
                Browse Ingenuity photos
              </Link>
            }
          >
            The flight log has not been imported for Ingenuity, so there is nothing to list. Photos
            may still be available.
          </EmptyState>
        )}
        {items && items.length > 0 && (
          <>
            <div class="toolbar">
              <div class="field">
                <label for="sort">Sort by</label>
                <select
                  id="sort"
                  value={selectKnown ? selectValue : ""}
                  onChange={(e) => {
                    setSort(e.currentTarget.value);
                  }}
                >
                  {!selectKnown && (
                    <option value="" disabled>
                      Column heading
                    </option>
                  )}
                  {SORT_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </div>
              <span class="hint only-wide">
                Click a column heading to sort; click again to reverse.
              </span>
            </div>
            <FlightTable
              items={items}
              sortKey={key}
              dir={dir}
              target={target}
              onSort={sortBy}
              roverId={rover.id}
            />
            <p class="xsmall muted" style={{ marginTop: "var(--s3)" }}>
              Distance is horizontal, in metres per flight. A dash after the arrow means the flight
              log does not name a landing site. Highlighted rows hold a record (highest, longest,
              fastest). Dates are Pacific time as in the flight log; the sol is the reliable key.
              Photos counts all Ingenuity photos of that sol, not only those from the flight.
            </p>
          </>
        )}
        <p class="api-line">
          API: <code>GET /api/v2/rovers/{rover.id}/flights</code>
        </p>
      </section>
    </>
  );
}

function Tiles({ summary, loading }: { summary: ReturnType<typeof summarize>; loading: boolean }) {
  if (loading) {
    return (
      <section class="tiles" aria-hidden="true">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} class="card tile">
            <div class="skeleton" style={{ height: 14, width: "50%" }} />
            <div class="skeleton" style={{ height: 30, marginTop: 8, width: "70%" }} />
          </div>
        ))}
      </section>
    );
  }
  if (!summary) return null;
  const t = hms(summary.airSeconds);
  return (
    <section class="tiles" aria-label="Flight summary">
      <div class="card tile">
        <div class="k">Flights</div>
        <div class="v">{summary.count}</div>
        <div class="s">
          sol {summary.firstSol} – {summary.lastSol}
        </div>
      </div>
      <div class="card tile">
        <div class="k">Total air time</div>
        <div class="v">
          {t.h}
          <small> h </small>
          {t.m}
          <small> min </small>
          {t.s}
          <small> s</small>
        </div>
        <div class="s">{fmt(Math.round(summary.airSeconds))} s in the air</div>
      </div>
      <div class="card tile">
        <div class="k">Total distance</div>
        <div class="v">
          {(summary.distanceM / 1000).toFixed(2)}
          <small> km</small>
        </div>
        <div class="s">sum of horizontal flight distances</div>
      </div>
      <div class="card tile">
        <div class="k">Max altitude</div>
        <div class="v">
          {summary.top.max_altitude_m}
          <small> m</small>
        </div>
        <div class="s">
          flight {summary.top.flight}, sol {summary.top.sol}
        </div>
      </div>
    </section>
  );
}

function FlightTable({
  items,
  sortKey,
  dir,
  target,
  onSort,
  roverId,
}: {
  items: Flight[];
  sortKey: SortKey;
  dir: SortDir;
  target: number | null;
  onSort: (k: SortKey) => void;
  roverId: string;
}) {
  const rows = sortFlights(items, sortKey, dir);
  const records = recordsOf(items);
  return (
    <div class="card flights-card" role="region" aria-label="Ingenuity flights" tabIndex={0}>
      <table class="flights" role="table">
        <caption class="visually-hidden">Ingenuity flights</caption>
        <thead role="rowgroup">
          <tr role="row">
            {COLUMNS.map((c) => {
              const sorted =
                c.key === sortKey ? (dir === "asc" ? "ascending" : "descending") : undefined;
              return (
                <th
                  key={c.key}
                  scope="col"
                  role="columnheader"
                  class={[c.num ? "num" : "", c.key === "route" ? "plain" : ""]
                    .filter(Boolean)
                    .join(" ")}
                  aria-sort={sorted}
                >
                  {c.key === "route" ? (
                    c.label
                  ) : (
                    <button type="button" onClick={() => onSort(c.key as SortKey)}>
                      {c.label}
                    </button>
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody role="rowgroup">
          {rows.map((f) => {
            const tags = records.get(f.flight) ?? [];
            const cls = [tags.length ? "rec" : "", target === f.flight ? "target" : ""]
              .filter(Boolean)
              .join(" ");
            return (
              <tr key={f.flight} id={`flight-${f.flight}`} class={cls || undefined} role="row">
                <th scope="row" role="rowheader" data-label="Flight">
                  Flight {f.flight}
                  {tags.map((t) => (
                    <span key={t} class="badge no-dot nrec">
                      {t}
                    </span>
                  ))}
                </th>
                <td class="num" role="cell" data-label="Sol">
                  {f.sol}
                </td>
                <td role="cell" data-label="Date (PT)">
                  <time dateTime={f.date}>{f.date}</time>
                </td>
                <td class="num" role="cell" data-label="Distance">
                  {fmt(f.distance_m)} m
                </td>
                <td class="num" role="cell" data-label="Max altitude">
                  {f.max_altitude_m} m
                </td>
                <td class="num" role="cell" data-label="Max speed">
                  {f.max_groundspeed_mps.toFixed(1)} m/s
                </td>
                <td class="num" role="cell" data-label="Duration">
                  <time dateTime={`PT${f.duration_s}S`}>{mmss(f.duration_s)}</time>
                  <span class="visually-hidden"> ({f.duration_s} seconds)</span>
                </td>
                <td class="route" role="cell" data-label="From → to">
                  {f.from}
                  <span class="arrow" aria-hidden="true">
                    →
                  </span>
                  <span class="visually-hidden"> to </span>
                  {f.to ?? (
                    <span class="na" title="Landing site not named in the flight log">
                      —
                    </span>
                  )}
                </td>
                <td class="num photos" role="cell" data-label="Photos">
                  <Link
                    href={`/rovers/${roverId}?sol=${f.sol}`}
                    aria-label={`${fmt(f.total_photos)} photos of sol ${f.sol} (flight ${f.flight})`}
                  >
                    {fmt(f.total_photos)}
                  </Link>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
