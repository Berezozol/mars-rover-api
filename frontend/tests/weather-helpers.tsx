import { render, screen, waitFor, within } from "@testing-library/preact";
import { expect } from "vitest";
import { App } from "../src/App";
import { curiosity, mockApi, perseverance } from "./helpers";
import { curItems, perItems, weatherDetail, weatherList, type WItem } from "./fixtures/weather";

type Handler = Parameters<typeof mockApi>[0][string];
type Rover = "perseverance" | "curiosity";

export const WX = (rover: Rover) => `/api/v2/rovers/${rover}/weather`;

/** All routes the weather section may ask for: both lists, both rover cards and a detail per listed sol. */
export function weatherRoutes(
  opts: { per?: WItem[]; cur?: WItem[]; over?: Record<string, Handler> } = {},
): Record<string, Handler> {
  const per = opts.per ?? perItems;
  const cur = opts.cur ?? curItems;
  const routes: Record<string, Handler> = {
    "/api/v2/rovers/perseverance": perseverance,
    "/api/v2/rovers/curiosity": curiosity,
    [WX("perseverance")]: weatherList("perseverance", per),
    [WX("curiosity")]: weatherList("curiosity", cur),
  };
  for (const it of per)
    routes[`${WX("perseverance")}/${it.sol}`] = weatherDetail("perseverance", it);
  for (const it of cur) routes[`${WX("curiosity")}/${it.sol}`] = weatherDetail("curiosity", it);
  return { ...routes, ...opts.over };
}

/** Mock the API for the weather section and open the Data page at `#weather`. */
export function openWeather(
  opts: Parameters<typeof weatherRoutes>[0] = {},
  path = "/data/weather",
) {
  const m = mockApi(weatherRoutes(opts));
  window.history.replaceState(null, "", path);
  render(<App />);
  return m;
}

export const panelOf = () => screen.findByRole("main");

/** Wait for the sol table: the section has its data (loading, error and empty states have no table). */
export async function readyPanel() {
  const panel = await panelOf();
  await within(panel).findByRole("table");
  return panel;
}

/** The section with the rover card loaded too: the lag in words appears once Rover.max_sol is known. */
export async function readyPanelWithRover() {
  const panel = await readyPanel();
  await waitFor(() => expect(panel.textContent).toMatch(/behind/i));
  return panel;
}

export const dailyChart = (panel: HTMLElement) =>
  within(panel)
    .queryAllByRole("img")
    .find((c) => /daily/i.test(c.getAttribute("aria-label") ?? ""));

// Parameter chips live in the "Parameter" group; the sort headers of the table are buttons too.
const params = (panel: HTMLElement) => within(panel).getByRole("group", { name: /Parameter/ });

export const chip = (panel: HTMLElement, name: RegExp | string) =>
  within(params(panel)).getByRole("button", { name });

export const hasChip = (panel: HTMLElement, name: RegExp | string) =>
  within(params(panel)).queryByRole("button", { name }) !== null;

export const callsTo = (m: ReturnType<typeof mockApi>, path: string) =>
  m.calls.filter((c) => c.pathname === path);

/** The row of a sol in the table (the sol may be written with a thousands separator). */
export const rowOf = (panel: HTMLElement, sol: number) =>
  within(within(panel).getByRole("table"))
    .getAllByRole("row")
    .find((r) => (r.firstElementChild?.textContent ?? "").replace(/[^\d]/g, "") === String(sol));
