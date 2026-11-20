import { useState } from "preact/hooks";
import { api } from "../api/client";
import type { Rover } from "../api/types";
import { ErrorAlert } from "../components/States";
import { PhotoTile } from "../components/PhotoTile";
import { fmt, statusLabel, visibleRovers } from "../lib/format";
import { useAsync, useRovers, useTitle } from "../lib/hooks";
import { Link, navigate, qs } from "../router";

function RoverCard({ rover, index }: { rover: Rover; index: number }) {
  const hrefLatest = `/rovers/${rover.id}`;
  return (
    <li class="card rover">
      <div class={`rover-art art-${index % 4}`} aria-hidden="true" />
      <article class="rover-body" aria-labelledby={`r-${rover.id}`}>
        <div class="rover-head">
          <h2 id={`r-${rover.id}`}>
            <Link href={hrefLatest}>{rover.name}</Link>
          </h2>
          <span class={`badge ${rover.status === "active" ? "badge-active" : "badge-complete"}`}>
            {statusLabel(rover.status)}
          </span>
        </div>
        <div class="rover-mission">
          Mission {rover.mission} · landed {rover.landing_date}
          {rover.end_date && <> · ended {rover.end_date}</>}
        </div>
        <dl class="stats">
          <div>
            <dt>Latest sol</dt>
            <dd>{rover.max_sol}</dd>
          </div>
          <div>
            <dt>Latest date</dt>
            <dd>{rover.max_date}</dd>
          </div>
          <div>
            <dt>Photos</dt>
            <dd>{fmt(rover.total_photos)}</dd>
          </div>
          <div>
            <dt>Cameras</dt>
            <dd>{rover.cameras_count}</dd>
          </div>
        </dl>
        {(rover.status === "active" || rover.data_source === "archive") && rover.last_synced_at && (
          <div class="synced">
            {rover.data_source === "archive" ? "Imported" : "Synced"}{" "}
            <time dateTime={rover.last_synced_at}>
              {rover.last_synced_at.slice(0, 16).replace("T", " ")} UTC
            </time>
          </div>
        )}
        <div class="rover-actions">
          <Link class="btn btn-primary btn-sm" href={hrefLatest}>
            Latest sol {rover.max_sol}
          </Link>
          <Link class="btn btn-sm" href={`/rovers/${rover.id}/timeline`}>
            Timeline
          </Link>
          <Link class="btn btn-sm" href={`/rovers/${rover.id}/map`}>
            Route map
          </Link>
        </div>
      </article>
    </li>
  );
}

function QuickJump({ rovers }: { rovers: Rover[] }) {
  const [rover, setRover] = useState(rovers[0]?.id ?? "");
  const [sol, setSol] = useState("");
  const max = rovers.find((r) => r.id === rover)?.max_sol ?? undefined;
  const submit = (e: Event) => {
    e.preventDefault();
    if (!rover) return;
    navigate(`/rovers/${rover}${qs({ sol: sol.trim() })}`);
  };
  return (
    <form class="card quick" role="search" aria-label="Jump to a sol" onSubmit={submit}>
      <div class="field">
        <label for="q-rover">Rover</label>
        <select id="q-rover" value={rover} onChange={(e) => setRover(e.currentTarget.value)}>
          {rovers.map((r) => (
            <option key={r.id} value={r.id}>
              {r.name}
            </option>
          ))}
        </select>
      </div>
      <div class="field">
        <label for="q-sol">Sol</label>
        <input
          id="q-sol"
          type="number"
          inputMode="numeric"
          min={0}
          max={max}
          placeholder={max !== undefined ? `0 – ${max}` : undefined}
          value={sol}
          onInput={(e) => setSol(e.currentTarget.value)}
        />
      </div>
      <button class="btn btn-primary" type="submit">
        Open sol
      </button>
      <Link class="btn" href={`/rovers/${rover}`}>
        Latest photos
      </Link>
      <span class="hint">Sol = a Martian day counted from landing (sol 0).</span>
    </form>
  );
}

function LatestStrip({ rover }: { rover: Rover }) {
  const [state, reload] = useAsync(
    (signal) => api.latest(rover.id, { sample_type: "full", per_page: 6 }, signal),
    [rover.id],
  );
  if (state.status === "error") {
    return <ErrorAlert error={state.error} onRetry={reload} />;
  }
  if (state.status === "loading") {
    return (
      <div class="latest-strip" aria-hidden="true">
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} class="skeleton" style={{ aspectRatio: "4/3" }} />
        ))}
      </div>
    );
  }
  const { sol, items, total } = state.data;
  const from = `/rovers/${rover.id}${qs({ sol, sample_type: "full" })}`;
  return (
    <>
      <div class="section-head">
        <h2 id="latest-title">
          Latest from {rover.name}
          {sol !== null && ` · sol ${sol}`}
        </h2>
        {sol !== null && (
          <Link href={from} class="small">
            All {fmt(total)} full frames of sol {sol} →
          </Link>
        )}
      </div>
      {items.length === 0 ? (
        <p class="muted">{sol === null ? "No photos yet." : `No full frames on sol ${sol} yet.`}</p>
      ) : (
        <ul class="latest-strip">
          {items.map((p) => (
            <PhotoTile key={p.id} photo={p} from={from} />
          ))}
        </ul>
      )}
    </>
  );
}

export function Home() {
  useTitle("Sol Index — Mars rover raw images");
  const [state, reload] = useRovers();
  const rovers = state.status === "ok" ? visibleRovers(state.data) : [];
  const hidden = state.status === "ok" ? state.data.length - rovers.length : 0;
  const featured = rovers.find((r) => r.id === "perseverance") ?? rovers[0];

  return (
    <>
      <section class="hero" aria-labelledby="hero-title">
        <h1 id="hero-title">Every raw image from the Mars rovers, sol by sol.</h1>
        <p>
          Browse unprocessed frames from the Mars rovers by Martian day, Earth date and camera. An
          open, community-run replacement for the archived Mars Rover Photos API.
        </p>
        {rovers.length > 0 && <QuickJump rovers={rovers} />}
      </section>

      <section aria-labelledby="rovers-title">
        <div class="section-head">
          <h2 id="rovers-title">Rovers</h2>
        </div>
        {state.status === "loading" && (
          <div class="grid-rovers" role="status" aria-live="polite">
            <span class="visually-hidden">Loading rovers…</span>
            {[0, 1].map((i) => (
              <div key={i} class="skeleton" style={{ height: 300 }} aria-hidden="true" />
            ))}
          </div>
        )}
        {state.status === "error" && <ErrorAlert error={state.error} onRetry={reload} />}
        {state.status === "ok" && rovers.length === 0 && (
          <p class="muted">No rover has photos yet. Check back after the first import.</p>
        )}
        {rovers.length > 0 && (
          <ul class={`grid-rovers n${Math.min(rovers.length, 4)}`}>
            {rovers.map((r, i) => (
              <RoverCard key={r.id} rover={r} index={i} />
            ))}
          </ul>
        )}
        {hidden > 0 && (
          <p class="small muted" style={{ marginTop: "var(--s3)" }}>
            More rovers appear when their archives are imported.
          </p>
        )}
      </section>

      {featured && (
        <section aria-labelledby="latest-title">
          <LatestStrip rover={featured} />
        </section>
      )}

      <section id="api" aria-labelledby="api-title">
        <div class="section-head">
          <h2 id="api-title">For developers</h2>
        </div>
        <div class="dev">
          <div class="card panel">
            <h3>Moving from the old API?</h3>
            <p class="small muted">
              Change the host and keep your <code>/mars-photos/api/v1/…</code> calls. v1 mirrors the
              archived API’s responses.
            </p>
            <pre>GET /mars-photos/api/v1/rovers/curiosity/photos?sol=1000</pre>
          </div>
          <div class="card panel">
            <h3>API v2</h3>
            <p class="small muted">
              All sample types, image sizes, real capture time and one error format. This site is
              built on v2 only.
            </p>
            <pre>GET /api/v2/rovers/perseverance/photos?sol=2031&amp;camera=SUPERCAM_RMI</pre>
          </div>
        </div>
        <p class="small" style={{ marginTop: "var(--s3)" }}>
          <a href="/developers">Developer guide</a> · <a href="/docs">Interactive API docs</a>
        </p>
      </section>
    </>
  );
}
