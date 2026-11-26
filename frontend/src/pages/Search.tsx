import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { ApiError, requestLine, searchPhotos } from "../api/client";
import { describeError, periodInvalidIds } from "../api/errors";
import type { PhotoPage, Rover } from "../api/types";
import { BusyAlert } from "../components/Busy";
import {
  CameraPicker,
  RoverChips,
  Segmented,
  toggleRoverDraft,
  useCameraLists,
  WhenFields,
} from "../components/FilterParts";
import { Pager } from "../components/Pager";
import { PhotoTile } from "../components/PhotoTile";
import { ErrorAlert, GridSkeleton, PageSkeleton } from "../components/States";
import { fmt, visibleRovers } from "../lib/format";
import { useAsync, useMedia, useRovers, useTitle } from "../lib/hooks";
import {
  activeCount,
  defaultFilters,
  emptyReasons,
  isHeavy,
  MAX_BROWSABLE,
  MAX_CAMERAS,
  needsSolHint,
  PER_PAGE_CHOICES,
  pageLimit,
  parseSearch,
  SORT_CHOICES,
  summaryText,
  toQuery,
  toSearchString,
  validateFilters,
  withoutHeavyFilters,
  wrapsMidnight,
  type FieldProblem,
  type SearchFilters,
  type TimeMode,
} from "../lib/search";
import { navigate, useLocation } from "../router";

export function Search() {
  const [rovers, reload] = useRovers();
  useTitle("Search photos — Sol Index");
  if (rovers.status === "loading") return <PageSkeleton label="Loading rovers…" />;
  if (rovers.status === "error") return <ErrorAlert error={rovers.error} onRetry={reload} />;
  return <SearchPage rovers={visibleRovers(rovers.data)} />;
}

const TIME_OPTIONS: { value: TimeMode; label: string }[] = [
  { value: "any", label: "Any time" },
  { value: "golden", label: "Golden hour" },
  { value: "custom", label: "Custom window" },
];

const SAMPLE_OPTIONS = [
  { value: "", label: "All" },
  { value: "full", label: "Full" },
  { value: "subframe", label: "Subframe" },
  { value: "downsampled", label: "Downsampled" },
];

/** The address is the applied search; the form holds a draft that becomes the address on "Search". */
function SearchPage({ rovers }: { rovers: Rover[] }) {
  const { pathname, search } = useLocation();
  const allIds = useMemo(() => rovers.map((r) => r.id), [rovers]);
  const idsKey = allIds.join(",");
  const nameOf = (id: string) => rovers.find((r) => r.id === id)?.name ?? id;

  // Only rovers the API lists count; a link with other names searches all rovers.
  const applied = useMemo<SearchFilters>(() => {
    const f = parseSearch(search);
    return { ...f, rovers: f.rovers.filter((r) => allIds.includes(r)) };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, idsKey]);
  const explicit = (f: SearchFilters): SearchFilters => ({
    ...f,
    rovers: f.rovers.length ? f.rovers : allIds,
  });

  const [draft, setDraft] = useState<SearchFilters>(() => explicit(applied));
  const [problems, setProblems] = useState<FieldProblem[]>([]);
  const [seen, setSeen] = useState(search);
  if (seen !== search) {
    setSeen(search);
    setDraft(explicit(applied));
    setProblems([]);
  }
  const set = (patch: Partial<SearchFilters>) => setDraft((d) => ({ ...d, ...patch }));

  // The filter panel folds into a button on phones.
  const wide = useMedia("(min-width: 760px)");
  const [open, setOpen] = useState(wide);
  const formRef = useRef<HTMLFormElement>(null);

  const [pickerOpen, setPickerOpen] = useState(true);
  const chosenRovers = rovers.filter((r) => draft.rovers.includes(r.id));
  const toggleRover = (id: string) => setDraft(toggleRoverDraft(draft, id, cams.lists, setDraft));
  const toggleCamera = (code: string) =>
    setDraft((d) =>
      d.cameras.includes(code)
        ? { ...d, cameras: d.cameras.filter((c) => c !== code) }
        : d.cameras.length >= MAX_CAMERAS
          ? d
          : { ...d, cameras: [...d.cameras, code] },
    );

  const problem = (key: FieldProblem["key"]) => problems.find((p) => p.key === key);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const query = useMemo(() => toQuery(applied, allIds), [applied, idsKey]);
  const [result, retry] = useAsync((signal) => searchPhotos(query, signal), [search, idsKey]);
  const invalidIds = [
    ...problems.flatMap((p) => p.ids),
    ...periodInvalidIds("search", result.status === "error" ? result.error : null),
  ];
  // Camera lists are fetched after the search itself, so they never starve it of a request slot.
  const cams = useCameraLists(draft.rovers, pickerOpen && result.status !== "loading");

  const onSubmit = (e: Event) => {
    e.preventDefault();
    const found = validateFilters(draft);
    setProblems(found);
    if (found.length) {
      setOpen(true);
      requestAnimationFrame(() => document.getElementById(found[0]!.ids[0]!)?.focus());
      return;
    }
    const url = `/search${toSearchString({ ...draft, page: 1 }, allIds)}`;
    if (!wide) setOpen(false);
    if (url === `${pathname}${search}`) retry();
    else navigate(url);
  };

  const reset = () => {
    setProblems([]);
    if (`${pathname}${search}` === "/search") {
      setDraft(explicit(defaultFilters()));
      retry();
    } else navigate("/search");
  };

  const resultsRef = useRef<HTMLElement>(null);
  const firstPage = useRef(true);
  useEffect(() => {
    if (firstPage.current) {
      firstPage.current = false;
      return;
    }
    resultsRef.current?.scrollIntoView?.({ block: "start" });
  }, [applied.page]);

  const hrefForPage = (p: number) => `/search${toSearchString({ ...applied, page: p }, allIds)}`;
  const heavy = isHeavy(draft);
  const count = activeCount(draft, rovers.length);
  const wraps = wrapsMidnight(draft);
  const hasCuriosity = chosenRovers.some((r) => r.id === "curiosity");
  const appliedRovers = rovers.filter((r) =>
    applied.rovers.length ? applied.rovers.includes(r.id) : true,
  );
  const reasons = emptyReasons(
    applied,
    appliedRovers,
    appliedRovers.some((r) => r.id === "curiosity"),
  );
  const sizeFilter = draft.minWidth !== "" || draft.minHeight !== "";

  return (
    <>
      <div class="page-head">
        <h1>Search photos</h1>
        <span class="small muted">
          {applied.rovers.length === 0 || applied.rovers.length >= rovers.length
            ? "Across all rovers"
            : applied.rovers.map(nameOf).join(" + ")}
        </span>
      </div>
      <p class="lede muted">
        Find photos from several rovers at once: by sol or date, camera, time of day on Mars and
        frame size. For one rover and one sol, the rover photo browser is quicker.
      </p>

      <form
        ref={formRef}
        class="card filters-card"
        noValidate
        aria-label="Photo search filters"
        data-testid="search-form"
        onSubmit={onSubmit}
      >
        <div class="filters-bar">
          <button
            class="btn btn-sm filters-toggle"
            type="button"
            aria-expanded={open}
            aria-controls="search-filters-panel"
            onClick={() => setOpen(!open)}
          >
            <span>{open ? "Hide filters" : "Filters"}</span>
            {count > 0 && <span class="badge badge-warn no-dot">{count} active</span>}
          </button>
          <div class="summary-text">{summaryText(draft, nameOf, rovers.length)}</div>
          <button class="btn btn-sm" type="button" onClick={reset}>
            Reset
          </button>
        </div>

        <div class="filters-panel" id="search-filters-panel" data-collapsed={String(!open)}>
          <fieldset class="fieldset">
            <legend>Rovers</legend>
            <RoverChips rovers={rovers} selected={draft.rovers} onToggle={toggleRover} />
            <span class="hint">At least one. Ingenuity uses Perseverance’s sols.</span>
          </fieldset>

          <fieldset class="fieldset">
            <legend>When</legend>
            <WhenFields
              idPrefix="search"
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
              invalid={invalidIds}
              error={problem("when")?.text}
              hint={
                draft.when === "sol"
                  ? "Sols are counted separately for each rover: sol 1000 is a different day for Curiosity and Perseverance. Leave both empty for all sols; the same value in both means one sol."
                  : "Dates are the day the photo was taken (UTC), inclusive. Either end may be left empty."
              }
            />
          </fieldset>

          <fieldset class="fieldset wide">
            <legend>Cameras</legend>
            <CameraPicker
              idPrefix="search"
              rovers={chosenRovers}
              selected={draft.cameras}
              max={MAX_CAMERAS}
              open={pickerOpen}
              onOpen={setPickerOpen}
              onToggle={toggleCamera}
              onClear={() => set({ cameras: [] })}
              lists={cams.lists}
              failed={cams.failed}
              onRetry={cams.retry}
            />
            <span class="hint">
              Up to {MAX_CAMERAS}. A rover without any of the chosen cameras adds no photos.
            </span>
            {needsSolHint(draft) && (
              <p class="note warn" data-testid="search-sol-hint">
                Tip: pick a sol (or a date) as well. A camera without a sol searches every photo of
                the rover, which is slow and can be refused when the server is busy.
              </p>
            )}
          </fieldset>

          <fieldset class="fieldset">
            <legend>Time of day on Mars</legend>
            <Segmented
              name="search-time"
              label="Local time on Mars"
              value={draft.time}
              options={TIME_OPTIONS}
              onChange={(time) =>
                set(time === "custom" ? { time } : { time, timeMin: "", timeMax: "" })
              }
            />
            <div class="time-opts">
              {draft.time === "golden" && (
                <p class="note">
                  Golden hour = 05:30–07:00 and 17:00–18:30 local solar time. A simplification: real
                  sunrise and sunset move by tens of minutes with the season.
                </p>
              )}
              {draft.time === "custom" && (
                <div class="inline-fields">
                  <div class="field">
                    <label for="search-time-min">From (local)</label>
                    <input
                      id="search-time-min"
                      type="time"
                      value={draft.timeMin}
                      aria-invalid={invalidIds.includes("search-time-min") ? "true" : undefined}
                      aria-describedby={problem("time") ? "search-err-time" : undefined}
                      onInput={(e) => set({ timeMin: e.currentTarget.value })}
                    />
                  </div>
                  <div class="field">
                    <label for="search-time-max">Until (local)</label>
                    <input
                      id="search-time-max"
                      type="time"
                      value={draft.timeMax}
                      aria-invalid={invalidIds.includes("search-time-max") ? "true" : undefined}
                      aria-describedby={problem("time") ? "search-err-time" : undefined}
                      onInput={(e) => set({ timeMax: e.currentTarget.value })}
                    />
                  </div>
                </div>
              )}
              {wraps && (
                <p class="note">
                  {draft.timeMin} is after {draft.timeMax}, so the window runs through midnight:
                  from {draft.timeMin} until {draft.timeMax} the next morning.
                </p>
              )}
              {problem("time") && (
                <div class="field-err" id="search-err-time" role="alert">
                  {problem("time")!.text}
                </div>
              )}
              {draft.time !== "any" && (
                <p class="note warn">
                  Photos without a local time are left out: about half of Curiosity’s, and
                  Perseverance photos loaded before 20 Nov 2026. Scale: mean solar time for
                  Curiosity, Perseverance and Ingenuity; the same clock reading is used as is for
                  every chosen rover.
                </p>
              )}
            </div>
          </fieldset>

          <fieldset class="fieldset">
            <legend>Minimum frame size (px)</legend>
            <div class="inline-fields">
              <div class="field">
                <label for="search-min-width">Width at least</label>
                <input
                  id="search-min-width"
                  type="number"
                  min={1}
                  max={32767}
                  step={1}
                  inputMode="numeric"
                  placeholder="any"
                  value={draft.minWidth}
                  aria-invalid={invalidIds.includes("search-min-width") ? "true" : undefined}
                  onInput={(e) => set({ minWidth: e.currentTarget.value })}
                />
              </div>
              <div class="field">
                <label for="search-min-height">Height at least</label>
                <input
                  id="search-min-height"
                  type="number"
                  min={1}
                  max={32767}
                  step={1}
                  inputMode="numeric"
                  placeholder="any"
                  value={draft.minHeight}
                  aria-invalid={invalidIds.includes("search-min-height") ? "true" : undefined}
                  onInput={(e) => set({ minHeight: e.currentTarget.value })}
                />
              </div>
            </div>
            {problem("size") && (
              <div class="field-err" role="alert">
                {problem("size")!.text}
              </div>
            )}
            {sizeFilter && hasCuriosity && (
              <p class="note warn">
                Curiosity photos have no known frame size, so they never match a size filter.
              </p>
            )}
          </fieldset>

          <fieldset class="fieldset">
            <legend>Order and paging</legend>
            <div class="inline-fields">
              <div class="field">
                <label for="search-sort">Sort</label>
                <select
                  id="search-sort"
                  value={draft.sort}
                  onChange={(e) => set({ sort: e.currentTarget.value })}
                >
                  {SORT_CHOICES.map((s) => (
                    <option key={s.value} value={s.value}>
                      {s.label}
                    </option>
                  ))}
                </select>
              </div>
              <div class="field">
                <label for="search-sample">Frame type</label>
                <select
                  id="search-sample"
                  value={draft.sampleType}
                  onChange={(e) => set({ sampleType: e.currentTarget.value })}
                >
                  {SAMPLE_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </div>
              <div class="field">
                <label for="search-per-page">Per page</label>
                <select
                  id="search-per-page"
                  value={draft.perPage}
                  onChange={(e) => set({ perPage: Number(e.currentTarget.value) })}
                >
                  {[...new Set([...PER_PAGE_CHOICES, draft.perPage])]
                    .sort((a, b) => a - b)
                    .map((n) => (
                      <option key={n} value={n}>
                        {n}
                      </option>
                    ))}
                </select>
              </div>
            </div>
            {draft.sort.endsWith("sol") && draft.rovers.length > 1 && (
              <p class="note">
                “Latest sol first” mixes eras when several rovers are chosen: Curiosity’s sol 5066
                outranks Perseverance’s sol 2031. Use “Newest on Mars” for a feed of what arrived
                last.
              </p>
            )}
          </fieldset>

          <div class="wide actions">
            <button class="btn btn-primary" type="submit" data-testid="search-submit">
              Search
            </button>
            {heavy && <span class="badge badge-warn">May be slow</span>}
            {heavy && (
              <span class="hint">
                This kind of search covers a lot of photos. If the server is busy it will ask you to
                retry; a single sol makes it fast.
              </span>
            )}
          </div>
        </div>
      </form>

      <section
        ref={resultsRef}
        id="results"
        aria-labelledby="search-result-title"
        aria-busy={result.status === "loading"}
        data-testid="search-results"
      >
        <div class="result-head">
          <h2 id="search-result-title">Results</h2>
          <span class="muted small" role="status">
            {result.status === "loading" && "Searching…"}
            {result.status === "ok" && summary(result.data)}
          </span>
        </div>

        {result.status === "loading" && <GridSkeleton count={Math.min(applied.perPage, 12)} />}
        {result.status === "error" && (
          <Failure
            error={result.error}
            retry={retry}
            applied={applied}
            allIds={allIds}
            onEdit={() => {
              setOpen(true);
              formRef.current?.scrollIntoView?.({ block: "start" });
            }}
          />
        )}
        {result.status === "ok" && (
          <Found data={result.data} hrefForPage={hrefForPage} reasons={reasons} onReset={reset} />
        )}
      </section>
      <p class="api-line">
        API: <code>{requestLine("/photos", query as Record<string, string | number>)}</code>
      </p>
    </>
  );
}

function summary(data: PhotoPage): string {
  if (data.total === 0) return "0 photos";
  const { pages } = pageLimit(data.total, data.per_page);
  return `${fmt(data.total)} photos · page ${fmt(data.page)} of ${fmt(pages)}`;
}

function Found({
  data,
  hrefForPage,
  reasons,
  onReset,
}: {
  data: PhotoPage;
  hrefForPage: (p: number) => string;
  reasons: string[];
  onReset: () => void;
}) {
  if (data.total === 0) {
    return (
      <div class="card empty" data-testid="search-empty">
        <svg width="48" height="48" viewBox="0 0 48 48" aria-hidden="true">
          <rect
            x="6"
            y="10"
            width="36"
            height="28"
            rx="4"
            fill="none"
            stroke="currentColor"
            stroke-width="2.5"
          />
          <circle cx="17" cy="21" r="3.5" fill="none" stroke="currentColor" stroke-width="2.5" />
          <path
            d="M8 35l11-9 7 6 6-5 9 8"
            fill="none"
            stroke="currentColor"
            stroke-width="2.5"
            stroke-linejoin="round"
          />
        </svg>
        <h3 style={{ marginTop: 12 }}>No photos match this search</h3>
        <p class="muted small" style={{ marginBottom: 8 }}>
          Check these, from most to least likely:
        </p>
        <ul class="reasons">
          {reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
        <div class="row">
          <button class="btn btn-sm" type="button" onClick={onReset}>
            Reset filters
          </button>
        </div>
      </div>
    );
  }
  const { pages, capped } = pageLimit(data.total, data.per_page);
  if (data.items.length === 0) {
    return (
      <div class="card empty">
        <h3>This page is empty</h3>
        <p class="muted small">There are {fmt(data.total)} photos, but not this many pages.</p>
        <div class="row">
          <a class="btn btn-sm" href={hrefForPage(1)}>
            Go to page 1
          </a>
        </div>
      </div>
    );
  }
  return (
    <>
      {capped && (
        <div
          class="note warn"
          style={{ marginBottom: "var(--s3)" }}
          data-testid="search-limit-note"
        >
          Only the first {fmt(MAX_BROWSABLE)} photos can be browsed ({fmt(pages)} pages at{" "}
          {data.per_page} per page). Narrow the search to reach the rest, for example a shorter
          range of sols.
        </div>
      )}
      <ul class="photo-grid">
        {data.items.map((p) => (
          <PhotoTile key={p.id} photo={p} showRover />
        ))}
      </ul>
      <Pager page={data.page} pages={pages} hrefFor={hrefForPage} />
    </>
  );
}

/** `503 service_busy` gets the countdown block; every other failure the shared error alert. */
function Failure({
  error,
  retry,
  applied,
  allIds,
  onEdit,
}: {
  error: unknown;
  retry: () => void;
  applied: SearchFilters;
  allIds: string[];
  onEdit: () => void;
}) {
  const busy =
    error instanceof ApiError &&
    error.status === 503 &&
    (error.code === "service_busy" || error.retryAfter !== null);
  if (busy) {
    const lighter = applied.time !== "any" || applied.minWidth !== "" || applied.minHeight !== "";
    return (
      <BusyAlert
        error={error}
        onRetry={retry}
        tips={[
          "pick a single sol, or a short range of sols;",
          "remove the time-of-day, frame-size or frame-type filter;",
          "use fewer cameras.",
        ]}
        actions={
          lighter ? (
            <button
              class="btn btn-sm"
              type="button"
              onClick={() =>
                navigate(`/search${toSearchString(withoutHeavyFilters(applied), allIds)}`)
              }
            >
              Drop time-of-day and size filters
            </button>
          ) : null
        }
      />
    );
  }
  const view = describeError(error);
  const pageIssue = view.fields.some((f) => f.label === "Page");
  return (
    <ErrorAlert
      error={error}
      onRetry={retry}
      title="We couldn’t run this search"
      actions={
        <>
          {pageIssue && (
            <button
              class="btn btn-sm"
              type="button"
              onClick={() => navigate(`/search${toSearchString({ ...applied, page: 1 }, allIds)}`)}
            >
              Back to page 1
            </button>
          )}
          <button class="btn btn-sm" type="button" onClick={onEdit}>
            Edit filters
          </button>
        </>
      }
    />
  );
}
