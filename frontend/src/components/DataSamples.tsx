import { useEffect } from "preact/hooks";
import { api } from "../api/client";
import { fmt, safeHref } from "../lib/format";
import { useAsync, useRovers } from "../lib/hooks";
import { niceScale, solTicks } from "../lib/radiation";
import { lag, SAMPLES_ROVER, summarize } from "../lib/samples";
import { Link } from "../router";
import { LineChart } from "./LineChart";
import { EmptyState, ErrorAlert } from "./States";

const dateOnly = (iso: string) => iso.slice(0, 10);

export function SamplesSection({ onMeta }: { onMeta: (text: string) => void }) {
  const [state, retry] = useAsync(() => api.samples(SAMPLES_ROVER), []);
  const [rovers] = useRovers();
  const maxSol =
    rovers.status === "ok"
      ? (rovers.data.find((r) => r.id === SAMPLES_ROVER)?.max_sol ?? null)
      : null;
  const data = state.status === "ok" ? state.data : null;
  const items = data?.items ?? [];
  const s = summarize(items);

  useEffect(() => {
    if (state.status === "loading") onMeta("Loading…");
    else if (state.status === "error") onMeta("Unavailable");
    else if (items.length === 0) onMeta("Not loaded yet");
    else
      onMeta(
        `Perseverance · ${s.count} sample tubes${
          s.firstSol !== null ? ` · sealed on sols ${s.firstSol}–${s.lastSol}` : ""
        }`,
      );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.status, items.length]);

  if (state.status === "loading") {
    return (
      <div role="status" aria-label="Loading samples">
        <span class="visually-hidden">Loading samples…</span>
        <div class="skeleton" style={{ height: 86 }} aria-hidden="true" />
        <div class="skeleton" style={{ height: 180, marginTop: 12 }} aria-hidden="true" />
        <div
          class="skeleton"
          style={{ height: 36, width: "60%", marginTop: 12 }}
          aria-hidden="true"
        />
      </div>
    );
  }
  if (state.status === "error") {
    return <ErrorAlert error={state.error} onRetry={retry} title="Samples could not be loaded" />;
  }
  if (!data || items.length === 0) {
    return (
      <EmptyState title="Samples not loaded yet">
        Sol Index has not fetched the sample table yet. Check back soon.
      </EmptyState>
    );
  }

  const sealed = items.flatMap((x) => (x.sol === null ? [] : [x.sol])).sort((a, b) => a - b);
  const xmax = Math.max(maxSol ?? 0, s.lastSol ?? 0, 1);
  const pts: [number, number][] = [[0, 0]];
  sealed.forEach((sol, i) => {
    pts.push([sol, i], [sol, i + 1]);
  });
  pts.push([xmax, sealed.length]);
  const scale = niceScale(sealed.length);
  const behind = lag(items, maxSol);
  const source = safeHref(data.source_url);

  return (
    <>
      <div class="card summary">
        <p class="what">
          Sample tubes sealed by the rover for a possible return to Earth: where each came from,
          whether it is still on the rover or already left in the depot.
        </p>
        <dl class="stats">
          <div>
            <dt>Rover</dt>
            <dd>Perseverance</dd>
          </div>
          <div>
            <dt>Samples</dt>
            <dd>
              {s.count}
              {s.tbd.length > 0 && ` (${sealed.length} sealed, ${s.tbd.length} not yet)`}
            </dd>
          </div>
          <div>
            <dt>Coverage</dt>
            <dd>{s.firstSol !== null ? `sols ${s.firstSol}–${s.lastSol}` : "—"}</dd>
          </div>
          <div>
            <dt>Page last edited</dt>
            <dd>
              {data.source_updated_at ? (
                <time dateTime={data.source_updated_at}>{dateOnly(data.source_updated_at)}</time>
              ) : (
                "—"
              )}
              {behind && (
                <>
                  {" "}
                  <span class="lag">(rover is at sol {behind.max})</span>
                </>
              )}
            </dd>
          </div>
        </dl>
        <p class="src">
          <b>Source:</b>{" "}
          {source ? (
            <a href={source} target="_blank" rel="noopener noreferrer">
              NASA Mars Rock Samples page ↗
            </a>
          ) : (
            "NASA Mars Rock Samples page"
          )}
          {data.updated_at && (
            <>
              , updated here <time dateTime={data.updated_at}>{dateOnly(data.updated_at)}</time>
            </>
          )}
          <br />
          <b>Credit:</b> NASA / Mars 2020 Perseverance science team; dossiers: NASA PDS Geosciences
          Node
        </p>
      </div>

      <div class="card chart-box">
        <div class="chart-title">
          <h3>Samples sealed, cumulative</h3>
          <span class="legend">
            <span>by sol, scale from 0</span>
          </span>
        </div>
        <LineChart
          testId="samples-chart"
          label={`Cumulative number of sealed samples by sol: ${sealed.length} by sol ${s.lastSol ?? 0}${
            behind ? ", unchanged since" : ""
          }`}
          height={150}
          xmax={xmax}
          ymax={scale.top}
          yticks={scale.ticks}
          xticks={solTicks(xmax, 4)}
          fy={(t) => fmt(t)}
          lines={[{ cls: "ln-s", pts }]}
        />
        <p class="chart-note">
          {sealed.length} samples sealed by sol {s.lastSol}
          {behind ? "; none after that on the source page" : ""}.
        </p>
      </div>

      <div class="panel-foot">
        <Link class="btn btn-primary" href={`/rovers/${SAMPLES_ROVER}/samples`}>
          All {s.count} samples
        </Link>
        <span class="hint muted small">
          Table with filters, dossiers, photos of each tube and route-map links.
        </span>
      </div>
      <p class="api-line xsmall mono muted">
        API: <code>GET /api/v2/rovers/perseverance/samples</code>
      </p>
    </>
  );
}
