// Pieces shared by the filter forms of Search and Stats: rover chips, camera picker, sol / date fields.
import { Fragment } from "preact";
import { useEffect, useState } from "preact/hooks";
import { api } from "../api/client";
import type { Camera, Rover } from "../api/types";
import { fmt } from "../lib/format";

interface RoverChipsProps {
  rovers: Rover[];
  /** Chosen rover ids (never empty). */
  selected: string[];
  onToggle: (id: string) => void;
}

/** One chip per rover; at least one stays chosen. */
export function RoverChips({ rovers, selected, onToggle }: RoverChipsProps) {
  return (
    <div class="chips" role="group" aria-label="Rovers">
      {rovers.map((r) => {
        const on = selected.includes(r.id);
        const last = on && selected.length === 1;
        return (
          <button
            key={r.id}
            type="button"
            class="chip"
            aria-pressed={on}
            aria-label={r.name}
            aria-disabled={last ? "true" : undefined}
            aria-describedby={last ? "rover-last-hint" : undefined}
            title={last ? `${r.name}: at least one rover must stay chosen` : undefined}
            onClick={() => !last && onToggle(r.id)}
          >
            {r.name}
            <span class="count" aria-hidden="true">
              {fmt(r.total_photos)}
            </span>
          </button>
        );
      })}
      <span id="rover-last-hint" class="visually-hidden">
        At least one rover must stay chosen.
      </span>
    </div>
  );
}

/**
 * Switch one rover in a draft. A removed rover takes its cameras with it (unless another chosen
 * rover has the same code); when its camera list is not known yet it is fetched and the cameras
 * are dropped as soon as it arrives.
 */
export function toggleRoverDraft<T extends { rovers: string[]; cameras: string[] }>(
  d: T,
  id: string,
  lists: Record<string, Camera[]>,
  update: (f: (cur: T) => T) => void,
): T {
  const on = d.rovers.includes(id);
  if (on && d.rovers.length === 1) return d;
  if (!on) return { ...d, rovers: [...d.rovers, id] };
  const prune = (gone: Camera[], cur: T): T => {
    const keep = new Set(cur.rovers.flatMap((r) => (lists[r] ?? []).map((c) => c.code)));
    const goneCodes = new Set(gone.map((c) => c.code));
    return { ...cur, cameras: cur.cameras.filter((c) => !goneCodes.has(c) || keep.has(c)) };
  };
  const next = { ...d, rovers: d.rovers.filter((r) => r !== id) };
  if (lists[id]) return prune(lists[id], next);
  api.cameras(id).then(
    (items) => update((cur) => (cur.rovers.includes(id) ? cur : prune(items, cur))),
    () => {},
  );
  return next;
}

/**
 * Camera lists of the chosen rovers. They are fetched when `enabled` turns on: the pages switch it
 * on once their main request has finished, so the lists never compete with it for the per-address
 * limit of concurrent requests (and go through the request queue).
 */
export function useCameraLists(rovers: string[], enabled: boolean) {
  const [lists, setLists] = useState<Record<string, Camera[]>>({});
  const [failed, setFailed] = useState<string[]>([]);
  const [tick, setTick] = useState(0);
  const key = rovers.join(",");
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    for (const r of rovers) {
      api.cameras(r).then(
        (items) => live && setLists((l) => (l[r] ? l : { ...l, [r]: items })),
        () => live && setFailed((f) => (f.includes(r) ? f : [...f, r])),
      );
    }
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, key, tick]);
  return {
    lists,
    failed: failed.filter((r) => rovers.includes(r) && !lists[r]),
    retry: () => {
      setFailed([]);
      setTick((t) => t + 1);
    },
  };
}

interface CameraPickerProps {
  idPrefix: string;
  rovers: Rover[];
  selected: string[];
  max: number;
  open: boolean;
  onOpen: (open: boolean) => void;
  onToggle: (code: string) => void;
  onClear: () => void;
  lists: Record<string, Camera[]>;
  failed: string[];
  onRetry: () => void;
}

/**
 * Chosen cameras plus a picker with the cameras of the chosen rovers. At most `max` codes; once
 * the limit is reached the other chips are unavailable.
 */
export function CameraPicker(p: CameraPickerProps) {
  const full = p.selected.length >= p.max;
  const listId = `${p.idPrefix}-cameras`;
  return (
    <>
      <div class="row" style={{ alignItems: "center" }}>
        <span class="counter" id={`${listId}-count`} aria-live="polite">
          {p.selected.length ? `${p.selected.length} of ${p.max} cameras chosen` : "All cameras"}
        </span>
        {p.selected.length > 0 && (
          <button class="btn btn-sm" type="button" onClick={p.onClear}>
            Clear cameras
          </button>
        )}
        <button
          class="btn btn-sm"
          type="button"
          aria-expanded={p.open}
          aria-controls={listId}
          onClick={() => p.onOpen(!p.open)}
        >
          {p.open ? "Hide cameras" : "Choose cameras"}
        </button>
      </div>
      {!p.open && p.selected.length > 0 && (
        <div class="chips" role="group" aria-label="Chosen cameras">
          {p.selected.map((code) => (
            <button
              key={code}
              type="button"
              class="chip"
              aria-pressed="true"
              title="Click to remove"
              onClick={() => p.onToggle(code)}
            >
              <span class="mono">{code}</span>
            </button>
          ))}
        </div>
      )}
      <div id={listId} role="group" aria-label="Camera list" hidden={!p.open}>
        {p.open &&
          p.rovers.map((r) => {
            const cams = p.lists[r.id];
            return (
              <div class="cam-group" key={r.id} role="group" aria-label={`${r.name} cameras`}>
                <div class="cam-title">{r.name}</div>
                {p.failed.includes(r.id) ? (
                  <p class="small">
                    Couldn’t load the cameras of {r.name}.{" "}
                    <button class="btn btn-sm" type="button" onClick={p.onRetry}>
                      Try again
                    </button>
                  </p>
                ) : !cams ? (
                  <p class="small muted" role="status">
                    Getting cameras…
                  </p>
                ) : (
                  <div class="chips">
                    {cams.map((c) => {
                      const on = p.selected.includes(c.code);
                      const blocked = !on && full;
                      return (
                        <button
                          key={c.code}
                          type="button"
                          class="chip"
                          aria-pressed={on}
                          aria-disabled={blocked ? "true" : undefined}
                          disabled={blocked}
                          title={`${c.name} — ${fmt(c.total_photos)} photos`}
                          onClick={() => p.onToggle(c.code)}
                        >
                          <span class="mono">{c.code}</span>
                          <span class="count" aria-hidden="true">
                            {fmt(c.total_photos)}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
      </div>
    </>
  );
}

interface WhenProps {
  idPrefix: string;
  mode: "sol" | "date";
  solMin: string;
  solMax: string;
  dateMin: string;
  dateMax: string;
  onMode: (m: "sol" | "date") => void;
  onChange: (patch: {
    solMin?: string;
    solMax?: string;
    dateMin?: string;
    dateMax?: string;
  }) => void;
  invalid: string[];
  error?: string;
  hint: string;
}

/** Sol or Earth date, never both: switching the mode clears the other pair. */
export function WhenFields(p: WhenProps) {
  const id = (s: string) => `${p.idPrefix}-${s}`;
  const bad = (s: string) => (p.invalid.includes(id(s)) ? "true" : undefined);
  return (
    <>
      <div class="segmented" role="radiogroup" aria-label="Find by">
        <input
          type="radio"
          name={id("mode")}
          id={id("mode-sol")}
          checked={p.mode === "sol"}
          onChange={() => p.onMode("sol")}
        />
        <label for={id("mode-sol")}>Sol</label>
        <input
          type="radio"
          name={id("mode")}
          id={id("mode-date")}
          checked={p.mode === "date"}
          onChange={() => p.onMode("date")}
        />
        <label for={id("mode-date")}>Earth date</label>
      </div>
      <div class="inline-fields" hidden={p.mode !== "sol"}>
        <div class="field">
          <label for={id("sol-min")}>From sol</label>
          <input
            id={id("sol-min")}
            type="number"
            min={0}
            step={1}
            inputMode="numeric"
            placeholder="first"
            disabled={p.mode !== "sol"}
            value={p.solMin}
            aria-invalid={bad("sol-min")}
            aria-describedby={p.error ? id("err-when") : undefined}
            onInput={(e) => p.onChange({ solMin: e.currentTarget.value })}
          />
        </div>
        <div class="field">
          <label for={id("sol-max")}>To sol</label>
          <input
            id={id("sol-max")}
            type="number"
            min={0}
            step={1}
            inputMode="numeric"
            placeholder="last"
            disabled={p.mode !== "sol"}
            value={p.solMax}
            aria-invalid={bad("sol-max")}
            aria-describedby={p.error ? id("err-when") : undefined}
            onInput={(e) => p.onChange({ solMax: e.currentTarget.value })}
          />
        </div>
      </div>
      <div class="inline-fields" hidden={p.mode !== "date"}>
        <div class="field">
          <label for={id("date-min")}>From date</label>
          <input
            id={id("date-min")}
            type="date"
            disabled={p.mode !== "date"}
            value={p.dateMin}
            aria-invalid={bad("date-min")}
            aria-describedby={p.error ? id("err-when") : undefined}
            onInput={(e) => p.onChange({ dateMin: e.currentTarget.value })}
          />
        </div>
        <div class="field">
          <label for={id("date-max")}>To date</label>
          <input
            id={id("date-max")}
            type="date"
            disabled={p.mode !== "date"}
            value={p.dateMax}
            aria-invalid={bad("date-max")}
            aria-describedby={p.error ? id("err-when") : undefined}
            onInput={(e) => p.onChange({ dateMax: e.currentTarget.value })}
          />
        </div>
      </div>
      <span class="hint">{p.hint}</span>
      {p.error && (
        <div class="field-err" id={id("err-when")} role="alert">
          {p.error}
        </div>
      )}
    </>
  );
}

/** Radio group of two or more labelled values (`segmented` look). */
export function Segmented<T extends string>(props: {
  name: string;
  label: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div class="segmented" role="radiogroup" aria-label={props.label}>
      {props.options.map((o) => (
        <Fragment key={o.value}>
          <input
            type="radio"
            name={props.name}
            id={`${props.name}-${o.value || "all"}`}
            checked={props.value === o.value}
            onChange={() => props.onChange(o.value)}
          />
          <label for={`${props.name}-${o.value || "all"}`}>{o.label}</label>
        </Fragment>
      ))}
    </div>
  );
}
