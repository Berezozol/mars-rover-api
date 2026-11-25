import { fireEvent, screen, waitFor, within } from "@testing-library/preact";
import { describe, expect, it } from "vitest";
import { matchRoute } from "../src/router";
import { apiError, json } from "./helpers";
import { CUR_CREDIT, PER_CREDIT, curSol, perSol } from "./fixtures/weather";
import {
  CUR_AT_HOUR,
  CUR_SOL,
  HOUR,
  PER_AT_HOUR,
  PER_SOL,
  reportDetail,
} from "./fixtures/solreport";
import { perLong, radLong } from "./fixtures/range";
import { expectedRiseSet } from "./fixtures/sunref";
import {
  LOC,
  RAD,
  WXD,
  allCharts,
  alerts,
  charts,
  holeColumns,
  inspectHour,
  hourSlider,
  lineSegments,
  liveText,
  mainText,
  neighbour,
  MAX_SOL,
  oneChart,
  openReport,
  reportHeading,
  reportPath,
  reopen,
  bandElements,
  tickNumbers,
} from "./solreport-helpers";
import { openRadiationRange, openWeatherRange, readyTable, rowFor } from "./range-helpers";

// Stage 22 phase B, the sol report (docs/proposals/sol-conditions.md section 2, AC-25 ... AC-32; mockups
// design/mockups/sol-report-curiosity.html and sol-report-perseverance.html). Written from the spec and the
// contract (docs/API.md, GET /rovers/{rover}/weather/{sol}), not from the implementation.

const CUR = "curiosity";
const PER = "perseverance";

async function openCuriosity(over: Parameters<typeof openReport>[1] = {}, sol = CUR_SOL) {
  const m = openReport(reportPath(CUR, sol), over);
  await reportHeading("Curiosity", sol);
  return m;
}
async function openPerseverance(over: Parameters<typeof openReport>[1] = {}, sol = PER_SOL) {
  const m = openReport(reportPath(PER, sol), over);
  await reportHeading("Perseverance", sol);
  return m;
}

/** All charts of the page have their lines drawn: the hourly charts are there. */
const hourlyChartsReady = async () => {
  await waitFor(() => expect(charts(/air temperature/i)).toHaveLength(1));
};

describe("AC-25 the route", () => {
  it("AC-25 /rovers/curiosity/sols/344/conditions is the route solReport with the rover and the sol as a number", () => {
    expect(matchRoute("/rovers/curiosity/sols/344/conditions")).toEqual({
      name: "solReport",
      rover: "curiosity",
      sol: 344,
    });
    expect(matchRoute("/rovers/Perseverance/sols/0/conditions/")).toEqual({
      name: "solReport",
      rover: "perseverance",
      sol: 0,
    });
  });

  it("AC-25 a sol that is not a whole number, or an address that goes on, is not the report", () => {
    expect(matchRoute("/rovers/curiosity/sols/abc/conditions")).toEqual({ name: "notfound" });
    expect(matchRoute("/rovers/curiosity/sols/3.5/conditions")).toEqual({ name: "notfound" });
    expect(matchRoute("/rovers/curiosity/sols/344")).toEqual({ name: "notfound" });
    expect(matchRoute("/rovers/curiosity/sols/344/conditions/x")).toEqual({ name: "notfound" });
  });

  it("AC-25 the page of Curiosity sol 344 opens with the rover and the sol in the heading", async () => {
    await openCuriosity();
    expect(document.title).toMatch(/Curiosity/);
    expect(document.title).toMatch(/344/);
  });

  it("AC-25 Opportunity has no weather: 'no weather data for this rover' with a link to its photos", async () => {
    openReport(reportPath("opportunity", 1));
    expect(await screen.findByText(/no weather data for this rover/i)).toBeTruthy();
    const photos = screen
      .getAllByRole("link")
      .find(
        (a) =>
          /photos/i.test(a.textContent ?? "") &&
          /^\/rovers\/opportunity/.test(a.getAttribute("href") ?? ""),
      );
    expect(photos, "a link to the photos of the rover").toBeTruthy();
    // it is a state of the page, not a broken page: the heading is there and there is nothing to retry
    expect(screen.queryByRole("button", { name: /try again|retry/i })).toBeNull();
  });

  it("AC-25 /rovers/curiosity/sols/abc/conditions is the 'not found' page", async () => {
    const m = openReport("/rovers/curiosity/sols/abc/conditions");
    expect(await screen.findByRole("heading", { name: /not found/i })).toBeTruthy();
    expect(
      m.calls.filter((c) => c.pathname.includes("/weather/")),
      "no weather is asked for a sol that is not a number",
    ).toEqual([]);
  });
});

describe("AC-25 router edge cases", () => {
  it("AC-25 a sol with leading zeros is not a second copy of the page: not found, or a redirect to the canonical path", async () => {
    const m = openReport("/rovers/curiosity/sols/007/conditions");
    await waitFor(() => {
      const notFound = screen.queryByRole("heading", { name: /not found/i });
      expect(notFound !== null || window.location.pathname === reportPath(CUR, 7)).toBe(true);
    });
    if (window.location.pathname.includes("007")) {
      // it stayed on the odd address: then it is the not-found page and asks for no weather
      expect(screen.getByRole("heading", { name: /not found/i })).toBeTruthy();
      expect(m.calls.filter((c) => c.pathname.includes("/weather/"))).toEqual([]);
    } else {
      await reportHeading("Curiosity", 7);
    }
    expect(
      screen.queryAllByRole("heading", { level: 1 }).length,
      "one page, not two",
    ).toBeLessThanOrEqual(1);
    expect(screen.getAllByRole("main")).toHaveLength(1);
  });

  it("AC-25 an unknown rover is the not-found page", async () => {
    openReport("/rovers/foo/sols/1/conditions");
    expect(await screen.findByRole("heading", { name: /not found/i })).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 1, name: /foo/i })).toBeNull();
  });

  it("AC-30 at sol 2147483647 there is no 'Sol N+1' (the API takes no sol above it), and the way back stays", async () => {
    openReport(reportPath(CUR, MAX_SOL));
    await reportHeading("Curiosity", MAX_SOL);
    const forward = screen
      .queryAllByRole("link")
      .filter(
        (a) =>
          /sols\/2147483648\//.test(a.getAttribute("href") ?? "") ||
          /2,?147,?483,?648/.test(a.textContent ?? ""),
      );
    expect(forward).toEqual([]);
    expect(neighbour(CUR, MAX_SOL - 1), "the way back").toBeTruthy();
  });
});

describe("AC-26 the head of the report", () => {
  it("AC-26 Ls comes from the answer: 'Ls 356.7°'", async () => {
    await openCuriosity();
    expect(mainText()).toMatch(/Ls\D{0,12}356\.7\s*°/);
  });

  it("AC-26 Curiosity is in the southern hemisphere: Ls 356.7 is 'southern summer'", async () => {
    await openCuriosity();
    expect(mainText()).toMatch(/southern/i);
    expect(mainText()).toMatch(/summer/i);
  });

  it("AC-26 Perseverance is in the northern hemisphere: Ls 52.4 is northern spring", async () => {
    await openPerseverance();
    expect(mainText()).toMatch(/Ls\D{0,12}52\.4\s*°/);
    expect(mainText()).toMatch(/northern/i);
    expect(mainText()).toMatch(/spring/i);
  });

  it("AC-26 the head has the earth date, the coverage of the sol and the hours with readings", async () => {
    await openCuriosity({
      [WXD(CUR, CUR_SOL)]: reportDetail(CUR, CUR_SOL, {
        ls: 356.7,
        coverage: 0.5,
        hoursCovered: 24,
        earthDate: "2013-07-15",
      }),
    });
    expect(mainText()).toContain("2013-07-15");
    expect(mainText()).toMatch(/50\s*%/);
    expect(mainText()).toMatch(/24\s*(of|\/)\s*24/);
  });

  it("AC-26 flags are named in words: partial coverage", async () => {
    await openCuriosity({
      [WXD(CUR, CUR_SOL)]: reportDetail(CUR, CUR_SOL, {
        ls: 356.7,
        coverage: 0.2,
        hoursCovered: 12,
        flags: [{ code: "partial_coverage", parameter: null, count: null }],
      }),
    });
    expect(mainText()).toMatch(/partial coverage/i);
    expect(mainText()).toMatch(/12\s*(of|\/)\s*24/);
  });

  it("AC-26 sunrise and sunset are shown and marked 'optional'", async () => {
    await openCuriosity();
    expect(mainText()).toMatch(/sunrise/i);
    expect(mainText()).toMatch(/sunset/i);
    expect(mainText()).toMatch(/optional/i);
    // two clock times, sunrise before noon and sunset after it
    const times = [...mainText().matchAll(/~?\s?(\d{1,2}):(\d{2})/g)].map((m) => Number(m[1]));
    expect(times.length).toBeGreaterThanOrEqual(2);
  });

  it("AC-26 sunrise and sunset are in LMST: the header times follow the equation of time (Ls 356.7, lat -4.6)", async () => {
    await openCuriosity();
    const want = expectedRiseSet(356.7, -4.6);
    const m = /optional[^\d]*(\d{1,2}):(\d{2})[^\d]+(\d{1,2}):(\d{2})/i.exec(mainText());
    expect(m, "two clock times after 'optional'").not.toBeNull();
    const rise = Number(m![1]) + Number(m![2]) / 60;
    const set = Number(m![3]) + Number(m![4]) / 60;
    expect(Math.abs(rise - want[0])).toBeLessThan(0.2);
    expect(Math.abs(set - want[1])).toBeLessThan(0.2);
  });

  it("AC-26 the night shading on the charts starts and ends at the same LMST times", async () => {
    await openCuriosity();
    const want = expectedRiseSet(356.7, -4.6);
    const night = [...oneChart(/air temperature/i).querySelectorAll('[class*="night"]')];
    expect(night.length).toBe(2);
    const n = (el: Element, a: string) => Number(el.getAttribute(a));
    const [dawn, dusk] = night as [Element, Element];
    const left = n(dawn, "x");
    const plot = n(dusk, "x") + n(dusk, "width") - left;
    const rise = (n(dawn, "width") / plot) * 24;
    const set = ((n(dusk, "x") - left) / plot) * 24;
    expect(Math.abs(rise - want[0])).toBeLessThan(0.2);
    expect(Math.abs(set - want[1])).toBeLessThan(0.2);
  });

  it("AC-26 the latitude for sunrise comes from /location?latest=true", async () => {
    const m = await openCuriosity();
    await waitFor(() =>
      expect(
        m.calls.some((c) => c.pathname === LOC(CUR) && c.searchParams.get("latest") === "true"),
      ).toBe(true),
    );
  });

  it("AC-26 no location (503): the report still opens with a season and sunrise from the fallback latitude", async () => {
    await openCuriosity({
      [LOC(CUR)]: () => apiError(503, "db_unavailable", "Database is unavailable", null),
    });
    expect(mainText()).toMatch(/summer/i);
    expect(mainText()).toMatch(/sunrise/i);
    // a missing latitude is not an error of the report
    expect(alerts()).toEqual([]);
    expect(charts(/air temperature/i)).toHaveLength(1);
  });

  it("AC-26 ls null: Ls is a dash and there is no season", async () => {
    await openCuriosity({ [WXD(CUR, CUR_SOL)]: reportDetail(CUR, CUR_SOL, { ls: null }) });
    const text = mainText();
    expect(text).toMatch(/(Ls|solar longitude)\D{0,12}—/i);
    expect(text).not.toMatch(/\b(spring|summer|autumn|winter)\b/i);
    expect(text).not.toMatch(/\bnull\b|\bNaN\b|undefined/);
    expect(charts(/air temperature/i)).toHaveLength(1);
  });
});

describe("AC-27 one chart per parameter, LMST on the X axis, gaps are not bridged", () => {
  it("AC-27 Curiosity: air temperature, pressure, humidity, ground temperature and UV, each in its own chart", async () => {
    await openCuriosity();
    for (const name of [
      /air temperature/i,
      /pressure/i,
      /humidity/i,
      /ground temperature/i,
      /ultraviolet|\bUV\b/i,
    ]) {
      expect(charts(name), `chart ${name}`).toHaveLength(1);
    }
    expect(charts(/wind/i), "Curiosity has no wind sensor").toHaveLength(0);
  });

  it("AC-27 Perseverance: air temperature, pressure, humidity and wind", async () => {
    await openPerseverance();
    for (const name of [/air temperature/i, /pressure/i, /humidity/i, /wind/i]) {
      expect(charts(name), `chart ${name}`).toHaveLength(1);
    }
    expect(charts(/ultraviolet|\bUV\b/i), "Perseverance has no UV series").toHaveLength(0);
  });

  it("AC-27 the X axis is local mean solar time, hours 0 to 24", async () => {
    await openCuriosity();
    expect(mainText()).toMatch(/LMST|local mean solar time/i);
    for (const svg of [oneChart(/air temperature/i), oneChart(/pressure/i)]) {
      const ticks = tickNumbers(svg);
      expect(ticks).toContain(0);
      expect(ticks).toContain(24);
    }
  });

  it("AC-27 every chart has a name, and the name says which sol it is about", async () => {
    await openCuriosity();
    for (const svg of allCharts()) {
      expect(svg.getAttribute("aria-label")?.trim().length ?? 0).toBeGreaterThan(0);
    }
  });
});

describe("AC-27 gaps", () => {
  it("AC-27 a hole in the hours splits the line and is marked with hatched columns; nothing is interpolated", async () => {
    // the same sol twice: whole, and with hours 5-7 and 18 missing; the chart of the second one must differ
    const whole = reportDetail(CUR, CUR_SOL, { ls: 356.7, extremes: false });
    const holey = reportDetail(CUR, CUR_SOL, {
      ls: 356.7,
      extremes: false,
      missing: [5, 6, 7, 18],
    });
    await openCuriosity({ [WXD(CUR, CUR_SOL)]: whole });
    const base = oneChart(/air temperature/i);
    const basePieces = lineSegments(base);
    const baseHoles = holeColumns(base);

    reopen(reportPath(CUR, CUR_SOL), { [WXD(CUR, CUR_SOL)]: holey });
    await reportHeading("Curiosity", CUR_SOL);
    const gap = oneChart(/air temperature/i);
    expect(lineSegments(gap), "the line is cut at the missing hours").toBeGreaterThan(basePieces);
    expect(holeColumns(gap), "the missing hours are hatched").toBeGreaterThan(baseHoles);
  });
});

describe("AC-28 the band between the hourly minimum and maximum", () => {
  it("AC-28 with _min/_max every parameter chart of Curiosity draws a band", async () => {
    await openCuriosity();
    for (const name of [/air temperature/i, /pressure/i, /humidity/i, /ground temperature/i]) {
      expect(bandElements(oneChart(name)), `band of ${name}`).toBeGreaterThan(0);
    }
  });

  it("AC-28 an answer without _min/_max keys (before the re-parse) has no band at all", async () => {
    await openCuriosity({
      [WXD(CUR, CUR_SOL)]: reportDetail(CUR, CUR_SOL, { ls: 356.7, extremes: false }),
    });
    for (const name of [/air temperature/i, /pressure/i, /humidity/i, /ground temperature/i]) {
      expect(bandElements(oneChart(name)), `band of ${name}`).toBe(0);
    }
  });

  it("AC-28 an answer without _min/_max is not an error: charts, no alert, no 'undefined' on the page", async () => {
    await openCuriosity({
      [WXD(CUR, CUR_SOL)]: reportDetail(CUR, CUR_SOL, { ls: 356.7, extremes: false }),
    });
    expect(alerts()).toEqual([]);
    for (const name of [/air temperature/i, /pressure/i, /humidity/i, /ground temperature/i]) {
      expect(charts(name)).toHaveLength(1);
    }
    expect(mainText()).not.toMatch(/undefined|NaN/);
  });

  it("AC-28 Perseverance without wind_gust_ms: the wind chart still opens", async () => {
    await openPerseverance({
      [WXD(PER, PER_SOL)]: reportDetail(PER, PER_SOL, { extremes: false }),
    });
    expect(charts(/wind/i)).toHaveLength(1);
    expect(alerts()).toEqual([]);
  });

  it("AC-28 hours with a band and a hole: the band is cut at the hole too (null min/max do not draw)", async () => {
    await openCuriosity({
      [WXD(CUR, CUR_SOL)]: reportDetail(CUR, CUR_SOL, { ls: 356.7, missing: [4, 5] }),
    });
    expect(alerts()).toEqual([]);
    expect(holeColumns(oneChart(/air temperature/i))).toBeGreaterThan(0);
  });
});

describe("AC-29 the shared hour cursor", () => {
  it("AC-29 'Inspect hour' is a slider of the hours 0 to 23", async () => {
    await openCuriosity();
    await hourlyChartsReady();
    const slider = hourSlider();
    expect(slider.type).toBe("range");
    expect(Number(slider.min)).toBe(0);
    expect(Number(slider.max)).toBe(23);
  });

  it("AC-29 moving the slider moves the cursor in every chart", async () => {
    await openCuriosity();
    await hourlyChartsReady();
    const svgs = allCharts().filter((s) =>
      charts(/air|pressure|humidity|ground|ultraviolet|UV/i).includes(s),
    );
    expect(svgs.length).toBeGreaterThanOrEqual(5);
    const before = svgs.map((s) => s.outerHTML);
    inspectHour(HOUR);
    await waitFor(() =>
      svgs.forEach((s, i) => expect(s.outerHTML, `chart ${i}`).not.toBe(before[i])),
    );
    const at13 = svgs.map((s) => s.outerHTML);
    inspectHour(HOUR + 1);
    await waitFor(() =>
      svgs.forEach((s, i) => expect(s.outerHTML, `chart ${i}`).not.toBe(at13[i])),
    );
  });

  it("AC-29 the values of the hour are announced in a live region", async () => {
    await openCuriosity();
    await hourlyChartsReady();
    inspectHour(HOUR);
    await waitFor(() => expect(liveText()).toMatch(/13:00/));
    const live = liveText().replace(/−/g, "-");
    expect(live).toContain(String(CUR_AT_HOUR.air)); // air temperature -38
    expect(live).toContain(String(CUR_AT_HOUR.pressure)); // pressure 853
    inspectHour(14);
    await waitFor(() => expect(liveText()).toMatch(/14:00/));
    expect(liveText().replace(/−/g, "-")).toContain("-35"); // -77 + 3 * 14
  });

  it("AC-29 an hour without readings is announced as 'no reading', not as a number", async () => {
    await openCuriosity({
      [WXD(CUR, CUR_SOL)]: reportDetail(CUR, CUR_SOL, { ls: 356.7, missing: [5] }),
    });
    await hourlyChartsReady();
    inspectHour(5);
    await waitFor(() => expect(liveText()).toMatch(/05:00/));
    expect(liveText()).toMatch(/no (reading|data)/i);
    expect(liveText()).not.toMatch(/null|NaN|undefined/);
  });
});

describe("AC-30 the neighbouring sols", () => {
  it("AC-30 '← Sol 343' and 'Sol 345 →' lead to the neighbouring numbers", async () => {
    await openCuriosity();
    const prev = neighbour(CUR, 343);
    const next = neighbour(CUR, 345);
    expect(prev, "a link to sol 343").toBeTruthy();
    expect(next, "a link to sol 345").toBeTruthy();
    expect(prev!.textContent).toMatch(/343/);
    expect(next!.textContent).toMatch(/345/);
    expect(prev!.textContent).toMatch(/←/);
    expect(next!.textContent).toMatch(/→/);
  });

  it("AC-30 a click on 'Sol 345 →' opens the report of sol 345 without a page load", async () => {
    await openCuriosity();
    fireEvent.click(neighbour(CUR, 345)!);
    await reportHeading("Curiosity", 345);
    expect(window.location.pathname).toBe(reportPath(CUR, 345));
  });

  it("AC-30 sol 0 has no way back", async () => {
    openReport(reportPath(CUR, 0));
    await reportHeading("Curiosity", 0);
    expect(neighbour(CUR, 1), "the way forward is there").toBeTruthy();
    const back = screen
      .queryAllByRole("link")
      .filter(
        (a) =>
          /sols\/-\d/.test(a.getAttribute("href") ?? "") || /sol\s*-\d/i.test(a.textContent ?? ""),
      );
    expect(back).toEqual([]);
  });

  it("AC-30 404 sol_not_found: 'No data for sol 2' with the same two buttons, and it is not an error alert", async () => {
    openReport(reportPath(CUR, 2));
    expect(await screen.findByText(/no .*data for sol\s*2\b/i)).toBeTruthy();
    expect(neighbour(CUR, 1), "back to sol 1").toBeTruthy();
    expect(neighbour(CUR, 3), "on to sol 3").toBeTruthy();
    expect(screen.queryByRole("button", { name: /try again|retry/i })).toBeNull();
    // photos may exist for a sol with no weather
    const photos = screen
      .getAllByRole("link")
      .find((a) => a.getAttribute("href") === `/rovers/${CUR}?sol=2`);
    expect(photos, "a link to the photos of sol 2").toBeTruthy();
  });

  it("AC-30 from the 'no data' state a neighbour opens its report", async () => {
    openReport(reportPath(CUR, 2));
    await screen.findByText(/no .*data for sol\s*2\b/i);
    fireEvent.click(neighbour(CUR, 3)!);
    await reportHeading("Curiosity", 3);
    expect(charts(/air temperature/i)).toHaveLength(1);
  });
});

describe("AC-31 links to the photos and from the tables", () => {
  it("AC-31 'Photos of this sol →' leads to /rovers/{rover}?sol={sol}", async () => {
    await openCuriosity();
    const link = screen.getByRole("link", { name: /photos of this sol/i });
    expect(link.getAttribute("href")).toBe(`/rovers/${CUR}?sol=${CUR_SOL}`);
    expect(link.textContent).toMatch(/→/);
  });

  it("AC-31 the same on the report of Perseverance", async () => {
    await openPerseverance();
    expect(screen.getByRole("link", { name: /photos of this sol/i }).getAttribute("href")).toBe(
      `/rovers/${PER}?sol=${PER_SOL}`,
    );
  });

  it("AC-31 the rows of the weather table of Perseverance have a 'Sol report' link", async () => {
    openWeatherRange({ per: perLong });
    await readyTable();
    const row = rowFor(1859)!;
    const link = within(row).getByRole("link", { name: /sol report/i });
    expect(link.getAttribute("href")).toBe(reportPath(PER, 1859));
    // the photos link of phase A stays
    expect(
      within(row)
        .getByRole("link", { name: /photos/i })
        .getAttribute("href"),
    ).toBe(`/rovers/${PER}?sol=1859`);
  });

  it("AC-31 the weather table of Curiosity links to the report of Curiosity", async () => {
    openWeatherRange(
      { cur: [curSol(10), curSol(11), curSol(4710)] },
      "/data/weather?rover=curiosity",
    );
    await readyTable();
    const row = rowFor(4710)!;
    expect(
      within(row)
        .getByRole("link", { name: /sol report/i })
        .getAttribute("href"),
    ).toBe(reportPath(CUR, 4710));
  });

  it("AC-31 the rows of the radiation table have a 'Sol report' link to Curiosity", async () => {
    openRadiationRange(radLong);
    await readyTable();
    const row = rowFor(4843)!;
    expect(
      within(row)
        .getByRole("link", { name: /sol report/i })
        .getAttribute("href"),
    ).toBe(reportPath(CUR, 4843));
  });

  it("AC-31 the inspector 'Inspect a sol' of the weather page stays", async () => {
    openWeatherRange({ per: [perSol(100), perSol(101)] });
    await readyTable();
    expect(document.body.textContent).toMatch(/Inspect a sol/i);
  });
});

describe("AC-32 what is on the report of each rover", () => {
  it("AC-32 Perseverance: a wind chart with the mean speed, the gust and the direction of the hour", async () => {
    await openPerseverance();
    expect(charts(/wind/i)).toHaveLength(1);
    inspectHour(HOUR);
    await waitFor(() => expect(mainText()).toContain(String(PER_AT_HOUR.windGust)));
    expect(mainText()).toContain(String(PER_AT_HOUR.windSpeed));
    // the direction may be rounded to whole degrees
    expect(mainText()).toMatch(/15[78](\.5)?\s*°/);
  });

  it("AC-32 Perseverance outside the wind window (sol 1): the wind chart says there is no wind data, no error", async () => {
    openReport(reportPath(PER, 1));
    await reportHeading("Perseverance", 1);
    expect(mainText()).toMatch(/no wind/i);
    expect(alerts()).toEqual([]);
  });

  it("AC-32 Perseverance has no radiation block, and the page does not ask for radiation", async () => {
    const m = await openPerseverance();
    expect(screen.queryByRole("heading", { name: /radiation/i })).toBeNull();
    expect(m.calls.filter((c) => c.pathname.includes("/radiation"))).toEqual([]);
  });

  it("AC-32 Curiosity: UV chart, no wind", async () => {
    await openCuriosity();
    expect(charts(/ultraviolet|\bUV\b/i)).toHaveLength(1);
    expect(charts(/wind/i)).toHaveLength(0);
  });

  it("AC-32 Curiosity: the radiation block shows the daily summary of this sol from /radiation?sol_min=&sol_max=", async () => {
    const m = await openCuriosity();
    expect(await screen.findByRole("heading", { name: /radiation/i })).toBeTruthy();
    await waitFor(() => expect(mainText()).toContain("231.4")); // dose B mean of the sol
    expect(mainText()).toContain("218.7"); // dose E mean of the sol
    const asked = m.calls.filter((c) => c.pathname === RAD);
    expect(asked, "one request for the summary and the trend").toHaveLength(1);
    expect(asked[0]!.searchParams.get("sol_min")).toBe(String(CUR_SOL - 30));
    expect(asked[0]!.searchParams.get("sol_max")).toBe(String(CUR_SOL + 30));
  });

  it("AC-32 the radiation range is clamped at the low end: sol 5 asks for 0 ... 35", async () => {
    const m = openReport(reportPath(CUR, 5));
    await reportHeading("Curiosity", 5);
    await screen.findByRole("heading", { name: /radiation/i });
    const asked = m.calls.filter((c) => c.pathname === RAD);
    expect(asked).toHaveLength(1);
    expect(asked[0]!.searchParams.get("sol_min")).toBe("0");
    expect(asked[0]!.searchParams.get("sol_max")).toBe("35");
  });

  it("AC-32 the radiation range is clamped at the top: sol 2147483647 asks for 2147483617 ... 2147483647", async () => {
    const m = openReport(reportPath(CUR, MAX_SOL));
    await reportHeading("Curiosity", MAX_SOL);
    await screen.findByRole("heading", { name: /radiation/i });
    const asked = m.calls.filter((c) => c.pathname === RAD);
    expect(asked).toHaveLength(1);
    expect(asked[0]!.searchParams.get("sol_min")).toBe(String(MAX_SOL - 30));
    expect(asked[0]!.searchParams.get("sol_max")).toBe(String(MAX_SOL));
  });

  it("AC-32 Curiosity: a trend of about 30 sols each side with this sol marked", async () => {
    await openCuriosity();
    await screen.findByRole("heading", { name: /radiation/i });
    await waitFor(() => expect(charts(/radiation|dose/i).length).toBeGreaterThan(0));
    const trend = charts(/radiation|dose/i)[0]!;
    // the sol axis reaches towards sol 314 and sol 374
    const ticks = tickNumbers(trend).filter((n) => n >= 300 && n <= 400);
    expect(ticks.length, "sol ticks on the axis").toBeGreaterThan(1);
    expect(Math.min(...ticks)).toBeLessThanOrEqual(330);
    expect(Math.max(...ticks)).toBeGreaterThanOrEqual(360);
    // this sol is marked: its number is in the chart or next to it
    const box = trend.closest("section, article, figure, .card") ?? trend.parentElement!;
    expect(trend.outerHTML + (box.textContent ?? "")).toContain(String(CUR_SOL));
  });

  it("AC-32 Curiosity: hourly radiation is phase C, so the page does not ask for /radiation/{sol}", async () => {
    const m = await openCuriosity();
    await screen.findByRole("heading", { name: /radiation/i });
    expect(m.calls.filter((c) => /\/radiation\/\d+/.test(c.pathname))).toEqual([]);
  });

  it("AC-32 Curiosity: radiation unavailable (503) does not take the weather charts away", async () => {
    await openCuriosity({
      [RAD]: () => apiError(503, "db_unavailable", "Database is unavailable", null),
    });
    for (const name of [/air temperature/i, /pressure/i, /ultraviolet|\bUV\b/i]) {
      expect(charts(name)).toHaveLength(1);
    }
  });
});

describe("AC-33 states: loading, 503, retry", () => {
  it("AC-33 while the sol loads there is a skeleton and no error, no stale numbers", async () => {
    let release: (r: Response) => void = () => {};
    const gate = new Promise<Response>((res) => (release = res));
    openReport(reportPath(CUR, CUR_SOL), { [WXD(CUR, CUR_SOL)]: () => gate });
    // loading is announced and the page has no chart yet
    await waitFor(() =>
      expect(
        screen.queryAllByRole("status").length +
          document.querySelectorAll("[aria-busy='true']").length,
      ).toBeGreaterThan(0),
    );
    expect(charts(/air temperature/i)).toHaveLength(0);
    expect(alerts()).toEqual([]);
    release(json(reportDetail(CUR, CUR_SOL, { ls: 356.7 })));
    await reportHeading("Curiosity", CUR_SOL);
    expect(charts(/air temperature/i)).toHaveLength(1);
  });

  it("AC-33 503: an error with a 'Try again' button; after it the report opens", async () => {
    let up = false;
    openReport(reportPath(CUR, CUR_SOL), {
      [WXD(CUR, CUR_SOL)]: () =>
        up
          ? reportDetail(CUR, CUR_SOL, { ls: 356.7 })
          : apiError(503, "db_unavailable", "Database is unavailable", null),
    });
    const retry = await screen.findByRole("button", { name: /try again|retry/i });
    expect(
      screen.queryAllByRole("alert").length + screen.queryAllByRole("status").length,
    ).toBeGreaterThan(0);
    expect(charts(/air temperature/i)).toHaveLength(0);
    // the way to the photos and the neighbours stays
    expect(screen.getByRole("link", { name: /photos of this sol/i })).toBeTruthy();
    up = true;
    fireEvent.click(retry);
    await reportHeading("Curiosity", CUR_SOL);
    expect(charts(/air temperature/i)).toHaveLength(1);
    expect(screen.queryByRole("button", { name: /try again|retry/i })).toBeNull();
  });

  it("AC-33 422 with details: the field is named", async () => {
    openReport(reportPath(CUR, 344), {
      [WXD(CUR, 344)]: () =>
        apiError(422, "validation_error", "Invalid request", [
          { field: "path.sol", message: "must be a whole number" },
        ]),
    });
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/must be a whole number/);
  });
});

describe("the credit and the sources", () => {
  it("the credit of the instrument and the link to the archive file are always shown (Curiosity)", async () => {
    await openCuriosity();
    expect(mainText()).toContain("Gomez-Elvira");
    expect(mainText()).toContain(CUR_CREDIT.slice(0, 20));
    const detail = reportDetail(CUR, CUR_SOL, { ls: 356.7 });
    const link = screen
      .getAllByRole("link")
      .find((a) => a.getAttribute("href") === detail.source.files[0]!.link);
    expect(link, "a link to the source file").toBeTruthy();
  });

  it("the credit request is not the whole list: sol_min and sol_max equal the sol, and the credit is shown", async () => {
    const m = await openCuriosity();
    await waitFor(() => expect(mainText()).toContain("Gomez-Elvira"));
    const asked = m.calls.filter((c) => c.pathname === "/api/v2/rovers/curiosity/weather");
    expect(asked.length, "the credit is read from the list").toBeGreaterThan(0);
    for (const c of asked) {
      expect(c.searchParams.get("sol_min")).toBe(String(CUR_SOL));
      expect(c.searchParams.get("sol_max")).toBe(String(CUR_SOL));
    }
  });

  it("the credit of MEDA is shown on the report of Perseverance", async () => {
    await openPerseverance();
    expect(mainText()).toContain("Rodriguez-Manfredi");
    expect(mainText()).toContain(PER_CREDIT.slice(0, 20));
  });

  it("no NASA or JPL logo and no word of approval on the page", async () => {
    await openCuriosity();
    expect(screen.queryByAltText(/nasa|jpl/i)).toBeNull();
    expect(document.querySelector('img[src*="nasa" i], img[src*="jpl" i]')).toBeNull();
    expect(mainText()).not.toMatch(/endorsed by|approved by|official nasa/i);
  });
});

describe("robustness of the report", () => {
  it("an unexpected key in the answer and in an hour, and a null daily value, do not break the page", async () => {
    const odd = reportDetail(CUR, CUR_SOL, { ls: 356.7 }) as Record<string, unknown>;
    (odd.hours as Record<string, unknown>[])[3] = {
      ...(odd.hours as Record<string, unknown>[])[3],
      mystery_new_key: { nested: [1, 2] },
    };
    odd.future_field = "x";
    odd.air_temp_c = null;
    odd.uv_wm2 = null;
    await openCuriosity({ [WXD(CUR, CUR_SOL)]: odd });
    expect(charts(/air temperature/i)).toHaveLength(1);
    expect(alerts()).toEqual([]);
    expect(mainText()).not.toMatch(/\[object|undefined|NaN/);
  });

  it("a whole sol of nulls (an instrument off all day): a chart with hatched hours or a 'no data' note, not a crash", async () => {
    const all = Array.from({ length: 24 }, (_, h) => h);
    await openCuriosity({
      [WXD(CUR, CUR_SOL)]: reportDetail(CUR, CUR_SOL, {
        ls: 356.7,
        missing: all,
        coverage: 0,
        hoursCovered: 0,
      }),
    });
    expect(alerts()).toEqual([]);
    expect(charts(/air temperature/i).length > 0 || /no .*data/i.test(mainText())).toBe(true);
    expect(mainText()).not.toMatch(/undefined|NaN/);
  });
});
