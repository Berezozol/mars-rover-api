import { useEffect, useRef, useState } from "preact/hooks";
import { api, type HealthRover, type HealthSync } from "../api/client";
import { PageSkeleton } from "../components/States";
import { useAsync, useTitle } from "../lib/hooks";

const REFRESH_MS = 60_000;

const roverName = (slug: string) => slug.charAt(0).toUpperCase() + slug.slice(1);

/** "5 minutes ago" style text; coarse on purpose, the exact UTC time is shown beside it. */
export function relativeTime(iso: string, now: number = Date.now()): string {
  if (!Number.isFinite(Date.parse(iso))) return iso;
  const sec = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  if (sec < 60) return "just now";
  const unit = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"} ago`;
  if (sec < 3600) return unit(Math.floor(sec / 60), "minute");
  if (sec < 86400) return unit(Math.floor(sec / 3600), "hour");
  return unit(Math.floor(sec / 86400), "day");
}

function utc(iso: string): string {
  return iso.replace("T", " ").replace(/(\.\d+)?(Z|\+00:00)$/, "") + " UTC";
}

const ICONS = { ok: "✓", stale: "!", degraded: "✕" } as const;

function overall(data: HealthSync): { text: string; kind: "ok" | "warn" | "err" } {
  if (data.status === "ok") return { text: "All systems normal", kind: "ok" };
  if (data.status === "stale") return { text: "Data may be behind", kind: "warn" };
  return { text: "Service degraded: database unavailable", kind: "err" };
}

function RoverCard({ r }: { r: HealthRover }) {
  return (
    <section class="card panel status-card" data-testid={`status-rover-${r.rover}`}>
      <h2 class="status-card-title">{roverName(r.rover)}</h2>
      <dl class="status-dl">
        <dt>Last successful sync</dt>
        <dd>
          {r.last_success_at ? (
            <>
              {relativeTime(r.last_success_at)}{" "}
              <span class="muted small">
                (<time dateTime={r.last_success_at}>{utc(r.last_success_at)}</time>)
              </span>
            </>
          ) : (
            "never"
          )}
        </dd>
        <dt>Newest photo</dt>
        <dd>
          {r.newest_photo_date ? (
            <time dateTime={r.newest_photo_date}>{r.newest_photo_date}</time>
          ) : (
            "no photos"
          )}
        </dd>
      </dl>
      {r.sync_stale && (
        <p class="badge badge-warn status-flag" data-testid="status-sync-late">
          <span aria-hidden="true">! </span>Sync is late: no successful run in the last 24 hours.
        </p>
      )}
      {r.photos_stale && (
        <div class="badge badge-warn status-flag" data-testid="status-photos-late">
          <span aria-hidden="true">! </span>No new photos for 21+ days
          <p class="small status-note">
            JPL publishes images with a delay, and while Mars is behind the Sun there is no contact
            with the rovers for up to two weeks.
          </p>
        </div>
      )}
      {!r.sync_stale && !r.photos_stale && (
        <p class="small muted">
          <span aria-hidden="true">✓ </span>Up to date
        </p>
      )}
    </section>
  );
}

export function Status() {
  useTitle("Status — Sol Index");
  const checkedAt = useRef(0);
  const [state, reload] = useAsync(async (signal) => {
    try {
      return await api.healthSync(signal);
    } finally {
      if (!signal.aborted) checkedAt.current = Date.now();
    }
  }, []);
  const [checked, setChecked] = useState<Date | null>(null);
  const [last, setLast] = useState<HealthSync | null>(null);
  const [failedAt, setFailedAt] = useState<Date | null>(null);
  const [tick, setTick] = useState(0);

  // Remember the last good answer and the time of the check.
  useEffect(() => {
    if (state.status === "ok") {
      const now = new Date();
      setChecked(now);
      setLast(state.data);
      setFailedAt(null);
    } else if (state.status === "error") {
      setFailedAt(new Date());
    }
  }, [state]);

  // One interval, restarted by every refresh; runs only while the tab is visible.
  useEffect(() => {
    const id = setInterval(() => {
      if (document.visibilityState === "visible") reload();
    }, REFRESH_MS);
    return () => clearInterval(id);
  }, [reload, tick]);

  // Coming back to the tab: refresh at once if the data is a minute old.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible" && Date.now() - checkedAt.current >= REFRESH_MS) {
        reload();
        setTick((t) => t + 1);
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [reload]);

  const refresh = () => {
    reload();
    setTick((t) => t + 1);
  };
  const loading = state.status === "loading";
  const data = state.status === "ok" ? state.data : last;
  const o = data ? overall(data) : null;

  return (
    <>
      <h1>Status</h1>
      <p class="lede muted">
        How fresh the data is. Sol Index copies photos from JPL feeds; this page shows when that
        last worked.
      </p>
      <div class="row status-bar">
        <button
          class="btn btn-sm"
          type="button"
          data-testid="status-refresh"
          disabled={loading}
          aria-busy={loading}
          onClick={refresh}
        >
          Refresh
        </button>
        <span class="small muted" data-testid="status-checked-at">
          {checked ? `Last checked ${checked.toLocaleTimeString()}` : "Not checked yet"}
        </span>
      </div>

      {state.status === "error" && !last && (
        <div class="alert alert-error" role="alert">
          <div>
            <div class="alert-title">Can’t load the status</div>
            <p>The status could not be read. Check your connection and try again.</p>
            <button class="btn btn-sm" type="button" onClick={refresh}>
              Try again
            </button>
          </div>
        </div>
      )}

      {state.status === "error" && last && failedAt && (
        <p class="small muted">
          Last refresh failed at {failedAt.toLocaleTimeString()} — retrying.
        </p>
      )}

      {!data && loading && <PageSkeleton label="Loading status…" />}

      {data && o && (
        <>
          <div class={`status-overall status-${o.kind}`} role="status" data-testid="status-overall">
            <span class="status-icon" aria-hidden="true">
              {ICONS[data.status]}
            </span>
            <span>{o.text}</span>
          </div>
          {data.status !== "degraded" && (
            <div class="status-grid">
              {(data.rovers ?? []).map((r) => (
                <RoverCard key={r.rover} r={r} />
              ))}
            </div>
          )}
          <p class="small muted">
            Spirit, Opportunity and Ingenuity are archived: their data is not updated.
          </p>
        </>
      )}
    </>
  );
}
