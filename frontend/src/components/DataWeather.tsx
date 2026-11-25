import type { ComponentChildren } from "preact";
import { useEffect, useMemo, useState } from "preact/hooks";
import { api } from "../api/client";
import type { WeatherParameter, WeatherResponse, WeatherSol } from "../api/types";
import { fmt } from "../lib/format";
import { useAsync } from "../lib/hooks";
import {
  bandOf,
  fmtV,
  isPartialLoad,
  lagWords,
  paramDigits,
  paramKeys,
  paramLabel,
  paramNote,
  qualityBadge,
  rawOf,
  WEATHER_ROVERS,
  type WeatherRover,
} from "../lib/weather";
import {
  clampRange,
  defaultRange,
  overviewOf,
  parseRangeQuery,
  rangeIntersects,
  roverFromQuery,
  type SolRange,
  writeRangeQuery,
} from "../lib/solRange";
import { About, dateOnly, Facts } from "./DataAbout";
import { SolRangeSlider } from "./SolRangeSlider";
import { SolTable, type SolColumn } from "./SolTable";
import { FlagPill, WeatherInspector } from "./WeatherInspector";
import { WeatherChart, WeatherLegend } from "./WeatherChart";
import { EmptyState, ErrorAlert } from "./States";

const SOURCE_NAME = "NASA PDS Atmospheres Node";

export function WeatherSection() {
  // `?rover=&from=&to=` is read once; without `rover` the first rover is shown.
  const [rover, setRover] = useState<WeatherRover>(
    () =>
      WEATHER_ROVERS.find((r) => r.id === roverFromQuery(window.location.search)) ??
      WEATHER_ROVERS[0]!,
  );
  // Choosing another rover starts from that rover's default range.
  const [switched, setSwitched] = useState(false);
  const [urlRange, setUrlRange] = useState<SolRange | null>(() =>
    parseRangeQuery(window.location.search),
  );
  const [state, retry] = useAsync(() => api.weather(rover.id), [rover.id]);
  // The newest sol of the rover decides the grey "not published yet" zone and the lag.
  const [info] = useAsync((signal) => api.rover(rover.id, signal), [rover.id]);
  const maxSol = info.status === "ok" ? info.data.max_sol : null;

  const data = state.status === "ok" ? state.data : null;
  const ready = data !== null && data.items.length > 0;

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
                onChange={() => {
                  if (r.id !== rover.id) {
                    setUrlRange(null);
                    setSwitched(true);
                    writeRangeQuery(null, r.id);
                  }
                  setRover(r);
                }}
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
        <WeatherView
          key={rover.id}
          rover={rover}
          data={data}
          maxSol={maxSol}
          initialRange={urlRange}
          persist={switched}
        />
      )}
      {state.status !== "loading" && !ready && <WeatherAbout rover={rover} data={data} />}
    </>
  );
}

function WeatherView({
  rover,
  data,
  maxSol,
  initialRange,
  persist,
}: {
  rover: WeatherRover;
  data: WeatherResponse;
  maxSol: number | null;
  initialRange: SolRange | null;
  /** Write the range to the address even before the visitor changes it (after choosing a rover). */
  persist: boolean;
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
  const [sel, setSel] = useState<number | null>(null);
  const [dragging, setDragging] = useState(false);
  const [jump, setJump] = useState<{ sol: number; n: number } | null>(null);
  const select = (sol: number, fromSlider = false) => {
    setSel(sol);
    setDragging(fromSlider);
  };

  // The range the visitor chose (or the address gave); null = the default, which follows the data.
  const bounds = { first, max: now };
  // A range of the address that misses the data altogether counts as invalid: the default is used.
  const startRange =
    initialRange && rangeIntersects(initialRange, { first, max: Math.max(maxSol ?? 0, source) })
      ? initialRange
      : null;
  const [chosen, setChosen] = useState<SolRange | null>(startRange);
  useEffect(() => {
    if (initialRange && !startRange) writeRangeQuery(null);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const dflt = defaultRange({ first, sourceMax: source });
  const range = clampRange(chosen ?? dflt, bounds);
  const isDefault = range[0] === dflt[0] && range[1] === dflt[1];
  useEffect(() => {
    if (chosen !== null || persist) writeRangeQuery(isDefault && !persist ? null : range);
  }, [range[0], range[1]]); // eslint-disable-line react-hooks/exhaustive-deps

  const overview = useMemo(
    () =>
      overviewOf(
        items.flatMap((r): [number, number][] => {
          const b = bandOf(r, k);
          return b ? [[r.sol, b.mean]] : [];
        }),
        { first, max: now },
      ),
    [items, k, first, now],
  );
  const inRange = items.filter((r) => r.sol >= range[0] && r.sol <= range[1]);
  const keyList = keys.join(",");
  const columns = useMemo(
    () => weatherColumns(keyList.split(",").filter(Boolean), parameters),
    [keyList, parameters],
  );
  const span = range[1] - range[0] + 1;
  const firstRow = inRange[0];
  const lastRow = inRange[inRange.length - 1];

  return (
    <>
      <Facts
        items={[
          ["Rover", `${rover.name} (${rover.instrument})`],
          ["Parameters", keys.map((x) => paramLabel(x)).join(", ")],
          ["Coverage", `sols ${first}–${source}`],
          ["Sols with data", fmt(items.length)],
          ["Newest sol", fmt(source)],
          [
            "Behind the rover",
            lag > 0 ? (
              <span class="lag">
                {lagWords(lag)}
                {partial && " · only the first sols are loaded so far"}
              </span>
            ) : (
              "up to date"
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
          domain={range}
          zoneFrom={source < range[1] ? source : null}
          roverName={rover.name}
          selected={sel ?? lastRow?.sol ?? last}
          onSelect={(sol) => {
            select(sol);
            setJump((j) => ({ sol, n: (j?.n ?? 0) + 1 }));
          }}
        />
        <p class="chart-note">
          {paramNote(k)} Hover the chart to read a sol; click it to inspect it and to find it in the
          table below.
        </p>
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
      </div>

      <WeatherInspector
        rover={rover.id}
        items={inRange}
        parameters={parameters}
        k={k}
        selected={sel ?? lastRow?.sol ?? last}
        picked={sel !== null}
        dragging={dragging}
        onSelect={select}
      />

      <SolTable
        rows={items}
        columns={columns}
        range={range}
        first={first}
        published={source}
        latest={now}
        roverName={rover.name}
        onJump={(sol) => setChosen([Math.min(range[0], sol), Math.max(range[1], sol)])}
        rowLink={(r) => [
          {
            href: `/rovers/${rover.id}?sol=${r.sol}`,
            label: "Photos",
            aria: `Photos of sol ${r.sol}`,
          },
          {
            href: `/rovers/${rover.id}/sols/${r.sol}/conditions`,
            label: "Sol report →",
            aria: `Sol report for sol ${r.sol}`,
          },
        ]}
        focus={jump}
      />
      <p class="xsmall muted" style={{ margin: 0 }}>
        Air: mean (lowest … highest). A flag never hides a value: means and extremes are always
        shown. <code>—</code> means the sensor exists but the sol has no valid readings.
      </p>
      <WeatherAbout rover={rover} data={data} keys={keys} />
    </>
  );
}

function WeatherAbout({
  rover,
  data,
  keys,
}: {
  rover: WeatherRover;
  data: WeatherResponse | null;
  keys?: string[];
}) {
  return (
    <About
      measures={
        <>
          Air measurements from the rover&rsquo;s weather station ({rover.instrument}): the daily
          lowest, highest and mean value of{" "}
          {(keys ?? []).length > 0
            ? keys!.map((x) => paramLabel(x)).join(", ")
            : "air temperature, pressure, humidity and more"}{" "}
          for each sol. Temperatures are in °C, pressure in pascals.
          {keys && !keys.includes("wind") && ` ${rover.name} has no wind data in the archive.`} A
          flag never hides a value: means and extremes are always shown.
        </>
      }
      sourceName={SOURCE_NAME}
      sourceUrl={data?.source_url}
      schedule="The mission archives are published in batches, months after the measurements; Sol Index checks them regularly."
      notes="Recent sols are always missing. The grey area on the chart marks sols not published yet."
      credit={data?.credit}
      endpoints={[`/rovers/${rover.id}/weather`, `/rovers/${rover.id}/weather/{sol}`]}
    />
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

/** Sort value of a parameter of a sol: the mean (wind: the mean speed, UV: the daily maximum). */
function sortOf(r: WeatherSol, key: string): number | null {
  const raw = rawOf(r, key);
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const v = "speed_mean_ms" in o ? o.speed_mean_ms : key === "uv_wm2" ? o.max : o.mean;
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function weatherColumns(
  keys: string[],
  parameters: Record<string, WeatherParameter>,
): SolColumn<WeatherSol>[] {
  return [
    {
      key: "sol",
      label: "Sol",
      numeric: true,
      firstDir: "desc",
      sortValue: (r) => r.sol,
      cell: (r) => r.sol,
    },
    {
      key: "date",
      label: "Date (UTC)",
      firstDir: "desc",
      sortValue: (r) => r.earth_date,
      cell: (r) => r.earth_date,
    },
    {
      key: "ls",
      label: "Ls",
      unit: "°",
      numeric: true,
      sortValue: (r) => r.ls,
      cell: (r) => fmtV(r.ls, 1),
    },
    ...keys.map((key): SolColumn<WeatherSol> => ({
      key,
      label: key === "uv_wm2" ? "UV max" : paramLabel(key),
      unit: parameters[key]?.unit,
      numeric: true,
      sortValue: (r) => sortOf(r, key),
      cell: (r) => cell(r, key),
    })),
    {
      key: "hours",
      label: "Hours",
      numeric: true,
      sortValue: (r) => r.hours_covered,
      cell: (r) => `${r.hours_covered}/24`,
    },
    {
      key: "flags",
      label: "Flags",
      sortable: false,
      className: "flags-cell",
      cell: (r) =>
        r.flags.length ? (
          r.flags.map((f, i) => <FlagPill key={i} f={f} />)
        ) : (
          <span class="muted">none</span>
        ),
    },
  ];
}
