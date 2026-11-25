import { screen, waitFor } from "@testing-library/preact";
import { describe, expect, it } from "vitest";
import { apiError } from "./helpers";
import { RAD_AT_HOUR, radDetail } from "./fixtures/radhours";
import { RAD_CREDIT } from "./fixtures/radiation";
import { CUR_SOL } from "./fixtures/solreport";
import {
  RADH,
  alerts,
  charts,
  holeColumns,
  inspectHour,
  lineSegments,
  liveText,
  mainText,
  oneChart,
  openReport,
  reopen,
  reportHeading,
  reportPath,
} from "./solreport-helpers";

// Stage 22 phase C, hourly radiation of Curiosity on the sol report (docs/proposals/sol-conditions.md,
// AC-48, AC-49; contract docs/API.md GET /rovers/curiosity/radiation/{sol}; mockup sol-report-curiosity.html).
// Written from the contract and the mockup, not from the implementation.

const CUR = "curiosity";
const DOSE = /dose rate/i;
const COUNT = /count rate/i;
const UNAVAILABLE = /hourly radiation (is )?not available for this sol/i;

async function open(over: Parameters<typeof openReport>[1] = {}, sol = CUR_SOL) {
  const m = openReport(reportPath(CUR, sol), over);
  await reportHeading("Curiosity", sol);
  return m;
}

/** The hourly radiation block is ready once its dose chart is drawn. */
const hourlyReady = () => waitFor(() => expect(charts(DOSE)).toHaveLength(1));

describe("AC-48 hourly radiation charts", () => {
  it("AC-48 the dose rate chart (E and B) and the count rate chart (B) are drawn from /radiation/{sol}", async () => {
    const m = await open();
    await hourlyReady();
    expect(charts(COUNT)).toHaveLength(1);
    expect(m.calls.filter((c) => c.pathname === RADH(CUR_SOL))).toHaveLength(1);
  });

  it("AC-48 the dose chart has two lines, E solid and B dashed", async () => {
    await open();
    await hourlyReady();
    const svg = oneChart(DOSE);
    expect(lineSegments(svg), "one line for E and one for B").toBeGreaterThanOrEqual(2);
    // jsdom has no CSS: the dash is an attribute, a class of a line, or the dashed key of the legend
    const card = svg.closest("section, article, figure, .card") ?? svg.parentElement!;
    expect(
      card.querySelectorAll('[stroke-dasharray], [class*="dash"], i.b, .ln.b').length,
      "B is dashed",
    ).toBeGreaterThan(0);
    // the two lines are told apart by name, not by colour alone
    const text = (svg.parentElement?.textContent ?? "") + (svg.getAttribute("aria-label") ?? "");
    expect(text).toMatch(/\bE\b/);
    expect(text).toMatch(/\bB\b/);
  });

  it("AC-48 the hourly dose rate is in µGy/h and the daily summary stays in µGy/day", async () => {
    await open();
    await hourlyReady();
    const card =
      oneChart(DOSE).closest("section, article, figure, .card") ?? oneChart(DOSE).parentElement!;
    expect(card.textContent ?? "").toMatch(/µGy\s*(\/|per)\s*h(our)?\b/i);
    // daily means of the sol (231.4 / 218.7) are per day, never per hour
    expect(mainText()).toMatch(/231\.4\s*µGy\s*\/\s*(day|sol)/);
    expect(mainText()).not.toMatch(/231\.4\s*µGy\s*(\/|per)\s*h(our)?\b/i);
    expect(mainText()).not.toMatch(/7\.9\s*µGy\s*\/\s*(day|sol)/);
  });

  it("AC-48 the count rate chart is in events per second", async () => {
    await open();
    await hourlyReady();
    const svg = oneChart(COUNT);
    const card = svg.closest("section, article, figure, .card") ?? svg.parentElement!;
    expect(card.textContent ?? "").toMatch(/\/\s*s\b|per second|events\/s/i);
  });

  it("AC-48 both charts have the LMST axis from 0 to 24 and a name", async () => {
    await open();
    await hourlyReady();
    for (const svg of [oneChart(DOSE), oneChart(COUNT)]) {
      expect(svg.getAttribute("aria-label")?.trim().length ?? 0).toBeGreaterThan(0);
      const ticks = [...svg.querySelectorAll("text")].map((t) => (t.textContent ?? "").trim());
      expect(ticks).toContain("0");
      expect(ticks).toContain("24");
    }
  });

  it("AC-48 the daily summary of the sol is still there next to the hours", async () => {
    await open();
    await hourlyReady();
    expect(mainText()).toContain("231.4");
    expect(mainText()).toContain("218.7");
  });

  it("AC-48 hours without observations break the lines and are hatched; nothing is interpolated", async () => {
    await open();
    await hourlyReady();
    const whole = oneChart(DOSE);
    const pieces = lineSegments(whole);
    const holes = holeColumns(whole);

    reopen(reportPath(CUR, CUR_SOL), {
      [RADH(CUR_SOL)]: radDetail(CUR_SOL, { missing: [5, 6, 7, 18] }),
    });
    await reportHeading("Curiosity", CUR_SOL);
    await hourlyReady();
    const gap = oneChart(DOSE);
    expect(lineSegments(gap)).toBeGreaterThan(pieces);
    expect(holeColumns(gap)).toBeGreaterThan(holes);
    expect(alerts()).toEqual([]);
  });

  it("AC-48 the shared hour cursor announces the hourly radiation values", async () => {
    await open();
    await hourlyReady();
    inspectHour(13);
    await waitFor(() => expect(liveText()).toMatch(/13:00/));
    await waitFor(() => expect(liveText()).toContain(String(RAD_AT_HOUR.doseB)));
    expect(liveText()).toContain(String(RAD_AT_HOUR.doseE));
    expect(liveText()).toMatch(/7\.6(00)?\b/);
  });

  it("AC-48 an hour without observations is announced as 'no reading', not as a number", async () => {
    await open({ [RADH(CUR_SOL)]: radDetail(CUR_SOL, { missing: [5] }) });
    await hourlyReady();
    inspectHour(5);
    await waitFor(() => expect(liveText()).toMatch(/05:00/));
    expect(liveText()).toMatch(/no (reading|data)/i);
    expect(liveText()).not.toMatch(/null|NaN|undefined/);
  });

  it("AC-48 every hour empty: a note or hatched charts, no crash, no error alert", async () => {
    await open({
      [RADH(CUR_SOL)]: radDetail(CUR_SOL, { missing: Array.from({ length: 24 }, (_, h) => h) }),
    });
    await waitFor(() =>
      expect(
        charts(DOSE).length > 0 ||
          UNAVAILABLE.test(mainText()) ||
          /no .*(data|reading)/i.test(mainText()),
      ).toBe(true),
    );
    expect(alerts()).toEqual([]);
    expect(mainText()).not.toMatch(/undefined|NaN|\bnull\b/);
  });
});

describe("AC-48 hourly radiation not available", () => {
  it("AC-48 hours: null -> the note 'hourly radiation not available for this sol', the daily summary, no hourly charts", async () => {
    await open({ [RADH(CUR_SOL)]: radDetail(CUR_SOL, { noHours: true }) });
    expect(await screen.findByText(UNAVAILABLE)).toBeTruthy();
    expect(mainText()).toContain("231.4");
    expect(charts(DOSE)).toHaveLength(0);
    expect(charts(COUNT)).toHaveLength(0);
    expect(alerts(), "a state of the data, not an error").toEqual([]);
    expect(mainText()).not.toMatch(/undefined|NaN/);
  });

  it("AC-48 404 sol_not_found -> the same note and the daily summary; weather charts stay", async () => {
    await open({
      [RADH(CUR_SOL)]: apiError(404, "sol_not_found", "No radiation data for sol 344", null),
    });
    expect(await screen.findByText(UNAVAILABLE)).toBeTruthy();
    expect(mainText()).toContain("231.4");
    expect(charts(DOSE)).toHaveLength(0);
    expect(charts(/air temperature/i)).toHaveLength(1);
    expect(alerts()).toEqual([]);
    expect(screen.queryByRole("button", { name: /try again|retry/i })).toBeNull();
  });

  it("AC-48 503 on the hours does not take the weather charts or the page away, and no hourly chart is faked", async () => {
    await open({
      [RADH(CUR_SOL)]: () => apiError(503, "db_unavailable", "Database is unavailable", null),
    });
    await waitFor(() =>
      expect(
        UNAVAILABLE.test(mainText()) || alerts().length > 0 || /try again|retry/i.test(mainText()),
      ).toBe(true),
    );
    expect(charts(DOSE)).toHaveLength(0);
    for (const name of [/air temperature/i, /pressure/i, /ultraviolet|\bUV\b/i]) {
      expect(charts(name)).toHaveLength(1);
    }
    expect(mainText()).not.toMatch(/undefined|NaN/);
  });

  it("AC-48 a sol with no hourly route for another sol: sol 343 asks for its own hours", async () => {
    const m = openReport(reportPath(CUR, 343));
    await reportHeading("Curiosity", 343);
    await waitFor(() => expect(m.calls.some((c) => c.pathname === RADH(343))).toBe(true));
    expect(m.calls.some((c) => c.pathname === RADH(CUR_SOL))).toBe(false);
  });
});

describe("AC-49 the timing caveat", () => {
  it("AC-49 timing_caveat true -> 'observation times may be shifted' next to the radiation block", async () => {
    await open({ [RADH(CUR_SOL)]: radDetail(CUR_SOL, { timingCaveat: true }) });
    await hourlyReady();
    const warn = await screen.findByText(/observation times may be shifted/i);
    const block = warn.closest("section") ?? warn.parentElement!;
    expect(block.textContent ?? "").toMatch(/radiation/i);
    // hours are still drawn: it is a warning, not an error
    expect(charts(DOSE)).toHaveLength(1);
    expect(alerts()).toEqual([]);
  });

  it("AC-49 timing_caveat false -> no warning", async () => {
    await open();
    await hourlyReady();
    expect(mainText()).not.toMatch(/observation times may be shifted/i);
  });

  it("AC-49 timing_caveat with hours: null -> the 'not available' note, and the warning does not hide it", async () => {
    await open({ [RADH(CUR_SOL)]: radDetail(CUR_SOL, { noHours: true, timingCaveat: true }) });
    expect(await screen.findByText(UNAVAILABLE)).toBeTruthy();
  });
});

describe("hourly radiation: credit, robustness", () => {
  it("the credit of the RAD data is shown with the hourly block", async () => {
    await open();
    await hourlyReady();
    expect(mainText()).toContain("SwRI");
    expect(mainText()).toContain(RAD_CREDIT.slice(0, 20));
  });

  it("the credit stays when the hours are not available", async () => {
    await open({ [RADH(CUR_SOL)]: radDetail(CUR_SOL, { noHours: true }) });
    await screen.findByText(UNAVAILABLE);
    expect(mainText()).toContain("SwRI");
  });

  it("an unexpected key and a null value inside an hour do not break the page", async () => {
    const odd = radDetail(CUR_SOL) as Record<string, unknown>;
    const hours = odd.hours as Record<string, unknown>[];
    hours[3] = { ...hours[3], mystery_new_key: { nested: [1, 2] }, dose_e_ugy_h: null };
    odd.future_field = "x";
    await open({ [RADH(CUR_SOL)]: odd });
    await hourlyReady();
    expect(alerts()).toEqual([]);
    expect(mainText()).not.toMatch(/\[object|undefined|NaN/);
  });

  it("no NASA or JPL logo and no word of approval in the radiation block", async () => {
    await open();
    await hourlyReady();
    expect(screen.queryByAltText(/nasa|jpl/i)).toBeNull();
    expect(mainText()).not.toMatch(/endorsed by|approved by|official nasa/i);
  });
});
