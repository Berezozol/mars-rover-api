import { useMemo, useState } from "preact/hooks";
import { api, ApiError } from "../api/client";
import type { WeatherHour, WeatherSolDetail } from "../api/types";
import { FlagPill } from "../components/WeatherInspector";
import { HourChart } from "../components/HourChart";
import { SolRadiationDaily } from "../components/SolRadiationDaily";
import { SolRadiationHourly, useHourlyRadiation } from "../components/SolRadiationHourly";
import { EmptyState, ErrorAlert } from "../components/States";
import { useAsync, useRovers, useTitle } from "../lib/hooks";
import { fallbackLat, hemisphereOf, hhmm, seasonOf, sunTimes } from "../lib/marsSeason";
import { RAD_ROVER } from "../lib/radiation";
import { type ChartDef, chartDefs, hourLabel, readoutOf } from "../lib/solReport";
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
  const [list] = useAsync(
    (signal) => {
      void signal;
      // The header of the list (credit, source_url) does not depend on the range: ask for one sol.
      return api.weatherRange(rover, sol, sol).catch(() => null);
    },
    [rover, sol],
  );

  const lat = loc.status === "ok" ? (loc.data?.items?.[0]?.lat ?? null) : null;
  const credit = (list.status === "ok" && list.data?.credit) || CREDIT_NEUTRAL;
  const sourceUrl = list.status === "ok" ? list.data?.source_url : undefined;

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
      e instanceof ApiError && e.code === "sol_not_found" ? (
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
      <Loaded
        rover={rover}
        name={name}
        sol={sol}
        detail={detail.data}
        lat={lat}
        credit={credit}
        sourceUrl={sourceUrl}
      />
    );
  }

  return (
    <>
      <Crumbs rover={rover} name={name} sol={sol} />
      {body}
    </>
  );
}

function Head({
  rover,
  name,
  sol,
  detail,
  lat,
}: {
  rover: string;
  name: string;
  sol: number;
  detail: WeatherSolDetail | null;
  lat: number | null;
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
                </dd>
              </div>
            )}
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
  sourceUrl,
}: {
  rover: string;
  name: string;
  sol: number;
  detail: WeatherSolDetail;
  lat: number | null;
  credit: string;
  sourceUrl?: string;
}) {
  const [cur, setCur] = useState<{ hour: number; active: boolean; live: boolean }>({
    hour: 13,
    active: false,
    live: false,
  });
  const [night, setNight] = useState(true);
  const defs = useMemo(() => chartDefs(detail), [detail]);
  const rad = useHourlyRadiation(rover, sol);
  const sun = sunTimes(detail.ls, lat ?? fallbackLat(rover));
  const clamp24 = (v: number) => Math.min(24, Math.max(0, v));
  const shade: [number, number] | null = night && sun ? [clamp24(sun[0]), clamp24(sun[1])] : null;
  const onHour = (h: number | null) =>
    setCur((c) => {
      if (h === null) return c.active ? { ...c, active: false, live: false } : c;
      // Same hour: keep the state object, so nothing re-renders on every mouse move.
      return c.active && c.hour === h && !c.live ? c : { hour: h, active: true, live: false };
    });
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
      <Head rover={rover} name={name} sol={sol} detail={detail} lat={lat} />
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
      <div class="hour-bar" style={{ marginTop: 16 }} role="group" aria-label="Shared hour cursor">
        <label for="hourIn">Inspect hour (LMST)</label>
        <input
          type="range"
          id="hourIn"
          min={0}
          max={23}
          step={1}
          value={cur.hour}
          aria-valuetext={`${hourLabel(cur.hour)} LMST`}
          onInput={(e) =>
            setCur({
              hour: Number((e.currentTarget as HTMLInputElement).value),
              active: true,
              live: true,
            })
          }
        />
        <output for="hourIn">{cur.active ? hourLabel(cur.hour) : "none selected"}</output>
        {sun && (
          <label class="chk">
            <input
              type="checkbox"
              checked={night}
              onChange={(e) => setNight((e.currentTarget as HTMLInputElement).checked)}
            />{" "}
            Shade night <span class="opt">approx.</span>
          </label>
        )}
      </div>
      <div class="visually-hidden" role="status" aria-live="polite" data-testid="hour-announce">
        {announce}
      </div>
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
        <HoursTable defs={defs} hour={cur.active ? cur.hour : null} />
      </div>
      <section class="card panel sr-sources" aria-labelledby="srcH" style={{ marginTop: 16 }}>
        <h2 id="srcH" style={{ fontSize: "var(--fs-md)" }}>
          Credit and source files
        </h2>
        <p class="credit-line">
          Credit: {credit}
          {sourceUrl && (
            <>
              {" "}
              (
              <a href={sourceUrl} rel="noopener noreferrer">
                archive
              </a>
              )
            </>
          )}
        </p>
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

function HoursTable({ defs, hour }: { defs: ChartDef[]; hour: number | null }) {
  const cols = defs.flatMap((d) =>
    d.noData
      ? []
      : d.series.map((s) => ({
          head: `${d.title}${d.series.length > 1 ? ` ${s.name}` : ""} (${d.unit})`,
          values: s.values,
          digits: d.digits,
        })),
  );
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
                {cols.map((c) => {
                  const v = c.values[h];
                  return (
                    <td key={c.head}>
                      {v === null || v === undefined
                        ? "–"
                        : `${v < 0 && Number(Math.abs(v).toFixed(c.digits)) !== 0 ? "−" : ""}${Math.abs(v).toFixed(c.digits)}`}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}
