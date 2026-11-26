import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { clearApiCache } from "../src/api/client";
import { App } from "../src/App";
import { apiError, curiosity, mockApi, pending, perseverance } from "./helpers";
import { radiationResponse } from "./fixtures/radiation";
import { radDetail } from "./fixtures/radhours";
import {
  CUR_SOL,
  PER_SOL,
  latestLocation,
  radAround,
  reportDetail,
  type ReportRover,
} from "./fixtures/solreport";

// Helpers of the stage-22 sol report tests (docs/proposals/sol-conditions.md, AC-25 ... AC-32). They look
// for things the way a user does: headings, links, charts by their accessible name, the "Inspect hour" slider.

type Handler = Parameters<typeof mockApi>[0][string];

export const WXD = (rover: string, sol: number) => `/api/v2/rovers/${rover}/weather/${sol}`;
export const LOC = (rover: string) => `/api/v2/rovers/${rover}/location`;
export const RAD = "/api/v2/rovers/curiosity/radiation";
/** `GET /rovers/curiosity/radiation/{sol}` (phase C): the hourly radiation of one sol. */
export const RADH = (sol: number) => `/api/v2/rovers/curiosity/radiation/${sol}`;
export const reportPath = (rover: string, sol: number) => `/rovers/${rover}/sols/${sol}/conditions`;

export const MAX_SOL = 2147483647;

const noSol = (sol: number) =>
  apiError(404, "sol_not_found", `No weather data for sol ${sol}`, null);

/**
 * Every route the report may ask for. Sols not listed are not mocked, so an unexpected request fails the test.
 * Curiosity: 0, 1, 3, 343, 344, 345 have data, sol 2 has none (the archive has no files for sols 2-8).
 * Perseverance: 0, 1, 99, 100, 101 have data.
 */
export function reportRoutes(over: Record<string, Handler> = {}): Record<string, Handler> {
  const routes: Record<string, Handler> = {
    "/api/v2/rovers": { items: [perseverance, curiosity, pending] },
    "/api/v2/rovers/perseverance": perseverance,
    "/api/v2/rovers/curiosity": curiosity,
    "/api/v2/rovers/opportunity": pending,
    // no list route on purpose: the credit is in the answer of `/weather/{sol}`, a request for the list is
    // a request without a mock and fails the test (AC-3 of small-fixes-2026-11-26)
    "/api/v2/rovers/foo": apiError(404, "rover_not_found", "No such rover", null),
    [WXD("foo", 1)]: apiError(404, "rover_not_found", "No such rover", null),
    [LOC("perseverance")]: latestLocation("perseverance"),
    [LOC("curiosity")]: latestLocation("curiosity"),
    // the weather-less rover: the API answers `404 not_found` (docs/API.md)
    [WXD("opportunity", 1)]: apiError(404, "not_found", "No weather data for this rover", null),
    [RAD]: (url: URL) => {
      const lo = Number(url.searchParams.get("sol_min") ?? 0);
      const hi = Number(url.searchParams.get("sol_max") ?? Number.MAX_SAFE_INTEGER);
      return radiationResponse(radAround.filter((r) => r.sol >= lo && r.sol <= hi));
    },
  };
  // edge sols: the bounds of the radiation range are clamped to 0 and 2147483647
  for (const sol of [0, 1, 3, 5, 7, 343, CUR_SOL, 345, MAX_SOL - 1, MAX_SOL]) {
    routes[WXD("curiosity", sol)] = reportDetail("curiosity", sol, { ls: 356.7 });
    routes[RADH(sol)] = radDetail(sol);
  }
  routes[WXD("curiosity", 2)] = noSol(2);
  // no weather does not hide radiation (decision 2026-11-25): the report asks for it, the archive has none
  routes[RADH(2)] = apiError(404, "sol_not_found", "No radiation data for sol 2", null);
  for (const sol of [0, 1, 99, PER_SOL, 101]) {
    routes[WXD("perseverance", sol)] = reportDetail("perseverance", sol);
  }
  return { ...routes, ...over };
}

/** Mock the API and open the report page at `path`. */
export function openReport(path: string, over: Record<string, Handler> = {}) {
  const m = mockApi(reportRoutes(over));
  window.history.replaceState(null, "", path);
  render(<App />);
  return m;
}

/** The report page has its data once the page heading names the rover and the sol. */
export const reportHeading = (rover: string, sol: number) =>
  screen.findByRole("heading", {
    level: 1,
    name: new RegExp(
      `${rover}.*sol\\s*${String(sol).replace(/(\d)(?=(\d{3})+$)/g, "$1,?")}(?!\\d)`,
      "i",
    ),
  });

export const mainEl = () => screen.getByRole("main");
export const mainText = () => (mainEl().textContent ?? "").replace(/\s+/g, " ");

/** SVG charts by the accessible name (the colour strip of hour coverage is an img too, but not an svg). */
export function charts(name: RegExp): SVGElement[] {
  return allCharts().filter((el) => name.test(el.getAttribute("aria-label") ?? ""));
}

export const oneChart = (name: RegExp): SVGElement => {
  const found = charts(name);
  if (found.length !== 1) throw new Error(`expected one chart ${name}, found ${found.length}`);
  return found[0]!;
};

/** Every svg image of the page: the hourly charts and, on Curiosity, the radiation trend. */
export function allCharts(): SVGElement[] {
  return screen
    .queryAllByRole("img")
    .filter((el) => el.tagName.toLowerCase() === "svg") as unknown as SVGElement[];
}

/** Numbers printed in an svg (axis ticks). */
export function tickNumbers(svg: Element): number[] {
  return [...svg.querySelectorAll("text")]
    .map((t) => (t.textContent ?? "").replace(/,/g, "").replace(/−/g, "-").trim())
    .filter((t) => /^-?\d+(\.\d+)?$/.test(t))
    .map(Number);
}

/** How many separate line pieces a chart has: `M` commands of the paths plus polylines. */
export function lineSegments(svg: Element): number {
  let n = svg.querySelectorAll("polyline").length;
  for (const p of svg.querySelectorAll("path"))
    n += (p.getAttribute("d")?.match(/M/gi) ?? []).length;
  return n;
}

/** Columns marked as "no reading": hatched rects, by name or by a pattern fill. */
export function holeColumns(svg: Element): number {
  return svg.querySelectorAll(
    '[class*="nodata"], [class*="no-data"], [class*="hatch"], [class*="hole"], [data-nodata], rect[fill^="url("]',
  ).length;
}

/** Elements that draw a min-max band (by name: SVG has no roles to look for). */
export const bandElements = (svg: Element) =>
  svg.querySelectorAll('[class*="band"], [data-testid*="band"]').length;

export const hourSlider = () => screen.getByLabelText(/inspect hour/i) as HTMLInputElement;

export function inspectHour(hour: number) {
  const slider = hourSlider();
  fireEvent.input(slider, { target: { value: String(hour) } });
  fireEvent.change(slider, { target: { value: String(hour) } });
}

/** Text of the live regions (aria-live polite or assertive, role=status): what a screen reader hears. */
export function liveText(): string {
  const regions = [
    ...document.querySelectorAll(
      '[aria-live="polite"], [aria-live="assertive"], [role="status"], [role="log"]',
    ),
  ];
  return regions
    .map((r) => r.textContent ?? "")
    .join(" | ")
    .replace(/\s+/g, " ");
}

/** The link to a sol next to this one: by its address, so the wording ("Sol 343", an arrow) can differ. */
export function neighbour(rover: string, sol: number) {
  return screen
    .queryAllByRole("link")
    .find((a) => a.getAttribute("href") === reportPath(rover, sol)) as
    HTMLAnchorElement | undefined;
}

export const alerts = () =>
  screen
    .queryAllByRole("alert")
    .map((a) => a.textContent?.trim() ?? "")
    .filter(Boolean);

/**
 * Open another page in the same test (to compare two answers): the old page goes, the API cache is cleared,
 * the new answers are mocked.
 */
export function reopen(path: string, over: Record<string, Handler> = {}) {
  cleanup();
  clearApiCache();
  return openReport(path, over);
}

export type { ReportRover };
