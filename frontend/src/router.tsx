// A tiny History API router: location state, navigate(), <Link> and route matching.
import type { ComponentChildren, JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";

export interface Loc {
  pathname: string;
  search: string;
}

const EVENT = "sol-index:navigate";

/** How the last navigation happened: a link or `navigate()` ("push"), or Back/Forward ("pop"). */
export type NavKind = "push" | "pop";
export interface NavDetail {
  kind: NavKind;
  /** The entry was replaced rather than added (filters and the like): the page itself did not change. */
  replace?: boolean;
}

// A page opened afresh (or reloaded) is treated like Back: its saved scroll position is restored.
let lastKind: NavKind = "pop";
// The saved position is applied once per visit: by the first page that asks, or never after a push.
let restorePending = true;
// True until the first navigation: the page was loaded (or reloaded), not reached by Back.
let firstLoad = true;

/** The saved scroll position of the current history entry (0 when there is none). */
export function savedScroll(): number {
  const y = readHistoryState<number>("scroll");
  return typeof y === "number" && y > 0 ? y : 0;
}

/** The kind of the last navigation (read by the layout and by `useRestoreScroll`). */
export function lastNavigationKind(): NavKind {
  return lastKind;
}

function current(): Loc {
  return { pathname: window.location.pathname, search: window.location.search };
}

function stateObject(): Record<string, unknown> {
  const s: unknown = window.history.state;
  return s && typeof s === "object" && !Array.isArray(s)
    ? { ...(s as Record<string, unknown>) }
    : {};
}

/** A value kept in the `history.state` of the current entry (survives Back, not shared by link). */
export function readHistoryState<T>(key: string): T | undefined {
  try {
    return stateObject()[key] as T | undefined;
  } catch {
    return undefined;
  }
}

/** Store a value in the `history.state` of the current entry; other fields stay. */
export function writeHistoryState(key: string, value: unknown): void {
  try {
    window.history.replaceState({ ...stateObject(), [key]: value }, "");
  } catch {
    // Some browsers limit how often the state may change; the value is just not remembered then.
  }
}

/** `replaceState` that keeps the other fields of `history.state`. */
export function replaceKeepingState(to: string): void {
  window.history.replaceState(stateObject(), "", to);
}

function announce(kind: NavKind, replace = false): void {
  // A replaced entry is the same visit: it must not turn a Back arrival into a link arrival.
  if (!replace) {
    lastKind = kind;
    restorePending = kind === "pop";
    firstLoad = false;
  }
  window.dispatchEvent(new CustomEvent<NavDetail>(EVENT, { detail: { kind, replace } }));
}

if (typeof window !== "undefined") {
  window.history.scrollRestoration = "manual";
  for (const ev of ["wheel", "touchstart", "keydown", "mousedown"]) {
    window.addEventListener(ev, () => (restorePending = false), { passive: true, capture: true });
  }
  // Registered once, before any component listener, so that `lastKind` is right when they run.
  window.addEventListener("popstate", () => announce("pop"));
}

export function navigate(to: string, opts: { replace?: boolean } = {}): void {
  try {
    if (opts.replace) {
      replaceKeepingState(to);
    } else {
      // Remember where the page was scrolled, so that Back can return there.
      writeHistoryState("scroll", Math.round(window.scrollY));
      window.history.pushState(null, "", to);
    }
  } catch {
    // Safari throws a SecurityError when the address changes too often. A replace keeps the app state
    // following (the path is the same); a push falls back to a full page load, so page and address agree.
    if (!opts.replace) {
      window.location.assign(to);
      return;
    }
  }
  announce("push", !!opts.replace);
}

export function useLocation(): Loc {
  const [loc, setLoc] = useState<Loc>(current);
  useEffect(() => {
    const sync = () => setLoc(current());
    window.addEventListener(EVENT, sync);
    sync();
    return () => {
      window.removeEventListener(EVENT, sync);
    };
  }, []);
  return loc;
}

/**
 * Put the page back where Back/Forward left it, once `ready` (its data is shown). Retries for up to
 * 2 s while the document is still too short; any action of the user stops it.
 */
export function useRestoreScroll(ready: boolean): void {
  const done = useRef(false);
  useEffect(() => {
    if (!ready || done.current) return;
    done.current = true;
    if (!restorePending) return;
    restorePending = false;
    const saved = readHistoryState<number>("scroll");
    // A fresh load restores only after a reload (a saved position) and never over a `#fragment`;
    // otherwise the page (anchor, "Jump to", scrollIntoView) is free to scroll itself.
    if (firstLoad && (typeof saved !== "number" || saved <= 0 || window.location.hash)) return;
    // No saved position means the top: the page may be left scrolled by the one we came from.
    const y = typeof saved === "number" && saved > 0 ? saved : 0;
    let timer = 0;
    const started = Date.now();
    const stop = () => {
      window.clearTimeout(timer);
      for (const ev of ["wheel", "touchstart", "keydown", "mousedown"]) {
        window.removeEventListener(ev, stop);
      }
    };
    // Done when the position is reached and the document has kept its height for ~300 ms: the
    // layout above (forms, counters, images) may still change height after the first success.
    let lastHeight = -1;
    let stable = 0;
    const step = () => {
      const height = document.documentElement.scrollHeight;
      const room = height - window.innerHeight;
      window.scrollTo?.(0, y);
      const there = room >= y && Math.abs(window.scrollY - y) <= 1;
      stable = there && height === lastHeight ? stable + 1 : 0;
      lastHeight = height;
      if (stable >= 6 || Date.now() - started > 2000) {
        stop();
        return;
      }
      timer = window.setTimeout(step, 50);
    };
    for (const ev of ["wheel", "touchstart", "keydown", "mousedown"]) {
      window.addEventListener(ev, stop, { passive: true });
    }
    step();
    return stop;
  }, [ready]);
}

/**
 * The `#fragment` of the address, plus a counter that grows on every navigation or `hashchange` (also to the
 * same fragment), so that a page can react to a link it is already on.
 */
export function useHash(): { hash: string; visit: number } {
  const [state, setState] = useState(() => ({ hash: window.location.hash, visit: 0 }));
  useEffect(() => {
    const sync = (e?: Event) =>
      setState((s) => {
        const replaced = (e as CustomEvent<NavDetail> | undefined)?.detail?.replace;
        // A replaced address with the same fragment (a filter changed) is not a new visit to the fragment.
        if (replaced && s.hash === window.location.hash) return s;
        return { hash: window.location.hash, visit: s.visit + 1 };
      });
    window.addEventListener("hashchange", sync);
    window.addEventListener(EVENT, sync);
    return () => {
      window.removeEventListener("hashchange", sync);
      window.removeEventListener(EVENT, sync);
    };
  }, []);
  return state;
}

/** Query string helpers over the current location. */
export function useQuery(): URLSearchParams {
  const { search } = useLocation();
  return new URLSearchParams(search);
}

/**
 * One query parameter as state. `parse` returns `undefined` for a value it does not accept: the
 * `fallback` is used then and the parameter is cleaned out of the address (`replace`). `set` writes
 * with `replace` and drops the parameter when the value is the default.
 */
export function useQueryParam<T>(
  name: string,
  parse: (raw: string) => T | undefined,
  fallback: T,
  format: (v: T) => string = String,
): [T, (v: T) => void] {
  const { pathname, search } = useLocation();
  const raw = new URLSearchParams(search).get(name);
  const parsed = raw === null ? undefined : parse(raw);
  const value = parsed === undefined ? fallback : parsed;
  const invalid = raw !== null && parsed === undefined;
  useEffect(() => {
    if (!invalid) return;
    const q = new URLSearchParams(window.location.search);
    q.delete(name);
    const s = q.toString();
    navigate(`${window.location.pathname}${s ? `?${s}` : ""}${window.location.hash}`, {
      replace: true,
    });
  }, [invalid, name, pathname]);
  const set = (v: T) => {
    const q = new URLSearchParams(window.location.search);
    if (format(v) === format(fallback)) q.delete(name);
    else q.set(name, format(v));
    const s = q.toString();
    const next = `${window.location.pathname}${s ? `?${s}` : ""}${window.location.hash}`;
    if (next !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
      navigate(next, { replace: true });
    }
  };
  return [value, set];
}

/** Build `?a=1&b=2` from an object, dropping empty values. */
export function qs(params: Record<string, string | number | undefined | null>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

type LinkProps = Omit<JSX.AnchorHTMLAttributes<HTMLAnchorElement>, "href"> & {
  href: string;
  children?: ComponentChildren;
};

/** An anchor that navigates without a page load, but keeps native behaviour for new tabs etc. */
export function Link({ href, onClick, ...rest }: LinkProps) {
  const handle = (e: JSX.TargetedMouseEvent<HTMLAnchorElement>) => {
    onClick?.(e);
    if (
      e.defaultPrevented ||
      e.button !== 0 ||
      e.metaKey ||
      e.ctrlKey ||
      e.shiftKey ||
      e.altKey ||
      (rest.target && rest.target !== "_self") ||
      !href.startsWith("/")
    ) {
      return;
    }
    e.preventDefault();
    navigate(href);
  };
  return <a {...rest} href={href} onClick={handle} />;
}

export type DataSet = "samples" | "radiation" | "weather";
export const DATA_SETS: DataSet[] = ["samples", "radiation", "weather"];

export type RouteMatch =
  | { name: "home" }
  | { name: "photos"; rover: string }
  | { name: "timeline"; rover: string }
  | { name: "map"; rover: string }
  | { name: "flights"; rover: string }
  | { name: "samples"; rover: string }
  | { name: "solReport"; rover: string; sol: number }
  | { name: "photo"; id: string }
  | { name: "data" }
  | { name: "dataSet"; set: DataSet }
  | { name: "search" }
  | { name: "stats" }
  | { name: "status" }
  | { name: "keys" }
  | { name: "keysConfirm" }
  | { name: "keysManage" }
  | { name: "keysDelete" }
  | { name: "notfound" };

export function matchRoute(pathname: string): RouteMatch {
  const parts = pathname.split("/").filter(Boolean).map(safeDecode);
  if (parts.length === 0) return { name: "home" };
  const [a, b, c] = parts;
  if (a === "rovers" && b && parts.length === 2) return { name: "photos", rover: b.toLowerCase() };
  if (a === "rovers" && b && c === "timeline" && parts.length === 3) {
    return { name: "timeline", rover: b.toLowerCase() };
  }
  if (a === "rovers" && b && c === "map" && parts.length === 3) {
    return { name: "map", rover: b.toLowerCase() };
  }
  if (a === "rovers" && b && c === "flights" && parts.length === 3) {
    return { name: "flights", rover: b.toLowerCase() };
  }
  if (a === "rovers" && b && c === "samples" && parts.length === 3) {
    return { name: "samples", rover: b.toLowerCase() };
  }
  if (a === "rovers" && b && c === "sols" && parts.length === 5 && parts[4] === "conditions") {
    const sol = parts[3]!;
    // A sol is a whole number the API accepts (0 .. 2^31-1); anything else is not a page.
    if (/^(0|[1-9]\d{0,9})$/.test(sol) && Number(sol) <= 2147483647) {
      return { name: "solReport", rover: b.toLowerCase(), sol: Number(sol) };
    }
    return { name: "notfound" };
  }
  if (a === "photos" && b && parts.length === 2) {
    // A photo id is a positive whole number; anything else is not a page (the API would answer 422).
    return /^[1-9]\d{0,9}$/.test(b) ? { name: "photo", id: b } : { name: "notfound" };
  }
  if (a === "data" && parts.length === 1) return { name: "data" };
  if (a === "data" && parts.length === 2 && (DATA_SETS as string[]).includes(b!)) {
    return { name: "dataSet", set: b as DataSet };
  }
  if (a === "search" && parts.length === 1) return { name: "search" };
  if (a === "stats" && parts.length === 1) return { name: "stats" };
  if (a === "status" && parts.length === 1) return { name: "status" };
  if (a === "keys" && parts.length === 1) return { name: "keys" };
  if (a === "keys" && b === "confirm" && parts.length === 2) return { name: "keysConfirm" };
  if (a === "keys" && b === "manage" && parts.length === 2) return { name: "keysManage" };
  if (a === "keys" && b === "delete" && parts.length === 2) return { name: "keysDelete" };
  return { name: "notfound" };
}

export function safeDecode(s: string): string {
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
}
