import type { ComponentChildren } from "preact";
import { useEffect, useId, useMemo, useRef, useState } from "preact/hooks";
import { fmt } from "../lib/format";
import type { SolRange } from "../lib/solRange";
import { Link } from "../router";
import { pageWindow } from "./Pager";

export interface SolColumn<T> {
  key: string;
  label: string;
  unit?: string;
  numeric?: boolean;
  /** Default true. Columns without a sort value are not sortable. */
  sortable?: boolean;
  /** `desc` for columns whose newest/largest value is read first (sol, date); default `asc`. */
  firstDir?: "asc" | "desc";
  sortValue?: (row: T) => number | string | null | undefined;
  cell: (row: T) => ComponentChildren;
  className?: string;
}

export interface RowLink {
  href: string;
  label: string;
  /** Accessible name; defaults to the label. */
  aria?: string;
}

interface Props<T extends { sol: number }> {
  /** All the loaded rows; the table shows those inside `range`. */
  rows: T[];
  columns: SolColumn<T>[];
  range: SolRange;
  /** First sol of the data (a sol before it cannot be added by widening the range). */
  first: number;
  /** `source_max_sol`: newest published sol. */
  published: number;
  /** The newest sol of the rover (Curiosity is at sol N). */
  latest: number;
  roverName: string;
  /** The visitor asks for a sol outside the range and chooses "Widen the range": include `sol`. */
  onJump: (sol: number) => void;
  /** Links at the end of a row, e.g. "Photos". */
  rowLink?: (row: T) => RowLink | RowLink[] | null;
  /** A jump requested from elsewhere (a click on the chart); `n` makes the same sol count again. */
  focus?: { sol: number; n: number } | null;
  pageSize?: number;
}

type Sort = { key: string; dir: 1 | -1 };

/** Sortable, paged table of the sols of a range, with "Jump to sol". Sorting is done in the browser. */
export function SolTable<T extends { sol: number }>(p: Props<T>) {
  const { columns, range, pageSize = 50 } = p;
  const id = useId();
  const [sort, setSort] = useState<Sort>({ key: "sol", dir: -1 });
  const [page, setPage] = useState(1);
  const [hl, setHl] = useState<number | null>(null);
  const [jumpIn, setJumpIn] = useState("");
  const [msg, setMsg] = useState<{ text: string; widen?: number } | null>(null);
  const [jumpN, setJumpN] = useState(0);
  const pending = useRef<number | null>(null);
  const rowEl = useRef<HTMLTableRowElement>(null);

  const inRange = useMemo(
    () => p.rows.filter((r) => r.sol >= range[0] && r.sol <= range[1]),
    [p.rows, range[0], range[1]], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const sorted = useMemo(() => {
    const col = columns.find((c) => c.key === sort.key) ?? columns[0]!;
    const val = col.sortValue ?? ((r: T) => r.sol);
    return inRange.slice().sort((a, b) => {
      const va = val(a);
      const vb = val(b);
      const na =
        va === null || va === undefined || (typeof va === "number" && !Number.isFinite(va));
      const nb =
        vb === null || vb === undefined || (typeof vb === "number" && !Number.isFinite(vb));
      if (na || nb) return na && nb ? a.sol - b.sol : na ? 1 : -1; // empty values always last
      const c = va! < vb! ? -1 : va! > vb! ? 1 : 0;
      return c * sort.dir || a.sol - b.sol;
    });
  }, [inRange, sort, columns]);

  const pages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const cur = Math.min(page, pages);
  const slice = sorted.slice((cur - 1) * pageSize, cur * pageSize);

  const jump = (sol: number) => {
    setJumpN((n) => n + 1);
    if (!Number.isInteger(sol) || sol < 0) {
      setMsg({ text: "Enter a sol number." });
      return;
    }
    const idx = sorted.findIndex((r) => r.sol === sol);
    if (idx >= 0) {
      setPage(Math.floor(idx / pageSize) + 1);
      setHl(sol);
      setMsg({
        text: `Sol ${fmt(sol)} is on page ${Math.floor(idx / pageSize) + 1}; the row is highlighted.`,
      });
      return;
    }
    if (sol > p.latest) {
      setMsg({
        text: `${p.roverName} has not reached sol ${fmt(sol)} yet (latest sol ${fmt(p.latest)}).`,
      });
      return;
    }
    if (sol > p.published) {
      setMsg({
        text: `Sol ${fmt(sol)} is not published yet (newest published sol: ${fmt(p.published)}).`,
      });
      return;
    }
    if (sol < p.first) {
      setMsg({ text: `The data starts at sol ${fmt(p.first)}; there is no sol ${fmt(sol)}.` });
      return;
    }
    if (sol < range[0] || sol > range[1]) {
      setMsg({
        text: `Sol ${fmt(sol)} is outside the selected range (${fmt(range[0])}–${fmt(range[1])}).`,
        widen: sol,
      });
      return;
    }
    if (sorted.length === 0) {
      setMsg({ text: "No sols with data in this range." });
      return;
    }
    let best = 0;
    sorted.forEach((r, i) => {
      if (Math.abs(r.sol - sol) < Math.abs(sorted[best]!.sol - sol)) best = i;
    });
    const near = sorted[best]!.sol;
    setPage(Math.floor(best / pageSize) + 1);
    setHl(near);
    setMsg({
      text: `No data for sol ${fmt(sol)}. Showing the nearest sol with data, ${fmt(near)}.`,
    });
  };
  const jumpRef = useRef(jump);
  jumpRef.current = jump;

  // A new range starts again from the first page; a jump that waited for the range then goes on.
  useEffect(() => {
    setPage(1);
    setHl(null);
    setMsg(null);
    const sol = pending.current;
    if (sol !== null) {
      pending.current = null;
      jumpRef.current(sol);
    }
  }, [range[0], range[1], p.rows]); // eslint-disable-line react-hooks/exhaustive-deps

  // A jump asked for by the chart.
  useEffect(() => {
    if (!p.focus) return;
    setJumpIn(String(p.focus.sol));
    jumpRef.current(p.focus.sol);
  }, [p.focus?.n, p.focus?.sol]); // eslint-disable-line react-hooks/exhaustive-deps

  // The highlighted row takes the focus.
  useEffect(() => {
    const el = rowEl.current;
    if (!el || hl === null) return;
    el.focus({ preventScroll: true });
    el.scrollIntoView?.({ block: "nearest" });
  }, [hl, cur, jumpN]);

  const setSorted = (key: string, dir?: 1 | -1) => {
    const col = columns.find((c) => c.key === key);
    setSort((s) => ({
      key,
      dir: dir ?? (s.key === key ? (-s.dir as 1 | -1) : col?.firstDir === "desc" ? -1 : 1),
    }));
    setPage(1);
  };
  const sortable = columns.filter((c) => c.sortable !== false);
  const heading = (c: SolColumn<T>) => (c.unit ? `${c.label}, ${c.unit}` : c.label);
  const linksOf = (r: T): RowLink[] => {
    const l = p.rowLink?.(r);
    return !l ? [] : Array.isArray(l) ? l : [l];
  };
  const hasLinks = p.rowLink !== undefined;
  const from = sorted.length ? (cur - 1) * pageSize + 1 : 0;

  return (
    <section class="card panel sol-section" aria-labelledby={`${id}-h`}>
      <div class="tbl-head">
        <div>
          <h2 id={`${id}-h`}>Sols in the selected range</h2>
          <p class="hint" style={{ margin: 0 }}>
            {fmt(inRange.length)} {inRange.length === 1 ? "sol" : "sols"} with data between sol{" "}
            {fmt(range[0])} and sol {fmt(range[1])}. Follows the range above.
          </p>
        </div>
        <div class="tbl-tools">
          <div class="sort-mobile">
            <div class="field">
              <label for={`${id}-sort`}>Sort by</label>
              <select
                id={`${id}-sort`}
                value={sort.key}
                onChange={(e) => setSorted(e.currentTarget.value, sort.dir)}
              >
                {sortable.map((c) => (
                  <option key={c.key} value={c.key}>
                    {heading(c)}
                  </option>
                ))}
              </select>
            </div>
            <button
              class="btn"
              type="button"
              aria-label={`Sort direction: ${sort.dir > 0 ? "ascending" : "descending"}. Activate to reverse.`}
              onClick={() => setSorted(sort.key, -sort.dir as 1 | -1)}
            >
              {sort.dir > 0 ? "Ascending" : "Descending"}
            </button>
          </div>
          <form
            class="jump"
            autocomplete="off"
            onSubmit={(e) => {
              e.preventDefault();
              const t = jumpIn.trim();
              if (!/^\d+$/.test(t)) {
                setJumpN((n) => n + 1);
                setMsg({ text: "Enter a sol number." });
                return;
              }
              jump(Number(t));
            }}
          >
            <div class="field">
              <label for={`${id}-jump`}>Jump to sol</label>
              <input
                id={`${id}-jump`}
                type="number"
                inputMode="numeric"
                min={0}
                step={1}
                placeholder={`e.g. ${range[1]}`}
                value={jumpIn}
                aria-describedby={`${id}-msg`}
                onInput={(e) => setJumpIn(e.currentTarget.value)}
              />
            </div>
            <button class="btn" type="submit">
              Go
            </button>
          </form>
        </div>
      </div>
      <p class="jump-msg" id={`${id}-msg`} role="status">
        {msg?.text}
        {msg?.widen !== undefined && (
          <button
            class="btn btn-sm"
            type="button"
            onClick={() => {
              const sol = msg.widen!;
              if (sol >= range[0] && sol <= range[1]) {
                jump(sol);
                return;
              }
              pending.current = sol;
              p.onJump(sol);
            }}
          >
            Widen the range
          </button>
        )}
      </p>
      <div class="table-scroll">
        <table class="sol-table">
          <caption class="visually-hidden">Sols in the selected range</caption>
          <thead>
            <tr>
              {columns.map((c) => {
                const on = sort.key === c.key;
                const lab = (
                  <>
                    {c.label}
                    {c.unit && <small>{c.unit}</small>}
                  </>
                );
                return (
                  <th
                    key={c.key}
                    scope="col"
                    class={c.numeric ? "n" : undefined}
                    aria-sort={
                      c.sortable === false
                        ? undefined
                        : on
                          ? sort.dir > 0
                            ? "ascending"
                            : "descending"
                          : "none"
                    }
                  >
                    {c.sortable === false ? (
                      lab
                    ) : (
                      <button type="button" class="sort" onClick={() => setSorted(c.key)}>
                        <span class="th-l">{lab}</span>
                        <span class="arr" aria-hidden="true" />
                      </button>
                    )}
                  </th>
                );
              })}
              {hasLinks && (
                <th scope="col">
                  <span class="visually-hidden">Links</span>
                </th>
              )}
            </tr>
          </thead>
          <tbody>
            {slice.map((r) => (
              <tr
                key={r.sol}
                class={r.sol === hl ? "hl" : undefined}
                ref={r.sol === hl ? rowEl : undefined}
                tabIndex={r.sol === hl ? -1 : undefined}
                data-sol={r.sol}
              >
                {columns.map((c, i) =>
                  i === 0 ? (
                    <th
                      key={c.key}
                      scope="row"
                      class={c.numeric ? "n" : undefined}
                      data-label={heading(c)}
                    >
                      {c.cell(r)}
                    </th>
                  ) : (
                    <td
                      key={c.key}
                      class={[c.numeric ? "n" : "", c.className ?? ""].filter(Boolean).join(" ")}
                      data-label={heading(c)}
                    >
                      {c.cell(r)}
                    </td>
                  ),
                )}
                {hasLinks && (
                  <td class="link">
                    {linksOf(r).map((l) => (
                      <Link key={l.href} href={l.href} aria-label={l.aria ?? l.label}>
                        {l.label}
                      </Link>
                    ))}
                  </td>
                )}
              </tr>
            ))}
            {slice.length === 0 && (
              <tr>
                <td colSpan={columns.length + (hasLinks ? 1 : 0)} class="n-empty">
                  No sols with data in this range.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div class="tbl-foot">
        <span class="hint">
          {sorted.length
            ? `Rows ${fmt(from)}–${fmt(from + slice.length - 1)} of ${fmt(sorted.length)} · page ${cur} of ${pages}`
            : "No rows"}
        </span>
        {pages > 1 && (
          <nav class="pager" aria-label="Table pages">
            <button
              class="btn btn-sm"
              type="button"
              disabled={cur <= 1}
              aria-label="Previous page"
              onClick={() => setPage(cur - 1)}
            >
              ‹ Prev
            </button>
            {pageWindow(cur, pages).map((n, i) =>
              n === "gap" ? (
                <span key={`g${i}`} class="muted" aria-hidden="true">
                  …
                </span>
              ) : (
                <button
                  key={n}
                  class="btn btn-sm"
                  type="button"
                  aria-label={`Page ${n}`}
                  aria-current={n === cur ? "page" : undefined}
                  onClick={() => setPage(n)}
                >
                  {n}
                </button>
              ),
            )}
            <button
              class="btn btn-sm"
              type="button"
              disabled={cur >= pages}
              aria-label="Next page"
              onClick={() => setPage(cur + 1)}
            >
              Next ›
            </button>
          </nav>
        )}
      </div>
    </section>
  );
}
