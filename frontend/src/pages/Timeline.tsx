import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { api } from "../api/client";
import type { Manifest, ManifestSol, Rover } from "../api/types";
import { RoverTabs } from "../components/RoverTabs";
import { EmptyState, ErrorAlert, PageSkeleton } from "../components/States";
import { fmt } from "../lib/format";
import { useAsync, useRovers, useTitle } from "../lib/hooks";
import { Link, navigate, qs, useQuery, useRestoreScroll } from "../router";

const MAX_CHART_SOLS = 1000;
const TABLE_ROWS = 20;
const PRESETS = [30, 100, 200];

/** Bin photo counts per 10 sols (index = sol / 10). */
export function binCounts(sols: ManifestSol[], maxSol: number): number[] {
  const bins = new Array<number>(Math.floor(maxSol / 10) + 1).fill(0);
  for (const s of sols) {
    const i = Math.floor(s.sol / 10);
    if (i < bins.length) bins[i]! += s.total_photos;
  }
  return bins;
}

/** Level 0 = no photos, 1–4 = quartiles of the non-empty bins. */
export function levelFn(bins: number[]): (v: number) => 0 | 1 | 2 | 3 | 4 {
  const sorted = bins.filter(Boolean).sort((a, b) => a - b);
  const q = (p: number) => sorted[Math.floor(p * (sorted.length - 1))] ?? 0;
  const cuts = [q(0.25), q(0.5), q(0.75)] as const;
  return (v) => (!v ? 0 : v <= cuts[0] ? 1 : v <= cuts[1] ? 2 : v <= cuts[2] ? 3 : 4);
}

function parseSolParam(v: string | null): number | undefined | "bad" {
  if (v === null || v === "") return undefined;
  return /^\d+$/.test(v) ? Number(v) : "bad";
}

export function Timeline({ roverId }: { roverId: string }) {
  const [rovers, reloadRovers] = useRovers();
  useTitle(`Timeline — ${roverId} — Sol Index`);
  if (rovers.status === "loading") return <PageSkeleton label="Loading rover…" />;
  if (rovers.status === "error") return <ErrorAlert error={rovers.error} onRetry={reloadRovers} />;
  const rover = rovers.data.find((r) => r.id === roverId);
  if (!rover) {
    return (
      <EmptyState
        title="Rover not found"
        actions={
          <Link class="btn btn-sm" href="/">
            All rovers
          </Link>
        }
      >
        We don’t know a rover called “{roverId}”.
      </EmptyState>
    );
  }
  return (
    <>
      <RoverTabs rovers={rovers.data} current={rover.id} to={(id) => `/rovers/${id}/timeline`} />
      <h1>{rover.name} mission timeline</h1>
      <p class="muted">Photos received per sol since landing. Select a sol to browse its photos.</p>
      {rover.max_sol === null ? (
        <EmptyState title="No photos yet">This rover has no imported photos.</EmptyState>
      ) : (
        <TimelineBody rover={rover} maxSol={rover.max_sol} />
      )}
    </>
  );
}

function TimelineBody({ rover, maxSol }: { rover: Rover; maxSol: number }) {
  const [state, retry] = useAsync(() => api.manifest(rover.id), [rover.id]);
  if (state.status === "loading") {
    return (
      <div role="status" aria-live="polite">
        <span class="visually-hidden">Loading the manifest…</span>
        <div class="skeleton" style={{ height: 70, marginBottom: 16 }} aria-hidden="true" />
        <div class="skeleton" style={{ height: 240 }} aria-hidden="true" />
      </div>
    );
  }
  if (state.status === "error") return <ErrorAlert error={state.error} onRetry={retry} />;
  return <Charts rover={rover} maxSol={maxSol} manifest={state.data} />;
}

interface TipState {
  lines: string[];
  x: number;
  y: number;
}

/** Tooltip for any element with `data-tip` (lines separated by a newline); hover and keyboard focus. */
function TipLayer() {
  const [tip, setTip] = useState<TipState | null>(null);
  useEffect(() => {
    const show = (e: Event) => {
      const el = (e.target as HTMLElement | null)?.closest?.<HTMLElement>("[data-tip]");
      if (!el) return;
      const r = el.getBoundingClientRect();
      const me = e as MouseEvent;
      setTip({
        lines: (el.dataset.tip ?? "").split("\n"),
        x: me.clientX || r.left + r.width / 2,
        y: me.clientY || r.top,
      });
    };
    const hide = () => setTip(null);
    const on: [string, EventListener][] = [
      ["mouseover", show],
      ["mousemove", show],
      ["focusin", show],
      ["mouseout", hide],
      ["focusout", hide],
    ];
    for (const [n, f] of on) document.addEventListener(n, f);
    return () => {
      for (const [n, f] of on) document.removeEventListener(n, f);
    };
  }, []);
  if (!tip) return null;
  return (
    <div
      class="tip"
      role="tooltip"
      style={{
        left: Math.max(8, Math.min(window.innerWidth - 268, tip.x - 100)),
        top: Math.max(8, tip.y - 12 - tip.lines.length * 18 - 16),
      }}
    >
      <strong>{tip.lines[0]}</strong>
      {tip.lines.slice(1).map((l, i) => (
        <div key={i}>{l}</div>
      ))}
    </div>
  );
}

function Charts({ rover, maxSol, manifest }: { rover: Rover; maxSol: number; manifest: Manifest }) {
  useRestoreScroll(true);
  const q = useQuery();
  const fromP = parseSolParam(q.get("from_sol"));
  const toP = parseSolParam(q.get("to_sol"));
  const to = typeof toP === "number" ? Math.min(toP, maxSol) : maxSol;
  const from = typeof fromP === "number" ? fromP : Math.max(0, to - 99);
  const bad = fromP === "bad" || toP === "bad";

  const bySol = useMemo(() => new Map(manifest.sols.map((s) => [s.sol, s])), [manifest]);
  const bins = useMemo(() => binCounts(manifest.sols, maxSol), [manifest, maxSol]);
  const level = useMemo(() => levelFn(bins), [bins]);
  const busiest = useMemo(
    () =>
      manifest.sols.reduce<ManifestSol | null>(
        (a, b) => (!a || b.total_photos > a.total_photos ? b : a),
        null,
      ),
    [manifest],
  );

  const setRange = (a: number, b: number) =>
    // A new history entry: Back returns to the previous range.
    navigate(`/rovers/${rover.id}/timeline${qs({ from_sol: a, to_sol: b })}`);

  return (
    <div>
      <div class="summary">
        {[
          [
            "Sols with photos",
            <>
              {fmt(manifest.sols.length)} <span class="small muted">of {fmt(maxSol + 1)}</span>
            </>,
          ],
          ["Total photos", fmt(rover.total_photos)],
          [
            "Latest sol",
            <>
              <Link href={`/rovers/${rover.id}`}>{maxSol}</Link>{" "}
              <span class="small muted">{rover.max_date}</span>
            </>,
          ],
          [
            "Busiest sol",
            busiest ? (
              <>
                <Link href={`/rovers/${rover.id}?sol=${busiest.sol}`}>{busiest.sol}</Link>{" "}
                <span class="small muted">{fmt(busiest.total_photos)}</span>
              </>
            ) : (
              "—"
            ),
          ],
          ["Cameras", rover.cameras_count],
        ].map(([k, v]) => (
          <div class="card tile-stat" key={String(k)}>
            <div class="k">{k}</div>
            <div class="v">{v}</div>
          </div>
        ))}
      </div>

      <section class="card chart-card" aria-labelledby="ov-title">
        <div class="chart-head">
          <div>
            <h2 id="ov-title">Whole mission</h2>
            <p class="small muted" style={{ margin: 0 }}>
              Each cell is 10 sols; stronger colour = more photos. Select a cell to zoom the chart
              below.
            </p>
          </div>
        </div>
        <Heat
          bins={bins}
          maxSol={maxSol}
          level={level}
          sols={manifest.sols}
          from={from}
          to={to}
          onPick={(bin) => {
            const c = bin * 10 + 5;
            const a = Math.max(0, Math.min(c - 50, maxSol - 99));
            setRange(a, Math.min(maxSol, Math.max(c + 49, 99)));
          }}
        />
        <div class="legend" aria-hidden="true">
          <span class="sw" style={{ background: "var(--seq-0)" }} /> none
          <span style={{ marginLeft: 8 }}>fewer</span>
          {[1, 2, 3, 4].map((n) => (
            <span key={n} class="sw" style={{ background: `var(--seq-${n})` }} />
          ))}
          <span>more</span>
          <span style={{ marginLeft: 8 }}>
            <span
              class="sw"
              style={{ boxShadow: "inset 0 0 0 2px var(--text)", verticalAlign: "middle" }}
            />{" "}
            selected range
          </span>
        </div>
      </section>

      <Detail
        rover={rover}
        maxSol={maxSol}
        bySol={bySol}
        from={from}
        to={to}
        bad={bad}
        setRange={setRange}
      />

      <TipLayer />
    </div>
  );
}

function Heat(props: {
  bins: number[];
  maxSol: number;
  level: (v: number) => number;
  sols: ManifestSol[];
  from: number;
  to: number;
  onPick: (bin: number) => void;
}) {
  const { bins, maxSol, level, from, to } = props;
  const datesOf = useMemo(() => {
    // first/last Earth date known inside each bin (sols arrive newest first)
    const m = new Map<number, [string, string]>();
    for (const s of props.sols) {
      const i = Math.floor(s.sol / 10);
      const cur = m.get(i);
      m.set(i, cur ? [cur[0], s.earth_date] : [s.earth_date, s.earth_date]);
    }
    return m;
  }, [props.sols]);

  // Roving focus: one tab stop for the whole strip, arrow keys move inside it.
  const [active, setActive] = useState(() => Math.min(Math.floor(from / 10), bins.length - 1));
  const onKeyDown = (e: KeyboardEvent) => {
    const step: Record<string, number> = {
      ArrowLeft: -1,
      ArrowRight: 1,
      ArrowUp: -50,
      ArrowDown: 50,
    };
    let next: number;
    if (e.key in step) next = active + step[e.key]!;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = bins.length - 1;
    else return;
    e.preventDefault();
    next = Math.max(0, Math.min(bins.length - 1, next));
    setActive(next);
    (e.currentTarget as HTMLElement).querySelector<HTMLElement>(`[data-bin="${next}"]`)?.focus();
  };

  const rows = [];
  for (let row = 0; row * 50 < bins.length; row++) {
    const a = row * 500;
    const b = Math.min(maxSol, a + 499);
    const cells = [];
    for (let i = row * 50; i < Math.min(bins.length, row * 50 + 50); i++) {
      const s0 = i * 10;
      const s1 = Math.min(maxSol, s0 + 9);
      const d = datesOf.get(i);
      const label = `Sols ${s0}–${s1}: ${fmt(bins[i]!)} photos`;
      const tipText = [
        `Sols ${s0}–${s1}`,
        d ? (d[0] === d[1] ? d[0] : `${d[1]} – ${d[0]}`) : "",
        label.split(": ")[1]!,
      ]
        .filter(Boolean)
        .join("\n");
      cells.push(
        <button
          key={i}
          type="button"
          class={`l${level(bins[i]!)}${s0 + 9 >= from && s0 <= to ? " in-range" : ""}`}
          aria-label={label}
          data-bin={i}
          tabIndex={i === active ? 0 : -1}
          onFocus={() => setActive(i)}
          data-tip={tipText}
          onClick={() => props.onPick(i)}
        />,
      );
    }
    rows.push(
      <div class="heat-row" key={row}>
        <span class="lbl">
          {a}–{b}
        </span>
        <div class="heat-cells">{cells}</div>
      </div>,
    );
  }
  return (
    <div
      class="heat"
      role="group"
      aria-label="Photos per 10 sols, whole mission. Use the arrow keys to move between cells."
      onKeyDown={onKeyDown}
    >
      {rows}
    </div>
  );
}

function Detail(props: {
  rover: Rover;
  maxSol: number;
  bySol: Map<number, ManifestSol>;
  from: number;
  to: number;
  bad: boolean;
  setRange: (a: number, b: number) => void;
}) {
  const { rover, maxSol, bySol, from, to } = props;
  const [fromText, setFromText] = useState(String(from));
  const [toText, setToText] = useState(String(to));
  const [formErr, setFormErr] = useState<{ field: "from" | "to"; text: string } | null>(null);
  const [showAll, setShowAll] = useState(false);
  // The fields follow the range when the address changes (links, Back), not on mount: the visitor may
  // have started typing before the mount effects run.
  const seen = useRef({ from, to });
  useEffect(() => {
    const prev = seen.current;
    if (prev.from === from && prev.to === to) return;
    seen.current = { from, to };
    setFromText(String(from));
    setToText(String(to));
    setShowAll(false);
    setFormErr(null);
  }, [from, to]);

  const invalid = props.bad || from > to;
  const tooWide = !invalid && to - from + 1 > MAX_CHART_SOLS;

  const sols = useMemo(() => {
    if (invalid || tooWide) return [];
    const out: { sol: number; entry: ManifestSol | undefined }[] = [];
    for (let s = from; s <= to; s++) out.push({ sol: s, entry: bySol.get(s) });
    return out;
  }, [bySol, from, to, invalid, tooWide]);

  const max = Math.max(1, ...sols.map((x) => x.entry?.total_photos ?? 0));
  const step = max > 1000 ? 500 : max > 400 ? 200 : 100;
  const top = Math.ceil(max / step) * step;
  const sum = sols.reduce((a, x) => a + (x.entry?.total_photos ?? 0), 0);
  const rows = sols.filter((x) => x.entry).reverse() as { sol: number; entry: ManifestSol }[];
  const shown = showAll ? rows : rows.slice(0, TABLE_ROWS);
  const n = sols.length;
  const every = n <= 30 ? 5 : n <= 100 ? 10 : n <= 300 ? 25 : 100;

  const apply = (e: Event) => {
    e.preventDefault();
    const whole = "Enter whole numbers, 0 or greater.";
    const runs = `Sols run 0–${maxSol}.`;
    const f = fromText.trim();
    const t = toText.trim();
    let err: { field: "from" | "to"; text: string } | null = null;
    if (!/^\d+$/.test(f)) err = { field: "from", text: whole };
    else if (!/^\d+$/.test(t)) err = { field: "to", text: whole };
    else if (Number(f) > maxSol) err = { field: "from", text: runs };
    else if (Number(t) > maxSol) err = { field: "to", text: runs };
    else if (Number(f) > Number(t)) {
      err = { field: "from", text: "“From” must not be greater than “To”." };
    }
    if (err) {
      setFormErr(err);
      requestAnimationFrame(() => document.getElementById(err.field)?.focus());
      return;
    }
    setFormErr(null);
    // The same range is not a new place in the history.
    if (Number(f) !== from || Number(t) !== to) props.setRange(Number(f), Number(t));
  };

  const dates = sols.map((x) => x.entry?.earth_date).filter(Boolean) as string[];
  const dateSpan = dates.length ? ` · ${dates[0]} – ${dates[dates.length - 1]}` : "";

  return (
    <section class="card chart-card" aria-labelledby="det-title">
      <form class="chart-head" onSubmit={apply} noValidate>
        <div>
          <h2 id="det-title">Photos per sol</h2>
          <p class="small muted" style={{ margin: 0 }}>
            {invalid || tooWide ? "" : `Sols ${from}–${to}${dateSpan} · ${fmt(sum)} photos`}
          </p>
        </div>
        <span class="spacer" />
        <div class="field">
          <label for="from">From sol</label>
          <input
            id="from"
            type="number"
            min={0}
            max={maxSol}
            aria-invalid={formErr?.field === "from" ? "true" : undefined}
            aria-describedby={formErr?.field === "from" ? "from-err" : undefined}
            style={{ width: 110 }}
            value={fromText}
            onInput={(e) => setFromText(e.currentTarget.value)}
          />
          {formErr?.field === "from" && (
            <span id="from-err" class="field-err">
              {formErr.text}
            </span>
          )}
        </div>
        <div class="field">
          <label for="to">To sol</label>
          <input
            id="to"
            type="number"
            min={0}
            max={maxSol}
            aria-invalid={formErr?.field === "to" ? "true" : undefined}
            aria-describedby={formErr?.field === "to" ? "to-err" : undefined}
            style={{ width: 110 }}
            value={toText}
            onInput={(e) => setToText(e.currentTarget.value)}
          />
          {formErr?.field === "to" && (
            <span id="to-err" class="field-err">
              {formErr.text}
            </span>
          )}
        </div>
        <button class="btn" type="submit">
          Apply
        </button>
        <div class="segmented" role="group" aria-label="Quick ranges">
          {PRESETS.map((p) => (
            <PresetOption
              key={p}
              n={p}
              checked={to === maxSol && from === Math.max(0, maxSol - p + 1)}
              onPick={() => props.setRange(Math.max(0, maxSol - p + 1), maxSol)}
            />
          ))}
        </div>
      </form>

      <div role="alert">
        {invalid && (
          <div class="alert alert-error range-err">
            <div>
              <div class="alert-title">Invalid range</div>
              <p style={{ margin: 0 }}>
                “From sol” must not be greater than “To sol”, and both must be whole numbers.
              </p>
            </div>
          </div>
        )}
        {tooWide && (
          <div class="alert alert-info range-err">
            <div>
              <div class="alert-title">Range too wide to chart</div>
              <p style={{ margin: 0 }}>
                Choose at most {fmt(MAX_CHART_SOLS)} sols, or pick a cell in the overview above.
              </p>
            </div>
          </div>
        )}
      </div>

      {!invalid && !tooWide && (
        <>
          <div class="bars-wrap">
            <div
              class="bars-plot"
              role="group"
              aria-label={`Photos per sol, sols ${from} to ${to}`}
            >
              {Array.from({ length: top / step }, (_, i) => (i + 1) * step).map((v) => (
                <div
                  key={v}
                  class="gridline"
                  style={{ bottom: `${(v / top) * 100}%` }}
                  aria-hidden="true"
                >
                  <span>{fmt(v)}</span>
                </div>
              ))}
              <div class="bars">
                {sols.map(({ sol, entry }) =>
                  entry ? (
                    <Link
                      key={sol}
                      href={`/rovers/${rover.id}?sol=${sol}`}
                      style={{ height: `${(entry.total_photos / top) * 100}%` }}
                      aria-label={`Sol ${sol}, ${entry.earth_date}: ${fmt(entry.total_photos)} photos`}
                      data-tip={`Sol ${sol} · ${entry.earth_date}\n${fmt(entry.total_photos)} photos · ${entry.cameras.length} cameras\nClick to browse`}
                    />
                  ) : (
                    <span
                      key={sol}
                      class="zero"
                      aria-hidden="true"
                      data-tip={`Sol ${sol}\nNo photos`}
                    />
                  ),
                )}
              </div>
            </div>
            <div class="x-axis" aria-hidden="true">
              {sols.map(({ sol }, i) =>
                sol % every === 0 ? (
                  <span key={sol} style={{ left: `${((i + 0.5) / n) * 100}%` }}>
                    {sol}
                  </span>
                ) : null,
              )}
            </div>
          </div>

          <h3 style={{ margin: "var(--s5) 0 var(--s2)" }} id="tbl-title">
            Sols in range
          </h3>
          {rows.length === 0 ? (
            <p class="muted small">No photos in this range.</p>
          ) : (
            <>
              <table class="sticky-head" aria-labelledby="tbl-title">
                <thead>
                  <tr>
                    <th scope="col">Sol</th>
                    <th scope="col">Earth date</th>
                    <th scope="col" class="num">
                      Photos
                    </th>
                    <th scope="col">Cameras</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map(({ sol, entry }) => (
                    <tr key={sol}>
                      <td>
                        <Link href={`/rovers/${rover.id}?sol=${sol}`}>{sol}</Link>
                      </td>
                      <td>{entry.earth_date}</td>
                      <td class="num">{fmt(entry.total_photos)}</td>
                      <td>
                        <span class="mono cam-list">{entry.cameras.join(", ")}</span>
                        <span class="cam-n" title={entry.cameras.join(", ")}>
                          {entry.cameras.length}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div class="row" style={{ marginTop: "var(--s3)", alignItems: "center" }}>
                {rows.length > TABLE_ROWS && (
                  <button
                    class="btn btn-sm"
                    type="button"
                    aria-expanded={showAll}
                    onClick={() => setShowAll((v) => !v)}
                  >
                    {showAll ? `Show first ${TABLE_ROWS}` : `Show all ${fmt(rows.length)} sols`}
                  </button>
                )}
                <span class="xsmall muted">
                  {fmt(shown.length)} of {fmt(rows.length)} sols with photos, newest first
                </span>
              </div>
            </>
          )}
        </>
      )}
      <p class="xsmall muted" style={{ margin: "var(--s3) 0 0" }}>
        API: <code>GET /api/v2/rovers/{rover.id}/manifest</code>
      </p>
    </section>
  );
}

function PresetOption({ n, checked, onPick }: { n: number; checked: boolean; onPick: () => void }) {
  return (
    <>
      <input type="radio" name="preset" id={`p${n}`} checked={checked} onChange={onPick} />
      <label for={`p${n}`}>{n === PRESETS[PRESETS.length - 1] ? `${n} sols` : n}</label>
    </>
  );
}
