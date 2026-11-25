import { fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { expect } from "vitest";
import { App } from "../src/App";
import { curiosity, mockApi, perseverance } from "./helpers";
import { radiationResponse } from "./fixtures/radiation";
import type { WItem } from "./fixtures/weather";
import { weatherRoutes } from "./weather-helpers";

// Helpers of the stage-22 range slider and sol table tests. They find things the way a user does:
// by role and visible name ("From sol", "Jump to sol", the two slider handles, the table).

export const RAD = "/api/v2/rovers/curiosity/radiation";

export function openWeatherRange(
  opts: { per?: WItem[]; cur?: WItem[]; over?: Record<string, unknown> } = {},
  path = "/data/weather",
) {
  const m = mockApi(
    weatherRoutes(opts as Parameters<typeof weatherRoutes>[0]) as Parameters<typeof mockApi>[0],
  );
  window.history.replaceState(null, "", path);
  render(<App />);
  return m;
}

export function openRadiationRange(
  items?: unknown[],
  over: Record<string, unknown> = {},
  path = "/data/radiation",
) {
  const m = mockApi({
    ...(weatherRoutes() as Parameters<typeof mockApi>[0]),
    "/api/v2/rovers": { items: [perseverance, curiosity] },
    "/api/v2/rovers/curiosity": curiosity,
    [RAD]: radiationResponse(items),
    ...over,
  } as Parameters<typeof mockApi>[0]);
  window.history.replaceState(null, "", path);
  render(<App />);
  return m;
}

/** The page has its data once the table is there (loading and error states have none). */
export const readyTable = () => screen.findByRole("table");

export const fromInput = () => screen.getByLabelText("From sol") as HTMLInputElement;
export const toInput = () => screen.getByLabelText("To sol") as HTMLInputElement;
export const startHandle = () => screen.getByRole("slider", { name: /range start/i });
export const endHandle = () => screen.getByRole("slider", { name: /range end/i });
export const jumpInput = () => screen.getByLabelText("Jump to sol") as HTMLInputElement;

/** The range the user sees in the two number fields. */
export const rangeInFields = (): [number, number] => [
  Number(fromInput().value),
  Number(toInput().value),
];

/** Numbers like "4,591" or "4591" -> 4591. */
export const digits = (s: string | null | undefined) => Number((s ?? "").replace(/[^\d-]/g, ""));

export const handleNow = (h: HTMLElement) => Number(h.getAttribute("aria-valuenow"));

/** Wait until the fields show the range. */
export async function expectRange(from: number, to: number) {
  await waitFor(() => expect(rangeInFields()).toEqual([from, to]));
}

export const preset = (name: RegExp | string) =>
  within(screen.getByRole("group", { name: /range presets/i })).getByRole("button", { name });

/**
 * Press Enter in a field the way a browser does: a key event, then the implicit submit of the form
 * the field belongs to (jsdom does not do the second step on its own).
 */
export function pressEnter(el: HTMLInputElement) {
  const proceed = fireEvent.keyDown(el, { key: "Enter", code: "Enter" });
  if (proceed && el.form) el.form.requestSubmit();
}

export function typeInto(el: HTMLInputElement, value: string) {
  fireEvent.input(el, { target: { value } });
  fireEvent.change(el, { target: { value } });
}

export const tableEl = () => screen.getByRole("table");
export const bodyRows = (table: HTMLElement = tableEl()) =>
  within(table).getAllByRole("row").slice(1);
export const solOfRow = (row: Element) => digits(row.firstElementChild?.textContent);
export const solsInTable = (table?: HTMLElement) => bodyRows(table).map(solOfRow);
export const rowFor = (sol: number) => bodyRows().find((r) => solOfRow(r) === sol);

/** A row the page marks as the found one: aria-current / aria-selected, or the mockup's `hl` class. */
export const isHighlighted = (row: Element) =>
  row.matches(
    '[aria-current]:not([aria-current="false"]), [aria-selected="true"], .hl, [data-highlighted]',
  );

export const pagerEl = () => screen.getByRole("navigation", { name: /table pages/i });
export const nextPage = () => within(pagerEl()).getByRole("button", { name: /next/i });
export const currentPage = () =>
  digits(within(pagerEl()).getByRole("button", { current: "page" }).textContent);

/** Text of every role=status region (the range summary and the jump message are both there). */
export const statusText = () =>
  screen
    .getAllByRole("status")
    .map((s) => s.textContent ?? "")
    .join(" | ");

/** The sort header of a column: its `aria-sort` lives on the columnheader. */
export const headerOf = (name: RegExp) =>
  within(tableEl())
    .getAllByRole("columnheader")
    .find((h) => name.test(h.textContent ?? ""))!;

/** Every number printed on an SVG axis (sol numbers and values; `1,234` is read as 1234). */
export function axisNumbers(svg: Element): number[] {
  return [...svg.querySelectorAll("text")]
    .map((t) => (t.textContent ?? "").replace(/,/g, "").replace(/−/g, "-").trim())
    .filter((t) => /^-?\d+(\.\d+)?$/.test(t))
    .map(Number);
}

/** The viewBox width of a chart svg. */
export const viewWidth = (svg: Element) =>
  Number((svg.getAttribute("viewBox") ?? "0 0 640 0").split(/\s+/)[2]);

export const chartByName = (name: RegExp) =>
  screen.getAllByRole("img").find((c) => name.test(c.getAttribute("aria-label") ?? "")) as
    SVGElement | undefined;

/** The shaded "not published yet" area inside a chart svg, or null. */
export const zoneOf = (svg: Element) =>
  svg.querySelector('[data-testid="not-published-zone"], rect.gap');

export const num = (el: Element, attr: string) => Number(el.getAttribute(attr));

/** The text of all alert regions: an empty alert region (a place held for errors) is "no error". */
export const alertText = () =>
  screen
    .queryAllByRole("alert")
    .map((a) => a.textContent?.trim() ?? "")
    .join(" ")
    .trim();
