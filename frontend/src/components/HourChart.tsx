import { useRef } from "preact/hooks";
import { type ChartDef, hourLabel, readoutOf } from "../lib/solReport";
import { niceTicks } from "../lib/weather";
import { useWidth } from "./BarChart";

interface Props {
  def: ChartDef;
  sol: number;
  /** Hours (LMST) before sunrise and after sunset are shaded; `null` = no shading. */
  night: [number, number] | null;
  /** Shared cursor: the hour all charts point at, or `null`. */
  hour: number | null;
  onHour: (hour: number | null) => void;
}

const L = 48;
const R = 10;
const T = 10;
const B = 26;

const num = (v: number, d: number) =>
  `${v < 0 && Number(Math.abs(v).toFixed(d)) !== 0 ? "−" : ""}${Math.abs(v).toFixed(d)}`;

const f = (n: number) => n.toFixed(1);

/** Runs of consecutive hours that have a value: a missing hour breaks the line (no interpolation). */
function runs<T>(values: (T | null | undefined)[]): { h: number; v: T }[][] {
  const out: { h: number; v: T }[][] = [];
  let cur: { h: number; v: T }[] = [];
  values.forEach((v, h) => {
    if (v === null || v === undefined) {
      if (cur.length) out.push(cur);
      cur = [];
    } else cur.push({ h, v });
  });
  if (cur.length) out.push(cur);
  return out;
}

/**
 * One parameter by local mean solar time, hours 0-24. The line is the hourly mean (broken at hours
 * without readings), the band the lowest to highest value of each hour (or of the whole sol when the
 * server has no hourly extremes). Hover, touch or the page's hour slider move the shared cursor.
 */
export function HourChart({ def, sol, night, hour, onHour }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const W = Math.max(useWidth(host, 360), 240);
  const H = W < 480 ? 175 : 195;
  const pw = W - L - R;
  const ph = H - T - B;

  // value range
  const all = def.series.flatMap((s) => s.values).filter((v): v is number => v !== null);
  let lo = Infinity;
  let hi = -Infinity;
  for (const v of all) {
    lo = Math.min(lo, v);
    hi = Math.max(hi, v);
  }
  for (const b of def.hourBand ?? []) {
    if (b) {
      lo = Math.min(lo, b.lo);
      hi = Math.max(hi, b.hi);
    }
  }
  if (!def.hourBand && def.solBand) {
    lo = Math.min(lo, def.solBand[0]);
    hi = Math.max(hi, def.solBand[1]);
  }
  if (def.refLine) hi = Math.max(hi, def.refLine.value);
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) {
    lo = 0;
    hi = 1;
  }
  if (def.zero) lo = Math.min(0, lo);
  const ticks = niceTicks(lo, hi, 4);
  const ymin = ticks[0]!;
  const ymax = ticks[ticks.length - 1]!;
  const step = ticks.length > 1 ? ticks[1]! - ticks[0]! : 1;
  const dec = step >= 1 ? 0 : step >= 0.1 ? 1 : step >= 0.01 ? 2 : 3;
  const X = (h: number) => L + (h / 24) * pw;
  const Y = (v: number) => T + (1 - (v - ymin) / (ymax - ymin || 1)) * ph;

  const xstep = W < 480 ? 6 : 3;
  const xticks: number[] = [];
  for (let h = 0; h <= 24; h += xstep) xticks.push(h);

  const holes: number[] = [];
  for (let h = 0; h < 24; h++) if (def.series.every((s) => s.values[h] === null)) holes.push(h);

  // hourly min-max band, one polygon per run of hours
  const bandPaths = runs(def.hourBand ?? []).map((run) => {
    if (run.length === 1) {
      const { h, v } = run[0]!;
      const w = (pw / 24) * 0.5;
      return (
        <rect
          key={`b${h}`}
          class="hr-band"
          x={f(X(h + 0.5) - w / 2)}
          y={f(Y(v.hi))}
          width={f(w)}
          height={f(Math.max(1, Y(v.lo) - Y(v.hi)))}
        />
      );
    }
    const top = run.map((p) => `${f(X(p.h + 0.5))} ${f(Y(p.v.hi))}`);
    const bottom = run
      .slice()
      .reverse()
      .map((p) => `${f(X(p.h + 0.5))} ${f(Y(p.v.lo))}`);
    return <path key={`b${run[0]!.h}`} class="hr-band" d={`M${[...top, ...bottom].join("L")}Z`} />;
  });

  const at = (clientX: number, svg: SVGSVGElement) => {
    const rc = svg.getBoundingClientRect();
    const x = ((clientX - rc.left) * W) / (rc.width || W);
    return Math.min(23, Math.max(0, Math.floor(((x - L) / pw) * 24)));
  };

  const summary = def.summary;
  const label = `${def.title}, ${def.unit}, hourly by local mean solar time, sol ${sol}. ${summary}`;
  const badge = def.quality ?? null;
  const twoLines = def.series.length > 1;

  return (
    <div class="card pc" id={`pc-${def.id}`} data-testid={`hour-chart-${def.id}`}>
      <div class="pc-head">
        <h3>{def.title}</h3>
        <span class="muted xsmall">{def.unit}</span>
        {badge && (
          <span class={`badge no-dot ${badge === "preliminary" ? "badge-prelim" : "badge-est"}`}>
            {badge}
          </span>
        )}
        {twoLines && (
          <span class="legend" style={{ marginLeft: "auto" }}>
            {def.series.map((s) => (
              <span key={s.name}>
                <i class={s.dashed ? "b" : ""} aria-hidden="true" />
                {s.name}
              </span>
            ))}
          </span>
        )}
      </div>
      <div class="pc-sum">{summary}</div>
      {def.noData ? null : (
        <>
          <div ref={host} class="pc-host">
            <svg
              class="chart hr-chart"
              viewBox={`0 0 ${W} ${H}`}
              width={W}
              height={H}
              role="img"
              aria-label={label}
              onPointerMove={(e) => {
                if (e.pointerType === "mouse" || e.buttons) {
                  onHour(at(e.clientX, e.currentTarget as SVGSVGElement));
                }
              }}
              onPointerDown={(e) => onHour(at(e.clientX, e.currentTarget as SVGSVGElement))}
              onPointerLeave={(e) => {
                if (e.pointerType === "mouse") onHour(null);
              }}
            >
              {ticks.map((t) => (
                <g key={t}>
                  <line class="grid" x1={L} x2={W - R} y1={f(Y(t))} y2={f(Y(t))} />
                  <text x={L - 6} y={f(Y(t) + 4)} text-anchor="end">
                    {num(t, dec)}
                  </text>
                </g>
              ))}
              {xticks.map((h) => (
                <g key={h}>
                  <line class="grid" x1={f(X(h))} x2={f(X(h))} y1={T} y2={T + ph} opacity={0.45} />
                  <text
                    x={f(X(h))}
                    y={H - 8}
                    text-anchor={h === 0 ? "start" : h === 24 ? "end" : "middle"}
                  >
                    {h}
                  </text>
                </g>
              ))}
              {night && (
                <>
                  <rect
                    class="night"
                    x={L}
                    y={T}
                    width={f(Math.max(0, X(night[0]) - L))}
                    height={ph}
                  />
                  <rect
                    class="night"
                    x={f(X(night[1]))}
                    y={T}
                    width={f(Math.max(0, W - R - X(night[1])))}
                    height={ph}
                  />
                </>
              )}
              {holes.map((h) => (
                <rect
                  key={h}
                  class="nodata"
                  data-testid="hour-gap"
                  x={f(X(h))}
                  y={T}
                  width={f(X(h + 1) - X(h))}
                  height={ph}
                />
              ))}
              {bandPaths}
              {!def.hourBand && def.solBand && (
                <g data-testid="sol-range">
                  <line
                    class="solref"
                    x1={L}
                    x2={W - R}
                    y1={f(Y(def.solBand[0]))}
                    y2={f(Y(def.solBand[0]))}
                  />
                  <line
                    class="solref"
                    x1={L}
                    x2={W - R}
                    y1={f(Y(def.solBand[1]))}
                    y2={f(Y(def.solBand[1]))}
                  />
                </g>
              )}
              {def.refLine && (
                <g>
                  <line
                    class="solref"
                    x1={L}
                    x2={W - R}
                    y1={f(Y(def.refLine.value))}
                    y2={f(Y(def.refLine.value))}
                  />
                  <text x={W - R - 4} y={f(Y(def.refLine.value) - 4)} text-anchor="end">
                    {def.refLine.label} {num(def.refLine.value, def.digits)}
                  </text>
                </g>
              )}
              <line class="axis" x1={L} x2={W - R} y1={T + ph} y2={T + ph} />
              {def.series.map((s, si) => (
                <g key={s.name}>
                  {runs(s.values).map((run) =>
                    run.length > 1 ? (
                      <path
                        key={run[0]!.h}
                        class={si ? "ln-hr-b" : "ln-hr"}
                        d={run
                          .map((p, i) => `${i ? "L" : "M"}${f(X(p.h + 0.5))} ${f(Y(p.v))}`)
                          .join("")}
                      />
                    ) : null,
                  )}
                  {s.values.map((v, h) =>
                    v === null ? null : (
                      <circle
                        key={h}
                        class={si ? "pt-b" : "pt"}
                        cx={f(X(h + 0.5))}
                        cy={f(Y(v))}
                        r={si ? 2 : 2.4}
                      />
                    ),
                  )}
                </g>
              ))}
              {hour !== null && (
                <g data-testid="hour-cursor">
                  <rect class="hl-col" x={f(X(hour))} y={T} width={f(pw / 24)} height={ph} />
                  <line
                    class="cursor"
                    x1={f(X(hour + 0.5))}
                    x2={f(X(hour + 0.5))}
                    y1={T}
                    y2={T + ph}
                  />
                </g>
              )}
            </svg>
          </div>
          <div class="pc-ro">
            {hour === null ? (
              <>Hover or tap the chart, or use the hour slider.</>
            ) : (
              <>
                {hourLabel(hour)} LMST
                <br />
                <b>{readoutOf(def, hour)}</b>
              </>
            )}
          </div>
        </>
      )}
      {def.noData && <p class="small muted pc-none">{def.noData}</p>}
    </div>
  );
}
