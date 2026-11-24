import { useState } from "preact/hooks";
import { api } from "../api/client";
import type { RadiationResponse, RadiationSol } from "../api/types";
import { fmt } from "../lib/format";
import { useAsync, useMedia, useRovers } from "../lib/hooks";
import {
  cover,
  dose,
  num,
  lagMonths,
  lagText,
  niceScale,
  RAD_ROVER,
  rate,
  solTicks,
  viewOf,
} from "../lib/radiation";
import { About, dateOnly, Facts } from "./DataAbout";
import { LineChart } from "./LineChart";
import { EmptyState, ErrorAlert } from "./States";

const SOURCE_NAME = "NASA PDS Planetary Plasma Interactions Node, set MSL-M-RAD-3-RDR-V1.0";

export function RadiationSection() {
  const [state, retry] = useAsync(() => api.radiation(RAD_ROVER), []);
  const [rovers] = useRovers();
  const maxSol =
    rovers.status === "ok" ? (rovers.data.find((r) => r.id === RAD_ROVER)?.max_sol ?? null) : null;

  const data = state.status === "ok" ? state.data : null;
  const ready = data !== null && data.items.length > 0;
  const lag = ready && maxSol !== null ? maxSol - (data.source_max_sol ?? 0) : 0;

  if (state.status === "loading") {
    return (
      <div role="status" aria-label="Loading radiation">
        <span class="visually-hidden">Loading radiation…</span>
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
    return <ErrorAlert error={state.error} onRetry={retry} title="Radiation could not be loaded" />;
  }
  if (!data || data.items.length === 0) {
    return (
      <>
        <EmptyState title="Radiation data not loaded yet">
          The first import of Curiosity RAD data has not run yet. The archive is large, so the first
          load takes a while. Check back soon.
          <br />
          Credit: {data?.credit}
        </EmptyState>
        <RadAbout lag={0} />
      </>
    );
  }
  return <RadiationView data={data} maxSol={maxSol} lag={lag} />;
}

function RadiationView({
  data,
  maxSol,
  lag,
}: {
  data: RadiationResponse;
  maxSol: number | null;
  lag: number;
}) {
  const v = viewOf(data.items);
  const narrow = useMedia("(max-width: 479px)", false);
  const [sol, setSol] = useState(v.last);
  const cur = v.bySol.get(sol);
  const source = data.source_max_sol ?? v.last;
  const xmax = Math.max(maxSol ?? 0, source, v.last);
  const zone = xmax > source ? { from: source, to: xmax, label: "not published yet" } : null;
  const dScale = niceScale(v.maxDose);
  const rScale = niceScale(v.maxRate);
  const xticks = solTicks(xmax, narrow ? 3 : 5);
  const pts = (pick: (x: RadiationSol) => number | undefined): [number, number][] =>
    data.items.flatMap((x) => {
      const y = pick(x);
      return num(y) ? [[x.sol, y] as [number, number]] : [];
    });
  const newest = data.items.slice(-3);

  return (
    <>
      <Facts
        items={[
          ["Rover", "Curiosity (instrument RAD)"],
          ["Coverage", `sols ${v.first}–${v.last}`],
          ["Newest sol", fmt(v.last)],
          ["Behind the rover", lag > 0 ? <span class="lag">{lagText(lag)}</span> : "up to date"],
          [
            "Archive release",
            data.source_release ? (
              <time dateTime={data.source_release}>{data.source_release}</time>
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
        ]}
      />

      <div class="card chart-box">
        <div class="chart-title">
          <h3>Dose rate per sol, µGy/day</h3>
          <span class="legend">
            <span>
              <i aria-hidden="true" /> E (main)
            </span>
            <span>
              <i class="b" aria-hidden="true" /> B
            </span>
            <span>scale from 0</span>
          </span>
        </div>
        <LineChart
          testId="radiation-chart"
          label={`Daily absorbed dose rate at Curiosity, micrograys per day, sols ${v.first} to ${v.last}. Detector E is solid, detector B dashed. The scale starts from 0 (zero).${zone ? ` Sols ${source + 1} to ${xmax} are not published yet.` : ""}`}
          height={narrow ? 200 : 230}
          xmax={xmax}
          ymax={dScale.top}
          yticks={dScale.ticks}
          xticks={xticks}
          fy={(t) => fmt(t)}
          zone={zone}
          cursor={
            cur
              ? {
                  x: cur.sol,
                  dots: [
                    ...(num(cur.dose_b_ugy_day?.mean)
                      ? [{ y: cur.dose_b_ugy_day.mean, cls: "dot-b" }]
                      : []),
                    ...(num(cur.dose_e_ugy_day?.mean)
                      ? [{ y: cur.dose_e_ugy_day.mean, cls: "dot-e" }]
                      : []),
                  ],
                }
              : { x: sol, dots: [] }
          }
          lines={[
            { cls: "ln-b", pts: pts((x) => x.dose_b_ugy_day?.mean), breakAfter: 10 },
            { cls: "ln-e", pts: pts((x) => x.dose_e_ugy_day?.mean), breakAfter: 10 },
          ]}
        />

        <div class="chart-title chart-title-2">
          <h3>Detector B count rate (raw), events/s</h3>
          <span class="legend">
            <span>scale from 0</span>
          </span>
        </div>
        <LineChart
          testId="count-rate-chart"
          label={`Raw count rate of detector B, events per second, sols ${v.first} to ${v.last}. The scale starts from 0 (zero).`}
          height={150}
          xmax={xmax}
          ymax={rScale.top}
          yticks={rScale.ticks}
          xticks={xticks}
          zone={zone ? { ...zone, label: "" } : null}
          cursor={
            cur
              ? {
                  x: cur.sol,
                  dots: num(cur.count_rate_b_per_s)
                    ? [{ y: cur.count_rate_b_per_s, cls: "dot-e" }]
                    : [],
                }
              : { x: sol, dots: [] }
          }
          lines={[{ cls: "ln-s", pts: pts((x) => x.count_rate_b_per_s), breakAfter: 10 }]}
        />

        <div class="inspect">
          <label for="rad-sol" class="small muted">
            Inspect a sol (arrow keys move one sol)
          </label>
          <input
            type="range"
            id="rad-sol"
            min={v.first}
            max={v.last}
            step={1}
            value={sol}
            aria-describedby="rad-readout"
            onInput={(e) => setSol(Number((e.currentTarget as HTMLInputElement).value))}
          />
          <div class="readout" id="rad-readout" aria-live="polite" aria-atomic="true">
            {cur ? (
              <>
                <b>Sol {cur.sol}</b> · {cur.earth_date} · E <b>{dose(cur.dose_e_ugy_day?.mean)}</b>{" "}
                · B <b>{dose(cur.dose_b_ugy_day?.mean)}</b> µGy/day · B count rate{" "}
                <b>{rate(cur.count_rate_b_per_s)}</b> events/s
              </>
            ) : (
              <b>No data for sol {sol}</b>
            )}
          </div>
        </div>
        <p class="chart-note">
          Absorbed dose rate, not equivalent dose (sieverts): the source has none. Solar events are
          not smoothed. The count rate is a raw detector counter, useful for comparing sols with
          each other, not a calibrated particle flux.
        </p>
      </div>

      <div class="table-scroll">
        <table class="last">
          <caption>Newest published sols (daily means)</caption>
          <thead>
            <tr>
              <th scope="col">Sol</th>
              <th scope="col">Date (UTC)</th>
              <th scope="col">E, µGy/day</th>
              <th scope="col">B, µGy/day</th>
              <th scope="col">B count rate, events/s</th>
              <th scope="col">Coverage</th>
            </tr>
          </thead>
          <tbody>
            {newest.map((x) => (
              <tr key={x.sol}>
                <td>{x.sol}</td>
                <td>{x.earth_date}</td>
                <td>{dose(x.dose_e_ugy_day?.mean)}</td>
                <td>{dose(x.dose_b_ugy_day?.mean)}</td>
                <td>{rate(x.count_rate_b_per_s)}</td>
                <td>{cover(x.coverage)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <RadAbout data={data} lag={lag} />
    </>
  );
}

function RadAbout({ data, lag }: { data?: RadiationResponse; lag: number }) {
  const m = lagMonths(lag);
  return (
    <About
      measures={
        <>
          Radiation measured at the surface by the RAD instrument: the absorbed dose rate per sol,
          in micrograys per day (µGy/day). Detector E is plastic, closest to human tissue; detector
          B is silicon. The second chart is the raw count rate of detector B, in events per second.
          Absorbed dose rate is not equivalent dose (sieverts): the source has none.
        </>
      }
      sourceName={SOURCE_NAME}
      sourceUrl={data?.source_url}
      schedule="The archive is released every 4–5 months; Sol Index checks it monthly. The archive holds the original daily files of the RAD team; Sol Index shows daily summaries of them."
      notes={
        <>
          The data always lags the rover by the archive release cycle
          {lag > 0 && m >= 1 ? (
            <>
              : now {fmt(lag)} sols (about {m} {m === 1 ? "month" : "months"}) behind
            </>
          ) : null}
          . Grey area on the chart marks sols not published yet. Solar events are not smoothed.
        </>
      }
      credit={data?.credit}
      citation={data?.citation}
      endpoints={["/rovers/curiosity/radiation"]}
    />
  );
}
