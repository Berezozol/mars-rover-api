import { useId, useRef } from "preact/hooks";
import { fmt } from "../lib/format";
import { useWidth } from "./BarChart";

export interface ChartLine {
  /** `ln-e`, `ln-b`, `ln-s`, ... (see data.css). */
  cls: string;
  pts: [number, number][];
  /** Lift the pen when two neighbouring points are further apart than this many x units. */
  breakAfter?: number;
}

interface Props {
  /** Text alternative of the picture. */
  label: string;
  height: number;
  /** Left end of the sol axis (default 0). */
  xmin?: number;
  xmax: number;
  /** Bottom of the value axis (default 0). */
  ymin?: number;
  /** Top of the value axis. */
  ymax: number;
  yticks: number[];
  xticks: number[];
  fy?: (v: number) => string;
  lines: ChartLine[];
  /** A shaded zone along the x axis, e.g. sols that are not published yet. It is cut at the plot area. */
  zone?: { from: number; to: number; label: string } | null;
  /** The inspected position: a vertical line and one dot per series. */
  cursor?: { x: number; dots: { y: number; cls: string }[] } | null;
  /** A click on the plot: the sol under the pointer. */
  onPick?: (sol: number) => void;
  testId?: string;
}

const L = 44;
const R = 10;
const T = 8;
const B = 22;

function path(
  l: ChartLine,
  X: (x: number) => number,
  Y: (y: number) => number,
  x0: number,
  x1: number,
): string {
  let d = "";
  let prev: number | null = null;
  for (const [x, y] of l.pts) {
    if (x < x0 || x > x1) continue;
    const pen =
      prev === null || (l.breakAfter !== undefined && x - prev > l.breakAfter) ? "M" : "L";
    d += `${pen}${X(x).toFixed(1)} ${Y(y).toFixed(1)}`;
    prev = x;
  }
  return d;
}

/** A line chart drawn at the width of its container (no horizontal scrolling). */
export function LineChart(p: Props) {
  const host = useRef<HTMLDivElement>(null);
  const clipId = useId();
  const w = Math.max(useWidth(host, 600), 240);
  const h = p.height;
  const x0 = p.xmin ?? 0;
  const y0 = p.ymin ?? 0;
  const X = (x: number) => L + ((x - x0) / (p.xmax - x0 || 1)) * (w - L - R);
  const Y = (y: number) => T + (1 - (y - y0) / (p.ymax - y0 || 1)) * (h - T - B);
  const fy = p.fy ?? String;
  const z = p.zone && p.zone.to > p.zone.from ? p.zone : null;
  // The zone never leaves the plot: it is cut at both sides of the axis.
  const zl = z ? Math.max(L, Math.min(X(z.from), w - R)) : 0;
  const zr = z ? Math.min(w - R, Math.max(X(z.to), L)) : 0;
  return (
    <div ref={host} class="chart-host">
      <svg
        class="chart"
        viewBox={`0 0 ${w} ${h}`}
        width={w}
        height={h}
        role="img"
        aria-label={p.label}
        data-testid={p.testId}
        style={p.onPick ? { cursor: "crosshair" } : undefined}
        onClick={
          p.onPick
            ? (ev) => {
                const rc = (ev.currentTarget as SVGSVGElement).getBoundingClientRect();
                const px = (ev.clientX - rc.left) * (w / (rc.width || w));
                const f = (px - L) / (w - L - R);
                if (f < 0 || f > 1) return;
                p.onPick!(Math.round(x0 + f * (p.xmax - x0)));
              }
            : undefined
        }
      >
        <defs>
          <clipPath id={clipId}>
            <rect x={L} y={T} width={Math.max(w - L - R, 0)} height={Math.max(h - T - B, 0)} />
          </clipPath>
        </defs>
        {p.yticks.map((t) => (
          <g key={t}>
            <line class="grid" x1={L} x2={w - R} y1={Y(t)} y2={Y(t)} />
            <text x={L - 6} y={Y(t) + 4} text-anchor="end">
              {fy(t)}
            </text>
          </g>
        ))}
        {p.xticks.map((t, i) => (
          <text
            key={t}
            x={X(t)}
            y={h - 6}
            text-anchor={i === 0 ? "start" : t >= p.xmax ? "end" : "middle"}
          >
            {fmt(t)}
          </text>
        ))}
        <line class="axis" x1={L} x2={w - R} y1={Y(y0)} y2={Y(y0)} />
        <g clip-path={`url(#${clipId})`}>
          {z && zr > zl && (
            <>
              <rect
                class="gap"
                data-testid="not-published-zone"
                x={zl}
                y={T}
                width={zr - zl}
                height={h - T - B}
              />
              {z.label && zr - zl > 90 && (
                <text class="zone-label" x={zr - 6} y={T + 14} text-anchor="end">
                  {z.label}
                </text>
              )}
            </>
          )}
          {p.lines.map((l) => (
            <path key={l.cls} class={`ln ${l.cls}`} d={path(l, X, Y, x0, p.xmax)} />
          ))}
        </g>
        {p.cursor && p.cursor.x >= x0 && p.cursor.x <= p.xmax && (
          <g>
            <line class="cursor" x1={X(p.cursor.x)} x2={X(p.cursor.x)} y1={T} y2={h - B} />
            {p.cursor.dots.map((d) => (
              <circle key={d.cls} class={d.cls} cx={X(p.cursor!.x)} cy={Y(d.y)} r={4.5} />
            ))}
          </g>
        )}
      </svg>
    </div>
  );
}
