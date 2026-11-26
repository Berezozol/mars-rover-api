import { useEffect, useMemo, useState } from "preact/hooks";
import { api } from "../api/client";
import type { RadiationResponse, RadiationSol } from "../api/types";
import { fmt } from "../lib/format";
import { useAsync, useMedia, useRovers } from "../lib/hooks";
import { cover, dose, num, lagMonths, lagText, RAD_ROVER, rate, viewOf } from "../lib/radiation";
import {
  clampRange,
  defaultRange,
  overviewOf,
  parseRangeQuery,
  rangeTicks,
  offScaleText,
  type ScaleMode,
  type SolRange,
  valueScale,
  writeRangeQuery,
  zeroBasedScale,
} from "../lib/solRange";
import { About, dateOnly, Facts } from "./DataAbout";
import { LineChart } from "./LineChart";
import { SolRangeSlider } from "./SolRangeSlider";
import { SolTable, type SolColumn } from "./SolTable";
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
  const [jump, setJump] = useState<{ sol: number; n: number } | null>(null);
  const [mode, setMode] = useState<ScaleMode>("auto");
  const source = data.source_max_sol ?? v.last;
  const now = Math.max(maxSol ?? 0, source, v.last);
  const bounds = { first: v.first, max: now };

  // The range the visitor chose (or the address gave); null = the default, which follows the data.
  const [chosen, setChosen] = useState<SolRange | null>(() =>
    parseRangeQuery(window.location.search, {
      first: v.first,
      max: Math.max(maxSol ?? 0, source, v.last),
    }),
  );
  const dflt = defaultRange({ first: v.first, sourceMax: source });
  const range = clampRange(chosen ?? dflt, bounds);
  const isDefault = range[0] === dflt[0] && range[1] === dflt[1];
  useEffect(() => {
    if (chosen !== null) writeRangeQuery(isDefault ? null : range);
  }, [range[0], range[1]]); // eslint-disable-line react-hooks/exhaustive-deps

  const inRange = useMemo(
    () => data.items.filter((x) => x.sol >= range[0] && x.sol <= range[1]),
    [data.items, range[0], range[1]], // eslint-disable-line react-hooks/exhaustive-deps
  );
  // The picked sol (chart click) stays inside the range.
  const lo = Math.max(range[0], v.first);
  const hi = Math.max(lo, Math.min(range[1], v.last));
  const shown = Math.min(Math.max(sol, lo), hi);
  const cur = v.bySol.get(shown);
  const overview = useMemo(
    () =>
      overviewOf(
        data.items.flatMap((x): [number, number][] =>
          num(x.dose_e_ugy_day?.mean) ? [[x.sol, x.dose_e_ugy_day.mean]] : [],
        ),
        { first: v.first, max: now },
      ),
    [data.items, v.first, now],
  );
  const zero = zeroBasedScale(mode, range);
  const dosePts = inRange.flatMap((x): [number, number][] => [
    ...(num(x.dose_e_ugy_day?.mean) ? [[x.sol, x.dose_e_ugy_day.mean] as [number, number]] : []),
    ...(num(x.dose_b_ugy_day?.mean) ? [[x.sol, x.dose_b_ugy_day.mean] as [number, number]] : []),
  ]);
  const ratePts = inRange.flatMap((x): [number, number][] =>
    num(x.count_rate_b_per_s) ? [[x.sol, x.count_rate_b_per_s]] : [],
  );
  const dScale = valueScale(dosePts, zero, mode !== "zero");
  const rScale = valueScale(ratePts, zero, mode !== "zero");
  const dOff = offScaleText(dScale.outliers, dose, "µGy/day");
  const rOff = offScaleText(rScale.outliers, rate, "events/s");
  const xticks = rangeTicks(range, narrow ? 2 : 4);
  const zone =
    source < range[1] ? { from: source + 0.5, to: range[1], label: "not published yet" } : null;
  const scaleText = zero ? "The scale starts from 0 (zero)." : "The scale is fitted to the range.";
  const pts = (pick: (x: RadiationSol) => number | undefined): [number, number][] =>
    inRange.flatMap((x) => {
      const y = pick(x);
      return num(y) ? [[x.sol, y] as [number, number]] : [];
    });
  const pick = (s: number) => {
    // The nearest sol that has data.
    let best: RadiationSol | undefined;
    for (const x of inRange) {
      if (!best || Math.abs(x.sol - s) < Math.abs(best.sol - s)) best = x;
    }
    if (!best) return;
    setSol(best.sol);
    setJump((j) => ({ sol: best.sol, n: (j?.n ?? 0) + 1 }));
  };
  const columns = useMemo(() => radiationColumns(), []);
  const span = range[1] - range[0] + 1;
  const firstRow = inRange[0];
  const lastRow = inRange[inRange.length - 1];

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
        <div class="wx-controls">
          <div class="field" style={{ minWidth: "12rem" }}>
            <label for="rad-scale">Vertical scale</label>
            <select
              id="rad-scale"
              value={mode}
              onChange={(e) => setMode(e.currentTarget.value as ScaleMode)}
            >
              <option value="auto">Auto (long ranges start at zero)</option>
              <option value="zero">From zero</option>
              <option value="fit">Fit to the range</option>
            </select>
          </div>
        </div>
        <div class="chart-title chart-title-2">
          <h3>Dose rate per sol, µGy/day</h3>
          <span class="legend">
            <span>
              <i aria-hidden="true" /> E (main)
            </span>
            <span>
              <i class="b" aria-hidden="true" /> B
            </span>
            <span>{zero ? "scale from 0" : "scale fitted to the range"}</span>
          </span>
        </div>
        <LineChart
          testId="radiation-chart"
          label={`Daily absorbed dose rate at Curiosity, micrograys per day, sols ${range[0]} to ${range[1]}. Detector E is solid, detector B dashed. ${scaleText}${zone ? ` Sols ${source + 1} to ${range[1]} are not published yet.` : ""}${dOff ? ` ${dOff}.` : ""}`}
          height={narrow ? 200 : 230}
          xmin={range[0]}
          xmax={range[1]}
          ymin={dScale.bottom}
          ymax={dScale.top}
          yticks={dScale.ticks}
          xticks={xticks}
          fy={(t) => fmt(t)}
          zone={zone}
          offScale={dOff ? dScale.outliers.map(([x]) => x) : null}
          onPick={pick}
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
              : { x: shown, dots: [] }
          }
          lines={[
            { cls: "ln-b", pts: pts((x) => x.dose_b_ugy_day?.mean), breakAfter: 10 },
            { cls: "ln-e", pts: pts((x) => x.dose_e_ugy_day?.mean), breakAfter: 10 },
          ]}
        />

        {dOff && (
          <p class="off-note" data-testid="off-scale-note">
            <span aria-hidden="true">↑</span> {dOff}
          </p>
        )}

        <div class="chart-title chart-title-2">
          <h3>Detector B count rate (raw), events/s</h3>
          <span class="legend">
            <span>{zero ? "scale from 0" : "scale fitted to the range"}</span>
          </span>
        </div>
        <LineChart
          testId="count-rate-chart"
          label={`Raw count rate of detector B, events per second, sols ${range[0]} to ${range[1]}. ${scaleText}${rOff ? ` ${rOff}.` : ""}`}
          height={150}
          xmin={range[0]}
          xmax={range[1]}
          ymin={rScale.bottom}
          ymax={rScale.top}
          yticks={rScale.ticks}
          xticks={xticks}
          zone={zone ? { ...zone, label: "" } : null}
          offScale={rOff ? rScale.outliers.map(([x]) => x) : null}
          onPick={pick}
          cursor={
            cur
              ? {
                  x: cur.sol,
                  dots: num(cur.count_rate_b_per_s)
                    ? [{ y: cur.count_rate_b_per_s, cls: "dot-e" }]
                    : [],
                }
              : { x: shown, dots: [] }
          }
          lines={[{ cls: "ln-s", pts: pts((x) => x.count_rate_b_per_s), breakAfter: 10 }]}
        />

        {rOff && (
          <p class="off-note" data-testid="off-scale-note">
            <span aria-hidden="true">↑</span> {rOff}
          </p>
        )}

        {data.credit && (
          <p class="credit credit-line xsmall muted" data-testid="data-credit-chart">
            Credit: {data.credit}
          </p>
        )}

        <SolRangeSlider
          bounds={bounds}
          published={source}
          range={range}
          onChange={setChosen}
          overview={overview}
          summary={
            <>
              Showing sols{" "}
              <b>
                {fmt(range[0])}–{fmt(range[1])}
              </b>{" "}
              · {fmt(span)} sols
              {firstRow && lastRow && ` · ${firstRow.earth_date} to ${lastRow.earth_date}`} ·{" "}
              {fmt(inRange.length)} with data
              {range[1] > source && (
                <>
                  {" "}
                  · <span class="lag">{fmt(range[1] - source)} after the newest published sol</span>
                </>
              )}
            </>
          }
        />

        <p class="chart-note">
          Absorbed dose rate, not equivalent dose (sieverts): the source has none. Solar events are
          not smoothed. The count rate is a raw detector counter, useful for comparing sols with
          each other, not a calibrated particle flux. Click a chart to find a sol in the table
          below.
        </p>
      </div>

      <SolTable
        rows={data.items}
        columns={columns}
        range={range}
        first={v.first}
        published={source}
        latest={now}
        roverName="Curiosity"
        onJump={(s) => setChosen([Math.min(range[0], s), Math.max(range[1], s)])}
        rowLink={(x) => [
          {
            href: `/rovers/${RAD_ROVER}?sol=${x.sol}`,
            label: "Photos",
            aria: `Photos of sol ${x.sol}`,
          },
          {
            href: `/rovers/${RAD_ROVER}/sols/${x.sol}/conditions`,
            label: "Sol report →",
            aria: `Sol report for sol ${x.sol}`,
          },
        ]}
        focus={jump}
      />

      {data.credit && (
        <p class="credit credit-line xsmall muted" data-testid="data-credit-table">
          Credit: {data.credit}
        </p>
      )}

      <RadAbout data={data} lag={lag} />
    </>
  );
}

function radiationColumns(): SolColumn<RadiationSol>[] {
  return [
    {
      key: "sol",
      label: "Sol",
      numeric: true,
      firstDir: "desc",
      sortValue: (x) => x.sol,
      cell: (x) => x.sol,
    },
    {
      key: "date",
      label: "Date (UTC)",
      firstDir: "desc",
      sortValue: (x) => x.earth_date,
      cell: (x) => x.earth_date,
    },
    {
      key: "e",
      label: "E",
      unit: "µGy/day",
      numeric: true,
      sortValue: (x) => x.dose_e_ugy_day?.mean,
      cell: (x) => dose(x.dose_e_ugy_day?.mean),
    },
    {
      key: "b",
      label: "B",
      unit: "µGy/day",
      numeric: true,
      sortValue: (x) => x.dose_b_ugy_day?.mean,
      cell: (x) => dose(x.dose_b_ugy_day?.mean),
    },
    {
      key: "rate",
      label: "B count rate",
      unit: "events/s",
      numeric: true,
      sortValue: (x) => x.count_rate_b_per_s,
      cell: (x) => rate(x.count_rate_b_per_s),
    },
    {
      key: "coverage",
      label: "Coverage",
      numeric: true,
      sortValue: (x) => x.coverage,
      cell: (x) => cover(x.coverage),
    },
  ];
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
