// "Photos near a point": a mode of the route map. The point and the radius live in the address
// (`?near=LAT,LON&radius_km=R`); the map, the fields and the stops of a rover all set the same point.
import type { JSX } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { ApiError, api, requestLine, searchPhotos } from "../api/client";
import { describeError } from "../api/errors";
import type { Camera, PhotoPage, Rover } from "../api/types";
import { fmt } from "../lib/format";
import { useAsync } from "../lib/hooks";
import {
  centerOn,
  dotsPath,
  fitView,
  latLabel,
  lonLabel,
  minPpu,
  nearestStop,
  niceStep,
  pathData,
  project,
  scaleBar,
  scaleLabel,
  unproject,
  zoomAt,
  type Prepared,
  type View,
} from "../lib/mapgeo";
import {
  fmtCoord,
  fmtRadius,
  isHeavyNear,
  nearHref,
  nearQuery,
  NEAR_PER_PAGE,
  nearestStopTo,
  parseLat,
  parseLon,
  parseRadius,
  radiusDeg,
  radiusToSlider,
  R_DEFAULT,
  R_MAX,
  R_MIN,
  sliderToRadius,
  stopsWithin,
  type NearState,
  type Point,
} from "../lib/near";
import { pageLimit, MAX_BROWSABLE } from "../lib/search";
import { Link, navigate } from "../router";
import { BusyAlert } from "./Busy";
import { Pager } from "./Pager";
import { PhotoTile } from "./PhotoTile";
import { ErrorAlert, GridSkeleton } from "./States";

interface Props {
  rover: Rover;
  data: Prepared;
  near: NearState;
}

const radiusLabel = (r: number) => `${fmtRadius(r)} km`;

export function NearView({ rover, data, near }: Props) {
  const { stops, kx, bounds, route } = data;
  const point = near.point;
  const radius = near.radius;
  // Delayed actions (the radius timer) must see the address as it is when they run, not when they were set.
  const nearRef = useRef(near);
  nearRef.current = near;
  const radTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => clearTimeout(radTimer.current ?? undefined), []);

  /** Address of this mode with some of its parts replaced; filters keep their values, the page restarts. */
  const hrefWith = (patch: {
    point?: Point | null;
    radius?: string | number;
    camera?: string;
    solMin?: string;
    solMax?: string;
    sort?: string;
    perPage?: number;
    page?: number;
  }) =>
    nearHref(rover.id, {
      point: "point" in patch ? patch.point : nearRef.current.point,
      radius:
        patch.radius ?? (nearRef.current.radius !== null ? nearRef.current.radius : R_DEFAULT),
      camera: "camera" in patch ? patch.camera : nearRef.current.camera,
      solMin: "solMin" in patch ? patch.solMin : nearRef.current.solMin,
      solMax: "solMax" in patch ? patch.solMax : nearRef.current.solMax,
      sort: patch.sort ?? nearRef.current.sort,
      perPage: patch.perPage ?? nearRef.current.perPage,
      page: patch.page ?? 1,
    });
  const go = (patch: Parameters<typeof hrefWith>[0]) =>
    navigate(hrefWith(patch), { replace: true });

  const inside = useMemo(
    () => (point && radius !== null ? stopsWithin(stops, point, radius) : []),
    [stops, point, radius],
  );
  const insideSet = useMemo(() => new Set(inside.map((o) => o.i)), [inside]);

  // ----- map view -----
  const mapRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [view, setView] = useState<View>(() => fitView(bounds, 600, 440));
  const viewRef = useRef(view);
  viewRef.current = view;
  const fitted = useRef(false);
  const [hover, setHover] = useState<number | null>(null);
  const [dragging, setDragging] = useState(false);
  const drag = useRef<{ x: number; y: number; cx: number; cy: number; moved: boolean } | null>(
    null,
  );
  const [live, setLive] = useState("");

  const circleView = (v: View, p: Point, r: number, pad = 0.35): View => {
    const cx = p.lon * kx;
    const cy = -p.lat;
    const d = radiusDeg(r);
    return fitView({ minX: cx - d, maxX: cx + d, minY: cy - d, maxY: cy + d }, v.W, v.H, pad);
  };

  useEffect(() => {
    const el = mapRef.current;
    if (!el) return;
    const apply = () => {
      const rect = el.getBoundingClientRect();
      const W = rect.width || 600;
      const H = rect.height || 440;
      const cur = viewRef.current;
      if (!fitted.current) {
        fitted.current = true;
        const base = fitView(bounds, W, H);
        setView(point && radius !== null ? circleView(base, point, radius) : base);
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

  const lowZoom = (v: View) => minPpu(bounds, v.W, v.H) * 0.6;

  // Ctrl/Cmd + wheel zooms; a plain wheel keeps scrolling the page.
  useEffect(() => {
    const el = mapRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      setView((v) =>
        zoomAt(
          v,
          e.deltaY < 0 ? 1.25 : 0.8,
          e.clientX - rect.left,
          e.clientY - rect.top,
          lowZoom(v),
        ),
      );
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const zoomBy = (f: number) => {
    const v = viewRef.current;
    setView(zoomAt(v, f, v.W / 2, v.H / 2, lowZoom(v)));
  };
  const showCircle = (p: Point, r: number) => setView((v) => circleView(v, p, r));

  // ----- setting the point -----
  const allStops = useMemo(() => stops.map((w, i) => ({ w, i })), [stops]);
  const placeAt = (p: Point, text?: string) => {
    clearTimeout(radTimer.current ?? undefined);
    go({ point: { lat: Number(fmtCoord(p.lat)), lon: Number(fmtCoord(p.lon)) }, page: 1 });
    if (text) setLive(text);
  };
  const placeOnStop = (i: number) => {
    const w = stops[i]!;
    placeAt(
      { lat: w.lat, lon: w.lon },
      `Point set on the stop of sol ${w.sol}, site ${w.site}, drive ${w.drive}.`,
    );
  };

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
        setView((v) => ({ ...v, cx: d.cx - dx / v.ppu, cy: d.cy - dy / v.ppu }));
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
    if (h !== null) {
      placeOnStop(h);
      return;
    }
    const p = unproject(view, kx, mx, my);
    placeAt({ lat: p.lat, lon: p.lon }, `Point set at ${latLabel(p.lat)}, ${lonLabel(p.lon)}.`);
  };

  /** The stop nearest to the point, or the last stop: where the arrow keys start from. */
  const currentStop = point ? (nearestStopTo(stops, point)?.i ?? null) : null;
  const onKeyDown = (e: JSX.TargetedKeyboardEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget) return;
    if (e.key === "+" || e.key === "=") {
      e.preventDefault();
      zoomBy(1.6);
    } else if (e.key === "-") {
      e.preventDefault();
      zoomBy(1 / 1.6);
    } else if (e.key === "Escape") {
      if (point) go({ point: null });
    } else if (stops.length) {
      let next: number | null = null;
      const at = currentStop;
      if (e.key === "ArrowRight" || e.key === "ArrowDown") {
        next = at === null ? stops.length - 1 : Math.min(at + 1, stops.length - 1);
      } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
        next = at === null ? stops.length - 1 : Math.max(at - 1, 0);
      } else if (e.key === "Home") next = 0;
      else if (e.key === "End") next = stops.length - 1;
      if (next === null) return;
      e.preventDefault();
      placeOnStop(next);
      const w = stops[next]!;
      const [x, y] = project(view, kx, w.lon, w.lat);
      if (x < 30 || x > view.W - 30 || y < 30 || y > view.H - 30) {
        setView((v) => centerOn(v, kx, w.lon, w.lat));
      }
    }
  };

  // ----- the fields -----
  const [latText, setLatText] = useState(point ? fmtCoord(point.lat) : "");
  const [lonText, setLonText] = useState(point ? fmtCoord(point.lon) : "");
  const [pointErr, setPointErr] = useState<string | null>(null);
  // Typed text follows the address when the point changes from outside (map, links); same value keeps the typed text.
  const [syncedPoint, setSyncedPoint] = useState(point);
  if (syncedPoint?.lat !== point?.lat || syncedPoint?.lon !== point?.lon) {
    setSyncedPoint(point);
    setLatText(point ? fmtCoord(point.lat) : "");
    setLonText(point ? fmtCoord(point.lon) : "");
    setPointErr(null);
  }
  /** Validates both fields; on success writes the point to the address. `silent`: do not complain about an empty pair. */
  const commitPoint = (lat: string, lon: string, silent = false) => {
    if (silent && (lat.trim() === "" || lon.trim() === "")) return;
    const a = parseLat(lat);
    const b = parseLon(lon);
    if (!a.ok) return setPointErr(a.error);
    if (!b.ok) return setPointErr(b.error);
    setPointErr(null);
    const p = { lat: a.value, lon: b.value };
    if (point && Math.abs(point.lat - p.lat) < 5e-5 && Math.abs(point.lon - p.lon) < 5e-5) return;
    placeAt(p, `Point set at ${latLabel(p.lat)}, ${lonLabel(p.lon)}.`);
    if (radius !== null) showCircle(p, radius);
  };

  const [solText, setSolText] = useState("");
  const stopOfSol = () => {
    const n = Number(solText);
    if (solText.trim() === "" || !Number.isInteger(n) || n < 0) {
      setPointErr("Enter a sol as a whole number, 0 or greater.");
      return;
    }
    // The last stop of that sol, else the nearest earlier one.
    let found = -1;
    stops.forEach((w, i) => {
      if (w.sol <= n && (found < 0 || w.sol >= stops[found]!.sol)) found = i;
    });
    if (found < 0) {
      setPointErr(`${rover.name} has no stop on or before sol ${n}.`);
      return;
    }
    setPointErr(null);
    placeOnStop(found);
    const w = stops[found]!;
    setView((v) => centerOn(v, kx, w.lon, w.lat));
  };

  const [radText, setRadText] = useState(near.radiusText);
  const [radErr, setRadErr] = useState<string | null>(near.radiusError);
  const [syncedRad, setSyncedRad] = useState(near.radiusText);
  if (syncedRad !== near.radiusText) {
    setSyncedRad(near.radiusText);
    setRadText(near.radiusText);
    setRadErr(near.radiusError);
  }
  const validateRadius = (text: string) => {
    const r = parseRadius(text);
    setRadErr(r.ok ? null : r.error);
    return r;
  };
  const commitRadius = (text: string) => {
    const r = validateRadius(text);
    if (!r.ok) return;
    const cur = nearRef.current;
    if (cur.radius !== null && cur.radius === r.value) return;
    go({ radius: r.value, page: 1 });
    if (cur.point) showCircle(cur.point, r.value);
  };

  // ----- stops inside the circle -----
  const nearest = point ? nearestStopTo(stops, point) : null;
  const sols = inside.map((o) => o.w.sol);
  const lo = sols.length ? Math.min(...sols) : null;
  const hi = sols.length ? Math.max(...sols) : null;
  const widenTo = nearest ? Math.min(R_MAX, Number((nearest.d * 1.05 + 0.01).toFixed(2))) : R_MAX;

  // ----- drawing -----
  const { W, H } = view;
  const baseD = useMemo(() => pathData(route, route.segs, view, kx), [route, kx, view]);
  const outsideD = useMemo(
    () => dotsPath(point ? stops.filter((_, i) => !insideSet.has(i)) : stops, view, kx),
    [stops, point, insideSet, view, kx],
  );
  const insideD = useMemo(
    () =>
      point
        ? dotsPath(
            inside.map((o) => o.w),
            view,
            kx,
          )
        : "",
    [point, inside, view, kx],
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
  const pin = point ? project(view, kx, point.lon, point.lat) : null;
  const ringPx = radius !== null ? radiusDeg(radius) * view.ppu : 0;
  const hovered = hover !== null ? stops[hover] : null;
  const hoverPt = hovered ? project(view, kx, hovered.lon, hovered.lat) : null;

  return (
    <div data-testid="near-mode">
      <details class="card coverage" open data-testid="near-coverage">
        <summary>What is covered, and what is not</summary>
        <ul>
          {(rover.id === "curiosity"
            ? [CURIOSITY_NOTE, PERSEVERANCE_NOTE]
            : [PERSEVERANCE_NOTE, CURIOSITY_NOTE]
          ).map((t) => (
            <li key={t.key}>{t.text}</li>
          ))}
          <li>
            Photos are matched to the stop where the rover stood, by the same rule as the location
            of a photo: the stop of the same site with the highest drive number up to the photo’s.
            So you find what the rover took while stopped within the radius, not what is visible in
            the frame.
          </li>
          <li>
            The whole work area of a rover is 5–25 km across: from about 10 km nearly everything
            matches.
          </li>
        </ul>
      </details>

      <div class="mp-layout">
        <section class="card mp-card" aria-labelledby="near-map-h">
          <h2 id="near-map-h" class="visually-hidden">
            Map: choose the search point
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
              disabled={!point || radius === null}
              onClick={() => point && radius !== null && showCircle(point, radius)}
            >
              Zoom to circle
            </button>
            <div class="mp-legend" role="group" aria-label="Legend">
              <span>
                <i class="lg-route" />
                Route
              </span>
              <span>
                <i class="lg-stop" />
                Stop
              </span>
              <span>
                <i class="lg-in" />
                Stop inside the circle
              </span>
              <span>
                <i class="lg-ring" />
                Search radius
              </span>
            </div>
          </div>
          <div
            class="mp-wrap"
            ref={mapRef}
            tabIndex={0}
            role="group"
            aria-roledescription="interactive map"
            aria-label="Map. Click to place the search point. With the map focused, the left and right arrow keys move the point to the previous or next stop, plus and minus zoom, Escape clears the point."
            aria-describedby="near-live"
            onKeyDown={onKeyDown}
          >
            <svg
              ref={svgRef}
              class={`near-svg${dragging ? " dragging" : ""}`}
              viewBox={`0 0 ${W} ${H}`}
              role="img"
              aria-label={`${rover.name} route with stops${
                point && radius !== null
                  ? `, the search point and a ${radiusLabel(radius)} radius`
                  : ", no search point yet"
              }`}
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
              <path class="stops-base" d={outsideD} />
              {insideD && <path class="stops-in" d={insideD} />}
              {pin && radius !== null && (
                <circle
                  class="ring"
                  cx={pin[0].toFixed(1)}
                  cy={pin[1].toFixed(1)}
                  r={ringPx.toFixed(1)}
                />
              )}
              {hoverPt && (
                <>
                  <circle
                    class="halo"
                    cx={hoverPt[0].toFixed(1)}
                    cy={hoverPt[1].toFixed(1)}
                    r="8"
                  />
                  <circle
                    class="hover-ring"
                    cx={hoverPt[0].toFixed(1)}
                    cy={hoverPt[1].toFixed(1)}
                    r="8"
                  />
                </>
              )}
              {pin && (
                <g transform={`translate(${pin[0].toFixed(1)} ${pin[1].toFixed(1)})`}>
                  <path class="pin-cross" d="M-10 0H10M0 -10V10" />
                  <circle class="pin-dot" r="5" />
                  <text class="pin-label" x="10" y="-10">
                    {point ? `${fmtCoord(point.lat)}, ${fmtCoord(point.lon)}` : ""}
                  </text>
                </g>
              )}
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
            {!point && <div class="map-hint">Click the map to place the point</div>}
            {hovered && hoverPt && (
              <div
                class="mp-tip"
                role="presentation"
                style={{
                  left: Math.min(Math.max(hoverPt[0] + 14, 4), Math.max(W - 214, 4)),
                  top: Math.max(hoverPt[1] - 70, 4),
                }}
              >
                <strong>Sol {hovered.sol}</strong>
                Site {hovered.site} · drive {hovered.drive}
                <br />
                {latLabel(hovered.lat)}, {lonLabel(hovered.lon)}
                <br />
                Click to use this stop
              </div>
            )}
          </div>
          <div class="mp-foot">
            <span id="near-live" aria-live="polite">
              {live ||
                (point && radius !== null
                  ? `Point ${latLabel(point.lat)}, ${lonLabel(point.lon)}. ${inside.length} stop${inside.length === 1 ? "" : "s"} within ${radiusLabel(radius)}.`
                  : " ")}
            </span>
            <span class="spacer" />
            <span>Plain background: no imagery. Drag to pan, Ctrl + wheel to zoom.</span>
            <span>Positions: JPL MMGIS map layers.</span>
          </div>
        </section>

        <aside class="mp-side" aria-label="Search point and radius">
          <form
            class="card panel"
            noValidate
            aria-labelledby="near-pt-h"
            onSubmit={(e) => {
              e.preventDefault();
              commitPoint(latText, lonText);
            }}
          >
            <h2 id="near-pt-h">Search point</h2>
            <div class="two">
              <div class="field">
                <label for="near-lat">Latitude (°)</label>
                <input
                  id="near-lat"
                  data-testid="near-lat"
                  type="text"
                  inputMode="decimal"
                  autoComplete="off"
                  value={latText}
                  aria-invalid={pointErr && !parseLat(latText).ok ? "true" : undefined}
                  aria-describedby={pointErr ? "near-pt-err" : undefined}
                  onInput={(e) => setLatText(e.currentTarget.value)}
                  onChange={(e) => commitPoint(e.currentTarget.value, lonText, true)}
                />
              </div>
              <div class="field">
                <label for="near-lon">Longitude (°E)</label>
                <input
                  id="near-lon"
                  data-testid="near-lon"
                  type="text"
                  inputMode="decimal"
                  autoComplete="off"
                  value={lonText}
                  aria-invalid={pointErr && !parseLon(lonText).ok ? "true" : undefined}
                  aria-describedby={pointErr ? "near-pt-err" : undefined}
                  onInput={(e) => setLonText(e.currentTarget.value)}
                  onChange={(e) => commitPoint(latText, e.currentTarget.value, true)}
                />
              </div>
              {(pointErr || near.pointError) && (
                <div class="full field-err" id="near-pt-err" role="alert">
                  {pointErr ?? near.pointError}
                </div>
              )}
              <div class="full row" style={{ alignItems: "center" }}>
                <button class="btn btn-primary btn-sm" type="submit">
                  Set point
                </button>
                <button
                  class="btn btn-sm"
                  type="button"
                  disabled={stops.length === 0}
                  onClick={() => stops.length && placeOnStop(stops.length - 1)}
                >
                  Latest stop
                </button>
                <button
                  class="btn btn-sm"
                  type="button"
                  disabled={!point}
                  onClick={() => go({ point: null })}
                >
                  Clear
                </button>
              </div>
              <div class="field">
                <label for="near-by-sol">Or stop of sol</label>
                <input
                  id="near-by-sol"
                  type="number"
                  min={0}
                  step={1}
                  inputMode="numeric"
                  placeholder="e.g. 1500"
                  value={solText}
                  onInput={(e) => setSolText(e.currentTarget.value)}
                />
              </div>
              <div class="field" style={{ justifyContent: "flex-end" }}>
                <button class="btn btn-sm" type="button" onClick={stopOfSol}>
                  Go to stop
                </button>
              </div>
            </div>
            <p class="hint" style={{ margin: "var(--s2) 0 0" }}>
              East longitude is positive. These fields do the same as clicking the map.
            </p>
          </form>

          <form
            class="card panel"
            noValidate
            aria-labelledby="near-rad-h"
            onSubmit={(e) => {
              e.preventDefault();
              commitRadius(radText);
            }}
          >
            <h2 id="near-rad-h">Radius</h2>
            <div class="radius-row">
              <input
                type="range"
                min={0}
                max={100}
                step={1}
                aria-label="Radius in kilometres (logarithmic slider)"
                aria-valuetext={radius !== null ? radiusLabel(radius) : undefined}
                value={radiusToSlider(
                  parseRadius(radText).ok
                    ? (parseRadius(radText) as { value: number }).value
                    : (radius ?? R_DEFAULT),
                )}
                onInput={(e) => {
                  const r = sliderToRadius(Number(e.currentTarget.value));
                  setRadText(String(r));
                  setRadErr(null);
                }}
                onChange={(e) =>
                  commitRadius(String(sliderToRadius(Number(e.currentTarget.value))))
                }
              />
              <div class="field" style={{ flex: "none" }}>
                <label for="near-radius" class="visually-hidden">
                  Radius in kilometres
                </label>
                <input
                  id="near-radius"
                  data-testid="near-radius"
                  type="text"
                  inputMode="decimal"
                  autoComplete="off"
                  value={radText}
                  aria-invalid={radErr ? "true" : undefined}
                  aria-describedby={radErr ? "near-radius-err" : undefined}
                  onInput={(e) => {
                    const text = e.currentTarget.value;
                    setRadText(text);
                    // A valid radius is applied shortly after typing stops; a wrong one is flagged at once.
                    clearTimeout(radTimer.current ?? undefined);
                    if (validateRadius(text).ok) {
                      radTimer.current = setTimeout(() => commitRadius(text), 350);
                    }
                  }}
                  onChange={(e) => {
                    clearTimeout(radTimer.current ?? undefined);
                    commitRadius(e.currentTarget.value);
                  }}
                />
              </div>
              <span class="muted small">km</span>
            </div>
            <div class="ticks" aria-hidden="true">
              <span style={{ left: "0%" }}>0.01</span>
              <span style={{ left: "27%" }}>0.1</span>
              <span style={{ left: "54%" }}>1</span>
              <span style={{ left: "81%" }}>10</span>
              <span style={{ left: "100%", transform: "translateX(-100%)" }}>50</span>
            </div>
            {radErr && (
              <div
                class="field-err"
                id="near-radius-err"
                role="alert"
                data-testid="near-radius-error"
              >
                {radErr}
              </div>
            )}
            <p class="hint" style={{ margin: "var(--s2) 0 0" }}>
              {R_MIN} to {R_MAX} km. The whole work area is 5–25 km across, so from about 10 km
              nearly everything matches.
            </p>
          </form>

          <section class="card panel found" aria-labelledby="near-found-h" aria-live="polite">
            <h2 id="near-found-h">Inside the circle</h2>
            {!point || radius === null ? (
              <span class="muted small">
                {radius === null ? "Fix the radius to see stops." : "Place the point to see stops."}
              </span>
            ) : inside.length === 0 && nearest ? (
              <>
                <span class="big">0 stops</span>
                <span class="small muted">
                  within {radiusLabel(radius)}. The nearest stop (sol {nearest.w.sol}) is{" "}
                  {radiusLabel(nearest.d)} away.
                </span>
                {nearest.d <= R_MAX && (
                  <div class="row">
                    <button
                      class="btn btn-sm"
                      type="button"
                      onClick={() => {
                        go({ radius: widenTo, page: 1 });
                        showCircle(point, widenTo);
                      }}
                    >
                      Widen to {radiusLabel(widenTo)}
                    </button>
                  </div>
                )}
              </>
            ) : (
              <>
                <span class="big">
                  {fmt(inside.length)} stop{inside.length === 1 ? "" : "s"}
                </span>
                <span class="small muted">
                  within {radiusLabel(radius)} · sols {lo}–{hi}
                </span>
              </>
            )}
          </section>
        </aside>
      </div>

      <Results
        rover={rover}
        near={near}
        inside={inside.length}
        hrefWith={hrefWith}
        go={go}
        onWiden={() => {
          if (point) {
            go({ radius: widenTo, page: 1 });
            showCircle(point, widenTo);
          }
        }}
      />
    </div>
  );
}

const CURIOSITY_NOTE = {
  key: "msl",
  text: "About half of Curiosity’s photos have no stop (no site or drive number in the source), so they never match, however large the radius.",
};
const PERSEVERANCE_NOTE = {
  key: "m20",
  text: "Perseverance photos from the landing (sols 0–9) have no stop and are not found.",
};

interface ResultsProps {
  rover: Rover;
  near: NearState;
  inside: number;
  hrefWith: (patch: {
    point?: Point | null;
    radius?: string | number;
    camera?: string;
    solMin?: string;
    solMax?: string;
    sort?: string;
    perPage?: number;
    page?: number;
  }) => string;
  go: (patch: {
    camera?: string;
    solMin?: string;
    solMax?: string;
    sort?: string;
    perPage?: number;
    page?: number;
  }) => void;
  onWiden: () => void;
}

/** Filters (camera, sols, order) and the grid of photos found near the point. */
function Results({ rover, near, inside, hrefWith, go, onWiden }: ResultsProps) {
  const query = useMemo(() => nearQuery(rover.id, near), [rover.id, near]);
  const [draft, setDraft] = useState({
    camera: near.camera,
    solMin: near.solMin,
    solMax: near.solMax,
    sort: near.sort as string,
    perPage: near.perPage,
  });
  const [seen, setSeen] = useState(
    `${near.camera}|${near.solMin}|${near.solMax}|${near.sort}|${near.perPage}`,
  );
  const key = `${near.camera}|${near.solMin}|${near.solMax}|${near.sort}|${near.perPage}`;
  if (seen !== key) {
    setSeen(key);
    setDraft({
      camera: near.camera,
      solMin: near.solMin,
      solMax: near.solMax,
      sort: near.sort,
      perPage: near.perPage,
    });
  }
  const [err, setErr] = useState<string | null>(null);

  // Camera codes are offered once the field is used, not before.
  const [wantCams, setWantCams] = useState(false);
  const [cams] = useAsync<Camera[]>(
    () => (wantCams ? api.cameras(rover.id).catch(() => [] as Camera[]) : Promise.resolve([])),
    [wantCams, rover.id],
  );

  const apply = async (e: Event) => {
    e.preventDefault();
    const isInt = (v: string) => v === "" || /^\d+$/.test(v);
    if (!isInt(draft.solMin) || !isInt(draft.solMax)) {
      setErr("Sols must be whole numbers, 0 or greater.");
      return;
    }
    if (draft.solMin !== "" && draft.solMax !== "" && Number(draft.solMin) > Number(draft.solMax)) {
      setErr("“From sol” must not be greater than “To sol”.");
      return;
    }
    const camera = draft.camera.trim().toUpperCase();
    if (camera) {
      // An unknown code would only come back as a 422: check it against the rover's cameras first.
      const known = await api.cameras(rover.id).catch(() => null);
      if (known && !known.some((c) => c.code === camera)) {
        setErr(
          `${rover.name} has no camera “${camera}”. Choose one of: ${known.map((c) => c.code).join(", ")}.`,
        );
        return;
      }
    }
    setErr(null);
    go({
      camera,
      solMin: draft.solMin,
      solMax: draft.solMax,
      sort: draft.sort,
      perPage: draft.perPage,
      page: 1,
    });
  };
  const heavy = isHeavyNear({ camera: draft.camera.trim() });

  const [result, retry] = useAsync<PhotoPage | null>(
    (signal) => (query ? searchPhotos(query, signal) : Promise.resolve(null)),
    [query ? JSON.stringify(query) : ""],
  );

  const resultsRef = useRef<HTMLElement>(null);
  const firstPage = useRef(true);
  useEffect(() => {
    if (firstPage.current) {
      firstPage.current = false;
      return;
    }
    resultsRef.current?.scrollIntoView?.({ block: "start" });
  }, [near.page]);

  const apiQuery = query as Record<string, string | number> | null;
  const total = result.status === "ok" && result.data ? result.data.total : 0;
  const { pages, capped } =
    result.status === "ok" && result.data
      ? pageLimit(result.data.total, result.data.per_page)
      : { pages: 1, capped: false };

  return (
    <>
      <form class="card refine" noValidate aria-label="Narrow the results" onSubmit={apply}>
        <div class="field">
          <label for="near-cam">Camera</label>
          <input
            id="near-cam"
            type="text"
            list="near-cam-list"
            autoComplete="off"
            placeholder="all cameras"
            value={draft.camera}
            onFocus={() => setWantCams(true)}
            onInput={(e) => setDraft({ ...draft, camera: e.currentTarget.value })}
          />
          <datalist id="near-cam-list">
            {(cams.status === "ok" ? cams.data : []).map((c) => (
              <option key={c.code} value={c.code}>
                {c.name}
              </option>
            ))}
          </datalist>
        </div>
        <div class="field">
          <label for="near-smin">From sol</label>
          <input
            id="near-smin"
            type="number"
            min={0}
            step={1}
            inputMode="numeric"
            placeholder="first"
            value={draft.solMin}
            aria-invalid={err ? "true" : undefined}
            onInput={(e) => setDraft({ ...draft, solMin: e.currentTarget.value })}
          />
        </div>
        <div class="field">
          <label for="near-smax">To sol</label>
          <input
            id="near-smax"
            type="number"
            min={0}
            step={1}
            inputMode="numeric"
            placeholder="last"
            value={draft.solMax}
            aria-invalid={err ? "true" : undefined}
            onInput={(e) => setDraft({ ...draft, solMax: e.currentTarget.value })}
          />
        </div>
        <div class="field">
          <label for="near-sort">Order</label>
          <select
            id="near-sort"
            value={draft.sort}
            onChange={(e) => setDraft({ ...draft, sort: e.currentTarget.value })}
          >
            <option value="-sol">Newest first</option>
            <option value="sol">Oldest first</option>
          </select>
        </div>
        <div class="field">
          <label for="near-per-page">Per page</label>
          <select
            id="near-per-page"
            value={draft.perPage}
            onChange={(e) => setDraft({ ...draft, perPage: Number(e.currentTarget.value) })}
          >
            {NEAR_PER_PAGE.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </div>
        <button class="btn btn-sm" type="submit">
          Apply
        </button>
        {heavy && <span class="badge badge-warn">May be slow</span>}
        {err && (
          <div class="field-err" role="alert" style={{ flexBasis: "100%" }}>
            {err}
          </div>
        )}
      </form>

      <section
        ref={resultsRef}
        aria-labelledby="near-result-title"
        aria-busy={result.status === "loading" && query !== null}
        data-testid="near-results"
      >
        <div class="result-head">
          <h2 id="near-result-title">Photos</h2>
          <span class="muted small" role="status">
            {query === null && ""}
            {query !== null && result.status === "loading" && "Searching…"}
            {query !== null &&
              result.status === "ok" &&
              result.data &&
              `${fmt(result.data.total)} photos at ${fmt(inside)} stop${inside === 1 ? "" : "s"} within ${near.radiusText} km · page ${fmt(result.data.page)} of ${fmt(pages)}`}
          </span>
        </div>
        {query === null && (
          <div class="card empty">
            <h3>
              {near.pointError
                ? "The search point in the link is not valid"
                : "Place a search point"}
            </h3>
            <p class="muted small">
              {near.pointError ??
                "Click the map or a stop, or type the latitude and longitude, to see the photos taken there."}
              {near.radiusError ? ` ${near.radiusError}` : ""}
            </p>
          </div>
        )}
        {query !== null && result.status === "loading" && (
          <GridSkeleton count={Math.min(near.perPage, 12)} />
        )}
        {query !== null && result.status === "error" && (
          <NearFailure error={result.error} retry={retry} onFirstPage={() => go({ page: 1 })} />
        )}
        {query !== null && result.status === "ok" && result.data && result.data.total === 0 && (
          <div class="card empty">
            {inside === 0 ? (
              <>
                <h3>No stops within {near.radiusText} km of this point</h3>
                <p class="muted small">
                  Photos are matched to the stops where the rover stood, so a point away from the
                  route finds nothing. Move the point onto the route, or widen the radius.
                </p>
                <div class="row">
                  <button class="btn btn-sm" type="button" onClick={onWiden}>
                    Widen the radius
                  </button>
                </div>
              </>
            ) : (
              <>
                <h3>
                  {inside} stop{inside === 1 ? "" : "s"} here, but no photos match your filters
                </h3>
                <ul class="reasons">
                  <li>
                    {near.camera
                      ? `Camera ${near.camera} may not have shot at these stops.`
                      : "The sol range may exclude every stop in the circle."}
                  </li>
                  <li>
                    {rover.id === "curiosity"
                      ? "About half of Curiosity’s photos have no stop and cannot be found by place."
                      : "Perseverance photos from the landing (sols 0–9) have no stop."}
                  </li>
                </ul>
              </>
            )}
          </div>
        )}
        {query !== null && result.status === "ok" && result.data && result.data.total > 0 && (
          <>
            {capped && (
              <div class="note warn" style={{ marginBottom: "var(--s3)" }}>
                Only the first {fmt(MAX_BROWSABLE)} photos can be browsed ({fmt(pages)} pages at{" "}
                {result.data.per_page} per page). Use a smaller radius or a sol range to reach the
                rest.
              </div>
            )}
            {result.data.items.length === 0 ? (
              <div class="card empty">
                <h3>This page is empty</h3>
                <p class="muted small">
                  There are {fmt(total)} photos, but not this many pages.{" "}
                  <Link href={hrefWith({ page: 1 })}>Go to page 1</Link>
                </p>
              </div>
            ) : (
              <>
                <ul class="photo-grid">
                  {result.data.items.map((p) => (
                    <PhotoTile key={p.id} photo={p} />
                  ))}
                </ul>
                <Pager
                  page={result.data.page}
                  pages={pages}
                  hrefFor={(p) => hrefWith({ page: p })}
                />
              </>
            )}
          </>
        )}
      </section>
      {apiQuery && (
        <p class="api-line">
          API: <code>{requestLine("/photos", apiQuery)}</code>
        </p>
      )}
    </>
  );
}

function NearFailure({
  error,
  retry,
  onFirstPage,
}: {
  error: unknown;
  retry: () => void;
  onFirstPage: () => void;
}) {
  const busy =
    error instanceof ApiError &&
    error.status === 503 &&
    (error.code === "service_busy" || error.retryAfter !== null);
  if (busy) {
    return (
      <BusyAlert
        error={error}
        onRetry={retry}
        testId="near-busy"
        retryTestId="near-retry"
        tips={[
          "use a smaller radius;",
          "leave the camera and sol-range fields empty, or pick a single sol;",
          "try again in a moment.",
        ]}
      />
    );
  }
  const pageIssue = describeError(error).fields.some((f) => f.label === "Page");
  return (
    <ErrorAlert
      error={error}
      onRetry={retry}
      title="We couldn’t run this search"
      actions={
        pageIssue ? (
          <button class="btn btn-sm" type="button" onClick={onFirstPage}>
            Back to page 1
          </button>
        ) : undefined
      }
    />
  );
}
