import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { api } from "../api/client";
import type { PhotoLocation, Traverse } from "../api/types";
import { useAsync } from "../lib/hooks";
import {
  fitView,
  flattenTraverse,
  latLabel,
  lonLabel,
  M_PER_DEG,
  metres,
  pathData,
  project,
  scaleBar,
  scaleLabel,
  type Bounds,
} from "../lib/mapgeo";
import { Link } from "../router";
import { mapHref } from "../lib/maplinks";

const W = 300;
const H = 190;
/** About the length of one drive: the radius of the "approximate" circle, metres. */
const DRIVE_M = 300;

/** Mini-map geometry: the whole route plus the photo position, fitted into 300 x 190. */
export function miniMapGeometry(traverse: Traverse, loc: { lat: number; lon: number }) {
  const route = flattenTraverse(traverse);
  let minLon = loc.lon;
  let maxLon = loc.lon;
  let minLat = loc.lat;
  let maxLat = loc.lat;
  for (let i = 0; i < route.lons.length; i++) {
    minLon = Math.min(minLon, route.lons[i]!);
    maxLon = Math.max(maxLon, route.lons[i]!);
    minLat = Math.min(minLat, route.lats[i]!);
    maxLat = Math.max(maxLat, route.lats[i]!);
  }
  const kx = Math.cos((((minLat + maxLat) / 2) * Math.PI) / 180);
  const bounds: Bounds = { minX: minLon * kx, maxX: maxLon * kx, minY: -maxLat, maxY: -minLat };
  const view = fitView(bounds, W, H, 0.06);
  const d = pathData(route, route.segs, view, kx, 0);
  const [x, y] = project(view, kx, loc.lon, loc.lat);
  return {
    d,
    x,
    y,
    view,
    bar: scaleBar(view.ppu, 90),
    uncertaintyPx: (DRIVE_M / M_PER_DEG) * view.ppu,
  };
}

/** True once the element has scrolled near the viewport (the route is large: fetch it lazily). */
function useNearViewport(ref: { current: Element | null }): boolean {
  const [near, setNear] = useState(typeof IntersectionObserver === "undefined");
  useEffect(() => {
    const el = ref.current;
    if (near || !el) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) setNear(true);
      },
      { rootMargin: "200px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [near, ref]);
  return near;
}

function MiniMap({ rover, loc, name }: { rover: string; loc: PhotoLocation; name: string }) {
  const holder = useRef<HTMLDivElement>(null);
  const near = useNearViewport(holder);
  const [state] = useAsync(
    () => (near ? api.traverse(rover) : new Promise<never>(() => {})),
    [rover, near],
  );
  const geo = useMemo(
    () =>
      state.status === "ok" && state.data.available && state.data.features.length
        ? miniMapGeometry(state.data, loc)
        : null,
    [state, loc],
  );
  if (state.status === "loading") {
    return (
      <div
        ref={holder}
        class="loc-map loc-map-ph skeleton"
        role="status"
        aria-label="Loading the route"
      />
    );
  }
  if (!geo) return null; // the text below still tells where it was
  return (
    <svg
      class="loc-map"
      viewBox={`0 0 ${W} ${H}`}
      role="img"
      aria-label={`${name} route with the position of this photo marked`}
    >
      <path class="loc-route" d={geo.d} />
      {!loc.exact && (
        <circle
          class="loc-unc"
          cx={geo.x.toFixed(1)}
          cy={geo.y.toFixed(1)}
          r={Math.max(geo.uncertaintyPx, 10).toFixed(1)}
        />
      )}
      <circle class="loc-ring" cx={geo.x.toFixed(1)} cy={geo.y.toFixed(1)} r="7" />
      <circle class="loc-pt" cx={geo.x.toFixed(1)} cy={geo.y.toFixed(1)} r="4" />
      <path
        class="loc-scale"
        d={`M10 ${H - 16}V${H - 10}H${(10 + geo.bar.px).toFixed(1)}V${H - 16}`}
      />
      <text class="loc-scale-t" x="10" y={H - 20}>
        {scaleLabel(geo.bar.km)}
      </text>
      <text class="loc-txt" x={W - 8} y="14" text-anchor="end">
        N ↑
      </text>
    </svg>
  );
}

/** "Location" block of the photo card (PhotoDetail.location); the caller hides it when null. */
export function LocationBlock({
  rover,
  roverName,
  loc,
}: {
  rover: string;
  roverName: string;
  loc: PhotoLocation;
}) {
  return (
    <section aria-labelledby="loc-h">
      <h2 id="loc-h">Location</h2>
      <MiniMap rover={rover} loc={loc} name={roverName} />
      <div class="row" style={{ margin: "var(--s2) 0", alignItems: "center" }}>
        {loc.exact ? (
          <>
            <span class="badge badge-active">Exact</span>
            <span class="small muted">taken at this stop</span>
          </>
        ) : (
          <span class="badge badge-warn">Approximate</span>
        )}
      </div>
      <dl>
        <dt>Position</dt>
        <dd>
          {latLabel(loc.lat)}, {lonLabel(loc.lon)}
        </dd>
        <dt>Elevation</dt>
        <dd>{loc.elev_geoid === null ? "—" : `${metres(loc.elev_geoid)} above areoid`}</dd>
        <dt>Stop</dt>
        <dd>
          Sol {loc.sol} · site {loc.site} · drive {loc.drive}
        </dd>
      </dl>
      <p class="loc-note">
        {loc.exact
          ? "Position of the stop where this frame was taken."
          : `Taken while driving after the stop at site ${loc.site}, drive ${loc.drive} (sol ${loc.sol}), or after the last published stop. The marked position is that stop; the real one can differ by up to one drive, often hundreds of metres.`}
      </p>
      <p class="small" style={{ margin: "var(--s2) 0 0" }}>
        <Link href={mapHref(rover, { stop: loc })}>Show on the route map</Link>
      </p>
    </section>
  );
}
