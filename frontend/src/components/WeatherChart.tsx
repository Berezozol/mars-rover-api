import { useId, useMemo, useRef, useState } from "preact/hooks";
import type { WeatherParameter, WeatherSol } from "../api/types";
import {
  bandOf,
  type Band,
  flagShort,
  flagWords,
  fmtV,
  hasGustLine,
  isPartial,
  nearestIndex,
  niceTicks,
  paramDigits,
  paramLabel,
  paramName,
  zeroBased,
} from "../lib/weather";
import { fmt } from "../lib/format";
import { useWidth } from "./BarChart";
import { EmptyState } from "./States";

const L = 46;
const R = 10;
const T = 24;
const B = 24;

interface Pt {
  r: WeatherSol;
  b: Band;
  partial: boolean;
}

const MARK_DIAMOND = (x: number) => `M${x} 4l6 6-6 6-6-6z`;
const MARK_CROSS = (x: number) => `M${x - 4.5} 5.5l9 9m-9 0l9-9`;

interface Props {
  items: WeatherSol[];
  k: string;
  parameter: WeatherParameter | undefined;
  /** The sol axis `[from, to]`. */
  domain: [number, number];
  /** Sols after this one are not published yet (grey zone), or null. */
  zoneFrom: number | null;
  roverName: string;
  selected: number;
  onSelect: (sol: number) => void;
}

/** Daily chart of one parameter: min-max band, mean line, partial sols drawn differently, anomaly marks. */
export function WeatherChart(p: Props) {
  const host = useRef<HTMLDivElement>(null);
  const w = Math.max(useWidth(host, 640), 280);
  const h = w < 480 ? 240 : 270;
  const [x0, x1] = p.domain;
  const [tipAt, setTipAt] = useState<{ i: number; left: number } | null>(null);
  const unit = p.parameter?.unit ?? "";
  const d = paramDigits(p.k);

  const g = useMemo(() => {
    const pts: Pt[] = [];
    for (const r of p.items) {
      if (r.sol < x0 || r.sol > x1) continue;
      const b = bandOf(r, p.k);
      if (b) pts.push({ r, b, partial: isPartial(r) });
    }
    const empty = pts.length === 0;
    let lo = empty ? 0 : Math.min(...pts.map((q) => q.b.lo ?? q.b.mean));
    const hi = empty ? 1 : Math.max(...pts.map((q) => q.b.hi));
    if (zeroBased(p.k)) lo = 0;
    const ticks =
      p.k === "humidity_pct" && hi > 100
        ? niceTicks(0, Math.ceil(hi / 10) * 10, 4)
        : niceTicks(lo, hi, 4);
    const ymin = ticks[0] ?? 0;
    const ymax = ticks[ticks.length - 1] ?? 1;
    const pw = w - L - R;
    const X = (s: number) => L + ((s - x0) / (x1 - x0 || 1)) * pw;
    const Y = (v: number) => T + (1 - (v - ymin) / (ymax - ymin || 1)) * (h - T - B);
    const px = pw / (x1 - x0 || 1);
    const bw = Math.max(1.4, px);
    const f = (n: number) => n.toFixed(1);
    const gap = (a: Pt, b: Pt) => b.r.sol - a.r.sol > 1.5;

    // Full sols: one polygon per run (a gap in the sols lifts the pen), partial sols: thin grey bars.
    let band = "";
    let part = "";
    let run: Pt[] = [];
    const flush = () => {
      const first = run[0];
      if (first && first.b.lo !== null) {
        if (run.length > 1) {
          band +=
            run.map((q, i) => `${i ? "L" : "M"}${f(X(q.r.sol))} ${f(Y(q.b.hi))}`).join("") +
            run
              .slice()
              .reverse()
              .map((q) => `L${f(X(q.r.sol))} ${f(Y(q.b.lo ?? q.b.mean))}`)
              .join("") +
            "Z";
        } else {
          const hh = Math.max(1, Y(first.b.lo) - Y(first.b.hi));
          band += `M${f(X(first.r.sol) - bw / 2)} ${f(Y(first.b.hi))}h${f(bw)}v${f(hh)}h${f(-bw)}z`;
        }
      }
      run = [];
    };
    for (const q of pts) {
      if (q.partial) {
        flush();
        if (q.b.lo !== null) {
          const hh = Math.max(1.5, Y(q.b.lo) - Y(q.b.hi));
          part += `M${f(X(q.r.sol) - bw / 2)} ${f(Y(q.b.hi))}h${f(bw)}v${f(hh)}h${f(-bw)}z`;
        }
        continue;
      }
      const last = run[run.length - 1];
      if (last && gap(last, q)) flush();
      run.push(q);
    }
    flush();

    let gust = "";
    let solid = "";
    let dashed = "";
    let lastStyle: boolean | null = null;
    pts.forEach((q, i) => {
      const prev = pts[i - 1];
      if (!prev || gap(prev, q)) {
        lastStyle = null;
        return;
      }
      if (hasGustLine(p.k)) {
        gust += `${lastStyle === null ? "M" + f(X(prev.r.sol)) + " " + f(Y(prev.b.hi)) : ""}L${f(X(q.r.sol))} ${f(Y(q.b.hi))}`;
      }
      const dash = q.partial || prev.partial;
      const seg = `${lastStyle === dash ? "" : `M${f(X(prev.r.sol))} ${f(Y(prev.b.mean))}`}L${f(X(q.r.sol))} ${f(Y(q.b.mean))}`;
      if (dash) dashed += seg;
      else solid += seg;
      lastStyle = dash;
    });

    const dots = px > 4 ? pts.filter((q) => q.partial) : [];
    const marks: { sol: number; code: string; x: number }[] = [];
    for (const q of pts) {
      for (const fl of q.r.flags) {
        if (fl.parameter !== p.k) continue;
        if (fl.code === "humidity_over_100" || fl.code === "out_of_range") {
          marks.push({ sol: q.r.sol, code: fl.code, x: X(q.r.sol) });
        }
      }
    }
    // The axis ends exactly at the ends of the range; round ticks in between keep their distance from them.
    const keep = (x1 - x0) * 0.1;
    const xt = [
      x0,
      ...niceTicks(x0, x1, w < 480 ? 2 : 4).filter((t) => t > x0 + keep && t < x1 - keep),
      x1,
    ];
    return {
      pts,
      ticks,
      ymin,
      X,
      Y,
      band,
      part,
      gust,
      solid,
      dashed,
      dots,
      marks,
      empty,
      xt,
      lo,
      hi,
    };
  }, [p.items, p.k, x0, x1, w, h]);

  const gustMode = hasGustLine(p.k);
  const sel = g.pts.find((q) => q.r.sol === p.selected);
  const sols = g.pts.map((q) => q.r.sol);
  const hum = p.k === "humidity_pct" && (g.ticks[g.ticks.length - 1] ?? 0) > 100;
  const clipId = useId();
  // The grey "not published yet" zone starts after the newest published sol and never leaves the plot.
  const gx =
    p.zoneFrom !== null && p.zoneFrom < x1
      ? Math.max(L, Math.min(g.X(p.zoneFrom + 0.5), w - R))
      : null;
  const label = g.empty
    ? `${paramLabel(p.k)}, daily values, ${p.roverName}, sols ${Math.round(x0)} to ${Math.round(x1)}. No data in this range.`
    : `${paramLabel(p.k)}, daily ${gustMode ? "mean and gust" : "minimum to maximum band and mean"}, ${p.roverName}, sols ${Math.round(x0)} to ${Math.round(x1)}. Overall ${fmtV(g.lo, d)} to ${fmtV(g.hi, d)} ${unit}.`;

  const nearest = (ev: MouseEvent | PointerEvent): number => {
    const svg = ev.currentTarget as SVGSVGElement;
    const rc = svg.getBoundingClientRect();
    const xv = ((ev.clientX - rc.left) * (w / (rc.width || w)) - L) / (w - L - R);
    return nearestIndex(sols, x0 + xv * (x1 - x0));
  };
  const tip = tipAt ? g.pts[tipAt.i] : null;

  return (
    <div ref={host} class="wx-chartwrap">
      <svg
        class="chart wxc"
        viewBox={`0 0 ${w} ${h}`}
        width={w}
        height={h}
        role="img"
        aria-label={label}
        data-testid="weather-chart"
        onPointerMove={(ev) => {
          if (ev.pointerType !== "mouse") return;
          const host2 = (ev.currentTarget as SVGSVGElement).parentElement;
          const left = ev.clientX - (host2?.getBoundingClientRect().left ?? 0) + 12;
          setTipAt({ i: nearest(ev), left });
        }}
        onPointerLeave={() => setTipAt(null)}
        onClick={(ev) => {
          const q = g.pts[nearest(ev)];
          if (q) p.onSelect(q.r.sol);
        }}
      >
        {(g.empty ? [] : g.ticks).map((t) => (
          <g key={t}>
            <line class="grid" x1={L} x2={w - R} y1={g.Y(t)} y2={g.Y(t)} />
            <text x={L - 6} y={g.Y(t) + 4} text-anchor="end">
              {fmtV(t, t % 1 ? 1 : 0)}
            </text>
          </g>
        ))}
        {g.xt.map((t, i) => (
          <text
            key={t}
            x={g.X(t)}
            y={h - 6}
            text-anchor={i === 0 ? "start" : i === g.xt.length - 1 ? "end" : "middle"}
          >
            {fmt(Math.round(t))}
          </text>
        ))}
        <line class="axis" x1={L} x2={w - R} y1={g.Y(g.ymin)} y2={g.Y(g.ymin)} />
        <defs>
          <clipPath id={clipId}>
            <rect x={L} y={T} width={Math.max(w - L - R, 0)} height={Math.max(h - T - B, 0)} />
          </clipPath>
        </defs>
        <g clip-path={`url(#${clipId})`}>
          {gx !== null && (
            <>
              <rect
                class="gap"
                data-testid="not-published-zone"
                x={gx}
                y={T}
                width={Math.max(w - R - gx, 0)}
                height={h - T - B}
              />
              {w - R - gx > 90 && (
                <text class="zone-label" x={w - R - 6} y={T + 14} text-anchor="end">
                  not published yet
                </text>
              )}
            </>
          )}
        </g>
        {hum && (
          <>
            <line class="wx-ref" x1={L} x2={w - R} y1={g.Y(100)} y2={g.Y(100)} />
            <text class="wx-reft" x={L + 4} y={g.Y(100) - 4}>
              100 %: above it is impossible
            </text>
          </>
        )}
        <g clip-path={`url(#${clipId})`}>
          {g.band && <path class="wx-band" d={g.band} />}
          {g.part && <path class="wx-band-p" d={g.part} />}
          {gustMode && g.gust && <path class="ln wx-gust" d={g.gust} />}
          {g.dashed && <path class="ln wx-mean-d" d={g.dashed} />}
          {g.solid && <path class="ln wx-mean" d={g.solid} />}
        </g>
        {g.dots.map((q) => (
          <circle
            key={q.r.sol}
            class="wx-pdot"
            data-testid="weather-mean-partial"
            data-sol={q.r.sol}
            cx={g.X(q.r.sol)}
            cy={g.Y(q.b.mean)}
            r={2.4}
          />
        ))}
        {g.marks.map((m) => (
          <path
            key={`${m.code}-${m.sol}`}
            class={m.code === "humidity_over_100" ? "wx-mk-h" : "wx-mk-o"}
            d={m.code === "humidity_over_100" ? MARK_DIAMOND(m.x) : MARK_CROSS(m.x)}
            data-testid="weather-flag-marker"
            data-code={m.code}
            data-sol={m.sol}
          />
        ))}
        {sel && (
          <>
            <line class="cursor" x1={g.X(sel.r.sol)} x2={g.X(sel.r.sol)} y1={T} y2={h - B} />
            <circle class="dot-e" cx={g.X(sel.r.sol)} cy={g.Y(sel.b.mean)} r={5} />
          </>
        )}
      </svg>
      {g.empty && (
        <EmptyState title="No data in this range">
          No sols with {paramName(p.k)} data between sol {Math.round(x0)} and sol {Math.round(x1)}.
          Choose another range above.
        </EmptyState>
      )}
      {tip && tipAt && (
        <div
          class="wx-tip"
          data-testid="weather-tooltip"
          aria-hidden="true"
          style={{ left: Math.max(4, Math.min(tipAt.left, w - 220)) }}
        >
          <b>Sol {tip.r.sol}</b> · {tip.r.earth_date}
          <br />
          {tipValues(p.k, tip.b, d, unit)}
          {tip.r.flags.map((fl, i) => (
            <span key={i}>
              <br />
              <span class="tip-f">{flagShort(fl)}:</span> {flagWords(fl)}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function tipValues(k: string, b: Band, d: number, unit: string): string {
  if (hasGustLine(k)) return `mean ${fmtV(b.mean, d)} ${unit}, gust ${fmtV(b.hi, d)} ${unit}`;
  if (b.lo === null || k === "uv_wm2")
    return `mean ${fmtV(b.mean, d)}, max ${fmtV(b.hi, d)} ${unit}`;
  return `mean ${fmtV(b.mean, d)} ${unit} (${fmtV(b.lo, d)} to ${fmtV(b.hi, d)})`;
}

/** Legend of the daily chart; the marks shown depend on the parameter. */
export function WeatherLegend({ k }: { k: string }) {
  const gust = hasGustLine(k);
  const cross = (
    <span>
      <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
        <path class="wx-mk-o" d="M1.5 1.5l9 9m-9 0l9-9" />
      </svg>{" "}
      readings left out
    </span>
  );
  return (
    <span class="legend">
      <span>
        <i class="sw" aria-hidden="true" />
        {gust ? "gust (dashed line)" : k === "uv_wm2" ? "0 to daily max" : "daily min–max"}
      </span>
      <span>
        <i aria-hidden="true" />
        mean
      </span>
      <span>
        <i class="sw sw-part" aria-hidden="true" />
        partial coverage (under 18 of 24 hours)
      </span>
      {k === "humidity_pct" && (
        <>
          <span>
            <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
              <path class="wx-mk-h" d="M6 0l6 6-6 6-6-6z" />
            </svg>{" "}
            above 100 %
          </span>
          {cross}
        </>
      )}
      {k !== "humidity_pct" && cross}
    </span>
  );
}
