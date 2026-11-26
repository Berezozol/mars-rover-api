import type { Dispatch, StateUpdater } from "preact/hooks";
import { useMemo, useRef, useState } from "preact/hooks";
import { api, ApiError } from "../api/client";
import type { RadiationHour, WeatherHour, WeatherSolDetail } from "../api/types";
import { FlagPill } from "../components/FlagPill";
import { HourChart } from "../components/HourChart";
import {
  SolRadiationDaily,
  SolRadiationDailyView,
  useDailyRadiation,
} from "../components/SolRadiationDaily";
import { SolRadiationHourly, useHourlyRadiation } from "../components/SolRadiationHourly";
import { EmptyState, ErrorAlert } from "../components/States";
import { useAsync, useRovers, useTitle } from "../lib/hooks";
import { fallbackLat, hemisphereOf, hhmm, seasonOf, sunTimes } from "../lib/marsSeason";
import { RAD_ROVER } from "../lib/radiation";
import { type ChartDef, chartDefs, fmtNum, hourLabel, readoutOf } from "../lib/solReport";
import { WEATHER_ROVERS, isPartial } from "../lib/weather";
import { Link } from "../router";
import { NotFound } from "./NotFound";

interface Props {
  rover: string;
  sol: number;
}

// Shown only when the server's own credit line could not be loaded.
const CREDIT_NEUTRAL =
  "the instrument team and the NASA Planetary Data System (see the archive files below)";

const MAX_SOL = 2147483647;
const reportPath = (rover: string, sol: number) => `/rovers/${rover}/sols/${sol}/conditions`;
const pad2 = (n: number) => String(n).padStart(2, "0");

/** The shared hour cursor: `active` false = no hour selected (the slider's position is then only a resting place). */
interface Cursor {
  hour: number;
  active: boolean;
  live: boolean;
}
const IDLE_CURSOR: Cursor = { hour: 13, active: false, live: false };

/** Cursor updates from the charts (`null` = leave): same hour keeps the state object, so nothing re-renders. */
const hourSetter = (setCur: Dispatch<StateUpdater<Cursor>>) => (h: number | null) =>
  setCur((c) => {
    if (h === null) return c.active ? { ...c, active: false, live: false } : c;
    return c.active && c.hour === h && !c.live ? c : { hour: h, active: true, live: false };
  });

/** The pinned bar: one row (label, slider, readout, clear button); the readout has a fixed width. */
function HourBar({ cur, setCur }: { cur: Cursor; setCur: Dispatch<StateUpdater<Cursor>> }) {
  const slider = useRef<HTMLInputElement>(null);
  const clear = () => {
    setCur((c) => ({ ...c, active: false, live: false }));
    slider.current?.focus();
  };
  return (
    <div class="hour-bar" style={{ marginTop: 16 }} role="group" aria-label="Shared hour cursor">
      <label for="hourIn">
        <span class="hb-i">Inspect </span>hour<span class="hb-lmst"> (LMST)</span>
      </label>
      <input
        ref={slider}
        type="range"
        id="hourIn"
        class={cur.active ? "" : "is-idle"}
        min={0}
        max={23}
        step={1}
        value={cur.hour}
        aria-valuetext={cur.active ? `${hourLabel(cur.hour)} LMST` : "No hour selected"}
        onInput={(e) =>
          setCur({
            hour: Number((e.currentTarget as HTMLInputElement).value),
            active: true,
            live: true,
          })
        }
      />
      <div class="hb-val">
        <output for="hourIn">{cur.active ? hourLabel(cur.hour) : "No hour selected"}</output>
        {cur.active && (
          <button type="button" class="btn btn-sm hb-clear" aria-label="Clear hour" onClick={clear}>
            Clear
          </button>
        )}
      </div>
    </div>
  );
}

/** Sol report: the hours of one sol, a chart per parameter (stage 22, phase B). */
export function SolReport({ rover, sol }: Props) {
  const wr = WEATHER_ROVERS.find((r) => r.id === rover);
  const name = wr?.name ?? rover.charAt(0).toUpperCase() + rover.slice(1);
  useTitle(`${name} sol ${sol} conditions — Sol Index`);

  if (!wr) return <NoWeather rover={rover} name={name} sol={sol} />;
  return <Report rover={rover} name={name} sol={sol} />;
}

/** A rover without weather data: its page says so; an id the API does not know is "not found". */
function NoWeather({ rover, name, sol }: { rover: string; name: string; sol: number }) {
  const [rovers] = useRovers();
  if (rovers.status === "ok" && !rovers.data.some((r) => r.id === rover)) return <NotFound />;
  {
    return (
      <>
        <Crumbs rover={rover} name={name} sol={sol} />
        <section class="card sr-head" aria-labelledby="srH">
          <h1 id="srH">
            {name} · sol {sol}
          </h1>
        </section>
        <div style={{ marginTop: 16 }}>
          <EmptyState
            title="No weather data for this rover"
            actions={
              <Link class="btn btn-primary" href={`/rovers/${rover}?sol=${sol}`}>
                Photos of this sol →
              </Link>
            }
          >
            Sol Index has weather data for Curiosity and Perseverance only.
          </EmptyState>
        </div>
      </>
    );
  }
}

function Crumbs({ rover, name, sol }: { rover: string; name: string; sol: number }) {
  return (
    <nav class="crumbs" aria-label="Breadcrumb">
      <Link href="/data">Data</Link> /{" "}
      <Link href={`/data/weather?rover=${rover}`}>Weather by sol</Link> / {name} /{" "}
      <span aria-current="page">Sol {sol}</span>
    </nav>
  );
}

function Report({ rover, name, sol }: { rover: string; name: string; sol: number }) {
  const [detail, retry] = useAsync((signal) => api.weatherSol(rover, sol, signal), [rover, sol]);
  const [loc] = useAsync((signal) => api.locationLatest(rover, signal).catch(() => null), [rover]);
  const lat = loc.status === "ok" ? (loc.data?.items?.[0]?.lat ?? null) : null;
  const credit = (detail.status === "ok" && detail.data.credit) || CREDIT_NEUTRAL;

  const head = (d: WeatherSolDetail | null) => (
    <Head rover={rover} name={name} sol={sol} detail={d} lat={lat} />
  );

  let body;
  if (detail.status === "loading") {
    body = (
      <>
        {head(null)}
        <div role="status" aria-label={`Loading sol ${sol}`} style={{ marginTop: 16 }}>
          <div class="charts" aria-hidden="true">
            {Array.from({ length: 5 }, (_, i) => (
              <div key={i} class="card pc">
                <div class="skeleton skel-h" style={{ width: "45%" }} />
                <div class="skeleton skel-chart" />
              </div>
            ))}
          </div>
        </div>
      </>
    );
  } else if (detail.status === "error") {
    const e = detail.error;
    body =
      e instanceof ApiError && e.code === "sol_not_found" && rover === RAD_ROVER ? (
        <NoWeatherRadiation rover={rover} name={name} sol={sol} head={head(null)} />
      ) : e instanceof ApiError && e.code === "sol_not_found" ? (
        <>
          {head(null)}
          <div style={{ marginTop: 16 }}>
            <EmptyState
              title={`No data for sol ${sol}`}
              actions={
                <Link class="btn btn-primary" href={`/rovers/${rover}?sol=${sol}`}>
                  Photos of this sol →
                </Link>
              }
            >
              The source archive has no weather files for this sol (the instrument was off, the sol
              is missing, or it is not published yet). Nothing is estimated or interpolated. Photos
              for the sol may still exist.
            </EmptyState>
          </div>
        </>
      ) : (
        <>
          {head(null)}
          <div style={{ marginTop: 16 }}>
            <span class="visually-hidden" role="status">
              Sol {sol} could not be loaded.
            </span>
            <ErrorAlert
              error={e}
              onRetry={retry}
              title={`Sol ${sol} could not be loaded`}
              alwaysTitle
            />
          </div>
        </>
      );
  } else {
    body = (
      <Loaded rover={rover} name={name} sol={sol} detail={detail.data} lat={lat} credit={credit} />
    );
  }

  return (
    <>
      <Crumbs rover={rover} name={name} sol={sol} />
      {body}
    </>
  );
}

/** Curiosity, no weather for the sol: the radiation blocks stay; with no radiation either, one empty state. */
function NoWeatherRadiation({
  rover,
  name,
  sol,
  head,
}: {
  rover: string;
  name: string;
  sol: number;
  head: preact.ComponentChild;
}) {
  void name;
  const rad = useHourlyRadiation(rover, sol);
  const daily = useDailyRadiation(rover, sol);
  const [cur, setCur] = useState<Cursor>(IDLE_CURSOR);
  const onHour = hourSetter(setCur);

  const isMissing = (e: unknown) =>
    e instanceof ApiError && (e.code === "sol_not_found" || e.status === 404);
  const hourlyGone =
    (rad.state.status === "error" && isMissing(rad.state.error)) ||
    (rad.state.status === "ok" && !rad.state.data?.hours);
  const dailyGone =
    (daily.state.status === "error" && isMissing(daily.state.error)) ||
    (daily.state.status === "ok" && !daily.state.data.items.some((x) => x.sol === sol));
  const nothing =
    rad.state.status !== "loading" && daily.state.status !== "loading" && hourlyGone && dailyGone;

  const photos = (
    <Link class="btn btn-primary" href={`/rovers/${rover}?sol=${sol}`}>
      Photos of this sol →
    </Link>
  );
  if (nothing) {
    return (
      <>
        {head}
        <div style={{ marginTop: 16 }}>
          <EmptyState title={`No data for sol ${sol}`} actions={photos}>
            There is no weather or radiation data for this sol in the source archives (the
            instruments were off, the sol is missing, or it is not published yet). Nothing is
            estimated or interpolated. Photos for the sol may still exist.
          </EmptyState>
        </div>
      </>
    );
  }
  const announce =
    cur.active && cur.live
      ? `${hourLabel(cur.hour)} LMST. ${rad.defs
          .filter((d) => !d.noData)
          .map((d) => `${d.title}: ${readoutOf(d, cur.hour)}`)
          .join(". ")}`
      : "";
  return (
    <>
      {head}
      <div style={{ marginTop: 16 }} data-testid="no-weather">
        <EmptyState title={`No weather data for sol ${sol}`} actions={photos}>
          The source archive has no weather files for this sol (the instrument was off, the sol is
          missing, or it is not published yet). Nothing is estimated or interpolated. Radiation data
          for the sol is shown below where it exists.
        </EmptyState>
      </div>
      {rad.defs.length > 0 && <HourBar cur={cur} setCur={setCur} />}
      <div class="visually-hidden" role="status" aria-live="polite" data-testid="hour-announce">
        {announce}
      </div>
      <div style={{ marginTop: 32 }}>
        <SolRadiationHourly
          state={rad.state}
          retry={rad.retry}
          defs={rad.defs}
          sol={sol}
          night={null}
          hour={cur.active ? cur.hour : null}
          onHour={onHour}
        />
      </div>
      <div style={{ marginTop: 32 }}>
        <SolRadiationDailyView sol={sol} {...daily} />
      </div>
    </>
  );
}

function Head({
  rover,
  name,
  sol,
  detail,
  lat,
  dose,
}: {
  rover: string;
  name: string;
  sol: number;
  detail: WeatherSolDetail | null;
  lat: number | null;
  /** Daily dose of detector E (µGy/day): `"loading"`, a number or `null` (no value); `undefined` = no tile. */
  dose?: "loading" | number | null;
}) {
  const hemi = hemisphereOf(rover);
  const season = detail && hemi ? seasonOf(detail.ls, hemi) : null;
  const sun = detail ? sunTimes(detail.ls, lat ?? fallbackLat(rover)) : null;
  const hours = detail?.hours ?? [];
  return (
    <section class="card sr-head" aria-labelledby="srH">
      <div class="sr-top">
        <h1 id="srH">{detail ? `${name} · sol ${sol}` : `Sol ${sol} report`}</h1>
        <div class="sr-nav" role="group" aria-label="Neighbouring sols">
          {sol > 0 ? (
            <Link class="btn" href={reportPath(rover, sol - 1)}>
              ← Sol {sol - 1}
            </Link>
          ) : (
            <span class="btn" aria-disabled="true">
              ← Previous sol
            </span>
          )}
          {sol < MAX_SOL && (
            <Link class="btn" href={reportPath(rover, sol + 1)}>
              Sol {sol + 1} →
            </Link>
          )}
        </div>
      </div>
      {detail && (
        <>
          <SolSummary detail={detail} dose={dose} />
          <dl class="facts sr-facts" aria-label="Sol summary">
            <div>
              <dt>Earth date</dt>
              <dd>{detail.earth_date}</dd>
            </div>
            <div>
              <dt>Solar longitude </dt>
              <dd>{detail.ls === null || detail.ls === undefined ? "Ls —" : `Ls ${detail.ls}°`}</dd>
            </div>
            {season && hemi && (
              <div>
                <dt>Season ({hemi === "north" ? "northern" : "southern"} hemisphere)</dt>
                <dd>{season}</dd>
              </div>
            )}
            {sun && (
              <div>
                <dt>
                  Sunrise / sunset{" "}
                  <span
                    class="opt"
                    title="Not in the API: computed in the browser from Ls and the rover's latitude, local mean solar time, accuracy of a few minutes at best"
                  >
                    optional
                  </span>
                </dt>
                <dd>
                  ~{hhmm(sun[0])} / ~{hhmm(sun[1])} <small class="muted">LMST, approximate</small>
                  <small class="muted sr-note">
                    Computed in the browser from Ls and the rover's latitude; not from the API.
                  </small>
                </dd>
              </div>
            )}
          </dl>
          <dl class="facts sr-facts sr-facts-2" aria-label="Data coverage">
            <div>
              <dt>Coverage</dt>
              <dd>{Math.round(detail.coverage * 100)} % of the sol</dd>
            </div>
            <div>
              <dt>Hours with readings</dt>
              <dd>{detail.hours_covered} of 24</dd>
            </div>
            <div>
              <dt>Flags</dt>
              <dd>
                {detail.flags.length ? (
                  detail.flags.map((f, i) => (
                    <span key={i}>
                      {i > 0 && " "}
                      <FlagPill f={f} />
                    </span>
                  ))
                ) : (
                  <span class="muted">none</span>
                )}
              </dd>
            </div>
          </dl>
          <HourStrip hours={hours} covered={detail.hours_covered} />
        </>
      )}
      <div class="sr-links">
        <Link class="btn btn-primary" href={`/rovers/${rover}?sol=${sol}`}>
          Photos of this sol →
        </Link>
        <Link class="btn" href={`/data/weather?rover=${rover}`}>
          Weather by sol
        </Link>
        {rover === RAD_ROVER && (
          <Link class="btn" href="/data/radiation">
            Radiation by sol
          </Link>
        )}
      </div>
    </section>
  );
}

/** The key figures of the sol, straight from the response (same rounding as the charts); `—` = no value. */
function SolSummary({
  detail,
  dose,
}: {
  detail: WeatherSolDetail;
  dose?: "loading" | number | null;
}) {
  const range = (s: { min?: number | null; max?: number | null } | null | undefined, d: number) =>
    s && typeof s.min === "number" && typeof s.max === "number"
      ? `${fmtNum(s.min, d)}…${fmtNum(s.max, d)}`
      : null;
  const one = (v: number | null | undefined, d: number) =>
    typeof v === "number" && Number.isFinite(v) ? fmtNum(v, d) : null;
  type Tile = { label: string; value: string | null; unit: string; pending?: boolean };
  const tiles: Tile[] = [
    { label: "Air temperature", value: range(detail.air_temp_c, 1), unit: "°C" },
    { label: "Pressure, mean", value: one(detail.pressure_pa?.mean, 1), unit: "Pa" },
  ];
  // Which tiles exist follows the response: a parameter the rover has no sensor for has no key.
  if (detail.uv_wm2 !== undefined) {
    tiles.push({ label: "UV, max", value: one(detail.uv_wm2?.max, 2), unit: "W/m²" });
  }
  if (detail.ground_temp_c !== undefined) {
    tiles.push({ label: "Ground temperature", value: range(detail.ground_temp_c, 1), unit: "°C" });
  }
  if (dose !== undefined) {
    tiles.push({
      label: "Dose E, mean",
      value: dose === "loading" ? null : one(dose, 1),
      unit: "µGy/day",
      pending: dose === "loading",
    });
  }
  return (
    <dl class="sr-sum" data-testid="sol-summary" aria-label="Key figures">
      {tiles.map((t) => (
        <div key={t.label}>
          <dt>{t.label}</dt>
          <dd>
            {t.value === null ? (t.pending ? "…" : "—") : t.value}
            {t.value !== null && <small> {t.unit}</small>}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function HourStrip({ hours, covered }: { hours: WeatherHour[]; covered: number }) {
  const cov = Array.from({ length: 24 }, (_, h) => hours.find((x) => x.hour === h)?.coverage ?? 0);
  return (
    <div class="sr-cov">
      <span id="hcl">Air-temperature readings by hour (LMST 0–23):</span>
      <span class="hc-row" role="img" aria-labelledby="hcl" aria-describedby="hcd">
        {cov.map((c, h) => (
          <span
            key={h}
            class={`hc ${c ? "" : "hc-0"}`}
            style={{ "--c": c }}
            title={`${pad2(h)}:00 LMST, coverage ${Math.round(c * 100)} %`}
          />
        ))}
      </span>
      <span class="hint" id="hcd">
        {covered} of 24 hours have readings. Instruments work in sessions, so coverage of 30–60 % is
        normal.
      </span>
    </div>
  );
}

function Loaded({
  rover,
  name,
  sol,
  detail,
  lat,
  credit,
}: {
  rover: string;
  name: string;
  sol: number;
  detail: WeatherSolDetail;
  lat: number | null;
  credit: string;
}) {
  const [cur, setCur] = useState<Cursor>(IDLE_CURSOR);
  const [night, setNight] = useState(true);
  const defs = useMemo(() => chartDefs(detail), [detail]);
  const rad = useHourlyRadiation(rover, sol);
  const sun = sunTimes(detail.ls, lat ?? fallbackLat(rover));
  const clamp24 = (v: number) => Math.min(24, Math.max(0, v));
  const shade: [number, number] | null = night && sun ? [clamp24(sun[0]), clamp24(sun[1])] : null;
  const onHour = hourSetter(setCur);
  const dose: "loading" | number | null | undefined =
    rover !== RAD_ROVER
      ? undefined
      : rad.state.status === "loading"
        ? "loading"
        : rad.state.status === "ok"
          ? rad.state.data
            ? (rad.state.data.dose_e_ugy_day?.mean ?? null)
            : undefined
          : // Error: a missing sol (404) has no tile; any other failure keeps it, with "—".
            rad.state.error instanceof ApiError &&
              (rad.state.error.code === "sol_not_found" || rad.state.error.status === 404)
            ? undefined
            : null;
  const hasHourlyBand = defs.some((d) => d.hourBand);
  const hasSolBand = defs.some((d) => !d.hourBand && d.solBand);
  const announce =
    cur.active && cur.live
      ? `${hourLabel(cur.hour)} LMST. ${[...defs, ...rad.defs]
          .filter((d) => !d.noData)
          .map((d) => `${d.title}: ${readoutOf(d, cur.hour)}`)
          .join(". ")}`
      : "";

  return (
    <>
      <Head rover={rover} name={name} sol={sol} detail={detail} lat={lat} dose={dose} />
      {isPartial(detail) && (
        <div class="alert alert-warn" role="note" style={{ marginTop: 16 }}>
          <div>
            <div class="alert-title">Partial coverage</div>
            <div>
              Readings exist in {detail.hours_covered} of 24 hours (flag{" "}
              <code>partial_coverage</code>). The daily means may lean towards day or night; hours
              without readings are hatched and the lines are broken there.
            </div>
          </div>
        </div>
      )}
      <HourBar cur={cur} setCur={setCur} />
      <div class="visually-hidden" role="status" aria-live="polite" data-testid="hour-announce">
        {announce}
      </div>
      <div class="sec-head" style={{ marginTop: 24 }}>
        <h2 id="weatherHourH">Weather by hour</h2>
      </div>
      {sun && (
        <div class="chart-opts">
          <label class="chk">
            <input
              type="checkbox"
              checked={night}
              onChange={(e) => setNight((e.currentTarget as HTMLInputElement).checked)}
            />{" "}
            Shade night <span class="opt">approx.</span>
          </label>
          <span class="hint">Approximate, see Sunrise / sunset above.</span>
        </div>
      )}
      <p class="hint" style={{ margin: "8px 0 0" }}>
        Horizontal axis of every chart: local mean solar time (LMST), hours 0–24. Dots are hourly
        means and the line breaks where an hour has no readings (nothing is interpolated).{" "}
        {hasHourlyBand
          ? "Shaded band: the lowest to highest value of each hour."
          : hasSolBand
            ? "Dashed lines: the lowest and highest value of the whole sol (this server has no hourly minimum and maximum yet)."
            : ""}
      </p>
      <div class="charts" style={{ marginTop: 12 }}>
        {defs.map((d) => (
          <HourChart
            key={d.id}
            def={d}
            sol={sol}
            night={shade}
            hour={cur.active ? cur.hour : null}
            onHour={onHour}
          />
        ))}
      </div>
      {rover === RAD_ROVER ? (
        <>
          <div style={{ marginTop: 32 }}>
            <SolRadiationHourly
              state={rad.state}
              retry={rad.retry}
              defs={rad.defs}
              sol={sol}
              night={shade}
              hour={cur.active ? cur.hour : null}
              onHour={onHour}
            />
          </div>
          <div style={{ marginTop: 32 }}>
            <SolRadiationDaily rover={rover} sol={sol} />
          </div>
        </>
      ) : (
        <p class="hint" style={{ marginTop: 16 }}>
          No radiation series for {name}: Sol Index has radiation data for Curiosity only.
        </p>
      )}
      <div style={{ marginTop: 16 }}>
        <HoursTable
          defs={defs}
          hours={detail.hours ?? []}
          radDefs={rad.defs}
          radHours={rad.state.status === "ok" ? (rad.state.data?.hours ?? null) : null}
          hour={cur.active ? cur.hour : null}
        />
      </div>
      <section class="card panel sr-sources" aria-labelledby="srcH" style={{ marginTop: 16 }}>
        <h2 id="srcH" style={{ fontSize: "var(--fs-md)" }}>
          Credit and source files
        </h2>
        <p class="credit-line">Credit: {credit}</p>
        <p>
          Hours are averages over local mean solar time (LMST). Gaps are hours without readings:
          lines are broken there, nothing is interpolated.
        </p>
        {detail.source?.files?.length > 0 && (
          <ul>
            {detail.source.files.map((f) => (
              <li key={f.file}>
                <code>{f.product}</code> {f.version} —{" "}
                <a href={f.link} rel="noopener noreferrer">
                  {f.file}
                </a>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

const DASH = "–";
const cell = (v: number | null | undefined, d: number): string =>
  v === null || v === undefined ? DASH : fmtNum(v, d);
const pct = (v: number | null | undefined): string =>
  v === null || v === undefined ? DASH : `${Math.round(v * 100)} %`;

function HoursTable({
  defs,
  hours,
  radDefs,
  radHours,
  hour,
}: {
  defs: ChartDef[];
  hours: WeatherHour[];
  radDefs: ChartDef[];
  radHours: RadiationHour[] | null;
  hour: number | null;
}) {
  type Col = { head: string; cells: string[] };
  const cols: Col[] = [];
  for (const d of defs) {
    if (d.noData) continue;
    for (const s of d.series) {
      cols.push({
        head: `${d.title}${d.series.length > 1 ? ` ${s.name}` : d.hourBand ? " mean" : ""} (${d.unit})`,
        cells: s.values.map((v) => cell(v, d.digits)),
      });
    }
    const band = d.hourBand;
    if (band) {
      cols.push({
        head: `${d.title} min (${d.unit})`,
        cells: Array.from({ length: 24 }, (_, h) => cell(band[h]?.lo, d.digits)),
      });
      cols.push({
        head: `${d.title} max (${d.unit})`,
        cells: Array.from({ length: 24 }, (_, h) => cell(band[h]?.hi, d.digits)),
      });
    }
  }
  const byHour = <T extends { hour: number }>(rows: T[] | null, h: number) =>
    rows?.find((r) => r.hour === h);
  cols.push({
    head: "Weather coverage",
    cells: Array.from({ length: 24 }, (_, h) => pct(byHour(hours, h)?.coverage)),
  });
  const dose = radDefs.find((d) => d.id === "rad-dose");
  if (radHours && dose && !dose.noData) {
    for (const s of dose.series) {
      cols.push({
        head: `Dose rate ${s.name} (µGy/h)`,
        cells: s.values.map((v) => cell(v, dose.digits)),
      });
    }
    cols.push({
      head: "Radiation coverage",
      cells: Array.from({ length: 24 }, (_, h) => pct(byHour(radHours, h)?.coverage)),
    });
  }
  return (
    <details class="tbl card panel">
      <summary>Hourly values as a table</summary>
      <div class="table-scroll" tabIndex={0} role="region" aria-label="Hourly values table">
        <table class="hours-table">
          <thead>
            <tr>
              <th scope="col">LMST hour</th>
              {cols.map((c) => (
                <th key={c.head} scope="col">
                  {c.head}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: 24 }, (_, h) => (
              <tr key={h} class={hour === h ? "cur" : ""}>
                <th scope="row">{pad2(h)}:00</th>
                {cols.map((c) => (
                  <td key={c.head}>{c.cells[h]}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}
