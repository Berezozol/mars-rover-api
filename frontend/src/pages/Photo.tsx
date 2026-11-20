import { Fragment } from "preact";
import { useLayoutEffect, useState } from "preact/hooks";
import { api, errorFromResponse, imageEndpoint, NETWORK_ERROR, ApiError } from "../api/client";
import { describeError } from "../api/errors";
import {
  FILTER_SAMPLE_TYPES,
  type ImageSize,
  type InSol,
  type PhotoDetail,
  type PhotoQuery,
} from "../api/types";
import { FlightBadge, FlightBlock } from "../components/FlightBlock";
import { SampleBlock } from "../components/SampleBlock";
import { LocationBlock } from "../components/LocationBlock";
import { photoHref } from "../components/PhotoTile";
import { PhotoTile } from "../components/PhotoTile";
import { ErrorAlert, PageSkeleton } from "../components/States";
import { dateTimeUtc, hoursBetween, safeHref, timeOf } from "../lib/format";
import { feedPath, hasFeed } from "../lib/feed";
import { useAsync, useRovers, useTitle } from "../lib/hooks";
import { Link, qs, safeDecode, useQuery } from "../router";

const SIZES: { value: ImageSize; label: string }[] = [
  { value: "small", label: "Small" },
  { value: "medium", label: "Medium" },
  { value: "large", label: "Large" },
  { value: "full", label: "Full" },
];

/** `images.full` that points at our own on-demand converter (Spirit and Opportunity). */
export function isConverted(photo: PhotoDetail): boolean {
  try {
    const path = new URL(photo.images.full, window.location.origin).pathname;
    return /^\/api\/v2\/photos\/\d+\/image$/.test(path);
  } catch {
    return false;
  }
}

/** Pixel size of the full frame from `width`/`height` (both set or both null). */
export function dimensionOf(photo: {
  width?: number | null;
  height?: number | null;
}): { w: number; h: number } | null {
  const { width, height } = photo;
  return width && height && width > 0 && height > 0 ? { w: width, h: height } : null;
}

/** Only list pages of our own interface are accepted as a "from" context. */
export function parseFrom(from: string | null): { rover: string; query: PhotoQuery } | null {
  if (!from || !from.startsWith("/rovers/") || from.startsWith("//")) return null;
  let url: URL;
  try {
    url = new URL(from, "http://local.invalid");
  } catch {
    return null;
  }
  const parts = url.pathname.split("/").filter(Boolean);
  if (parts.length !== 2 || parts[0] !== "rovers" || !parts[1]) return null;
  const q = url.searchParams;
  const num = (k: string) => (/^\d+$/.test(q.get(k) ?? "") ? Number(q.get(k)) : undefined);
  const raw = (k: string) => q.get(k) ?? undefined;
  const query: PhotoQuery = {
    sol: num("sol"),
    earth_date: q.get("earth_date") ?? undefined,
    camera: q.get("camera") ?? undefined,
    sample_type: q.get("sample_type") ?? undefined,
    // Stop filters are passed as-is, even when invalid: the API answers 422 and says why.
    site: raw("site"),
    drive_from: raw("drive_from"),
    drive_to: raw("drive_to"),
    page: num("page") ?? 1,
    per_page: num("per_page") ?? 24,
  };
  return { rover: safeDecode(parts[1]).toLowerCase(), query };
}

function withPage(from: string, page: number): string {
  const url = new URL(from, "http://local.invalid");
  if (page > 1) url.searchParams.set("page", String(page));
  else url.searchParams.delete("page");
  return `${url.pathname}${url.search}`;
}

interface Neighbors {
  newer: { id: number; from: string | undefined } | null;
  older: { id: number; from: string | undefined } | null;
  /** null when the photo itself is outside the camera / sample-type filter of the list. */
  position: number | null;
  total: number;
  /** "sol" for sol lists (from in_sol), "list" for Earth-date lists. */
  scope: "sol" | "list";
}

/** What the list the visitor came from tells us (our own list addresses only). */
export interface ListContext {
  /** The validated list address, or null if there was none (or it was foreign). */
  from: string | null;
  /** Earth-date lists cannot use `in_sol`: they are walked page by page. */
  byDate: boolean;
  /** Filters sent to `GET /photos/{id}` so that `in_sol` follows the list's camera / sample type. */
  filters: { camera?: string; sample_type?: string };
}

export function listContext(from: string | null): ListContext {
  const ctx = parseFrom(from);
  if (!from || !ctx) return { from: null, byDate: false, filters: {} };
  // Earth-date and stop lists are not a single sol: `in_sol` cannot describe them, they are walked.
  if (ctx.query.earth_date !== undefined || ctx.query.site !== undefined) {
    return { from, byDate: true, filters: {} };
  }
  const camera = ctx.query.camera?.toUpperCase();
  const st = ctx.query.sample_type;
  return {
    from,
    byDate: false,
    filters: {
      camera: camera || undefined,
      sample_type: st && (FILTER_SAMPLE_TYPES as readonly string[]).includes(st) ? st : undefined,
    },
  };
}

/** Newer/older neighbours from `in_sol` (the contract's order of the sol's list). */
export function neighborsFromInSol(inSol: InSol, from: string | null): Neighbors {
  const f = from ?? undefined;
  return {
    newer: inSol.newer_id !== null ? { id: inSol.newer_id, from: f } : null,
    older: inSol.older_id !== null ? { id: inSol.older_id, from: f } : null,
    position: inSol.position,
    total: inSol.total,
    scope: "sol",
  };
}

/** Earth-date and stop lists: neighbours inside the list page the visitor came from (edges fetch one more page). */
async function loadDateNeighbors(photo: PhotoDetail, from: string): Promise<Neighbors | null> {
  const ctx = parseFrom(from);
  if (!ctx || ctx.rover !== photo.rover) return null;
  const q = ctx.query;
  const page = q.page ?? 1;
  const perPage = q.per_page ?? 24;
  const data = await api.photos(ctx.rover, q);
  const idx = data.items.findIndex((p) => p.id === photo.id);
  if (idx < 0) return null;
  let newer = idx > 0 ? { id: data.items[idx - 1]!.id, from } : null;
  let older = idx < data.items.length - 1 ? { id: data.items[idx + 1]!.id, from } : null;
  if (!newer && page > 1) {
    const prev = await api.photos(ctx.rover, { ...q, page: page - 1 });
    const last = prev.items[prev.items.length - 1];
    if (last) newer = { id: last.id, from: withPage(from, page - 1) };
  }
  if (!older && page * perPage < data.total) {
    const next = await api.photos(ctx.rover, { ...q, page: page + 1 });
    const first = next.items[0];
    if (first) older = { id: first.id, from: withPage(from, page + 1) };
  }
  return {
    newer,
    older,
    position: (page - 1) * perPage + idx + 1,
    total: data.total,
    scope: "list",
  };
}

/** The list address without the camera / sample type of its filters. */
export function withoutFilters(from: string): string {
  const url = new URL(from, "http://local.invalid");
  url.searchParams.delete("camera");
  url.searchParams.delete("sample_type");
  return `${url.pathname}${url.search}`;
}

export function PhotoPage({ id }: { id: string }) {
  const from = useQuery().get("from");
  const [state, retry] = useAsync(
    async (signal) => {
      const ctx = listContext(from);
      const filtered = !!(ctx.filters.camera || ctx.filters.sample_type);
      let photo: PhotoDetail;
      let usedFilters = filtered;
      try {
        photo = await api.photo(id, signal, ctx.filters);
      } catch (e) {
        // A stale or hand-made address with a camera this rover doesn't have: show the photo anyway.
        if (e instanceof ApiError && e.status === 422 && filtered) {
          usedFilters = false;
          photo = await api.photo(id, signal);
        } else throw e;
      }
      // A list of another rover says nothing about this photo.
      const foreign = ctx.from !== null && parseFrom(ctx.from)?.rover !== photo.rover;
      if (foreign && usedFilters) {
        usedFilters = false;
        photo = await api.photo(id, signal);
      }
      let final: ListContext = ctx;
      if (foreign) final = { from: null, byDate: false, filters: {} };
      else if (filtered && !usedFilters && ctx.from) {
        final = { from: withoutFilters(ctx.from), byDate: false, filters: {} };
      }
      return { photo, ctx: final };
    },
    [id, from],
  );
  useTitle(
    state.status === "ok"
      ? `Photo ${state.data.photo.id} — ${state.data.photo.camera.code} — Sol Index`
      : "Photo — Sol Index",
  );
  if (state.status === "loading") return <PageSkeleton label="Loading photo…" />;
  if (state.status === "error") {
    return (
      <ErrorAlert
        error={state.error}
        onRetry={retry}
        actions={
          <Link class="btn btn-sm" href="/">
            Back to rovers
          </Link>
        }
      />
    );
  }
  return <PhotoView photo={state.data.photo} ctx={state.data.ctx} />;
}

function PhotoView({ photo, ctx }: { photo: PhotoDetail; ctx: ListContext }) {
  const roverName = photo.rover.charAt(0).toUpperCase() + photo.rover.slice(1);
  const solHref = `/rovers/${photo.rover}${qs({ sol: photo.sol })}`;
  const camHref = `/rovers/${photo.rover}${qs({ sol: photo.sol, camera: photo.camera.code })}`;
  const from = ctx.from;
  const [nb] = useAsync(
    () =>
      ctx.byDate && from ? loadDateNeighbors(photo, from).catch(() => null) : Promise.resolve(null),
    [photo.id, from, ctx.byDate],
  );
  let neighbors: Neighbors | null = null;
  if (ctx.byDate) neighbors = nb.status === "ok" ? nb.data : null;
  else if (photo.in_sol) neighbors = neighborsFromInSol(photo.in_sol, from);
  const filterText = [ctx.filters.camera, ctx.filters.sample_type].filter(Boolean).join(" · ");
  const sameFrom = from ?? camHref;

  return (
    <>
      <nav class="crumbs" aria-label="Breadcrumb">
        <Link href="/">Rovers</Link> › <Link href={`/rovers/${photo.rover}`}>{roverName}</Link> ›{" "}
        <Link href={solHref}>Sol {photo.sol}</Link> ›{" "}
        <Link href={camHref}>{photo.camera.code}</Link> ›{" "}
        <span aria-current="page">#{photo.id}</span>
      </nav>

      <div class="layout">
        <div>
          <Viewer key={photo.id} photo={photo} />

          <nav class="seq" aria-label={`Photos on sol ${photo.sol}`}>
            {neighbors?.newer ? (
              <Link
                class="btn"
                href={photoHref(neighbors.newer.id, neighbors.newer.from)}
                rel="prev"
              >
                ← Newer
              </Link>
            ) : (
              <span class="btn" aria-disabled="true">
                ← Newer
              </span>
            )}
            <span class="pos">
              {positionText(neighbors, photo.sol, photo.camera.code, filterText)}
            </span>
            {neighbors?.older ? (
              <Link
                class="btn"
                href={photoHref(neighbors.older.id, neighbors.older.from)}
                rel="next"
              >
                Older →
              </Link>
            ) : (
              <span class="btn" aria-disabled="true">
                Older →
              </span>
            )}
          </nav>

          <SameCamera photo={photo} from={sameFrom} />
        </div>

        <Meta photo={photo} roverName={roverName} solHref={solHref} />
      </div>
    </>
  );
}

/** "17 of 52 on sol 2031 · SUPERCAM_RMI" (or the date-list / no-data variants). */
export function positionText(
  n: Neighbors | null,
  sol: number,
  camera: string,
  filterText: string,
): string {
  if (!n) return `Sol ${sol} · ${camera}`;
  if (n.position === null) return `Sol ${sol} · outside the filter of this list`;
  const counts = `${n.position.toLocaleString("en-US")} of ${n.total.toLocaleString("en-US")}`;
  if (n.scope === "list") return `${counts} in this list`;
  return `${counts} on sol ${sol}${filterText ? ` · ${filterText}` : ""}`;
}

function Viewer({ photo }: { photo: PhotoDetail }) {
  const available = (s: ImageSize) => (s === "full" ? true : !!photo.images[s]);
  const converted = isConverted(photo);
  const onlyFull = !photo.images.small && !photo.images.medium && !photo.images.large;
  const defaultSize: ImageSize = photo.images.medium
    ? "medium"
    : photo.images.small
      ? "small"
      : "full";
  const [size, setSize] = useState<ImageSize>(defaultSize);

  const dim = dimensionOf(photo);
  const alt = `${photo.camera.name} frame from ${roverName(photo)}, sol ${photo.sol}, ${timeOf(photo.taken_at)} UTC`;
  const url = imageEndpoint(photo.id, size); // "Open image" and the converter
  // Ordinary sizes are shown straight from images.* (no extra request through our API).
  const directUrl = photo.images[size] ?? photo.images.full;
  // Full frames of Spirit/Opportunity are converted on demand: fetch them so errors can be explained.
  const viaFetch = converted && size === "full";

  return (
    <div class="card viewer">
      <div class="stage">
        {viaFetch ? (
          <ConvertedImage key={`${photo.id}-full`} url={url} alt={alt} dim={dim} />
        ) : (
          <DirectImage
            key={`${photo.id}-${size}`}
            url={directUrl}
            alt={alt}
            dim={size === "full" ? dim : null}
            ratio={size === "full" ? null : dim}
          />
        )}
      </div>

      <div class="viewer-bar">
        <div class="field inline-field">
          <span class="label" id="size-l">
            Size
          </span>
          <div class="segmented" role="radiogroup" aria-labelledby="size-l">
            {SIZES.map(({ value, label }) => (
              <Fragment key={value}>
                <input
                  type="radio"
                  name="size"
                  id={`sz-${value}`}
                  value={value}
                  checked={size === value}
                  disabled={!available(value)}
                  onChange={() => setSize(value)}
                />
                <label
                  for={`sz-${value}`}
                  title={available(value) ? undefined : "Not published for this rover"}
                >
                  {label}
                </label>
              </Fragment>
            ))}
          </div>
        </div>
        <span class="spacer" />
        <a class="btn btn-sm" href={url} target="_blank" rel="noopener noreferrer">
          Open image ↗
        </a>
      </div>
      <p class="note" style={{ margin: "8px 0 0" }}>
        {converted
          ? "Small is the archive preview. Full is rendered on demand from the original data file, so it can take a while the first time."
          : onlyFull
            ? "Raw images of this rover are published in one size only, so Small, Medium and Large are unavailable."
            : "Full is the original file as published by the source."}
      </p>
      <p class="credit-line">
        <span class="muted">Credit:</span> <strong>{photo.credit ?? "not provided"}</strong>
      </p>
    </div>
  );
}

function roverName(photo: PhotoDetail): string {
  return photo.rover.charAt(0).toUpperCase() + photo.rover.slice(1);
}

function DirectImage({
  url,
  alt,
  dim,
  ratio,
}: {
  url: string;
  alt: string;
  /** Pixel size of the shown file (reserves its box). */
  dim: { w: number; h: number } | null;
  /** Proportions only, for sizes whose pixel size is unknown. */
  ratio: { w: number; h: number } | null;
}) {
  const [phase, setPhase] = useState<"loading" | "ok" | "error">("loading");
  const [attempt, setAttempt] = useState(0);
  return (
    <>
      {phase === "loading" && (
        <div class="stage-state" role="status">
          Loading image…
        </div>
      )}
      {phase === "error" && (
        <div class="stage-state" role="alert">
          <strong>This image couldn’t be loaded.</strong>
          <p>The image server may be busy or the file is missing.</p>
          <button
            class="btn btn-sm"
            type="button"
            onClick={() => (setPhase("loading"), setAttempt((a) => a + 1))}
          >
            Try again
          </button>
        </div>
      )}
      <img
        key={attempt}
        src={url}
        alt={alt}
        width={dim?.w}
        height={dim?.h}
        decoding="async"
        style={{
          ...(ratio ? { aspectRatio: `${ratio.w} / ${ratio.h}` } : {}),
          ...(phase === "ok" ? {} : { display: "none" }),
        }}
        onLoad={() => setPhase("ok")}
        onError={() => setPhase("error")}
      />
    </>
  );
}

function ConvertedImage({
  url,
  alt,
  dim,
}: {
  url: string;
  alt: string;
  dim: { w: number; h: number } | null;
}) {
  const [state, retry] = useAsync(
    async (signal) => {
      let res: Response;
      try {
        res = await fetch(url, { signal });
      } catch (e) {
        if (signal.aborted) throw e;
        throw new ApiError({ status: 0, code: NETWORK_ERROR, message: "Network error" });
      }
      if (!res.ok) throw await errorFromResponse(res);
      const blob = await res.blob();
      const objectUrl = URL.createObjectURL(blob);
      if (signal.aborted) {
        URL.revokeObjectURL(objectUrl); // cancelled while the body was downloading
        throw new DOMException("Aborted", "AbortError");
      }
      return objectUrl;
    },
    [url],
  );
  const objectUrl = state.status === "ok" ? state.data : null;
  // Layout effect: the object URL is registered for release in the same commit that shows it.
  useLayoutEffect(
    () => () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    },
    [objectUrl],
  );

  if (state.status === "loading") {
    return (
      <div class="stage-state" role="status" aria-live="polite">
        <strong>Preparing the full-size frame…</strong>
        <p>
          It is rendered from the original archive data on first request and can take up to a
          minute.
        </p>
      </div>
    );
  }
  if (state.status === "error") {
    const v = describeError(state.error);
    return (
      <div class="stage-state" role="alert">
        <strong>{v.title}</strong>
        <p>{v.message}</p>
        {v.retryable && (
          <button class="btn btn-sm" type="button" onClick={retry}>
            Try again
          </button>
        )}
        <p class="xsmall">
          Error code: <code>{v.code}</code>
        </p>
      </div>
    );
  }
  return <img src={state.data} alt={alt} width={dim?.w} height={dim?.h} decoding="async" />;
}

function SameCamera({ photo, from }: { photo: PhotoDetail; from: string }) {
  const [state] = useAsync(
    () =>
      api
        .photos(photo.rover, { sol: photo.sol, camera: photo.camera.code, per_page: 12 })
        .catch(() => null),
    [photo.id],
  );
  if (state.status !== "ok" || !state.data || state.data.items.length < 2) return null;
  const { items, total } = state.data;
  return (
    <section style={{ marginTop: "var(--s5)" }} aria-labelledby="same-cam">
      <h2 id="same-cam" style={{ fontSize: "var(--fs-md)" }}>
        {photo.camera.code} on sol {photo.sol}{" "}
        <span class="muted small">· {total.toLocaleString("en-US")} photos</span>
      </h2>
      <ul class="photo-grid" style={{ marginTop: "var(--s3)" }}>
        {items.slice(0, 6).map((p) => (
          <PhotoTile key={p.id} photo={p} from={from} />
        ))}
      </ul>
    </section>
  );
}

function Meta({
  photo,
  roverName: rn,
  solHref,
}: {
  photo: PhotoDetail;
  roverName: string;
  solHref: string;
}) {
  const [rovers] = useRovers();
  const feed =
    rovers.status === "ok" && hasFeed(rovers.data.find((r) => r.id === photo.rover) ?? {});
  const link = safeHref(photo.source.link);
  const isData = !!link && /\.img$/i.test(link);
  return (
    <aside class="card panel meta" aria-labelledby="ph-title">
      <section>
        <h1 id="ph-title">{photo.title ?? `${photo.camera.name}, sol ${photo.sol}`}</h1>
        <div class="row" style={{ gap: 8, marginBottom: "var(--s3)" }}>
          <span class="badge no-dot">{photo.sample_type}</span>
          <span class="badge no-dot mono">#{photo.id}</span>
          <FlightBadge photo={photo} />
        </div>
        <dl>
          <dt>Rover</dt>
          <dd>
            <Link href={`/rovers/${photo.rover}`}>{rn}</Link>
          </dd>
          <dt>Sol</dt>
          <dd>
            <Link href={solHref}>{photo.sol}</Link>
          </dd>
          <dt>Earth date</dt>
          <dd>{photo.earth_date}</dd>
          <dt>Taken at</dt>
          <dd>
            <time dateTime={photo.taken_at}>{dateTimeUtc(photo.taken_at)}</time>
          </dd>
          {photo.local_time && (
            <>
              <dt>Local time</dt>
              <dd>
                {photo.local_time.time}{" "}
                <abbr
                  title={
                    photo.local_time.kind === "ltst"
                      ? "Local true solar time on Mars"
                      : "Local mean solar time on Mars"
                  }
                >
                  {photo.local_time.kind.toUpperCase()}
                </abbr>
              </dd>
            </>
          )}
          <dt>Camera</dt>
          <dd>
            <span class="mono">{photo.camera.code}</span>
            <br />
            <span class="muted">{photo.camera.name}</span>
            {feed && (
              <>
                <br />
                <a
                  class="rss-link"
                  href={feedPath(photo.rover, { camera: photo.camera.code })}
                  title="New photos from this camera, in an RSS reader"
                >
                  <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true">
                    <circle cx="3" cy="13" r="1.8" fill="currentColor" />
                    <path
                      d="M1.5 7.2a7.3 7.3 0 0 1 7.3 7.3M1.5 2.2a12.3 12.3 0 0 1 12.3 12.3"
                      fill="none"
                      stroke="currentColor"
                      stroke-width="2"
                      stroke-linecap="round"
                    />
                  </svg>{" "}
                  Subscribe to this camera
                </a>
              </>
            )}
          </dd>
          <dt>Sample type</dt>
          <dd>{photo.sample_type}</dd>
          {photo.site !== null && photo.drive !== null && (
            <>
              <dt>Site / drive</dt>
              <dd>
                {photo.site} / {photo.drive}
              </dd>
            </>
          )}
          {photo.received_at && (
            <>
              <dt>Received</dt>
              <dd>
                <time dateTime={photo.received_at}>{dateTimeUtc(photo.received_at)}</time>{" "}
                <span class="muted">
                  ({hoursBetween(photo.taken_at, photo.received_at)} h after capture)
                </span>
              </dd>
            </>
          )}
        </dl>
      </section>

      <FlightBlock photo={photo} />

      <SampleBlock photo={photo} />

      {photo.location && <LocationBlock rover={photo.rover} roverName={rn} loc={photo.location} />}

      {photo.caption && (
        <section>
          <h2>Caption</h2>
          <p class="small">{photo.caption}</p>
        </section>
      )}

      <section>
        <h2>Credit &amp; source</h2>
        <dl>
          <dt>Credit</dt>
          <dd>{photo.credit ?? "Not provided"}</dd>
          <dt>Source ID</dt>
          <dd class="mono xsmall">{photo.source.id}</dd>
        </dl>
        {link && (
          <p style={{ marginTop: "var(--s3)" }}>
            <a href={link} target="_blank" rel="noopener noreferrer">
              {isData ? "Original data file (PDS) ↗" : "View on the mission’s raw-image page ↗"}
            </a>
          </p>
        )}
        <p class="note">
          Sol Index is unofficial; the source page is the authority for this image.
        </p>
      </section>

      <section>
        <details>
          <summary class="small">
            Technical fields (<code>extra</code>)
          </summary>
          <pre class="raw">{JSON.stringify(photo.extra, null, 2)}</pre>
        </details>
        <details style={{ marginTop: 8 }}>
          <summary class="small">Use via API</summary>
          <pre class="raw">
            {`GET /api/v2/photos/${photo.id}\nGET /api/v2/photos/${photo.id}/image?size=large`}
          </pre>
        </details>
      </section>
    </aside>
  );
}
