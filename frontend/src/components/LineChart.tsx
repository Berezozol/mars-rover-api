import { useRef } from "preact/hooks";
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
  xmax: number;
  /** Value axis, starting at 0. */
  ymax: number;
  yticks: number[];
  xticks: number[];
  fy?: (v: number) => string;
  lines: ChartLine[];
  /** A shaded zone along the x axis, e.g. sols that are not published yet. */
  zone?: { from: number; to: number; label: string } | null;
  /** The inspected position: a vertical line and one dot per series. */
  cursor?: { x: number; dots: { y: number; cls: string }[] } | null;
  testId?: string;
}

const L = 44;
const R = 10;
const T = 8;
const B = 22;

function path(l: ChartLine, X: (x: number) => number, Y: (y: number) => number): string {
  let d = "";
  let prev: number | null = null;
  for (const [x, y] of l.pts) {
    const pen =
      prev === null || (l.breakAfter !== undefined && x - prev > l.breakAfter) ? "M" : "L";
    d += `${pen}${X(x).toFixed(1)} ${Y(y).toFixed(1)}`;
    prev = x;
  }
  return d;
}

/** A line chart drawn at the width of its container (no horizontal scrolling), axes from zero. */
export function LineChart(p: Props) {
  const host = useRef<HTMLDivElement>(null);
  const w = Math.max(useWidth(host, 600), 240);
  const h = p.height;
  const X = (x: number) => L + (x / p.xmax) * (w - L - R);
  const Y = (y: number) => T + (1 - y / p.ymax) * (h - T - B);
  const fy = p.fy ?? String;
  const z = p.zone && p.zone.to > p.zone.from ? p.zone : null;
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
      >
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
        <line class="axis" x1={L} x2={w - R} y1={Y(0)} y2={Y(0)} />
        {z && (
          <>
            <rect
              class="gap"
              x={X(z.from)}
              y={T}
              width={Math.max(X(z.to) - X(z.from), 0)}
              height={h - T - B}
            />
            {z.label && (
              <text class="zone-label" x={X(z.from) + 6} y={T + 14}>
                {z.label}
              </text>
            )}
          </>
        )}
        {p.lines.map((l) => (
          <path key={l.cls} class={`ln ${l.cls}`} d={path(l, X, Y)} />
        ))}
        {p.cursor && (
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
