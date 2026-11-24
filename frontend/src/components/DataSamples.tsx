import { api } from "../api/client";
import { fmt, safeHref } from "../lib/format";
import { useAsync, useRovers } from "../lib/hooks";
import { niceScale, solTicks } from "../lib/radiation";
import { lag, SAMPLES_ROVER, summarize } from "../lib/samples";
import { Link } from "../router";
import { About, dateOnly, Facts } from "./DataAbout";
import { LineChart } from "./LineChart";
import { EmptyState, ErrorAlert } from "./States";

export function SamplesSection() {
  const [state, retry] = useAsync(() => api.samples(SAMPLES_ROVER), []);
  const [rovers] = useRovers();
  const maxSol =
    rovers.status === "ok"
      ? (rovers.data.find((r) => r.id === SAMPLES_ROVER)?.max_sol ?? null)
      : null;
  const data = state.status === "ok" ? state.data : null;
  const items = data?.items ?? [];
  const s = summarize(items);

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
      <>
        <EmptyState title="Samples not loaded yet">
          Sol Index has not fetched the sample table yet. Check back soon.
        </EmptyState>
        <SamplesAbout />
      </>
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
      <Facts
        items={[
          ["Rover", "Perseverance"],
          [
            "Samples",
            `${s.count}${s.tbd.length > 0 ? ` (${sealed.length} sealed, ${s.tbd.length} not yet)` : ""}`,
          ],
          ["Coverage", s.firstSol !== null ? `sols ${s.firstSol}–${s.lastSol}` : "—"],
          [
            "Source page edited",
            data.source_updated_at ? (
              <time dateTime={data.source_updated_at}>{dateOnly(data.source_updated_at)}</time>
            ) : (
              "—"
            ),
          ],
          [
            "Last update here",
            data.updated_at ? (
              <time dateTime={data.updated_at}>{dateOnly(data.updated_at)}</time>
            ) : (
              "—"
            ),
          ],
          ["Rover is at", behind ? <span class="lag">sol {behind.max}</span> : "up to date"],
        ]}
      />

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
      <SamplesAbout source={source} />
    </>
  );
}

function SamplesAbout({ source }: { source?: string | null }) {
  return (
    <About
      measures="Sample tubes sealed by the rover for a possible return to Earth: where each came from, whether it is still on the rover or already left in the depot. The chart counts sealed tubes by sol."
      sourceName="NASA Mars Rock Samples page"
      sourceUrl={source}
      schedule="Sol Index re-reads the page regularly; the page itself is edited by hand, so new tubes appear after the science team updates it."
      notes="A tube is counted when the page gives the sol it was sealed on; planned tubes without a sol are listed as not yet sealed."
      credit="NASA / Mars 2020 Perseverance science team; dossiers: NASA PDS Geosciences Node"
      endpoints={["/rovers/perseverance/samples"]}
    />
  );
}
