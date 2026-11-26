import { Fragment } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { api, photosRequestLine } from "../api/client";
import type { Camera, NearestSols, Photo, PhotoQuery, Rover } from "../api/types";
import { FILTER_SAMPLE_TYPES } from "../api/types";
import { Pager } from "../components/Pager";
import { PhotoTile } from "../components/PhotoTile";
import { RoverTabs } from "../components/RoverTabs";
import { Rover3D } from "../components/Rover3D";
import { Subscribe } from "../components/Subscribe";
import { EmptyState, ErrorAlert, GridSkeleton, PageSkeleton } from "../components/States";
import { fmt, short, statusLabel } from "../lib/format";
import { stopLabel, type StopFilter } from "../lib/maplinks";
import { useAsync, useRovers, useTitle } from "../lib/hooks";
import { Link, navigate, qs, useQuery, useRestoreScroll } from "../router";

const PER_PAGE_CHOICES = [24, 48, 96];

interface Filters {
  sol?: string;
  earth_date?: string;
  camera?: string;
  sample_type?: string;
  /** Stop filter from the route map; `drive_from` / `drive_to` only count together with `site`. */
  site?: string;
  drive_from?: string;
  drive_to?: string;
  page?: number;
  per_page?: number;
}

function parseFilters(q: URLSearchParams): Filters {
  const num = (k: string) => {
    const v = q.get(k);
    return v !== null && /^\d+$/.test(v) ? Number(v) : undefined;
  };
  return {
    sol: q.get("sol") ?? undefined,
    earth_date: q.get("earth_date") ?? undefined,
    camera: q.get("camera")?.toUpperCase() || undefined,
    sample_type: q.get("sample_type") ?? undefined,
    // Stop filters are passed as-is, even when invalid: the API answers 422 and says why.
    site: q.get("site") ?? undefined,
    drive_from: q.get("drive_from") ?? undefined,
    drive_to: q.get("drive_to") ?? undefined,
    page: num("page"),
    per_page: num("per_page"),
  };
}

export function photosUrl(rover: string, f: Filters): string {
  return `/rovers/${rover}${qs({
    sol: f.sol,
    earth_date: f.earth_date,
    camera: f.camera,
    sample_type: f.sample_type,
    site: f.site,
    drive_from: f.drive_from,
    drive_to: f.drive_to,
    page: f.page && f.page > 1 ? f.page : undefined,
    per_page: f.per_page && f.per_page !== 24 ? f.per_page : undefined,
  })}`;
}

export function Photos({ roverId }: { roverId: string }) {
  const [rovers, reload] = useRovers();
  useTitle(`Photos — ${roverId} — Sol Index`);

  if (rovers.status === "loading") return <PageSkeleton label="Loading rover…" />;
  if (rovers.status === "error") return <ErrorAlert error={rovers.error} onRetry={reload} />;
  const rover = rovers.data.find((r) => r.id === roverId);
  if (!rover) {
    return (
      <EmptyState
        title="Rover not found"
        actions={
          <Link class="btn btn-sm" href="/">
            All rovers
          </Link>
        }
      >
        We don’t know a rover called “{roverId}”.
      </EmptyState>
    );
  }
  return (
    <>
      <RoverTabs rovers={rovers.data} current={rover.id} to={(id) => `/rovers/${id}`} />
      <PageHead rover={rover} />
      <Rover3D roverId={rover.id} />
      {rover.max_sol === null ? (
        <EmptyState title="No photos yet">This rover has no imported photos.</EmptyState>
      ) : (
        <Browser rover={rover} maxSol={rover.max_sol} />
      )}
    </>
  );
}

function PageHead({ rover }: { rover: Rover }) {
  const q = useQuery();
  return (
    <div class="page-head">
      <h1>{rover.name} photos</h1>
      <span class={`badge ${rover.status === "active" ? "badge-active" : "badge-complete"}`}>
        {statusLabel(rover.status)}
      </span>
      {rover.max_sol !== null && (
        <span class="small muted">
          Sols 0 – {rover.max_sol} · {fmt(rover.total_photos)} photos
        </span>
      )}
      {rover.id === "ingenuity" && (
        <Link class="btn btn-sm" href={`/rovers/${rover.id}/flights`}>
          Flights
        </Link>
      )}
      {rover.max_sol !== null && (
        <Subscribe
          rover={rover}
          camera={q.get("camera")?.toUpperCase() || undefined}
          sampleType={q.get("sample_type") || undefined}
        />
      )}
    </div>
  );
}

function Browser({ rover, maxSol }: { rover: Rover; maxSol: number }) {
  const f = parseFilters(useQuery());
  const byDate = f.earth_date !== undefined;
  // `drive_from` / `drive_to` are optional; any stop parameter counts as a stop filter and is sent
  // as-is, so the API explains a bad or incomplete one (422), as it does for `sol`.
  const stopped = f.site !== undefined || f.drive_from !== undefined || f.drive_to !== undefined;
  const stop: StopFilter | null =
    f.site !== undefined ? { site: f.site, drive_from: f.drive_from, drive_to: f.drive_to } : null;
  // Without sol/earth_date the browser shows the latest sol (decision of the owner);
  // a stop is the exception: its photos can span several sols, so all of them are listed.
  const effective: Filters = {
    ...f,
    sol: byDate || stopped ? f.sol : (f.sol ?? String(maxSol)),
  };
  const perPage = f.per_page ?? 24;
  const page = f.page ?? 1;
  const query: PhotoQuery = {
    sol: effective.sol !== undefined && effective.sol !== "" ? Number(effective.sol) : undefined,
    earth_date: f.earth_date,
    camera: f.camera,
    sample_type: f.sample_type,
    site: f.site,
    drive_from: f.drive_from,
    drive_to: f.drive_to,
    page,
    per_page: perPage,
  };
  // A non-numeric sol is sent as-is so the API explains the problem (422), as the contract says.
  const rawSol = effective.sol;
  const apiQuery: PhotoQuery =
    rawSol !== undefined && !/^\d+$/.test(rawSol)
      ? { ...query, sol: rawSol as unknown as number }
      : query;

  const [photos, retry] = useAsync(
    (signal) => api.photos(rover.id, apiQuery, signal),
    [
      rover.id,
      rawSol,
      f.earth_date,
      f.camera,
      f.sample_type,
      f.site,
      f.drive_from,
      f.drive_to,
      page,
      perPage,
    ],
  );

  useRestoreScroll(photos.status === "ok");

  const anchorSol =
    rawSol !== undefined && /^\d+$/.test(rawSol)
      ? Number(rawSol)
      : photos.status === "ok"
        ? photos.data.items[0]?.sol
        : undefined;
  // Camera counts are for the sol being viewed (`/cameras?sol=`). An Earth-date or stop search can
  // span sols, so it shows the mission-wide counts.
  const wide = byDate || rawSol === undefined;
  const [cameras] = useAsync(
    () => api.cameras(rover.id, wide ? undefined : anchorSol).catch(() => [] as Camera[]),
    [rover.id, wide, anchorSol],
  );
  const prevSol = !wide && anchorSol !== undefined && anchorSol > 0 ? anchorSol - 1 : undefined;
  const nextSol =
    !wide && anchorSol !== undefined && anchorSol < maxSol ? anchorSol + 1 : undefined;
  const solDate = photos.status === "ok" ? photos.data.items[0]?.earth_date : undefined;

  const go = (next: Filters, opts?: { replace?: boolean }) =>
    navigate(photosUrl(rover.id, next), opts);
  // Changing a filter returns to page 1.
  const change = (patch: Partial<Filters>) => go({ ...f, sol: effective.sol, ...patch, page: 1 });

  const resultsRef = useRef<HTMLElement>(null);
  // Set by the previous/next sol links: after the move, the focus goes to the results heading.
  const focusTitle = useRef(false);
  useEffect(() => {
    if (!focusTitle.current) return;
    focusTitle.current = false;
    document.getElementById("result-title")?.focus({ preventScroll: true });
  }, [rawSol]);
  const firstPage = useRef(true);
  useEffect(() => {
    if (firstPage.current) {
      firstPage.current = false;
      return;
    }
    resultsRef.current?.scrollIntoView?.({ block: "start" });
  }, [page]);

  const base = byDate ? (f.earth_date as string) : rawSol === undefined ? "" : `Sol ${rawSol}`;
  const heading = stop ? [base, stopLabel(stop)].filter(Boolean).join(" · ") : base;
  const clearStop = () =>
    go({ ...f, site: undefined, drive_from: undefined, drive_to: undefined, page: 1 });
  const fromUrl = photosUrl(rover.id, { ...effective, page, per_page: perPage });
  const hrefForPage = (p: number) =>
    photosUrl(rover.id, { ...f, sol: effective.sol, page: p, per_page: perPage });

  return (
    <>
      <FilterForm
        rover={rover}
        maxSol={maxSol}
        byDate={byDate}
        sol={rawSol ?? ""}
        stop={stop}
        onClearStop={clearStop}
        earthDate={f.earth_date ?? solDate ?? rover.max_date ?? ""}
        perSol={!wide && anchorSol !== undefined}
        anchorSol={anchorSol}
        camera={f.camera}
        sampleType={f.sample_type}
        cameras={cameras.status === "ok" ? cameras.data : []}
        onSol={(s) => go({ ...f, earth_date: undefined, sol: s, page: 1 })}
        onDate={(d) => go({ ...f, sol: undefined, earth_date: d, page: 1 })}
        onMode={(mode) =>
          mode === "date"
            ? go({
                ...f,
                sol: undefined,
                earth_date: solDate ?? rover.max_date ?? undefined,
                page: 1,
              })
            : go({ ...f, earth_date: undefined, sol: String(anchorSol ?? maxSol), page: 1 })
        }
        onLatest={() =>
          go({
            ...f,
            earth_date: undefined,
            sol: undefined,
            site: undefined,
            drive_from: undefined,
            drive_to: undefined,
            page: 1,
          })
        }
        onCamera={(c) => change({ camera: c })}
        onSample={(s) => change({ sample_type: s })}
        prevSol={prevSol}
        nextSol={nextSol}
        solHref={(sol) =>
          photosUrl(rover.id, { ...f, earth_date: undefined, sol: String(sol), page: 1 })
        }
        onStep={(e) => {
          // Only a plain click stays on this page; a new-tab click must not arm the focus move.
          if (e.button === 0 && !e.ctrlKey && !e.metaKey && !e.shiftKey && !e.altKey) {
            focusTitle.current = true;
          }
        }}
      />

      <section
        ref={resultsRef}
        aria-labelledby="result-title"
        aria-busy={photos.status === "loading"}
        id="results"
      >
        <div class="result-head">
          <h2 id="result-title" tabIndex={-1}>
            {heading}
          </h2>
          <span class="muted small" id="result-sub" aria-live="polite">
            {photos.status === "loading" && "Loading…"}
            {photos.status === "ok" && resultSummary(photos.data, solDate, byDate)}
          </span>
          <span class="spacer" />
          <div class="field inline-field">
            <label for="per-page">Per page</label>
            <select
              id="per-page"
              value={perPage}
              onChange={(e) =>
                go({ ...f, sol: effective.sol, per_page: Number(e.currentTarget.value), page: 1 })
              }
            >
              {[...new Set([...PER_PAGE_CHOICES, perPage])]
                .sort((a, b) => a - b)
                .map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
            </select>
          </div>
        </div>

        {photos.status === "loading" && <GridSkeleton count={Math.min(perPage, 12)} />}
        {photos.status === "error" && (
          <ErrorAlert
            error={photos.error}
            onRetry={retry}
            title="We couldn’t run this search"
            actions={
              <Link class="btn btn-sm" href={`/rovers/${rover.id}`}>
                Go to latest sol
              </Link>
            }
          />
        )}
        {photos.status === "ok" && (
          <Results
            data={photos.data}
            rover={rover}
            heading={heading}
            sol={anchorSol}
            byDate={byDate}
            filtered={!!(f.camera || f.sample_type || stopped)}
            fromUrl={fromUrl}
            hrefForPage={hrefForPage}
            onReset={() =>
              change({
                camera: undefined,
                sample_type: undefined,
                site: undefined,
                drive_from: undefined,
                drive_to: undefined,
              })
            }
          />
        )}
        <p class="api-line">
          API: <code>{photosRequestLine(rover.id, query)}</code>
        </p>
      </section>
    </>
  );
}

function stopTitle(s: StopFilter): string {
  const from = s.drive_from ?? 0;
  const base = `Photos with site ${s.site}, drive ${from}`;
  if (s.drive_to === undefined) return `${base} and later drives of the same site`;
  return String(s.drive_to) === String(from) ? base : `${base} to ${s.drive_to}`;
}

function resultSummary(
  data: {
    items: { earth_date: string; sol: number }[];
    page: number;
    per_page: number;
    total: number;
  },
  hereDate: string | undefined,
  byDate: boolean,
): string {
  if (!data.total) return "0 photos";
  const first = data.items[0];
  const from = (data.page - 1) * data.per_page + 1;
  const range = data.items.length ? `${fmt(from)}–${fmt(from + data.items.length - 1)} of ` : "";
  const context = byDate
    ? first
      ? `sol ${first.sol}`
      : ""
    : (hereDate ?? first?.earth_date ?? "");
  return `${context ? `${context} · ` : ""}${range}${fmt(data.total)} photos`;
}

interface ResultsProps {
  data: {
    items: Photo[];
    page: number;
    per_page: number;
    total: number;
    nearest_sols?: NearestSols | null;
  };
  rover: Rover;
  heading: string;
  sol: number | undefined;
  byDate: boolean;
  filtered: boolean;
  fromUrl: string;
  hrefForPage: (p: number) => string;
  onReset: () => void;
}

function Results(p: ResultsProps) {
  const { data } = p;
  const pages = Math.max(1, Math.ceil(data.total / data.per_page));
  if (data.total === 0 && p.filtered) {
    return (
      <EmptyState
        title="No photos match these filters"
        actions={
          <button class="btn btn-sm" type="button" onClick={p.onReset}>
            Reset filters
          </button>
        }
      >
        Try another camera or sample type, or clear the stop filter.
      </EmptyState>
    );
  }
  if (data.total === 0) {
    const near = data.nearest_sols;
    return (
      <EmptyState
        title={
          !p.heading
            ? "No photos found"
            : p.byDate
              ? `No photos on ${p.heading}`
              : `No photos on ${p.heading.toLowerCase()}`
        }
        actions={
          <>
            {near?.before != null && (
              <Link class="btn btn-sm" href={`/rovers/${p.rover.id}?sol=${near.before}`}>
                ‹ Sol {near.before}
              </Link>
            )}
            {near?.after != null && (
              <Link class="btn btn-sm" href={`/rovers/${p.rover.id}?sol=${near.after}`}>
                Sol {near.after} ›
              </Link>
            )}
            <Link class="btn btn-sm" href={`/rovers/${p.rover.id}/timeline`}>
              Open timeline
            </Link>
          </>
        }
      >
        The rover may not have taken or sent images that day, or they haven’t reached the public
        feed yet.
        {near && (near.before != null || near.after != null)
          ? " The nearest sols with photos are linked below."
          : ""}
      </EmptyState>
    );
  }
  if (data.items.length === 0) {
    return (
      <EmptyState
        title="This page is empty"
        actions={
          <Link class="btn btn-sm" href={p.hrefForPage(1)}>
            Go to page 1
          </Link>
        }
      >
        There are {fmt(data.total)} photos, but not this many pages.
      </EmptyState>
    );
  }
  return (
    <>
      <ul class="photo-grid">
        {data.items.map((ph) => (
          <PhotoTile key={ph.id} photo={ph} from={p.fromUrl} />
        ))}
      </ul>
      <Pager page={data.page} pages={pages} hrefFor={p.hrefForPage} />
    </>
  );
}

interface FormProps {
  rover: Rover;
  maxSol: number;
  byDate: boolean;
  sol: string;
  stop: StopFilter | null;
  onClearStop: () => void;
  earthDate: string;
  /** Camera counts are for the viewed sol (true) or the whole mission (false). */
  perSol: boolean;
  anchorSol: number | undefined;
  camera: string | undefined;
  sampleType: string | undefined;
  cameras: Camera[];
  prevSol: number | undefined;
  nextSol: number | undefined;
  solHref: (sol: number) => string;
  onStep: (e: MouseEvent) => void;
  onSol: (s: string) => void;
  onDate: (d: string) => void;
  onMode: (m: "sol" | "date") => void;
  onLatest: () => void;
  onCamera: (c: string | undefined) => void;
  onSample: (s: string | undefined) => void;
}

function FilterForm(p: FormProps) {
  const [solText, setSolText] = useState(p.sol);
  const [dateText, setDateText] = useState(p.earthDate);
  useEffect(() => setSolText(p.sol), [p.sol]);
  useEffect(() => setDateText(p.earthDate), [p.earthDate]);

  const active = p.cameras.filter((c) => !p.perSol || c.total_photos > 0);
  const other = p.cameras.filter((c) => p.perSol && c.total_photos === 0);
  const solTotal = p.cameras.reduce((n, c) => n + c.total_photos, 0);
  const cameraUnlisted = p.camera && !p.cameras.some((c) => c.code === p.camera);

  const chip = (c: Camera, dim: boolean) => (
    <button
      key={c.code}
      type="button"
      class={`chip${dim ? " dim" : ""}`}
      aria-pressed={p.camera === c.code}
      title={`${c.name} — ${fmt(c.total_photos)} photos ${p.perSol ? "on this sol" : "across all sols"}`}
      onClick={() => p.onCamera(p.camera === c.code ? undefined : c.code)}
    >
      <span class="mono">{c.code}</span>
      <span class="count">{short(c.total_photos)}</span>
    </button>
  );

  const submit = (e: Event) => {
    e.preventDefault();
    if (p.byDate) p.onDate(dateText);
    else p.onSol(solText.trim());
  };

  return (
    <form class="card filters" aria-label="Photo filters" onSubmit={submit}>
      <div class="when">
        <div class="field">
          <span class="label" id="mode-label">
            Find by
          </span>
          <div class="segmented" role="radiogroup" aria-labelledby="mode-label">
            <input
              type="radio"
              name="mode"
              id="mode-sol"
              checked={!p.byDate}
              onChange={() => p.byDate && p.onMode("sol")}
            />
            <label for="mode-sol">Sol</label>
            <input
              type="radio"
              name="mode"
              id="mode-date"
              checked={p.byDate}
              onChange={() => !p.byDate && p.onMode("date")}
            />
            <label for="mode-date">Earth date</label>
          </div>
        </div>
        <div class="field">
          <label for="sol">Sol</label>
          <div class="sol-step">
            <Link
              class="btn btn-sm"
              aria-label="Previous sol"
              aria-disabled={p.byDate || p.prevSol === undefined}
              tabIndex={p.byDate || p.prevSol === undefined ? -1 : undefined}
              href={p.prevSol === undefined ? "#" : p.solHref(p.prevSol)}
              onClick={(e) =>
                p.byDate || p.prevSol === undefined ? e.preventDefault() : p.onStep(e)
              }
            >
              ‹
            </Link>
            <input
              id="sol"
              type="number"
              inputMode="numeric"
              min={0}
              max={p.maxSol}
              value={p.byDate ? "" : solText}
              disabled={p.byDate}
              aria-describedby="sol-hint"
              onInput={(e) => setSolText(e.currentTarget.value)}
            />
            <Link
              class="btn btn-sm"
              aria-label="Next sol"
              aria-disabled={p.byDate || p.nextSol === undefined}
              tabIndex={p.byDate || p.nextSol === undefined ? -1 : undefined}
              href={p.nextSol === undefined ? "#" : p.solHref(p.nextSol)}
              onClick={(e) =>
                p.byDate || p.nextSol === undefined ? e.preventDefault() : p.onStep(e)
              }
            >
              ›
            </Link>
          </div>
        </div>
        <div class="field">
          <label for="earth-date">Earth date (UTC)</label>
          <input
            id="earth-date"
            type="date"
            value={p.byDate ? dateText : ""}
            disabled={!p.byDate}
            onInput={(e) => setDateText(e.currentTarget.value)}
          />
        </div>
        <button class="btn" type="submit">
          Go
        </button>
        <button class="btn btn-primary" type="button" onClick={p.onLatest}>
          Latest sol
        </button>
        <span class="hint" id="sol-hint">
          Sol and Earth date can’t be combined — pick one.
        </span>
      </div>

      {p.stop && (
        <div class="filter-block" role="group" aria-labelledby="stop-label">
          <span class="label" id="stop-label">
            Route stop
          </span>
          <div class="chips">
            <button
              type="button"
              class="chip chip-clear"
              aria-pressed="true"
              aria-label={`${stopLabel(p.stop)}: clear this filter`}
              title={stopTitle(p.stop)}
              onClick={p.onClearStop}
            >
              {stopLabel(p.stop)} <span aria-hidden="true">×</span>
            </button>
          </div>
        </div>
      )}

      <div class="filter-block" role="group" aria-labelledby="cam-label">
        <span class="label" id="cam-label">
          Camera{" "}
          <span class="muted" style={{ textTransform: "none", letterSpacing: 0, fontWeight: 400 }}>
            · counts are {p.perSol ? "for this sol" : "across all sols"}
          </span>
        </span>
        <div class="chips">
          <button
            type="button"
            class="chip"
            aria-pressed={!p.camera}
            onClick={() => p.onCamera(undefined)}
          >
            All cameras
            {p.perSol && p.cameras.length > 0 && (
              <span class="count">{fmt(solTotal)} on this sol</span>
            )}
          </button>
          {cameraUnlisted && (
            <button
              type="button"
              class="chip"
              aria-pressed="true"
              onClick={() => p.onCamera(undefined)}
            >
              <span class="mono">{p.camera}</span>
            </button>
          )}
          {active.map((c) => chip(c, false))}
        </div>
        {other.length > 0 && (
          <details class="more">
            <summary>Other cameras ({other.length}, no photos on this sol)</summary>
            <div class="chips">{other.map((c) => chip(c, true))}</div>
          </details>
        )}
      </div>

      <div class="filter-block">
        <span class="label" id="st-label">
          Sample type
        </span>
        <div class="segmented" role="radiogroup" aria-labelledby="st-label">
          {[
            ["", "All"],
            ...FILTER_SAMPLE_TYPES.map((t) => [t, t[0]!.toUpperCase() + t.slice(1)]),
          ].map(([value, label]) => (
            <Fragment key={value}>
              <input
                type="radio"
                name="st"
                id={`st-${value || "all"}`}
                checked={(p.sampleType ?? "") === value}
                onChange={() => p.onSample(value || undefined)}
              />
              <label for={`st-${value || "all"}`}>{label}</label>
            </Fragment>
          ))}
        </div>
      </div>
    </form>
  );
}
