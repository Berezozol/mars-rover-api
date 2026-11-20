import { useEffect } from "preact/hooks";
import type { Rover } from "../api/types";

export type FeedGroup = "sol" | "photo";

/** Only rovers with regular sync have a feed (`feed.xml`); archive rovers return 404. */
export function hasFeed(rover: Pick<Rover, "data_source">): boolean {
  return rover.data_source === "sync";
}

export interface FeedOptions {
  camera?: string | null;
  sample_type?: string | null;
  group?: FeedGroup;
}

/** Feed path with query; the default `group=sol` is omitted, camera codes are upper-case. */
export function feedPath(rover: string, o: FeedOptions = {}): string {
  const p = new URLSearchParams();
  if (o.camera) p.set("camera", o.camera.toUpperCase());
  if (o.sample_type) p.set("sample_type", o.sample_type);
  if (o.group === "photo") p.set("group", "photo");
  const q = p.toString();
  return `/api/v2/rovers/${rover}/feed.xml${q ? `?${q}` : ""}`;
}

/** Absolute feed address for the reader (the current host, as the contract says). */
export function feedUrl(rover: string, o: FeedOptions = {}): string {
  return `${window.location.origin}${feedPath(rover, o)}`;
}

/** "Perseverance · NAVCAM_LEFT · full" (no camera: "all cameras"). */
export function feedLabel(roverName: string, o: FeedOptions): string {
  const parts = [roverName, o.camera ? o.camera.toUpperCase() : "all cameras"];
  if (o.sample_type) parts.push(o.sample_type);
  return parts.join(" · ");
}

/** Copy text; falls back to selecting the field when the clipboard is unavailable. */
export async function copyText(
  text: string,
  input: HTMLInputElement | null,
): Promise<"copied" | "selected"> {
  try {
    if (!navigator.clipboard?.writeText) throw new Error("no clipboard");
    await navigator.clipboard.writeText(text);
    return "copied";
  } catch {
    input?.focus();
    input?.select();
    return "selected";
  }
}

const HEAD_ATTR = "data-sol-index-feed";

/**
 * Add (or replace) the page-specific `<link rel="alternate">`; returns a remover.
 * The static link of the same rover (`data-static-feed` in index.html) is taken out while the page
 * link is present, so readers don't see the feed twice, and put back on removal.
 */
export function setFeedLink(href: string, title: string, rover?: string): () => void {
  document.head.querySelectorAll(`link[${HEAD_ATTR}]`).forEach((n) => n.remove());
  const hidden = rover
    ? [...document.head.querySelectorAll(`link[data-static-feed="${rover}"]`)]
    : [];
  hidden.forEach((n) => n.remove());
  const link = document.createElement("link");
  link.rel = "alternate";
  link.type = "application/rss+xml";
  link.href = href;
  link.title = title;
  link.setAttribute(HEAD_ATTR, "");
  document.head.appendChild(link);
  return () => {
    link.remove();
    hidden.forEach((n) => {
      if (!document.head.querySelector(`link[data-static-feed="${rover}"]`)) {
        document.head.appendChild(n);
      }
    });
  };
}

/** Keep the page-specific feed link in `<head>` while `href` is set. */
export function useFeedLink(href: string | null, title: string, rover?: string): void {
  useEffect(() => {
    if (!href) return;
    return setFeedLink(href, title, rover);
  }, [href, title, rover]);
}
