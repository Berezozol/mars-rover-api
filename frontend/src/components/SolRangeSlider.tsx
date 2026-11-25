import type { ComponentChildren } from "preact";
import { useEffect, useId, useRef, useState } from "preact/hooks";
import {
  MIN_RANGE_SOLS,
  presetRange,
  RANGE_PRESETS,
  type RangeBounds,
  type SolRange,
} from "../lib/solRange";
import { niceTicks } from "../lib/weather";
import { fmt } from "../lib/format";

interface Props {
  /** The sol axis of the track: the first sol of the data and the newest sol of the rover. */
  bounds: RangeBounds;
  /** `source_max_sol`: sols after it are not published yet (hatched part of the track). */
  published: number;
  range: SolRange;
  onChange: (range: SolRange) => void;
  /** A coarse picture of all the data, values 0..1 or null (see `overviewOf`). */
  overview?: (number | null)[];
  /** What the range holds, shown next to the title (dates, number of sols with data). */
  summary?: ComponentChildren;
}

const GAP = MIN_RANGE_SOLS - 1;
const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), hi);
type Which = "from" | "to";

/**
 * Two-handle range slider over the whole data, with "From sol" / "To sol" fields and presets.
 * Handles are `role="slider"`: arrows move by 1 sol, PageUp/PageDown by 10, Home/End to the limit.
 */
export function SolRangeSlider({ bounds, published, range, onChange, overview, summary }: Props) {
  const { first, max } = bounds;
  const [from, to] = range;
  const id = useId();
  const track = useRef<HTMLDivElement>(null);
  const live = useRef({ range, bounds, onChange });
  live.current = { range, bounds, onChange };
  const drag = useRef<{ which: Which | "span"; x: number; from: number; to: number } | null>(null);
  const [dragging, setDragging] = useState<Which | null>(null);

  const span = Math.max(max - first, 1);
  const pct = (v: number) => ((clamp(v, first, max) - first) / span) * 100;

  const set = (a: number, b: number) => {
    const cur = live.current.range;
    if (a !== cur[0] || b !== cur[1]) live.current.onChange([a, b]);
  };
  const move = (which: Which, v: number) => {
    const [a, b] = live.current.range;
    const { first: lo, max: hi } = live.current.bounds;
    if (which === "from") set(clamp(Math.round(v), lo, b - GAP), b);
    else set(a, clamp(Math.round(v), a + GAP, hi));
  };
  const solAt = (clientX: number): number => {
    const r = track.current?.getBoundingClientRect();
    const { first: lo, max: hi } = live.current.bounds;
    if (!r || r.width === 0) return lo;
    return Math.round(lo + clamp((clientX - r.left) / r.width, 0, 1) * (hi - lo));
  };

  const onKey = (which: Which) => (e: KeyboardEvent) => {
    const steps: Record<string, number> = {
      ArrowLeft: -1,
      ArrowDown: -1,
      ArrowRight: 1,
      ArrowUp: 1,
      PageUp: 10,
      PageDown: -10,
    };
    const [a, b] = live.current.range;
    const { first: lo, max: hi } = live.current.bounds;
    let v = which === "from" ? a : b;
    const step = steps[e.key];
    if (step !== undefined) v += step;
    else if (e.key === "Home") v = which === "from" ? lo : a + GAP;
    else if (e.key === "End") v = which === "from" ? b - GAP : hi;
    else return;
    e.preventDefault();
    move(which, v);
  };

  const capture = (el: Element, pointerId: number) => {
    try {
      el.setPointerCapture?.(pointerId);
    } catch {
      // not supported (tests)
    }
  };
  const onHandleDown = (which: Which) => (e: PointerEvent) => {
    e.preventDefault();
    e.stopPropagation();
    capture(e.currentTarget as Element, e.pointerId);
    drag.current = { which, x: e.clientX, from: range[0], to: range[1] };
    setDragging(which);
    (e.currentTarget as HTMLElement).focus();
  };
  const onHandleMove = (which: Which) => (e: PointerEvent) => {
    if (e.pointerType === "mouse" && e.buttons === 0) return endDrag();
    if (drag.current?.which === which) move(which, solAt(e.clientX));
  };
  const endDrag = () => {
    drag.current = null;
    setDragging(null);
  };
  const onSpanDown = (e: PointerEvent) => {
    if (e.target !== e.currentTarget) return;
    e.preventDefault();
    capture(e.currentTarget as Element, e.pointerId);
    drag.current = { which: "span", x: e.clientX, from: range[0], to: range[1] };
  };
  const onSpanMove = (e: PointerEvent) => {
    const d = drag.current;
    if (!d || d.which !== "span") return;
    if (e.pointerType === "mouse" && e.buttons === 0) return endDrag();
    const w = track.current?.getBoundingClientRect().width || 1;
    const shift = Math.round(((e.clientX - d.x) / w) * (max - first));
    const width = d.to - d.from;
    const a = clamp(d.from + shift, first, max - width);
    set(a, a + width);
  };
  const onTrackDown = (e: PointerEvent) => {
    const t = e.target as Element;
    if (t.closest(".rs-h") || t.closest(".rs-sel")) return;
    const v = solAt(e.clientX);
    move(Math.abs(v - from) <= Math.abs(v - to) ? "from" : "to", v);
  };

  // Fields: a draft is checked as a whole before the range changes.
  const [draft, setDraft] = useState({ from: String(from), to: String(to) });
  const [bad, setBad] = useState<{ from?: boolean; to?: boolean; msg: string } | null>(null);
  useEffect(() => {
    setDraft({ from: String(from), to: String(to) });
    setBad(null);
  }, [from, to]);

  const commit = (d = draft) => {
    const isInt = (s: string) => /^\s*\d+\s*$/.test(s);
    const fail = (msg: string, f: boolean, t: boolean) => setBad({ from: f, to: t, msg });
    if (!isInt(d.from) || !isInt(d.to)) {
      return fail("Enter whole sol numbers in both fields.", !isInt(d.from), !isInt(d.to));
    }
    const a = Number(d.from);
    const b = Number(d.to);
    if (a === from && b === to) return setBad(null);
    if (a < first) return fail(`From sol must be at least ${fmt(first)}.`, true, false);
    if (a > max) return fail(`From sol cannot be larger than ${fmt(max)}.`, true, false);
    if (b > max) return fail(`To sol cannot be larger than ${fmt(max)}.`, false, true);
    if (b < first) return fail(`To sol must be at least ${fmt(first)}.`, false, true);
    if (a > b) return fail("From sol must not be greater than To sol.", true, true);
    if (b - a < GAP) return fail(`Select at least ${MIN_RANGE_SOLS} sols.`, false, true);
    setBad(null);
    live.current.onChange([a, b]);
  };
  const onKeyInput = (e: KeyboardEvent) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    commit();
  };

  const ticks = niceTicks(first, max, 6).filter((t) => t >= first && t <= max);
  const path = overview
    ? overview
        .map((v, i) =>
          v === null
            ? ""
            : `${overview[i - 1] == null ? "M" : "L"}${((i / Math.max(overview.length - 1, 1)) * 1000).toFixed(1)} ${(92 - v * 80).toFixed(1)}`,
        )
        .join("")
    : "";
  const errId = `${id}-err`;
  const valuetext = (v: number) => `sol ${v}${v > published ? " (not published yet)" : ""}`;

  return (
    <div class="rs" role="group" aria-labelledby={`${id}-h`}>
      <div class="rs-title">
        <h3 id={`${id}-h`}>Range of sols</h3>
        <div class="rs-sum">{summary}</div>
      </div>
      <div class="rs-wrap">
        <div class="rs-track" ref={track} onPointerDown={onTrackDown} data-testid="range-track">
          <svg class="rs-ov" viewBox="0 0 1000 100" preserveAspectRatio="none" aria-hidden="true">
            <path d={path} />
          </svg>
          {published < max && (
            <div
              class="rs-unpub"
              style={{ width: `${Math.max(0, 100 - pct(published))}%` }}
              aria-hidden="true"
            />
          )}
          <div class="rs-dim rs-dim-l" style={{ width: `${pct(from)}%` }} />
          <div class="rs-dim rs-dim-r" style={{ width: `${100 - pct(to)}%` }} />
          <div
            class="rs-sel"
            style={{ left: `${pct(from)}%`, width: `${pct(to) - pct(from)}%` }}
            onPointerDown={onSpanDown}
            onPointerMove={onSpanMove}
            onPointerUp={endDrag}
            onPointerCancel={endDrag}
            onLostPointerCapture={endDrag}
          />
          {(["from", "to"] as const).map((which) => {
            const v = which === "from" ? from : to;
            return (
              <div
                key={which}
                class={`rs-h${dragging === which ? " drag" : ""}`}
                style={{ left: `${pct(v)}%` }}
                role="slider"
                tabIndex={0}
                aria-label={which === "from" ? "Range start, sol" : "Range end, sol"}
                aria-valuemin={which === "from" ? first : from + GAP}
                aria-valuemax={which === "from" ? to - GAP : max}
                aria-valuenow={v}
                aria-valuetext={valuetext(v)}
                onKeyDown={onKey(which)}
                onPointerDown={onHandleDown(which)}
                onPointerMove={onHandleMove(which)}
                onPointerUp={endDrag}
                onPointerCancel={endDrag}
                onLostPointerCapture={endDrag}
              >
                <span class="rs-hv" aria-hidden="true">
                  {fmt(v)}
                </span>
              </div>
            );
          })}
        </div>
        <div class="rs-axis" aria-hidden="true">
          {ticks.map((t) => (
            <span key={t} style={{ left: `${pct(t)}%` }}>
              {fmt(t)}
            </span>
          ))}
        </div>
      </div>
      <div class="rs-inputs">
        {(["from", "to"] as const).map((which) => (
          <div class="field" key={which}>
            <label for={`${id}-${which}`}>{which === "from" ? "From sol" : "To sol"}</label>
            <input
              id={`${id}-${which}`}
              type="number"
              inputMode="numeric"
              min={first}
              max={max}
              step={1}
              value={draft[which]}
              aria-invalid={bad?.[which] ? "true" : undefined}
              aria-describedby={bad ? errId : undefined}
              onInput={(e) =>
                setDraft({ ...draft, [which]: (e.currentTarget as HTMLInputElement).value })
              }
              onChange={(e) => {
                const next = { ...draft, [which]: (e.currentTarget as HTMLInputElement).value };
                setDraft(next);
                commit(next);
              }}
              onKeyDown={onKeyInput}
              onBlur={() => commit()}
            />
          </div>
        ))}
        <div class="rs-presets" role="group" aria-label="Range presets">
          {RANGE_PRESETS.map((p) => {
            const r = presetRange(p.id, { first, sourceMax: published });
            return (
              <button
                key={p.id}
                class="btn btn-sm"
                type="button"
                aria-pressed={r[0] === from && r[1] === to}
                onClick={() => onChange(r)}
              >
                {p.label}
              </button>
            );
          })}
        </div>
      </div>
      {bad && (
        <p class="rs-err" id={errId} role="alert">
          {bad.msg}
        </p>
      )}
      <p class="hint rs-hint">
        The track shows every sol; the hatched part on the right is not published yet. Drag a
        handle, drag the highlighted span to move it, or focus a handle: arrow keys move it by 1
        sol, PageUp and PageDown by 10, Home and End to the limit. The default range ends at the
        newest published sol, not at the rover&rsquo;s latest sol.
      </p>
    </div>
  );
}
