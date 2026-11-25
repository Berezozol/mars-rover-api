import { screen, waitFor } from "@testing-library/preact";
import { describe, expect, it } from "vitest";
import { apiError } from "./helpers";
import { radiationResponse } from "./fixtures/radiation";
import { CUR_SOL, PER_SOL } from "./fixtures/solreport";
import {
  RAD,
  RADH,
  WXD,
  alerts,
  charts,
  mainText,
  openReport,
  reportPath,
} from "./solreport-helpers";

// Owner decision 2026-11-25: on the Curiosity sol report the radiation blocks (hourly and daily) are shown
// even when the sol has no weather (`/weather/{sol}` -> 404 sol_not_found). Perseverance and non-404 weather
// errors behave as before. Wording is matched loosely on purpose.

const CUR = "curiosity";
const PER = "perseverance";
const HOURLY = /radiation \(rad\),? hourly/i;
const DAILY = /radiation \(rad\),? daily/i;
const NO_WEATHER = /no weather/i;

const noWeather = (sol: number) =>
  apiError(404, "sol_not_found", `No weather data for sol ${sol}`, null);

const noRadiationHours = (sol: number) =>
  apiError(404, "sol_not_found", `No radiation data for sol ${sol}`, null);

const photosLink = (rover: string, sol: number) =>
  screen
    .queryAllByRole("link", { name: /photos of this sol/i })
    .filter((a) => a.getAttribute("href") === `/rovers/${rover}?sol=${sol}`);

describe("sol report without weather: radiation is still shown", () => {
  it("Curiosity, weather 404, radiation present: both radiation blocks and the hourly charts", async () => {
    openReport(reportPath(CUR, CUR_SOL), { [WXD(CUR, CUR_SOL)]: noWeather(CUR_SOL) });
    expect(await screen.findByRole("heading", { name: HOURLY })).toBeTruthy();
    expect(await screen.findByRole("heading", { name: DAILY })).toBeTruthy();
    await waitFor(() => expect(charts(/dose rate/i)).toHaveLength(1));
    expect(charts(/count rate/i)).toHaveLength(1);
    // the daily values of the sol are there
    await waitFor(() => expect(mainText()).toContain("231.4"));
    expect(alerts()).toEqual([]);
    expect(mainText()).not.toMatch(/undefined|NaN|\bnull\b/);
  });

  it("Curiosity, weather 404: the page says the weather is missing and keeps the photos link", async () => {
    openReport(reportPath(CUR, CUR_SOL), { [WXD(CUR, CUR_SOL)]: noWeather(CUR_SOL) });
    await screen.findByRole("heading", { name: HOURLY });
    expect(mainText()).toMatch(NO_WEATHER);
    expect(mainText()).toMatch(new RegExp(`\\b${CUR_SOL}\\b`));
    expect(photosLink(CUR, CUR_SOL).length).toBeGreaterThan(0);
    // no weather charts are invented
    expect(charts(/air temperature/i)).toHaveLength(0);
    expect(charts(/pressure/i)).toHaveLength(0);
  });

  it("Curiosity, weather 404: the weather empty state sits above the radiation, no weather-only controls", async () => {
    openReport(reportPath(CUR, CUR_SOL), { [WXD(CUR, CUR_SOL)]: noWeather(CUR_SOL) });
    const note = await screen.findByTestId("no-weather");
    const hourly = await screen.findByRole("heading", { name: HOURLY });
    expect(note.compareDocumentPosition(hourly) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await waitFor(() => expect(charts(/dose rate/i)).toHaveLength(1));
    expect(screen.queryByLabelText(/shade night/i)).toBeNull();
  });

  it("Curiosity, weather 404: the hourly radiation is asked for this sol only", async () => {
    const m = openReport(reportPath(CUR, CUR_SOL), { [WXD(CUR, CUR_SOL)]: noWeather(CUR_SOL) });
    await waitFor(() => expect(charts(/dose rate/i)).toHaveLength(1));
    expect(m.calls.filter((c) => c.pathname === RADH(CUR_SOL))).toHaveLength(1);
    expect(
      m.calls.some((c) => /\/radiation\/\d+$/.test(c.pathname) && c.pathname !== RADH(CUR_SOL)),
    ).toBe(false);
  });
});

describe("sol report with no weather and no radiation", () => {
  const bothMissing = {
    [WXD(CUR, CUR_SOL)]: noWeather(CUR_SOL),
    [RADH(CUR_SOL)]: noRadiationHours(CUR_SOL),
    [RAD]: radiationResponse([]),
  };

  it("weather 404, radiation 404 and no daily row: one plain empty state, no radiation charts, no error", async () => {
    openReport(reportPath(CUR, CUR_SOL), bothMissing);
    expect(await screen.findByText(/no data for sol 344/i)).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("status", { name: /loading/i })).toBeNull());
    expect(charts(/dose rate/i)).toHaveLength(0);
    expect(charts(/count rate/i)).toHaveLength(0);
    expect(alerts()).toEqual([]);
    expect(mainText()).not.toMatch(/undefined|NaN|\bnull\b/);
    expect(photosLink(CUR, CUR_SOL).length).toBeGreaterThan(0);
  });

  it("weather 404, hourly radiation 404 but the daily row exists: no crash, no error alert", async () => {
    openReport(reportPath(CUR, CUR_SOL), {
      [WXD(CUR, CUR_SOL)]: noWeather(CUR_SOL),
      [RADH(CUR_SOL)]: noRadiationHours(CUR_SOL),
    });
    await screen.findByRole("heading", { level: 1 });
    await waitFor(() => expect(mainText()).toMatch(/no (weather )?data|no weather/i));
    expect(charts(/dose rate/i)).toHaveLength(0);
    expect(alerts()).toEqual([]);
    expect(mainText()).not.toMatch(/undefined|NaN|\bnull\b/);
  });
});

describe("sol report: other rovers and other errors are unchanged", () => {
  it("Perseverance, weather 404: the empty state, the photos link, and no radiation request", async () => {
    const m = openReport(reportPath(PER, PER_SOL), { [WXD(PER, PER_SOL)]: noWeather(PER_SOL) });
    expect(await screen.findByText(/no data for sol 100/i)).toBeTruthy();
    expect(photosLink(PER, PER_SOL).length).toBeGreaterThan(0);
    expect(screen.queryByRole("heading", { name: HOURLY })).toBeNull();
    expect(screen.queryByRole("heading", { name: DAILY })).toBeNull();
    expect(charts(/dose rate/i)).toHaveLength(0);
    expect(alerts()).toEqual([]);
    expect(m.calls.filter((c) => /radiation/i.test(c.pathname))).toEqual([]);
  });

  it("Curiosity, weather 503: the error with a retry button, no radiation blocks, no 'no weather' note", async () => {
    openReport(reportPath(CUR, CUR_SOL), {
      [WXD(CUR, CUR_SOL)]: () => apiError(503, "db_unavailable", "Database is unavailable", null),
    });
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("button", { name: /try again|retry/i })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: HOURLY })).toBeNull();
    expect(screen.queryByRole("heading", { name: DAILY })).toBeNull();
    expect(charts(/dose rate/i)).toHaveLength(0);
    expect(mainText()).not.toMatch(NO_WEATHER);
  });
});
