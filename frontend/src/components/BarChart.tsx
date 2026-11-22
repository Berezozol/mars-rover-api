import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { fmt } from "../lib/format";
import { axisLabel, niceMax, periodLabel, periodName } from "../lib/stats";

export interface Bar {
  /** `YYYY` or `YYYY-MM`. */
  period: string;
  photos: number;
  /** Sols with photos in the period; null for a period the client filled with zero. */
  sols: number | null;
}

interface Props {
  /** Visible heading of the chart, also used in its text summary. */
  title: string;
  unit: "month" | "year";
  bars: Bar[];
  id: string;
}

const M = { l: 46, r: 8, t: 10, b: 24 };
const HEIGHT = 230;

/** Width of an element, kept up to date; `fallback` where nothing can be measured (tests). */
function useWidth(ref: { current: HTMLElement | null }, fallback: number): number {
  const [w, setW] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setW(el.clientWidth || fallback);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return w;
}

/**
 * Photos per period as bars with an axis of its own (each rover has a scale of its own, otherwise
 * the helicopter would vanish next to the rovers). The graphic is an image with a text summary; one
 * bar can be read at a time by pointing at it or with the arrow keys, and the Table view lists the
 * same numbers.
 */
export function BarChart({ title, unit, bars, id }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const cw = Math.max(useWidth(host, 640), 320);
  const [cur, setCur] = useState(-1);

  const n = bars.length;
  const step = Math.max(6, Math.min(48, Math.floor((cw - M.l - M.r) / Math.max(n, 1))));
  const W = Math.max(cw, M.l + M.r + step * n);
  const max = Math.max(...bars.map((b) => b.photos), 0);
  const top = niceMax(max);
  const y = (v: number) => M.t + (HEIGHT - M.t - M.b) * (1 - v / top);
  const peak = bars.reduce((a, b) => (b.photos > a.photos ? b : a), bars[0]!);
  const last = bars[n - 1]!;
  const withPhotos = bars.filter((b) => b.photos > 0).length;

  const summary = `${title}. ${n} ${unit}${n === 1 ? "" : "s"} shown, ${withPhotos} with photos. Peak: ${periodName(peak.period)}, ${fmt(peak.photos)} photos. Latest: ${periodName(last.period)}, ${fmt(last.photos)} photos.`;
  const reading =
    cur < 0
      ? `Peak: ${periodName(peak.period)}, ${fmt(peak.photos)} photos. Point at a bar or use the arrow keys to read one.`
      : `${periodName(bars[cur]!.period)}: ${fmt(bars[cur]!.photos)} photos${
          bars[cur]!.sols ? ` on ${fmt(bars[cur]!.sols!)} sols` : ""
        }`;

  const scroller = useRef<HTMLDivElement>(null);
  // The newest period is at the right: show it first.
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollLeft = el.scrollWidth;
  }, [n, W]);

  const onKey = (e: JSX.TargetedKeyboardEvent<HTMLDivElement>) => {
    let i: number;
    if (e.key === "ArrowRight") i = Math.min(cur + 1, n - 1);
    else if (e.key === "ArrowLeft") i = Math.max(cur < 0 ? n - 1 : cur - 1, 0);
    else if (e.key === "Home") i = 0;
    else if (e.key === "End") i = n - 1;
    else return;
    e.preventDefault();
    setCur(i);
    const el = e.currentTarget;
    const x = M.l + i * step;
    if (x < el.scrollLeft + 20 || x > el.scrollLeft + el.clientWidth - 20) {
      el.scrollLeft = x - el.clientWidth / 2;
    }
  };
  const onMove = (e: JSX.TargetedPointerEvent<HTMLDivElement>) => {
    const svg = e.currentTarget.querySelector("svg");
    if (!svg) return;
    const i = Math.floor((e.clientX - svg.getBoundingClientRect().left - M.l) / step);
    setCur(i >= 0 && i < n ? i : -1);
  };

  const ticks = [0, 0.25, 0.5, 0.75, 1].map((k) => top * k);
  // A label is about 40 px wide: label every k-th year (a year is 12 bars wide in the month chart).
  const every = Math.max(1, Math.ceil(40 / (unit === "month" ? step * 12 : step)));

  return (
    <div ref={host}>
      <p class="chart-read" id={`${id}-read`} aria-live="polite" aria-atomic="true">
        {reading}
      </p>
      <div
        ref={scroller}
        class="chart-scroll"
        tabIndex={0}
        role="group"
        aria-roledescription="bar chart"
        aria-label={`${title}. Use the left and right arrow keys to read one bar at a time; Home and End jump to the first and last. The Table view lists the same numbers.`}
        aria-describedby={`${id}-read`}
        onKeyDown={onKey}
        onPointerMove={onMove}
        onPointerLeave={() => setCur(-1)}
      >
        <svg
          width={W}
          height={HEIGHT}
          viewBox={`0 0 ${W} ${HEIGHT}`}
          role="img"
          aria-label={summary}
          data-testid="stats-chart"
        >
          <title>{title}</title>
          {ticks.map((t) => (
            <g key={t}>
              <line class="gl" x1={M.l} x2={W - M.r} y1={y(t)} y2={y(t)} />
              <text class="ax" x={M.l - 6} y={y(t) + 4} text-anchor="end">
                {axisLabel(t)}
              </text>
            </g>
          ))}
          <line class="axis-line" x1={M.l} x2={W - M.r} y1={y(0)} y2={y(0)} />
          {bars.map((b, i) => {
            const x = M.l + i * step;
            const h = y(0) - y(b.photos);
            const label =
              unit === "year"
                ? i % every === 0
                : b.period.endsWith("-01") && Number(b.period.slice(0, 4)) % every === 0;
            return (
              <g key={b.period}>
                <rect
                  class={i === cur ? "bar-r on" : "bar-r"}
                  x={x + (step > 10 ? 1 : 0.5)}
                  y={y(b.photos)}
                  width={Math.max(step - (step > 10 ? 2 : 1), 1)}
                  height={Math.max(h, b.photos ? 1 : 0)}
                />
                {label && (
                  <text class="ax" x={x + step / 2} y={HEIGHT - 6} text-anchor="middle">
                    {periodLabel(b.period)}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}
