import { Fragment } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { api, photosRequestLine } from "../api/client";
import type { Camera, ManifestSol, PhotoQuery, Rover } from "../api/types";
import { FILTER_SAMPLE_TYPES } from "../api/types";
import { Pager } from "../components/Pager";
import { PhotoTile } from "../components/PhotoTile";
import { RoverTabs } from "../components/RoverTabs";
import { EmptyState, ErrorAlert, GridSkeleton, PageSkeleton } from "../components/States";
import { fmt, short, statusLabel } from "../lib/format";
import { useAsync, useRovers, useTitle } from "../lib/hooks";
import { Link, navigate, qs, useQuery } from "../router";

const PER_PAGE_CHOICES = [24, 48, 96];
const SOL_WINDOW = 60;
const MAX_SOL = 2_147_483_647;

interface Filters {
  sol?: string;
  earth_date?: string;
  camera?: string;
  sample_type?: string;
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
      {rover.max_sol === null ? (
        <EmptyState title="No photos yet">This rover has no imported photos.</EmptyState>
      ) : (
        <Browser rover={rover} maxSol={rover.max_sol} />
      )}
    </>
  );
}

function PageHead({ rover }: { rover: Rover }) {
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
    </div>
  );
}

function Browser({ rover, maxSol }: { rover: Rover; maxSol: number }) {
  const f = parseFilters(useQuery());
  const byDate = f.earth_date !== undefined;
  // Without sol/earth_date the browser shows the latest sol (decision of the owner).
  const effective: Filters = {
    ...f,
    sol: byDate ? f.sol : (f.sol ?? String(maxSol)),
  };
  const perPage = f.per_page ?? 24;
  const page = f.page ?? 1;
  const query: PhotoQuery = {
    sol: effective.sol !== undefined && effective.sol !== "" ? Number(effective.sol) : undefined,
    earth_date: f.earth_date,
    camera: f.camera,
    sample_type: f.sample_type,
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
    [rover.id, rawSol, f.earth_date, f.camera, f.sample_type, page, perPage],
  );

  const [cameras] = useAsync(() => api.cameras(rover.id).catch(() => [] as Camera[]), [rover.id]);

  const anchorSol =
    rawSol !== undefined && /^\d+$/.test(rawSol)
      ? Number(rawSol)
      : photos.status === "ok"
        ? photos.data.items[0]?.sol
        : undefined;
  const [window_] = useAsync(
    () =>
      anchorSol === undefined
        ? Promise.resolve(null)
        : api
            .manifest(rover.id, {
              from_sol: Math.max(0, anchorSol - SOL_WINDOW),
              to_sol: Math.min(MAX_SOL, anchorSol + SOL_WINDOW),
            })
            .catch(() => null),
    [rover.id, anchorSol],
  );
  const nearby: ManifestSol[] = window_.status === "ok" && window_.data ? window_.data.sols : [];
  const here = nearby.find((s) => s.sol === anchorSol);
  const prevSol = nearby.filter((s) => anchorSol !== undefined && s.sol < anchorSol)[0]; // sorted sol DESC
  const nextSol = [...nearby].reverse().find((s) => anchorSol !== undefined && s.sol > anchorSol);

  const go = (next: Filters, opts?: { replace?: boolean }) =>
    navigate(photosUrl(rover.id, next), opts);
  // Changing a filter returns to page 1.
  const change = (patch: Partial<Filters>) => go({ ...f, sol: effective.sol, ...patch, page: 1 });

  const resultsRef = useRef<HTMLElement>(null);
  const firstPage = useRef(true);
  useEffect(() => {
    if (firstPage.current) {
      firstPage.current = false;
      return;
    }
    resultsRef.current?.scrollIntoView?.({ block: "start" });
  }, [page]);

  const heading = byDate ? (f.earth_date as string) : `Sol ${rawSol}`;
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
        earthDate={f.earth_date ?? here?.earth_date ?? rover.max_date ?? ""}
        hereEarthDate={here?.earth_date}
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
                earth_date: here?.earth_date ?? rover.max_date ?? undefined,
                page: 1,
              })
            : go({ ...f, earth_date: undefined, sol: String(anchorSol ?? maxSol), page: 1 })
        }
        onLatest={() => go({ ...f, earth_date: undefined, sol: undefined, page: 1 })}
        onCamera={(c) => change({ camera: c })}
        onSample={(s) => change({ sample_type: s })}
        here={here}
        prevSol={prevSol?.sol}
        nextSol={nextSol?.sol}
      />

      <section
        ref={resultsRef}
        aria-labelledby="result-title"
        aria-busy={photos.status === "loading"}
        id="results"
      >
        <div class="result-head">
          <h2 id="result-title">{heading}</h2>
          <span class="muted small" id="result-sub" aria-live="polite">
            {photos.status === "loading" && "Loading…"}
            {photos.status === "ok" && resultSummary(photos.data, here?.earth_date, byDate)}
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
            filtered={!!(f.camera || f.sample_type)}
            here={here}
            prevSol={prevSol?.sol}
            nextSol={nextSol?.sol}
            fromUrl={fromUrl}
            hrefForPage={hrefForPage}
            onReset={() => change({ camera: undefined, sample_type: undefined })}
          />
        )}
        <p class="api-line">
          API: <code>{photosRequestLine(rover.id, query)}</code>
        </p>
      </section>
    </>
  );
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
  data: { items: import("../api/types").Photo[]; page: number; per_page: number; total: number };
  rover: Rover;
  heading: string;
  sol: number | undefined;
  byDate: boolean;
  filtered: boolean;
  here: ManifestSol | undefined;
  prevSol: number | undefined;
  nextSol: number | undefined;
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
        {p.here ? `Sol ${p.here.sol} has ${fmt(p.here.total_photos)} photos in total. ` : ""}Try
        another camera or sample type.
      </EmptyState>
    );
  }
  if (data.total === 0) {
    const sol = p.sol;
    return (
      <EmptyState
        title={p.byDate ? `No photos on ${p.heading}` : `No photos on ${p.heading.toLowerCase()}`}
        actions={
          <>
            {sol !== undefined && p.prevSol !== undefined && (
              <Link class="btn btn-sm" href={`/rovers/${p.rover.id}?sol=${p.prevSol}`}>
                ‹ Sol {p.prevSol}
              </Link>
            )}
            {sol !== undefined && p.nextSol !== undefined && (
              <Link class="btn btn-sm" href={`/rovers/${p.rover.id}?sol=${p.nextSol}`}>
                Sol {p.nextSol} ›
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
  earthDate: string;
  hereEarthDate: string | undefined;
  anchorSol: number | undefined;
  camera: string | undefined;
  sampleType: string | undefined;
  cameras: Camera[];
  here: ManifestSol | undefined;
  prevSol: number | undefined;
  nextSol: number | undefined;
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

  const onSolSet = new Set(p.here?.cameras ?? []);
  const known = p.here !== undefined;
  const active = p.cameras.filter((c) => !known || onSolSet.has(c.code));
  const other = p.cameras.filter((c) => known && !onSolSet.has(c.code));
  const cameraUnlisted = p.camera && !p.cameras.some((c) => c.code === p.camera);

  const chip = (c: Camera, dim: boolean) => (
    <button
      key={c.code}
      type="button"
      class={`chip${dim ? " dim" : ""}`}
      aria-pressed={p.camera === c.code}
      title={`${c.name} — ${fmt(c.total_photos)} photos across all sols`}
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
              aria-label="Previous sol with photos"
              aria-disabled={p.byDate || p.prevSol === undefined}
              tabIndex={p.byDate || p.prevSol === undefined ? -1 : undefined}
              href={p.prevSol === undefined ? "#" : `/rovers/${p.rover.id}?sol=${p.prevSol}`}
              onClick={(e) => (p.byDate || p.prevSol === undefined) && e.preventDefault()}
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
              aria-label="Next sol with photos"
              aria-disabled={p.byDate || p.nextSol === undefined}
              tabIndex={p.byDate || p.nextSol === undefined ? -1 : undefined}
              href={p.nextSol === undefined ? "#" : `/rovers/${p.rover.id}?sol=${p.nextSol}`}
              onClick={(e) => (p.byDate || p.nextSol === undefined) && e.preventDefault()}
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

      <div class="filter-block" role="group" aria-labelledby="cam-label">
        <span class="label" id="cam-label">
          Camera{" "}
          <span class="muted" style={{ textTransform: "none", letterSpacing: 0, fontWeight: 400 }}>
            · counts are across all sols
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
            {p.here && <span class="count">{fmt(p.here.total_photos)} on this sol</span>}
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
