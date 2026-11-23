import type { ComponentChildren } from "preact";
import { useEffect, useState } from "preact/hooks";
import { api } from "../api/client";
import type { WeatherParameter, WeatherResponse, WeatherSol } from "../api/types";
import { fmt, safeHref } from "../lib/format";
import { useAsync } from "../lib/hooks";
import {
  domainOf,
  fmtV,
  isPartialLoad,
  lagWords,
  paramDigits,
  paramKeys,
  paramLabel,
  paramNote,
  qualityBadge,
  rawOf,
  type View,
  WEATHER_ROVERS,
  type WeatherRover,
} from "../lib/weather";
import { FlagPill, WeatherInspector } from "./WeatherInspector";
import { WeatherChart, WeatherLegend } from "./WeatherChart";
import { EmptyState, ErrorAlert } from "./States";

const dateOnly = (iso: string) => iso.slice(0, 10);
const SOURCE_NAME = "NASA PDS Atmospheres Node";

export function WeatherSection({ onMeta }: { onMeta: (text: string) => void }) {
  const [rover, setRover] = useState<WeatherRover>(WEATHER_ROVERS[0]!);
  const [state, retry] = useAsync(() => api.weather(rover.id), [rover.id]);
  // The newest sol of the rover decides the grey "not published yet" zone and the lag.
  const [info] = useAsync((signal) => api.rover(rover.id, signal), [rover.id]);
  const maxSol = info.status === "ok" ? info.data.max_sol : null;

  const data = state.status === "ok" ? state.data : null;
  const ready = data !== null && data.items.length > 0;

  useEffect(() => {
    if (state.status === "loading") onMeta("Loading…");
    else if (state.status === "error") onMeta("Unavailable");
    else if (!data || data.items.length === 0) onMeta("Not loaded yet");
    else {
      const first = data.items[0]!.sol;
      const last = data.source_max_sol ?? data.items[data.items.length - 1]!.sol;
      onMeta(
        `${rover.name} (${rover.instrument}) · ${paramKeys(data.parameters)
          .map((k) => paramLabel(k))
          .join(", ")} · sols ${first}–${last}`,
      );
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.status, ready, rover.id]);

  return (
    <>
      <div class="rover-switch" role="radiogroup" aria-label="Rover">
        <span class="label" aria-hidden="true">
          Rover
        </span>
        <div class="segmented">
          {WEATHER_ROVERS.map((r) => (
            <span key={r.id} class="seg">
              <input
                type="radio"
                name="wx-rover"
                id={`wx-${r.id}`}
                value={r.id}
                checked={rover.id === r.id}
                onChange={() => setRover(r)}
              />
              <label for={`wx-${r.id}`}>
                {r.name} ({r.instrument})
              </label>
            </span>
          ))}
        </div>
      </div>

      {state.status === "loading" ? (
        <div role="status" aria-label="Loading weather">
          <span class="visually-hidden">Loading weather…</span>
          <div class="skeleton" style={{ height: 86 }} aria-hidden="true" />
          <div class="skeleton" style={{ height: 180, marginTop: 12 }} aria-hidden="true" />
          <div
            class="skeleton"
            style={{ height: 36, width: "60%", marginTop: 12 }}
            aria-hidden="true"
          />
        </div>
      ) : state.status === "error" ? (
        <ErrorAlert error={state.error} onRetry={retry} title="Weather could not be loaded" />
      ) : !data || data.items.length === 0 ? (
        <EmptyState title="Weather data not loaded yet">
          No weather sols have been imported for {rover.name} yet. The mission archives publish in
          batches, so recent sols are always missing.
          {data?.credit && (
            <>
              <br />
              Credit: {data.credit}
            </>
          )}
        </EmptyState>
      ) : (
        <WeatherView key={rover.id} rover={rover} data={data} maxSol={maxSol} />
      )}
    </>
  );
}

function WeatherView({
  rover,
  data,
  maxSol,
}: {
  rover: WeatherRover;
  data: WeatherResponse;
  maxSol: number | null;
}) {
  const { items, parameters } = data;
  const keys = paramKeys(parameters);
  const first = items[0]!.sol;
  const last = items[items.length - 1]!.sol;
  const source = data.source_max_sol ?? last;
  const now = Math.max(maxSol ?? source, source);
  const lag = now - source;
  const partial = isPartialLoad(source, now);

  const [k, setK] = useState(
    keys.includes("air_temp_c") ? "air_temp_c" : (keys[0] ?? "air_temp_c"),
  );
  const [view, setView] = useState<View>(partial && rover.id === "curiosity" ? "loaded" : "all");
  const [sel, setSel] = useState<number | null>(null);
  const [dragging, setDragging] = useState(false);
  const select = (sol: number, fromSlider = false) => {
    setSel(sol);
    setDragging(fromSlider);
  };
  const domain = domainOf(view, first, source, now);
  const archive = safeHref(data.source_url);
  const rows = items.slice(-8).reverse();

  return (
    <>
      <div class="card summary">
        <p class="what">
          Air measurements from the rover&rsquo;s weather station ({rover.instrument}): the daily
          lowest, highest and mean value of {keys.map((x) => paramLabel(x)).join(", ")} for each
          sol.
          {!keys.includes("wind") && ` ${rover.name} has no wind data in the archive.`}
        </p>
        <dl class="stats">
          <div>
            <dt>Rover</dt>
            <dd>
              {rover.name} ({rover.instrument})
            </dd>
          </div>
          <div>
            <dt>Coverage</dt>
            <dd>
              sols {first}–{source} · {fmt(items.length)} sols with data
            </dd>
          </div>
          <div>
            <dt>Last update</dt>
            <dd>
              {data.updated_at ? (
                <time dateTime={data.updated_at}>{dateOnly(data.updated_at)}</time>
              ) : (
                "—"
              )}
            </dd>
          </div>
          <div>
            <dt>Newest sol</dt>
            <dd>
              {source}
              {lag > 0 && (
                <>
                  {" · "}
                  <span class="lag">
                    {lagWords(lag)}
                    {partial && " · only the first sols are loaded so far"}
                  </span>
                </>
              )}
            </dd>
          </div>
        </dl>
        <p class="src">
          <b>Source:</b>{" "}
          {archive ? (
            <a href={archive} target="_blank" rel="noopener noreferrer">
              {SOURCE_NAME} ↗
            </a>
          ) : (
            SOURCE_NAME
          )}
          . The archive is published in batches, months after the measurements.
          <br />
          <b>Credit:</b> {data.credit}
        </p>
      </div>

      <div class="wx-controls">
        <div class="field">
          <span class="label" id="wx-param-l">
            Parameter
          </span>
          <div class="chips" role="group" aria-labelledby="wx-param-l">
            {keys.map((key) => {
              const q = qualityBadge(parameters[key]);
              return (
                <button
                  key={key}
                  class="chip"
                  type="button"
                  aria-pressed={k === key}
                  onClick={() => setK(key)}
                >
                  {paramLabel(key)}
                  {q && <span class="badge badge-warn no-dot">{q}</span>}
                </button>
              );
            })}
          </div>
        </div>
        <div class="field">
          <label for="wx-view">Sols shown</label>
          <select
            id="wx-view"
            value={view}
            onChange={(e) => setView(e.currentTarget.value as View)}
          >
            <option value="all">
              Whole mission ({Math.min(1, first)}–{fmt(now)})
            </option>
            <option value="365">Last 365 sols</option>
            <option value="100">Last 100 sols</option>
            <option value="loaded">Loaded data only</option>
          </select>
        </div>
      </div>

      <div class="card chart-box">
        <div class="chart-title">
          <h3>{rover.name}: daily values by sol</h3>
          <WeatherLegend k={k} />
        </div>
        <WeatherChart
          items={items}
          k={k}
          parameter={parameters[k]}
          domain={domain}
          zoneFrom={now > source ? source : null}
          roverName={rover.name}
          selected={sel ?? last}
          onSelect={select}
        />
        <p class="chart-note">
          {paramNote(k)} Hover the chart to read a sol; click it, or use the slider below, to
          inspect it.
        </p>
      </div>

      <WeatherInspector
        rover={rover.id}
        items={items}
        parameters={parameters}
        k={k}
        selected={sel ?? last}
        picked={sel !== null}
        dragging={dragging}
        onSelect={select}
      />

      <WeatherTable rows={rows} keys={keys} parameters={data.parameters} />
      <p class="xsmall muted" style={{ margin: 0 }}>
        Air: mean (lowest … highest). A flag never hides a value: means and extremes are always
        shown. <code>—</code> means the sensor exists but the sol has no valid readings.
      </p>
      <p class="api-line xsmall mono muted">
        API: <code>GET /api/v2/rovers/{rover.id}/weather</code> · <code>/weather/{"{sol}"}</code>
      </p>
    </>
  );
}

/** `<mean> (<extra>)` cells, in the units of the column header. */
function cell(r: WeatherSol, key: string): ComponentChildren {
  const raw = rawOf(r, key);
  if (raw === undefined) return <span class="muted">n/a</span>;
  if (raw === null || typeof raw !== "object")
    return (
      <span title="no valid value on this sol">
        —<span class="visually-hidden"> no data</span>
      </span>
    );
  const o = raw as Record<string, number | null | undefined>;
  const d = paramDigits(key);
  if ("speed_mean_ms" in o) {
    return (
      <>
        {fmtV(o.speed_mean_ms, d)} <small>(gust {fmtV(o.gust_ms, d)})</small>
      </>
    );
  }
  if (key === "uv_wm2") return fmtV(o.max, d);
  if (key === "air_temp_c") {
    return (
      <>
        {fmtV(o.mean, d)}{" "}
        <small>
          ({fmtV(o.min, d)} … {fmtV(o.max, d)})
        </small>
      </>
    );
  }
  if (key === "humidity_pct") {
    return (
      <>
        {fmtV(o.mean, d)} <small>(max {fmtV(o.max, d)})</small>
      </>
    );
  }
  return fmtV(o.mean, d);
}

function WeatherTable({
  rows,
  keys,
  parameters,
}: {
  rows: WeatherSol[];
  keys: string[];
  parameters: Record<string, WeatherParameter>;
}) {
  const head = (key: string): string => {
    const unit = parameters[key]?.unit;
    return `${key === "uv_wm2" ? "UV max" : paramLabel(key)}${unit ? `, ${unit}` : ""}`;
  };
  return (
    <table class="last wx-table">
      <caption>Newest published sols</caption>
      <thead>
        <tr>
          <th scope="col">Sol</th>
          <th scope="col">Date (UTC)</th>
          {keys.map((key) => (
            <th scope="col" key={key}>
              {head(key)}
            </th>
          ))}
          <th scope="col">Hours</th>
          <th scope="col">Flags</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.sol}>
            <th scope="row" data-label="Sol">
              {r.sol}
            </th>
            <td data-label="Date (UTC)">{r.earth_date}</td>
            {keys.map((key) => (
              <td key={key} data-label={head(key)}>
                {cell(r, key)}
              </td>
            ))}
            <td data-label="Hours">{r.hours_covered}/24</td>
            <td data-label="Flags" class="flags-cell">
              {r.flags.length ? (
                r.flags.map((f, i) => <FlagPill key={i} f={f} />)
              ) : (
                <span class="muted">none</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
