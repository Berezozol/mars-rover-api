import type { Inputs } from "preact/hooks";
import { useCallback, useEffect, useRef, useState } from "preact/hooks";
import { api } from "../api/client";
import type { Rover } from "../api/types";

export type Async<T> =
  { status: "loading" } | { status: "ok"; data: T } | { status: "error"; error: unknown };

/**
 * Run an async loader whenever `deps` change; stale results are ignored.
 * `reload` re-runs the same loader (for "Try again" buttons).
 */
export function useAsync<T>(
  load: (signal: AbortSignal) => Promise<T>,
  deps: Inputs,
): [Async<T>, () => void] {
  const [state, setState] = useState<Async<T>>({ status: "loading" });
  const [tick, setTick] = useState(0);
  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => {
    const ctrl = new AbortController();
    setState({ status: "loading" });
    loadRef
      .current(ctrl.signal)
      .then((data) => {
        if (!ctrl.signal.aborted) setState({ status: "ok", data });
      })
      .catch((error) => {
        if (!ctrl.signal.aborted) setState({ status: "error", error });
      });
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return [state, reload];
}

export function useTitle(title: string): void {
  useEffect(() => {
    document.title = title;
  }, [title]);
}

/** All rovers (cached). */
export function useRovers(): [Async<Rover[]>, () => void] {
  return useAsync(() => api.rovers(), []);
}

/** True while the viewport matches the media query; `fallback` where `matchMedia` does not exist (tests). */
export function useMedia(query: string, fallback = true): boolean {
  const get = () =>
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia(query).matches
      : fallback;
  const [matches, setMatches] = useState(get);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(query);
    const on = () => setMatches(mq.matches);
    on();
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, [query]);
  return matches;
}
