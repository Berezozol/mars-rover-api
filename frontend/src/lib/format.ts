import type { Images, Rover } from "../api/types";
import { FILTER_SAMPLE_TYPES } from "../api/types";

export const fmt = (n: number): string => n.toLocaleString("en-US");

export const short = (n: number): string =>
  n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n);

/** `2026-11-06T12:16:25Z` -> `12:16:25` (UTC). */
export const timeOf = (iso: string): string => iso.slice(11, 19);

/** `2026-11-06T12:16:25Z` -> `2026-11-06 12:16:25 UTC`. */
export const dateTimeUtc = (iso: string): string => `${iso.slice(0, 10)} ${iso.slice(11, 19)} UTC`;

export const isFilterable = (sampleType: string): boolean =>
  (FILTER_SAMPLE_TYPES as readonly string[]).includes(sampleType);

/** Thumbnail candidates in fallback order: small, medium, full (large is skipped on purpose). */
export function thumbSources(images: Images): string[] {
  // Never fall back to a URL inside our own API: for Spirit/Opportunity `full` is an on-demand
  // conversion, and a grid of 24-96 tiles must not start 24-96 conversions.
  return [images.small, images.medium, images.full].filter(
    (s): s is string => !!s && !isOwnApiUrl(s),
  );
}

/** True for a URL whose path is inside this server's API (`/api/...`). */
export function isOwnApiUrl(url: string): boolean {
  try {
    return new URL(url, window.location.origin).pathname.startsWith("/api/");
  } catch {
    return false;
  }
}

/** Only http(s) links from API data are rendered as links (blocks `javascript:` and similar). */
export function safeHref(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    return u.protocol === "http:" || u.protocol === "https:" ? url : null;
  } catch {
    return null;
  }
}

/** Rovers that have photos, active first, then newest landing first. */
export function visibleRovers(rovers: Rover[]): Rover[] {
  return rovers
    .filter((r) => r.max_sol !== null)
    .sort((a, b) => {
      const act = Number(b.status === "active") - Number(a.status === "active");
      return act || b.landing_date.localeCompare(a.landing_date);
    });
}

export const statusLabel = (status: string): string =>
  status === "active" ? "Active" : status === "complete" ? "Mission complete" : status;

/** Hours between two ISO timestamps, rounded. */
export function hoursBetween(a: string, b: string): number {
  return Math.max(0, Math.round((Date.parse(b) - Date.parse(a)) / 3_600_000));
}
