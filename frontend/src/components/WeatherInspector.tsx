import { useEffect, useMemo, useState } from "preact/hooks";
import { api, ApiError } from "../api/client";
import type { WeatherFlag, WeatherHour, WeatherParameter, WeatherSol } from "../api/types";
import { useAsync } from "../lib/hooks";
import {
  bandOf,
  compass,
  flagClass,
  flagShort,
  flagWords,
  fmtV,
  hourKey,
  hourTitle,
  isAnomaly,
  isNum,
  nearestIndex,
  niceTicks,
  paramDigits,
  paramKeys,
  paramLabel,
  qualityBadge,
  rawOf,
} from "../lib/weather";
import { ErrorAlert } from "./States";

export function FlagPill({ f }: { f: WeatherFlag }) {
  return <span class={`flag flag-${flagClass(f)}`}>{flagShort(f)}</span>;
}

interface Props {
  rover: string;
  items: WeatherSol[];
  parameters: Record<string, WeatherParameter>;
  /** The parameter picked above the chart. */
  k: string;
  selected: number;
  /** False until the visitor chooses a sol: then no hourly request is made. */
  picked: boolean;
  /** True while the last change came from the slider (the hourly request then waits for a pause). */
  dragging: boolean;
  onSelect: (sol: number, fromSlider?: boolean) => void;
}

/** Slider, anomaly buttons, values of the chosen sol, hourly strip and small hourly charts. */
export function WeatherInspector({
  rover,
  items,
  parameters,
  k,
  selected,
  picked,
  dragging,
  onSelect,
}: Props) {
  const sols = useMemo(() => items.map((r) => r.sol), [items]);
  const idx = nearestIndex(sols, selected);
  const r = items[idx];
  const anomalies = useMemo(() => items.filter(isAnomaly).map((x) => x.sol), [items]);
  const prev = [...anomalies].reverse().find((s) => s < selected);
  const next = anomalies.find((s) => s > selected);

  if (!r) return null;
  return (
    <div class="card chart-box">
      <div class="chart-title">
        <h3>Inspect a sol</h3>
      </div>
      <div class="inspect">
        <label for="wx-sol" class="small muted">
          Sol (arrow keys move to the next loaded sol)
        </label>
        <input
          type="range"
          id="wx-sol"
          min={0}
          max={items.length - 1}
          step={1}
          value={idx}
          aria-valuetext={`Sol ${r.sol}${r.flags.length ? ", " + r.flags.map(flagShort).join(", ") : ""}`}
          onInput={(e) => {
            const s = sols[Number((e.currentTarget as HTMLInputElement).value)];
            if (s !== undefined) onSelect(s, true);
          }}
        />
        <div class="wx-nav">
          <button
            class="btn btn-sm"
            type="button"
            disabled={prev === undefined}
            onClick={() => prev !== undefined && onSelect(prev)}
          >
            Previous anomaly
          </button>
          <button
            class="btn btn-sm"
            type="button"
            disabled={next === undefined}
            onClick={() => next !== undefined && onSelect(next)}
          >
            Next anomaly
          </button>
          <span class="xsmall muted">
            jumps to sols with humidity above 100 % or readings left out
          </span>
        </div>
      </div>
      <SolDetail
        rover={rover}
        sol={r}
        parameters={parameters}
        k={k}
        picked={picked}
        dragging={dragging}
      />
    </div>
  );
}

function SolDetail({
  rover,
  sol: r,
  parameters,
  k,
  picked,
  dragging,
}: {
  rover: string;
  sol: WeatherSol;
  parameters: Record<string, WeatherParameter>;
  k: string;
  picked: boolean;
  dragging: boolean;
}) {
  return (
    <div id="wx-inspect-body" aria-live="polite" class="wx-inspect-body">
      <div class="readout">
        <b>Sol {r.sol}</b> · {r.earth_date} (UTC)
        {r.ls !== null && r.ls !== undefined ? ` · Ls ${r.ls}°` : ""} · <b>{r.hours_covered}</b> of
        24 hours measured · coverage <b>{r.coverage.toFixed(2)}</b>
        <br />
        <ValueLine sol={r} k={k} />
      </div>
      {r.flags.length > 0 ? (
        <ul class="flag-list" aria-label={`Flags for sol ${r.sol}`}>
          {r.flags.map((f, i) => (
            <li key={i}>
              <FlagPill f={f} /> <span>{flagWords(f)}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p class="xsmall muted" style={{ margin: 0 }}>
          No flags for this sol.
        </p>
      )}
      {picked ? (
        <HourlyLoader rover={rover} sol={r} parameters={parameters} dragging={dragging} />
      ) : (
        <p class="small muted" style={{ margin: 0 }}>
          Choose a sol on the chart or with the slider to see its hourly values.
        </p>
      )}
    </div>
  );
}

/** Asks for `/weather/{sol}` (after a short pause while the slider moves) and shows the hours. */
function HourlyLoader({
  rover,
  sol: r,
  parameters,
  dragging,
}: {
  rover: string;
  sol: WeatherSol;
  parameters: Record<string, WeatherParameter>;
  dragging: boolean;
}) {
  const [wanted, setWanted] = useState(r.sol);
  useEffect(() => {
    if (wanted === r.sol) return;
    // A jump (button, click on the chart) asks at once; a drag of the slider waits for a pause.
    if (!dragging) {
      setWanted(r.sol);
      return;
    }
    const t = setTimeout(() => setWanted(r.sol), 150);
    return () => clearTimeout(t);
  }, [r.sol, wanted, dragging]);
  const [detail, retry] = useAsync(
    (signal) => api.weatherSol(rover, wanted, signal),
    [rover, wanted],
  );
  const settled = wanted === r.sol;

  if (!settled || detail.status === "loading") {
    return (
      <div role="status" aria-label={`Loading sol ${r.sol}`}>
        <div class="skeleton" style={{ height: 24 }} aria-hidden="true" />
        <div class="wx-minis" style={{ marginTop: 12 }}>
          {paramKeys(parameters).map((key) => (
            <div key={key} class="skeleton" style={{ height: 120 }} aria-hidden="true" />
          ))}
        </div>
      </div>
    );
  }
  if (detail.status === "error") {
    if (detail.error instanceof ApiError && detail.error.code === "sol_not_found") {
      return (
        <div class="card empty">
          <h3>No data for sol {r.sol}</h3>
          <p class="muted small">
            The archive has no weather files for this sol (the instrument was off, or the sol is
            missing in the source). Pick another sol.
          </p>
        </div>
      );
    }
    return (
      <ErrorAlert
        error={detail.error}
        onRetry={retry}
        title={`Sol ${r.sol} could not be loaded`}
        alwaysTitle
      />
    );
  }
  return <Hourly hours={detail.data.hours} sol={r} parameters={parameters} />;
}

function ValueLine({ sol: r, k }: { sol: WeatherSol; k: string }) {
  const d = paramDigits(k);
  const b = bandOf(r, k);
  if (!b) {
    const missing = rawOf(r, k) === undefined;
    return (
      <span class="muted">
        {paramLabel(k)}: {missing ? "no sensor on this rover" : "no value on this sol"}
        {k === "wind" && !missing ? " (Perseverance wind exists only for sols 36–313)" : ""}
      </span>
    );
  }
  const v = rawOf(r, k) as Record<string, unknown>;
  if ("speed_mean_ms" in v) {
    const dir = v.direction_mode_deg;
    return (
      <>
        Wind <b>{fmtV(b.mean, d)}</b> m/s mean, gust <b>{fmtV(b.hi, d)}</b> m/s
        {isNum(dir) && (
          <>
            , direction <b>{compass(dir)}</b> ({dir}°, azimuth from north)
          </>
        )}
      </>
    );
  }
  if (b.lo === null || k === "uv_wm2") {
    return (
      <>
        {paramLabel(k)} mean <b>{fmtV(b.mean, d)}</b>, max <b>{fmtV(b.hi, d)}</b>
      </>
    );
  }
  return (
    <>
      {paramLabel(k)} mean <b>{fmtV(b.mean, d)}</b>, min <b>{fmtV(b.lo, d)}</b>, max{" "}
      <b>{fmtV(b.hi, d)}</b>
    </>
  );
}

function Hourly({
  hours,
  sol: r,
  parameters,
}: {
  hours: WeatherHour[];
  sol: WeatherSol;
  parameters: Record<string, WeatherParameter>;
}) {
  const keys = paramKeys(parameters);
  return (
    <>
      <div class="hours-strip">
        <span class="xsmall muted">Hours with air-temperature readings, 0–23 LMST</span>
        <span
          class="hc-row"
          role="img"
          aria-label={`${r.hours_covered} of 24 local hours have air temperature readings`}
        >
          {hours.map((o) => (
            <span
              key={o.hour}
              class={`hc${o.coverage ? "" : " hc-0"}`}
              style={{ "--c": String(o.coverage) } as Record<string, string>}
              title={`hour ${o.hour}: ${o.coverage ? Math.round(o.coverage * 100) + " % of the hour measured" : "no measurements"}`}
            />
          ))}
        </span>
      </div>
      <div class="wx-minis">
        {keys.map((key) => {
          const hk = hourKey(key);
          const vals = hours.map((o) => {
            const v = (o as unknown as Record<string, unknown>)[hk];
            return isNum(v) ? v : null;
          });
          const ok = vals.filter((v): v is number => v !== null);
          const dd = paramDigits(key);
          const q = qualityBadge(parameters[key]);
          return (
            <div key={key} class="card mini">
              <div class="k">
                {hourTitle(hk)}
                {q && <span class="badge badge-warn no-dot">{q}</span>}
              </div>
              <div class="v">
                {ok.length ? `${fmtV(Math.min(...ok), dd)} … ${fmtV(Math.max(...ok), dd)}` : "–"}{" "}
                <small>{parameters[key]?.unit}</small>
              </div>
              <MiniChart
                vals={vals}
                k={key}
                title={hourTitle(hk)}
                unit={parameters[key]?.unit ?? ""}
              />
            </div>
          );
        })}
      </div>
      <p class="chart-note">
        X axis: LMST hours (local mean solar time), 0–23. A broken line means no readings in that
        hour. Each chart has its own scale.
      </p>
    </>
  );
}

const MW = 300;
const MH = 92;
const ML = 38;
const MR = 6;
const MT = 8;
const MB = 20;

function MiniChart({
  vals,
  k,
  title,
  unit,
}: {
  vals: (number | null)[];
  k: string;
  title: string;
  unit: string;
}) {
  const ok = vals.filter((v): v is number => v !== null);
  if (!ok.length) {
    return (
      <div class="mini-empty" style={{ height: MH }}>
        no data for this parameter
      </div>
    );
  }
  let lo = Math.min(...ok);
  const hi = Math.max(...ok);
  if (k === "uv_wm2") lo = 0;
  const tk =
    k === "humidity_pct" && hi > 100
      ? niceTicks(0, Math.ceil(hi / 10) * 10, 2)
      : niceTicks(lo, hi, 2);
  const ymin = tk[0] ?? 0;
  const ymax = tk[tk.length - 1] ?? 1;
  const X = (hr: number) => ML + ((hr + 0.5) / 24) * (MW - ML - MR);
  const Y = (v: number) => MT + (1 - (v - ymin) / (ymax - ymin || 1)) * (MH - MT - MB);
  let d = "";
  let pen = false;
  vals.forEach((v, i) => {
    if (v === null) {
      pen = false;
      return;
    }
    d += `${pen ? "L" : "M"}${X(i).toFixed(1)} ${Y(v).toFixed(1)}`;
    pen = true;
  });
  const dd = paramDigits(k);
  return (
    <svg
      class="chart"
      viewBox={`0 0 ${MW} ${MH}`}
      role="img"
      aria-label={`${title} by local hour, ${ok.length} of 24 hours measured, from ${fmtV(lo, dd)} to ${fmtV(hi, dd)} ${unit}`}
    >
      {tk.map((t) => (
        <g key={t}>
          <line class="grid" x1={ML} x2={MW - MR} y1={Y(t)} y2={Y(t)} />
          <text x={ML - 5} y={Y(t) + 4} text-anchor="end">
            {fmtV(t, t % 1 ? 1 : 0)}
          </text>
        </g>
      ))}
      {[0, 6, 12, 18, 24].map((t) => (
        <text
          key={t}
          x={ML + (t / 24) * (MW - ML - MR)}
          y={MH - 5}
          text-anchor={t === 0 ? "start" : t === 24 ? "end" : "middle"}
        >
          {t}
        </text>
      ))}
      {k === "humidity_pct" && ymax > 100 && (
        <line class="wx-ref" x1={ML} x2={MW - MR} y1={Y(100)} y2={Y(100)} />
      )}
      <path class="ln wx-mean" d={d} />
      {vals.map(
        (v, i) =>
          v !== null &&
          vals[i - 1] == null &&
          vals[i + 1] == null && <circle key={i} class="dot-e" cx={X(i)} cy={Y(v)} r={3} />,
      )}
    </svg>
  );
}
