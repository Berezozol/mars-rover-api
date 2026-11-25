import { useMemo } from "preact/hooks";
import { api, ApiError } from "../api/client";
import type { RadiationSolDetail } from "../api/types";
import { type Async, useAsync } from "../lib/hooks";
import { type ChartDef, radiationDefs } from "../lib/solReport";
import { HourChart } from "./HourChart";
import { ErrorAlert } from "./States";

/** The hourly RAD of a sol and its chart definitions; the page uses the definitions for the cursor announcement too. */
export function useHourlyRadiation(rover: string, sol: number) {
  const [state, retry] = useAsync<RadiationSolDetail | null>(
    (signal) =>
      rover === "curiosity" ? api.radiationSol(rover, sol, signal) : Promise.resolve(null),
    [rover, sol],
  );
  const data = state.status === "ok" ? state.data : null;
  const defs = useMemo(() => (data?.hours ? radiationDefs(data, data.hours) : []), [data]);
  return { state, retry, defs };
}

interface Props {
  state: Async<RadiationSolDetail | null>;
  retry: () => void;
  defs: ChartDef[];
  sol: number;
  /** Shared with the weather charts of the page. */
  night: [number, number] | null;
  hour: number | null;
  onHour: (hour: number | null) => void;
}

/** Shown when the server's own credit line is not available. */
const CREDIT_FALLBACK = "MSL RAD (SwRI) / NASA PDS Planetary Plasma Interactions Node";

/**
 * Hourly RAD of one sol (Curiosity, stage 22 phase C): dose rate of detectors E and B (µGy per hour) and
 * the count rate of detector B, by local mean solar time, on the same hour cursor as the weather charts.
 * `hours: null` and `404 sol_not_found` give a short explanation; the daily summary below stays.
 */
export function SolRadiationHourly({ state, retry, defs, sol, night, hour, onHour }: Props) {
  let body;
  if (state.status === "loading") {
    body = (
      <div role="status" aria-label="Loading hourly radiation">
        <div class="charts" aria-hidden="true">
          {[0, 1].map((i) => (
            <div key={i} class="card pc">
              <div class="skeleton skel-h" style={{ width: "45%" }} />
              <div class="skeleton skel-chart" />
            </div>
          ))}
        </div>
      </div>
    );
  } else if (state.status === "error") {
    const e = state.error;
    const missing = e instanceof ApiError && (e.code === "sol_not_found" || e.status === 404);
    body = missing ? (
      <div class="card panel" data-testid="rad-hourly-unavailable">
        <p class="small" style={{ margin: 0 }}>
          Hourly radiation is not available for this sol: the RAD archive has no summary for sol{" "}
          {sol}. The RAD archive is published in batches and has gaps.
        </p>
      </div>
    ) : (
      <ErrorAlert
        error={e}
        onRetry={retry}
        title="Hourly radiation could not be loaded"
        alwaysTitle
      />
    );
  } else if (!state.data?.hours) {
    body = (
      <div class="card panel" data-testid="rad-hourly-unavailable">
        <p class="small" style={{ margin: 0 }}>
          Hourly radiation is not available for this sol: the hours are not computed, or the
          observation times of the sol contradict each other. The daily values below are still
          valid.
        </p>
      </div>
    );
  } else {
    body = (
      <>
        {state.data?.timing_caveat === true && (
          <div
            class="alert alert-warn"
            role="note"
            data-testid="rad-timing-caveat"
            style={{ marginBottom: 12 }}
          >
            <div>
              <div class="alert-title">Observation times may be shifted</div>
              <div>
                The source lists this sol's file among known timing errors, so the split by hour may
                be shifted. The daily values are not affected.
              </div>
            </div>
          </div>
        )}
        <p class="hint" style={{ margin: "0 0 12px" }}>
          Dose rate is in µGy per hour (the daily summary below is in µGy per day). Dots are hourly
          means weighted by live time; hours without observations are hatched.
        </p>
        <div class="charts">
          {defs.map((d) => (
            <HourChart key={d.id} def={d} sol={sol} night={night} hour={hour} onHour={onHour} />
          ))}
        </div>
        <p class="credit-line xsmall muted">Credit: {state.data?.credit || CREDIT_FALLBACK}</p>
      </>
    );
  }

  return (
    <section id="radiation-hourly" aria-labelledby="radHourH">
      <div class="sec-head">
        <h2 id="radHourH">Radiation (RAD), hourly</h2>
      </div>
      {body}
    </section>
  );
}
