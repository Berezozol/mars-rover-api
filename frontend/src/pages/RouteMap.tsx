import type { JSX } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { api } from "../api/client";
import type { LocationResponse, Rover, Sample, Traverse, Waypoint } from "../api/types";
import { NearView } from "../components/NearView";
import { RoverTabs } from "../components/RoverTabs";
import { SampleBadges, TubeLink } from "../components/SampleParts";
import { EmptyState, ErrorAlert, PageSkeleton } from "../components/States";
import { fmt } from "../lib/format";
import { useAsync, useRovers, useTitle } from "../lib/hooks";
import {
  boundsOf,
  centerOn,
  dotsPath,
  fitView,
  highlightRuns,
  km,
  latLabel,
  lonLabel,
  M_PER_DEG,
  metres,
  minPpu,
  nearestStop,
  niceStep,
  pathData,
  prepare,
  project,
  rangeError,
  scaleBar,
  scaleLabel,
  stopsInRange,
  unproject,
  zoomAt,
  type Prepared,
  type View,
} from "../lib/mapgeo";
import { findStop, mapHref, stopPhotosHref } from "../lib/maplinks";
import {
  groupByStop,
  MATCH_TEXT,
  samplesHref,
  SAMPLES_ROVER,
  solPhotosHref,
  stopKey,
} from "../lib/samples";
import { nearHref, parseNearState, supportsNear, type NearState } from "../lib/near";
import { Link, navigate, qs, useLocation, useQuery, useRestoreScroll } from "../router";

const LIST_PAGE = 8;

const intParam = (v: string | null): number | null =>
  v !== null && /^\d+$/.test(v) ? Number(v) : null;

export function RouteMapPage({ roverId }: { roverId: string }) {
  const [rovers, reload] = useRovers();
  const { search } = useLocation();
  const near = parseNearState(search);
  useTitle(`${near.active ? "Photos near a point" : "Route map"} — ${roverId} — Sol Index`);
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
  const canNear = supportsNear(rover.id);
  return (
    <>
      <RoverTabs rovers={rovers.data} current={rover.id} to={(id) => `/rovers/${id}/map`} />
      <ViewTabs rover={rover} rovers={rovers.data} />
      {canNear && (
        <nav class="mode-switch" aria-label="Route map mode" data-testid="near-mode-switch">
          <Link href={`/rovers/${rover.id}/map`} aria-current={near.active ? undefined : "page"}>
            Browse stops
          </Link>
          <Link href={nearHref(rover.id)} aria-current={near.active ? "page" : undefined}>
            Photos near a point
          </Link>
        </nav>
      )}
      {near.active && !canNear ? (
        <div class="card empty" data-testid="near-unsupported">
          <h1 style={{ fontSize: "var(--fs-lg)" }}>
            Photos near a point is not available for {rover.name}
          </h1>
          <p class="muted small">
            Searching by place works for Curiosity and Perseverance only: they are the rovers whose
            stops are published. {rover.name}’s photos are still available.
          </p>
          <div class="row">
            <Link class="btn btn-sm" href={`/rovers/${rover.id}`}>
              Browse {rover.name} photos
            </Link>
            <Link class="btn btn-sm" href="/rovers/perseverance/map?near=">
              Perseverance near a point
            </Link>
            <Link class="btn btn-sm" href="/rovers/curiosity/map?near=">
              Curiosity near a point
            </Link>
          </div>
        </div>
      ) : (
        <>
          <h1>{near.active ? "Photos near a point" : `${rover.name} route map`}</h1>
          <p class="muted">
            {near.active
              ? "Click the map (or a stop) to drop a pin, choose how far to look, and see the photos the rover took while stopped within that distance."
              : "Where the rover has stopped, sol by sol. Select a stop to see its details and open that sol’s photos."}
          </p>
          <MapLoader rover={rover} near={near.active ? near : null} />
        </>
      )}
    </>
  );
}

/** Photos / Timeline / Route map of this rover, plus Flights next to Perseverance (where Ingenuity flew). */
function ViewTabs({ rover, rovers }: { rover: Rover; rovers: Rover[] }) {
  const heli = rovers.find((r) => r.id === "ingenuity");
  return (
    <nav class="view-tabs" aria-label={`Views of ${rover.name}`}>
      <Link href={`/rovers/${rover.id}`}>Photos</Link>
      <Link href={`/rovers/${rover.id}/timeline`}>Timeline</Link>
      <Link href={`/rovers/${rover.id}/map`} aria-current="page">
        Route map
      </Link>
      {rover.id === SAMPLES_ROVER && <Link href={samplesHref()}>Samples</Link>}
      {heli && (rover.id === "perseverance" || rover.id === "ingenuity") && (
        <Link
          href={`/rovers/${heli.id}/flights`}
          title="Flights of the Ingenuity helicopter, which flew from Perseverance’s landing site"
        >
          Flights
        </Link>
      )}
    </nav>
  );
}

/** The last published stop from `location?latest=true`; it is a bonus, so any failure hides it. */
function LatestPosition({
  rover,
  stops,
  onShow,
}: {
  rover: Rover;
  stops: Waypoint[];
  onShow: (index: number) => void;
}) {
  const [state] = useAsync(
    (signal) => api.locationLatest(rover.id, signal).catch(() => null),
    [rover.id],
  );
  const res = state.status === "ok" ? state.data : null;
  // A server that ignores `latest` would return every stop: the last one is still the latest.
  const w = res?.available ? (res.items[res.items.length - 1] ?? null) : null;
  if (!res || !w) return null;
  const index = findStop(stops, w.sol, w.site, w.drive)?.index ?? null;
  const behind = rover.max_sol !== null && w.sol < rover.max_sol;
  return (
    <section class="card latest-pos" aria-label="Latest position">
      <div>
        <div class="k">
          <i />
          Latest position
        </div>
        <div class="v">
          Sol {w.sol} · site {w.site}, drive {w.drive}
        </div>
      </div>
      <div>
        <div class="k">Coordinates</div>
        <div class="v">
          {latLabel(w.lat)}, {lonLabel(w.lon)}
        </div>
      </div>
      {w.elev_geoid !== null && (
        <div>
          <div class="k">Elevation</div>
          <div class="v">{metres(w.elev_geoid)}</div>
        </div>
      )}
      <button
        class="btn btn-sm"
        type="button"
        disabled={index === null}
        onClick={() => index !== null && onShow(index)}
      >
        Show on map
      </button>
      <Link class="btn btn-sm" href={`/rovers/${rover.id}${qs({ sol: w.sol })}`}>
        Photos of sol {w.sol}
      </Link>
      {behind && (
        <span class="small muted">Latest published stop; photos reach sol {rover.max_sol}.</span>
      )}
      <span class="api">GET /api/v2/rovers/{rover.id}/location?latest=true</span>
    </section>
  );
}

interface Loaded {
  traverse: Traverse;
  location: LocationResponse;
  /** Perseverance only. Markers are a bonus: when the list cannot be loaded the map works without them. */
  samples: Sample[] | null;
}

function MapLoader({ rover, near }: { rover: Rover; near: NearState | null }) {
  const [state, retry] = useAsync<Loaded>(async () => {
    const [traverse, location, samples] = await Promise.all([
      api.traverse(rover.id),
      api.location(rover.id),
      rover.id === SAMPLES_ROVER
        ? api.samples(rover.id).then(
            (r) => r.items,
            () => null,
          )
        : Promise.resolve(null),
    ]);
    return { traverse, location, samples };
  }, [rover.id]);

  if (state.status === "loading") {
    return (
      <>
        <section class="mp-summary" aria-hidden="true">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} class="card tile-stat">
              <div class="skeleton" style={{ height: 14, width: "60%", marginBottom: 8 }} />
              <div class="skeleton" style={{ height: 22, width: "40%" }} />
            </div>
          ))}
        </section>
        <section class="card mp-card" aria-label="Route map">
          <div class="mp-wrap" aria-busy="true">
            <div class="mp-overlay" role="status">
              <div>
                <p style={{ margin: 0 }}>Loading the route…</p>
                <p class="small muted" style={{ margin: "8px 0 0" }}>
                  The whole route is fetched once and kept for this visit.
                </p>
              </div>
            </div>
          </div>
        </section>
      </>
    );
  }
  if (state.status === "error") {
    return (
      <ErrorAlert
        error={state.error}
        onRetry={retry}
        title="We couldn’t load the route"
        actions={
          <Link class="btn btn-sm" href={`/rovers/${rover.id}`}>
            Browse {rover.name} photos
          </Link>
        }
      />
    );
  }
  return (
    <MapData
      rover={rover}
      traverse={state.data.traverse}
      location={state.data.location}
      samples={state.data.samples}
      near={near}
    />
  );
}

function MapData({
  rover,
  traverse,
  location,
  samples,
  near,
}: {
  rover: Rover;
  traverse: Traverse;
  location: LocationResponse;
  samples: Sample[] | null;
  near: NearState | null;
}) {
  const unavailable = !traverse.available || !location.available;
  const prepared = useMemo(
    () => (unavailable ? null : prepare(traverse, location)),
    [unavailable, traverse, location],
  );
  if (!prepared) {
    return (
      <section class="card mp-card" aria-label="Route map">
        <div class="mp-wrap">
          <div class="mp-overlay">
            <div>
              {unavailable ? (
                <>
                  <h2>No location data for {rover.name}</h2>
                  <p class="small muted">
                    {rover.id === "ingenuity"
                      ? "The helicopter has no route of its own: it flew short hops near Perseverance. "
                      : "Positions are published for Curiosity and Perseverance only. "}
                    {rover.name}’s photos are still available.
                  </p>
                </>
              ) : (
                <>
                  <h2>Route data hasn’t been loaded yet</h2>
                  <p class="small muted">
                    There are no stops or route segments for {rover.name} yet. Check back later; its
                    photos are available meanwhile.
                  </p>
                </>
              )}
              <Link class="btn btn-primary btn-sm" href={`/rovers/${rover.id}`}>
                Browse {rover.name} photos
              </Link>
            </div>
          </div>
        </div>
        <p class="api-line" style={{ margin: 0, padding: "var(--s2) var(--s3)" }}>
          GET /api/v2/rovers/{rover.id}/location → available: {String(location.available)}, items:{" "}
          {location.items.length}; /traverse → features: {traverse.features.length}
        </p>
      </section>
    );
  }
  if (near) return <NearView rover={rover} data={prepared} near={near} />;
  return <RouteMapView rover={rover} data={prepared} location={location} samples={samples} />;
}

function RouteMapView({
  rover,
  data,
  location,
  samples,
}: {
  rover: Rover;
  data: Prepared;
  location: LocationResponse;
  samples: Sample[] | null;
}) {
  const { stops, kx, bounds, route } = data;
  const q = useQuery();
  const from = intParam(q.get("from_sol"));
  const to = intParam(q.get("to_sol"));
  const hasRange = from !== null || to !== null;
  const inRange = useMemo(() => stopsInRange(stops, from, to), [stops, from, to]);
  const pool = useMemo(
    () => (hasRange ? inRange : stops.map((w, i) => ({ w, i }))),
    [hasRange, inRange, stops],
  );
  const allStops = useMemo(() => stops.map((w, i) => ({ w, i })), [stops]);
  const inRangeSet = useMemo(() => new Set(inRange.map((o) => o.i)), [inRange]);
  const highlight = useMemo(
    () => highlightRuns(data, from, to, inRange),
    [data, from, to, inRange],
  );

  // Sample markers: samples sharing a stop are drawn as one marker with a count.
  const [showSamples, setShowSamples] = useState(true);
  const groups = useMemo(() => groupByStop(samples ?? []), [samples]);
  const groupAt = useMemo(() => new Map(groups.map((g) => [g.key, g])), [groups]);
  const placed = groups.reduce((n, g) => n + g.items.length, 0);
  const hasSamples = samples !== null && samples.length > 0;
  const samplesAt = (w: Waypoint): Sample[] =>
    showSamples ? (groupAt.get(stopKey(w.sol, w.site, w.drive))?.items ?? []) : [];

  useRestoreScroll(true);
  const askedSol = intParam(q.get("sol"));
  const initialFound = useRef(
    findStop(stops, askedSol, intParam(q.get("site")), intParam(q.get("drive"))),
  );
  const initialStop = useRef<number | null>(initialFound.current?.index ?? null);
  const [sel, setSel] = useState<number | null>(initialStop.current);
  // Said when a stop other than the asked one was chosen (or none): cleared by the visitor's own choice.
  const [fallback, setFallback] = useState<string | null>(() => {
    const f = initialFound.current;
    if (askedSol === null || (f && f.exact)) return null;
    return f
      ? `No stop on sol ${askedSol} — showing the last stop before it, sol ${stops[f.index]!.sol}.`
      : `No stops on or before sol ${askedSol}.`;
  });
  const [hover, setHover] = useState<number | null>(null);
  const [listPage, setListPage] = useState(() => {
    const si = initialStop.current;
    const pos = si === null ? -1 : pool.findIndex((o) => o.i === si);
    return pos >= 0 ? Math.floor((pool.length - 1 - pos) / LIST_PAGE) + 1 : 1;
  });
  const [live, setLive] = useState("");
  const [view, setView] = useState<View>(() => fitView(bounds, 600, 440));
  const viewRef = useRef(view);
  viewRef.current = view;
  const mapRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const fitted = useRef(false);
  const drag = useRef<{ x: number; y: number; cx: number; cy: number; moved: boolean } | null>(
    null,
  );
  const [dragging, setDragging] = useState(false);
  const pendingDrag = useRef<{ dx: number; dy: number } | null>(null);
  const raf = useRef(0);
  useEffect(() => () => cancelAnimationFrame(raf.current), []);
  const [fromText, setFromText] = useState(from === null ? "" : String(from));
  const [toText, setToText] = useState(to === null ? "" : String(to));
  const [rangeErr, setRangeErr] = useState<string | null>(null);
  // The typed text follows the address when the address changes (presets, "Clear", links).
  const [synced, setSynced] = useState({ from, to });
  if (synced.from !== from || synced.to !== to) {
    setSynced({ from, to });
    setFromText(from === null ? "" : String(from));
    setToText(to === null ? "" : String(to));
  }

  const lowZoom = (v: View) => minPpu(bounds, v.W, v.H) * 0.6;
  const fitRange = (v: View, list: { w: Waypoint }[]): View => {
    const b = boundsOf(
      list.map((o) => o.w),
      kx,
    );
    return b ? fitView(b, v.W, v.H, 0.2) : fitView(bounds, v.W, v.H);
  };

  // Measure the drawing area; the first measurement chooses the initial view.
  useEffect(() => {
    const el = mapRef.current;
    if (!el) return;
    const apply = () => {
      const r = el.getBoundingClientRect();
      const W = r.width || 600;
      const H = r.height || 440;
      const cur = viewRef.current;
      if (!fitted.current) {
        fitted.current = true;
        let next = fitView(bounds, W, H);
        const si = initialStop.current;
        if (si !== null) {
          const w = stops[si]!;
          next = centerOn(next, kx, w.lon, w.lat, Math.max(next.ppu, W / (1500 / M_PER_DEG)));
        } else if (hasRange && inRange.length) {
          next = fitRange(next, inRange);
        }
        setView(next);
      } else if (cur.W !== W || cur.H !== H) {
        setView({ ...cur, W, H });
      }
    };
    apply();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(apply);
    ro.observe(el);
    return () => ro.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Ctrl/Cmd + wheel zooms; a plain wheel keeps scrolling the page (needs a non-passive listener).
  useEffect(() => {
    const el = mapRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      const r = el.getBoundingClientRect();
      setView((v) =>
        zoomAt(v, e.deltaY < 0 ? 1.25 : 0.8, e.clientX - r.left, e.clientY - r.top, lowZoom(v)),
      );
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const last = stops.length ? stops[stops.length - 1]! : null;

  // The selected stop lives in the address (`sol`, `site`, `drive`): replaced, not pushed.
  const firstSel = useRef(true);
  useEffect(() => {
    // The mount only shows what the address already says (and a fallback note keeps the asked address).
    if (firstSel.current) {
      firstSel.current = false;
      return;
    }
    setFallback(null);
    const w = sel === null ? undefined : stops[sel];
    const next = mapHref(rover.id, { stop: w, from, to });
    if (next !== `${window.location.pathname}${window.location.search}`) {
      navigate(next, { replace: true });
    }
    // Only a change of the selection writes the address.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sel]);

  const select = (idx: number | null, center = false) => {
    setSel(idx);
    if (idx === null) return;
    const w = stops[idx]!;
    if (center) setView((v) => centerOn(v, kx, w.lon, w.lat));
    const pos = pool.findIndex((o) => o.i === idx);
    if (pos >= 0) setListPage(Math.floor((pool.length - 1 - pos) / LIST_PAGE) + 1);
    setLive(
      `Sol ${w.sol}, site ${w.site}, drive ${w.drive}. ${latLabel(w.lat)}, ${lonLabel(w.lon)}.`,
    );
  };

  const zoomBy = (f: number) => {
    const v = viewRef.current;
    const s = sel !== null ? stops[sel] : null;
    const [ax, ay] = s ? project(v, kx, s.lon, s.lat) : [v.W / 2, v.H / 2];
    const onScreen = ax >= 0 && ax <= v.W && ay >= 0 && ay <= v.H;
    setView(zoomAt(v, f, onScreen ? ax : v.W / 2, onScreen ? ay : v.H / 2, lowZoom(v)));
  };

  const applyRange = (f: number | null, t: number | null) => {
    const w = sel === null ? undefined : stops[sel];
    navigate(mapHref(rover.id, { stop: w, from: f, to: t }), { replace: true });
    setListPage(1);
    const list = stopsInRange(stops, f, t);
    setView((v) =>
      f === null && t === null ? fitView(bounds, v.W, v.H) : list.length ? fitRange(v, list) : v,
    );
  };

  const onSubmit = (e: Event) => {
    e.preventDefault();
    const err = rangeError(fromText, toText);
    setRangeErr(err);
    if (err) return;
    const f = fromText.trim() === "" ? null : Number(fromText);
    const t = toText.trim() === "" ? null : Number(toText);
    applyRange(f, t);
  };

  // ----- pointer interaction -----
  const local = (e: PointerEvent | MouseEvent): [number, number] => {
    const r = svgRef.current?.getBoundingClientRect();
    return [e.clientX - (r?.left ?? 0), e.clientY - (r?.top ?? 0)];
  };
  const onPointerDown = (e: JSX.TargetedPointerEvent<SVGSVGElement>) => {
    if (e.pointerType === "touch") return;
    drag.current = { x: e.clientX, y: e.clientY, cx: view.cx, cy: view.cy, moved: false };
    e.currentTarget.setPointerCapture?.(e.pointerId);
  };
  const onPointerMove = (e: JSX.TargetedPointerEvent<SVGSVGElement>) => {
    const d = drag.current;
    if (d) {
      const dx = e.clientX - d.x;
      const dy = e.clientY - d.y;
      if (Math.abs(dx) + Math.abs(dy) > 4) {
        d.moved = true;
        setDragging(true);
      }
      if (d.moved) {
        // One view update per frame, however many pointer events arrive.
        pendingDrag.current = { dx, dy };
        if (raf.current === 0) {
          raf.current = requestAnimationFrame(() => {
            raf.current = 0;
            const p = pendingDrag.current;
            const start = drag.current;
            if (p && start) {
              setView((v) => ({ ...v, cx: start.cx - p.dx / v.ppu, cy: start.cy - p.dy / v.ppu }));
            }
          });
        }
        return;
      }
    }
    const [mx, my] = local(e);
    const h = nearestStop(allStops, view, kx, mx, my);
    if (h !== hover) setHover(h);
  };
  const onPointerUp = (e: JSX.TargetedPointerEvent<SVGSVGElement>) => {
    const moved = drag.current?.moved;
    drag.current = null;
    setDragging(false);
    if (moved) return;
    const [mx, my] = local(e);
    const h = nearestStop(allStops, view, kx, mx, my);
    if (h !== null) select(h);
    else if (e.pointerType === "mouse") setSel(null);
  };

  const onKeyDown = (e: JSX.TargetedKeyboardEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget) return;
    if (e.key === "+" || e.key === "=") {
      e.preventDefault();
      zoomBy(1.6);
      return;
    }
    if (e.key === "-") {
      e.preventDefault();
      zoomBy(1 / 1.6);
      return;
    }
    if (e.key === "Escape") {
      setSel(null);
      return;
    }
    const pos = sel === null ? -1 : pool.findIndex((o) => o.i === sel);
    if (e.key === "Enter") {
      if (pos >= 0) document.getElementById("open-photos")?.focus();
      return;
    }
    if (!pool.length) return;
    let next: number | null = null;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = Math.min(pos + 1, pool.length - 1);
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
      next = Math.max(pos < 0 ? pool.length - 1 : pos - 1, 0);
    } else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = pool.length - 1;
    if (next === null) return;
    e.preventDefault();
    const w = pool[next]!.w;
    const [x, y] = project(view, kx, w.lon, w.lat);
    select(pool[next]!.i, x < 30 || x > view.W - 30 || y < 30 || y > view.H - 30);
  };

  // ----- drawing data -----
  const { W, H } = view;
  const baseD = useMemo(() => pathData(route, route.segs, view, kx), [route, kx, view]);
  const hiD = useMemo(
    () => (highlight.runs.length ? pathData(route, highlight.runs, view, kx) : ""),
    [route, kx, view, highlight],
  );
  const dotsBaseD = useMemo(
    () => dotsPath(hasRange ? stops.filter((_, i) => !inRangeSet.has(i)) : stops, view, kx),
    [stops, hasRange, inRangeSet, view, kx],
  );
  const dotsHiD = useMemo(
    () =>
      hasRange
        ? dotsPath(
            inRange.map((o) => o.w),
            view,
            kx,
          )
        : "",
    [hasRange, inRange, view, kx],
  );
  const grid = useMemo(() => {
    const step = niceStep(view.ppu, kx, 90);
    const dec = Math.max(0, Math.ceil(-Math.log10(step)));
    const a = unproject(view, kx, 0, H);
    const b = unproject(view, kx, W, 0);
    const lines: {
      key: string;
      x1: number;
      y1: number;
      x2: number;
      y2: number;
      label: string;
      lx: number;
      ly: number;
    }[] = [];
    for (let lon = Math.ceil(a.lon / step) * step; lon <= b.lon; lon += step) {
      const x = project(view, kx, lon, 0)[0];
      lines.push({
        key: `o${lon}`,
        x1: x,
        y1: 0,
        x2: x,
        y2: H,
        label: `${lon.toFixed(dec)}°E`,
        lx: x + 4,
        ly: H - 6,
      });
    }
    for (let lat = Math.ceil(a.lat / step) * step; lat <= b.lat; lat += step) {
      const y = project(view, kx, 0, lat)[1];
      lines.push({
        key: `a${lat}`,
        x1: 0,
        y1: y,
        x2: W,
        y2: y,
        label: `${lat.toFixed(dec)}°${lat >= 0 ? "N" : "S"}`,
        lx: 6,
        ly: y - 4,
      });
    }
    return lines;
  }, [view, kx, W, H]);
  const bar = scaleBar(view.ppu);

  const shown = hover ?? sel;
  const shownStop = shown !== null ? stops[shown] : null;
  const lastPt = last ? project(view, kx, last.lon, last.lat) : null;
  const rangeEmpty = hasRange && inRange.length === 0;

  // ----- side panels -----
  const selected = sel !== null ? stops[sel] : null;
  const newestFirst = pool.slice().reverse();
  const pages = Math.max(1, Math.ceil(newestFirst.length / LIST_PAGE));
  const page = Math.min(Math.max(listPage, 1), pages);
  const rows = newestFirst.slice((page - 1) * LIST_PAGE, page * LIST_PAGE);

  const notes: JSX.Element[] = [];
  if (
    rover.max_sol !== null &&
    location.source_max_sol !== null &&
    location.source_max_sol < rover.max_sol
  ) {
    notes.push(
      <p key="max" style={{ margin: 0 }}>
        <strong>Position data ends at sol {location.source_max_sol}.</strong> Photos reach sol{" "}
        {rover.max_sol}; the source publishes stops irregularly, so the latest sols may have no stop
        yet.
      </p>,
    );
  }
  if (hasRange && highlight.approximate) {
    notes.push(
      <p key="apx" style={{ margin: 0 }}>
        <strong>Approximate range highlight.</strong> {rover.name}’s route segments carry no sol, so
        the highlight follows the route between the first and last stop of the range.
      </p>,
    );
  }

  const tipPos = (() => {
    if (!shownStop) return null;
    const [x, y] = project(view, kx, shownStop.lon, shownStop.lat);
    const tw = 210;
    const th = 84 + 16 * samplesAt(shownStop).length;
    let left = x + 14;
    let top = y - th - 10;
    if (left + tw > W - 4) left = x - tw - 14;
    if (left < 4) left = 4;
    if (top < 4) top = y + 16;
    return { left, top };
  })();

  return (
    <>
      <LatestPosition rover={rover} stops={stops} onShow={(i) => select(i, true)} />

      {notes.length > 0 && <div class="banner">{notes}</div>}

      <section class="mp-summary" aria-label="Route summary">
        <div class="card tile-stat">
          <div class="k">Stops</div>
          <div class="v">{fmt(stops.length)}</div>
          <div class="s">{stops.length ? `sol ${stops[0]!.sol} to ${last!.sol}` : "none yet"}</div>
        </div>
        <div class="card tile-stat">
          <div class="k">Distance (source)</div>
          <div class="v">{km(last?.dist_total_m)}</div>
          <div class="s">as reported at the latest stop</div>
        </div>
        <div class="card tile-stat">
          <div class="k">Latest stop</div>
          <div class="v">{last ? `Sol ${last.sol}` : "—"}</div>
          <div class="s">{last ? `site ${last.site}, drive ${last.drive}` : ""}</div>
        </div>
        <div class="card tile-stat">
          <div class="k">Updated</div>
          <div class="v" style={{ fontSize: "var(--fs-md)" }}>
            {location.updated_at ? location.updated_at.slice(0, 10) : "—"}
          </div>
          <div class="s">
            {location.source_max_sol !== null ? `data through sol ${location.source_max_sol}` : ""}
          </div>
        </div>
      </section>

      <div class="mp-layout">
        <section class="card mp-card" aria-labelledby="map-h">
          <h2 id="map-h" class="visually-hidden">
            Route map
          </h2>
          <div class="mp-tools" role="toolbar" aria-label="Map controls">
            <button
              class="btn btn-sm"
              type="button"
              aria-label="Zoom out"
              onClick={() => zoomBy(1 / 1.6)}
            >
              −
            </button>
            <button
              class="btn btn-sm"
              type="button"
              aria-label="Zoom in"
              onClick={() => zoomBy(1.6)}
            >
              +
            </button>
            <button class="btn btn-sm" type="button" onClick={() => setView(fitView(bounds, W, H))}>
              Whole route
            </button>
            <button
              class="btn btn-sm"
              type="button"
              disabled={!hasRange || rangeEmpty}
              onClick={() => setView((v) => fitRange(v, inRange))}
            >
              Selected range
            </button>
            <button
              class="btn btn-sm"
              type="button"
              disabled={!last}
              onClick={() => last && select(stops.length - 1, true)}
            >
              Latest stop
            </button>
            {hasSamples && (
              <label class="tgl">
                <input
                  type="checkbox"
                  checked={showSamples}
                  onChange={(e) => setShowSamples(e.currentTarget.checked)}
                />{" "}
                Samples
              </label>
            )}
            <div class="mp-legend" role="group" aria-label="Legend">
              <span>
                <i class="lg-route" />
                Route
              </span>
              {hasRange && (
                <span>
                  <i class="lg-range" />
                  Sol range
                </span>
              )}
              <span>
                <i class="lg-stop" />
                Stop
              </span>
              <span>
                <i class="lg-latest" />
                Latest
              </span>
              {hasSamples && showSamples && (
                <span>
                  <i class="lg-sample" />
                  Sample sealed here
                </span>
              )}
              {hasSamples && showSamples && groups.some((g) => g.approximate) && (
                <span>
                  <i class="lg-sample lg-apx" />
                  Sample, approximate stop
                </span>
              )}
            </div>
          </div>
          <div
            class="mp-wrap"
            ref={mapRef}
            tabIndex={0}
            role="group"
            aria-roledescription="interactive route map"
            aria-label="Route map. Use left and right arrow keys to step through stops, plus and minus to zoom, Enter to go to the photos button, Escape to clear."
            aria-describedby="mp-live"
            onKeyDown={onKeyDown}
          >
            <svg
              ref={svgRef}
              class={dragging ? "dragging" : undefined}
              viewBox={`0 0 ${W} ${H}`}
              role="img"
              aria-label={`${rover.name} route with ${stops.length} stops on a latitude and longitude grid`}
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerLeave={() => {
                if (!drag.current) setHover(null);
              }}
            >
              {grid.map((g) => (
                <g key={g.key}>
                  <line class="g-line" x1={g.x1} y1={g.y1} x2={g.x2} y2={g.y2} />
                  <text class="g-label" x={g.lx} y={g.ly}>
                    {g.label}
                  </text>
                </g>
              ))}
              <path class="route-base" d={baseD} />
              {hiD && <path class="route-hi" d={hiD} />}
              <path class="stops-base" d={dotsBaseD} />
              {dotsHiD && <path class="stops-hi" d={dotsHiD} />}
              {hasSamples && showSamples && (
                <g class="sample-markers">
                  {groups.map((g) => {
                    const [x, y] = project(view, kx, g.lon, g.lat);
                    if (x < -12 || x > W + 12 || y < -12 || y > H + 12) return null;
                    return (
                      <g
                        key={g.key}
                        class="smp-g"
                        data-stop={g.key}
                        transform={`translate(${x.toFixed(1)} ${y.toFixed(1)})`}
                      >
                        <path class={g.approximate ? "smp smp-apx" : "smp"} d="M0 -9L8 5H-8Z" />
                        {g.items.length > 1 && (
                          <text class="smp-n" x="9" y="-3">
                            {g.items.length}
                          </text>
                        )}
                      </g>
                    );
                  })}
                </g>
              )}
              {last && lastPt && (
                <>
                  <g transform={`translate(${lastPt[0].toFixed(1)} ${lastPt[1].toFixed(1)})`}>
                    <rect
                      class="latest-mark"
                      x="-7"
                      y="-7"
                      width="14"
                      height="14"
                      transform="rotate(45)"
                    />
                  </g>
                  <text
                    class="latest-label"
                    x={Math.min(Math.max(lastPt[0] + 14, 4), W - 150)}
                    y={Math.min(Math.max(lastPt[1] - 12, 14), H - 40)}
                  >
                    Latest stop · sol {last.sol}
                  </text>
                </>
              )}
              {hover !== null && hover !== sel && stops[hover] && (
                <HoverRing view={view} kx={kx} w={stops[hover]!} />
              )}
              {selected && <SelRing view={view} kx={kx} w={selected} />}
              <g>
                <path class="scale-line" d={`M14 20V26H${(14 + bar.px).toFixed(1)}V20`} />
                <text class="scale-text" x="14" y="40">
                  {scaleLabel(bar.km)}
                </text>
              </g>
              <g transform={`translate(${W - 26} 30)`}>
                <path d="M0 -16L6 6L0 2L-6 6Z" fill="var(--text)" />
                <text class="north" x="0" y="22" text-anchor="middle">
                  N
                </text>
              </g>
            </svg>
            {shownStop && tipPos && (
              <div
                class="mp-tip"
                role="presentation"
                style={{ left: tipPos.left, top: tipPos.top }}
              >
                <strong>Sol {shownStop.sol}</strong>
                Site {shownStop.site} · drive {shownStop.drive}
                <br />
                {latLabel(shownStop.lat)}, {lonLabel(shownStop.lon)}
                <br />
                Drive {km(shownStop.dist_m)} · total {km(shownStop.dist_total_m)}
                {samplesAt(shownStop).map((x) => (
                  <div key={x.number}>
                    <span aria-hidden="true">▲ </span>Sample {x.number} — {x.name}
                  </div>
                ))}
              </div>
            )}
            {rangeEmpty && (
              <div class="mp-overlay top">
                <div>
                  <h2>
                    No stops in sols {from ?? "first"}–{to ?? "last"}
                  </h2>
                  <p class="small muted">
                    The rover may have stayed put, or the source has not published stops for these
                    sols yet. This is not an error.
                  </p>
                  <button
                    class="btn btn-primary btn-sm"
                    type="button"
                    onClick={() => applyRange(null, null)}
                  >
                    Clear range
                  </button>
                </div>
              </div>
            )}
          </div>
          <div class="mp-foot">
            <span id="mp-live" aria-live="polite">
              {live || " "}
            </span>
            <span class="spacer" />
            {hasSamples && showSamples && (
              <span id="smp-foot">
                ▲ {placed} sample{placed === 1 ? "" : "s"} shown at {groups.length} stop
                {groups.length === 1 ? "" : "s"}
                {samples!.length > placed &&
                  `; ${samples!.length - placed} not placed (no sol on the NASA page, or no stop yet)`}
                .
              </span>
            )}
            <span>Plain background: no imagery. Drag to pan, Ctrl + wheel to zoom.</span>
            <span>Positions: JPL MMGIS map layers.</span>
          </div>
        </section>

        <aside class="mp-side" aria-label="Filters and details">
          <form class="card panel" noValidate aria-labelledby="rng-h" onSubmit={onSubmit}>
            <h2 id="rng-h">Sol range</h2>
            <div class="mp-range">
              <div class="field">
                <label for="from-sol">From sol</label>
                <input
                  id="from-sol"
                  type="number"
                  min={0}
                  inputMode="numeric"
                  placeholder="first"
                  value={fromText}
                  aria-invalid={rangeErr ? "true" : undefined}
                  onInput={(e) => setFromText(e.currentTarget.value)}
                />
              </div>
              <div class="field">
                <label for="to-sol">To sol</label>
                <input
                  id="to-sol"
                  type="number"
                  min={0}
                  inputMode="numeric"
                  placeholder="last"
                  value={toText}
                  aria-invalid={rangeErr ? "true" : undefined}
                  onInput={(e) => setToText(e.currentTarget.value)}
                />
              </div>
              {rangeErr && (
                <div class="full field-err" role="alert">
                  {rangeErr}
                </div>
              )}
              <div class="full row" style={{ alignItems: "center" }}>
                <button class="btn btn-primary btn-sm" type="submit">
                  Highlight
                </button>
                <button
                  class="btn btn-sm"
                  type="button"
                  disabled={location.source_max_sol === null}
                  onClick={() => {
                    const max = location.source_max_sol ?? 0;
                    setRangeErr(null);
                    applyRange(Math.max(0, max - 100), max);
                  }}
                >
                  Last 100 sols
                </button>
                <button
                  class="btn btn-sm"
                  type="button"
                  onClick={() => {
                    setRangeErr(null);
                    applyRange(null, null);
                  }}
                >
                  Clear
                </button>
              </div>
            </div>
            <p class="hint" style={{ margin: "var(--s3) 0 0" }}>
              {hasRange
                ? `${fmt(inRange.length)} stop${inRange.length === 1 ? "" : "s"} in range.`
                : "Leave both fields empty to show every stop."}
              {stops.length > 0 && ` Route data spans sol ${stops[0]!.sol}–${last!.sol}.`}
            </p>
          </form>

          <section class="card panel meta" aria-labelledby="sel-h">
            <h2 id="sel-h">Selected stop</h2>
            {fallback && (
              <p class="hint" role="status" data-testid="stop-fallback">
                {fallback}
              </p>
            )}
            {selected ? (
              <div>
                <dl>
                  <dt>Sol</dt>
                  <dd>{selected.sol}</dd>
                  <dt>Site / drive</dt>
                  <dd>
                    {selected.site} / {selected.drive}
                  </dd>
                  <dt>Position</dt>
                  <dd>
                    {latLabel(selected.lat)}
                    <br />
                    {lonLabel(selected.lon)}
                  </dd>
                  <dt>Elevation</dt>
                  <dd>
                    {metres(selected.elev_geoid)} <span class="apx">above areoid</span>
                  </dd>
                  <dt>This drive</dt>
                  <dd>{km(selected.dist_m)}</dd>
                  <dt>Total (source)</dt>
                  <dd>{km(selected.dist_total_m)}</dd>
                </dl>
                <div class="row" style={{ marginTop: "var(--s3)", alignItems: "center" }}>
                  <Link
                    class="btn btn-primary btn-sm"
                    href={`/rovers/${rover.id}${qs({ sol: selected.sol })}`}
                    id="open-photos"
                  >
                    Photos of sol {selected.sol}
                  </Link>
                  <Link
                    class="btn btn-sm"
                    href={stopPhotosHref(rover.id, stops, selected)}
                    id="open-stop-photos"
                    title={`Photos taken at site ${selected.site}, drive ${selected.drive}`}
                  >
                    Photos at this stop
                  </Link>
                  {supportsNear(rover.id) && (
                    <Link
                      class="btn btn-sm"
                      href={nearHref(rover.id, {
                        point: { lat: selected.lat, lon: selected.lon },
                        radius: 1,
                      })}
                      data-testid="stop-near-button"
                      title="Photos taken at stops within 1 km of this stop"
                    >
                      Photos within 1 km
                    </Link>
                  )}
                  <button class="btn btn-sm" type="button" onClick={() => setSel(null)}>
                    Clear
                  </button>
                </div>
                <SampleCards samples={samplesAt(selected)} />
                <p class="apx" style={{ margin: "var(--s2) 0 0" }}>
                  <strong>Photos of sol {selected.sol}</strong> = everything taken that day,
                  possibly at several stops. <strong>Photos at this stop</strong> = frames taken
                  here and on the way to the next stop (site {selected.site}, from drive{" "}
                  {selected.drive}).
                </p>
              </div>
            ) : (
              <p class="muted small" style={{ margin: 0 }}>
                Select a stop on the map or in the list.
              </p>
            )}
          </section>

          <section class="card panel" aria-labelledby="list-h">
            <h2 id="list-h">Stops</h2>
            {rows.length === 0 ? (
              <p class="muted small" style={{ margin: 0 }}>
                No stops in this range.
              </p>
            ) : (
              <>
                <table class="mp-table">
                  <caption>
                    {hasRange ? "Stops in the sol range" : "All stops"}, newest first (
                    {fmt(newestFirst.length)})
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">Sol</th>
                      <th scope="col">Site/drive</th>
                      <th scope="col">Drive</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((o) => {
                      const here = samplesAt(o.w);
                      return (
                        <tr key={o.i} class={o.i === sel ? "sel" : undefined}>
                          <td>
                            <button
                              type="button"
                              aria-pressed={o.i === sel}
                              aria-label={`Select stop: sol ${o.w.sol}, site ${o.w.site}, drive ${o.w.drive}${
                                here.length ? `, ${here.length} sample(s) sealed here` : ""
                              }`}
                              onClick={() => select(o.i, true)}
                            >
                              {o.w.sol}
                            </button>
                            {here.length > 0 && (
                              <span class="smp-mark" aria-hidden="true" title="Sample sealed here">
                                {" "}
                                ▲{here.length > 1 ? here.length : ""}
                              </span>
                            )}
                          </td>
                          <td>
                            {o.w.site}/{o.w.drive}
                          </td>
                          <td>{km(o.w.dist_m)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                <div class="mp-pager">
                  <button
                    class="btn btn-sm"
                    type="button"
                    disabled={page <= 1}
                    onClick={() => setListPage(page - 1)}
                  >
                    Newer
                  </button>
                  <span class="spacer">
                    Page {page} of {fmt(pages)}
                  </span>
                  <button
                    class="btn btn-sm"
                    type="button"
                    disabled={page >= pages}
                    onClick={() => setListPage(page + 1)}
                  >
                    Older
                  </button>
                </div>
              </>
            )}
          </section>
        </aside>
      </div>

      <p class="api-line">
        API: <code>GET /api/v2/rovers/{rover.id}/traverse</code> +{" "}
        <code>GET /api/v2/rovers/{rover.id}/location</code>
        {hasSamples && (
          <>
            {" "}
            + <code>GET /api/v2/rovers/{rover.id}/samples</code>
          </>
        )}
      </p>
    </>
  );
}

function HoverRing({ view, kx, w }: { view: View; kx: number; w: Waypoint }) {
  const [x, y] = project(view, kx, w.lon, w.lat);
  return (
    <>
      <circle class="halo" cx={x.toFixed(1)} cy={y.toFixed(1)} r="8" />
      <circle class="hover-ring" cx={x.toFixed(1)} cy={y.toFixed(1)} r="8" />
    </>
  );
}

function SelRing({ view, kx, w }: { view: View; kx: number; w: Waypoint }) {
  const [x, y] = project(view, kx, w.lon, w.lat);
  return <circle class="sel-ring" cx={x.toFixed(1)} cy={y.toFixed(1)} r="11" />;
}

/** "Sample sealed here" cards of the selected stop. `by_sol` placements say that they are approximate. */
function SampleCards({ samples }: { samples: Sample[] }) {
  if (samples.length === 0) return null;
  return (
    <div class="smp-cards">
      <h3 style={{ margin: "var(--s4) 0 0", fontSize: "var(--fs-sm)" }}>
        {samples.length === 1 ? "Sample sealed here" : `${samples.length} samples sealed here`}
      </h3>
      {samples.map((x) => {
        const l = x.location!;
        return (
          <div key={x.number} class={`smp-card${l.match === "by_sol" ? " smp-card-apx" : ""}`}>
            <h3>
              <span aria-hidden="true" style={{ color: "var(--map-sample)" }}>
                ▲{" "}
              </span>
              <Link href={samplesHref(x.number)}>
                Sample {x.number} — {x.name}
              </Link>
            </h3>
            <div style={{ margin: "4px 0" }}>
              <SampleBadges x={x} />
            </div>
            {x.sol !== null && (
              <div class="small">
                Sealed on sol {x.sol}
                {x.sealed_on ? ` (${x.sealed_on})` : ""}
                {x.feature_name ? ` · ${x.feature_name}` : ""}
              </div>
            )}
            <div class="apx" style={{ marginTop: 4 }}>
              {MATCH_TEXT[l.match]}
              {x.sol !== null && l.sol !== x.sol ? ` Stop of sol ${l.sol}.` : ""}
            </div>
            {x.sol !== null && (
              <div class="small" style={{ marginTop: 6 }}>
                <TubeLink sol={x.sol} number={x.number} after=" · " />
                <Link href={solPhotosHref(x.sol)}>Photos of sol {x.sol}</Link>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
