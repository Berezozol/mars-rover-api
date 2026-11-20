// A tiny History API router: location state, navigate(), <Link> and route matching.
import type { ComponentChildren, JSX } from "preact";
import { useEffect, useState } from "preact/hooks";

export interface Loc {
  pathname: string;
  search: string;
}

const EVENT = "sol-index:navigate";

function current(): Loc {
  return { pathname: window.location.pathname, search: window.location.search };
}

export function navigate(to: string, opts: { replace?: boolean } = {}): void {
  if (opts.replace) window.history.replaceState(null, "", to);
  else window.history.pushState(null, "", to);
  window.dispatchEvent(new Event(EVENT));
}

export function useLocation(): Loc {
  const [loc, setLoc] = useState<Loc>(current);
  useEffect(() => {
    const sync = () => setLoc(current());
    window.addEventListener("popstate", sync);
    window.addEventListener(EVENT, sync);
    sync();
    return () => {
      window.removeEventListener("popstate", sync);
      window.removeEventListener(EVENT, sync);
    };
  }, []);
  return loc;
}

/** Query string helpers over the current location. */
export function useQuery(): URLSearchParams {
  const { search } = useLocation();
  return new URLSearchParams(search);
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

export type RouteMatch =
  | { name: "home" }
  | { name: "photos"; rover: string }
  | { name: "timeline"; rover: string }
  | { name: "map"; rover: string }
  | { name: "flights"; rover: string }
  | { name: "photo"; id: string }
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
  if (a === "photos" && b && parts.length === 2) return { name: "photo", id: b };
  return { name: "notfound" };
}

export function safeDecode(s: string): string {
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
}
