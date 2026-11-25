import { api, ApiError } from "../api/client";
import { useAsync } from "../lib/hooks";
import { cover, dose, niceScale, num, rate } from "../lib/radiation";
import { fmt } from "../lib/format";
import { LineChart, type ChartLine } from "./LineChart";
import { ErrorAlert } from "./States";

/** Sols on each side of the report's sol in the trend. */
export const TREND_SOLS = 30;

interface Props {
  rover: string;
  sol: number;
}

/**
 * Daily RAD summary of the sol with a small trend of the surrounding sols (Curiosity). Hourly radiation
 * is not served yet (stage 22 phase C): this block is the one that phase replaces with hourly charts.
 */
export function SolRadiationDaily({ rover, sol }: Props) {
  const lo = Math.max(0, sol - TREND_SOLS);
  const hi = Math.min(2147483647, sol + TREND_SOLS);
  const [state, retry] = useAsync(
    (signal) => api.radiationRange(rover, lo, hi, signal),
    [rover, sol],
  );

  let body;
  if (state.status === "loading") {
    body = (
      <div role="status" aria-label="Loading radiation">
        <div class="skeleton" style={{ height: 80 }} aria-hidden="true" />
      </div>
    );
  } else if (state.status === "error") {
    const notFound = state.error instanceof ApiError && state.error.status === 404;
    body = notFound ? (
      <p class="muted small" style={{ margin: 0 }}>
        This rover has no radiation data.
      </p>
    ) : (
      <ErrorAlert error={state.error} onRetry={retry} title="Radiation could not be loaded" />
    );
  } else {
    const items = state.data.items;
    const today = items.find((x) => x.sol === sol);
    const e: [number, number][] = [];
    const b: [number, number][] = [];
    let max = 0;
    for (const x of items) {
      if (typeof x.dose_e_ugy_day?.mean === "number") {
        e.push([x.sol, x.dose_e_ugy_day.mean]);
        max = Math.max(max, x.dose_e_ugy_day.mean);
      }
      if (typeof x.dose_b_ugy_day?.mean === "number") {
        b.push([x.sol, x.dose_b_ugy_day.mean]);
        max = Math.max(max, x.dose_b_ugy_day.mean);
      }
    }
    const { top, ticks } = niceScale(max);
    const lines: ChartLine[] = [
      { cls: "ln-e", pts: e, breakAfter: 3 },
      { cls: "ln-b", pts: b, breakAfter: 3 },
    ];
    const dots = [
      ...(today && num(today.dose_e_ugy_day?.mean)
        ? [{ y: today.dose_e_ugy_day.mean, cls: "dot-e" }]
        : []),
      ...(today && num(today.dose_b_ugy_day?.mean)
        ? [{ y: today.dose_b_ugy_day.mean, cls: "dot-b" }]
        : []),
    ];
    body = (
      <>
        {today ? (
          <dl class="facts facts-sm" aria-label={`Daily radiation, sol ${sol}`}>
            <div>
              <dt>Dose, detector E</dt>
              <dd>{dose(today.dose_e_ugy_day?.mean)} µGy/day</dd>
            </div>
            <div>
              <dt>Dose, detector B</dt>
              <dd>{dose(today.dose_b_ugy_day?.mean)} µGy/day</dd>
            </div>
            <div>
              <dt>Count rate, detector B</dt>
              <dd>{rate(today.count_rate_b_per_s)} events/s</dd>
            </div>
            <div>
              <dt>Coverage</dt>
              <dd>{cover(today.coverage)}</dd>
            </div>
          </dl>
        ) : (
          <p class="muted small">
            There is no daily radiation summary for sol {sol}. The RAD archive is published in
            batches and has gaps.
          </p>
        )}
        {e.length + b.length > 0 && (
          <>
            <h3 class="rad-trend-title">
              Daily dose rate, sols {lo}–{hi}
            </h3>
            <LineChart
              label={`Daily absorbed dose, detectors E and B, sols ${lo} to ${hi}${
                today ? `; sol ${sol} is marked` : ""
              }`}
              height={150}
              xmin={lo}
              xmax={hi}
              ymax={top}
              yticks={ticks}
              xticks={[lo, sol, hi].filter((v, i, a) => a.indexOf(v) === i)}
              fy={fmt}
              lines={lines}
              cursor={today ? { x: sol, dots } : null}
              testId="rad-trend"
            />
            <div class="legend" style={{ marginTop: 4 }}>
              <span>
                <i aria-hidden="true" />E
              </span>
              <span>
                <i class="b" aria-hidden="true" />B
              </span>
              <span class="muted xsmall">µGy/day</span>
            </div>
          </>
        )}
        <p class="credit-line xsmall muted">
          Credit:{" "}
          {state.data.credit || "MSL RAD (SwRI) / NASA PDS Planetary Plasma Interactions Node"}
        </p>
      </>
    );
  }

  return (
    <section id="radiation" aria-labelledby="radH" class="rad-daily">
      <div class="sec-head">
        <h2 id="radH">Radiation (RAD), daily</h2>
        <span class="badge badge-est no-dot">hourly values planned</span>
      </div>
      <div class="card panel">{body}</div>
    </section>
  );
}
