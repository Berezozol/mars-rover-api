import { fireEvent, screen, waitFor, within } from "@testing-library/preact";
import { describe, expect, it } from "vitest";
import { apiError } from "./helpers";
import { weatherList } from "./fixtures/weather";
import {
  PER_FIRST,
  PER_GAP,
  PER_SOURCE_MAX,
  CUR_FIRST,
  CUR_SOURCE_MAX,
  curLong,
  perLong,
} from "./fixtures/range";
import { WX } from "./weather-helpers";
import {
  alertText,
  axisNumbers,
  bodyRows,
  chartByName,
  currentPage,
  endHandle,
  expectRange,
  fromInput,
  handleNow,
  headerOf,
  isHighlighted,
  jumpInput,
  nextPage,
  num,
  openWeatherRange,
  pagerEl,
  pressEnter,
  preset,
  rangeInFields,
  readyTable,
  rowFor,
  solOfRow,
  solsInTable,
  startHandle,
  statusText,
  tableEl,
  toInput,
  typeInto,
  viewWidth,
  zoneOf,
} from "./range-helpers";

// Stage 22 phase A, weather (docs/proposals/sol-conditions.md section 2, AC-1 ... AC-14; mockup
// design/mockups/weather-range.html). Perseverance: sols 3-1859 published (a gap at 1800-1805), the rover is at
// sol 2031. Curiosity: sols 9-4710 published, the rover is at sol 5066.

const MAX_SOL_PER = 2031;
const label = (el: Element | undefined) => el?.getAttribute("aria-label") ?? "";
const daily = () => chartByName(/daily/i);

async function openPerseverance(path?: string) {
  const m = openWeatherRange({ per: perLong }, path);
  await readyTable();
  // Rover.max_sol (2031) comes with the rover card: the limits of the range need it
  await waitFor(() => expect(document.body.textContent).toMatch(/172 sols/));
  return m;
}

async function openCuriosity() {
  const m = openWeatherRange({ cur: curLong });
  await readyTable();
  fireEvent.click(screen.getByRole("radio", { name: /Curiosity/ }));
  await expectRange(CUR_SOURCE_MAX - 668, CUR_SOURCE_MAX);
  await waitFor(() => expect(document.body.textContent).toMatch(/356 sols/));
  return m;
}

/** Set both fields the way a user does and wait for the new range. */
async function setRange(from: number, to: number) {
  typeInto(toInput(), String(to));
  pressEnter(toInput());
  typeInto(fromInput(), String(from));
  pressEnter(fromInput());
  await expectRange(from, to);
}

describe("AC-1 the default range", () => {
  it("AC-1 Perseverance opens on the last Mars year before source_max_sol, not before the rover's sol", async () => {
    await openPerseverance();
    await expectRange(PER_SOURCE_MAX - 668, PER_SOURCE_MAX);
    expect(handleNow(startHandle())).toBe(PER_SOURCE_MAX - 668);
    expect(handleNow(endHandle())).toBe(PER_SOURCE_MAX);
    expect(solsInTable()[0]).toBe(PER_SOURCE_MAX);
  });

  it("AC-1 Curiosity: first sol 9, source_max 4710 -> [4042, 4710] although the rover is at sol 5066", async () => {
    await openCuriosity();
    expect(rangeInFields()).toEqual([4042, 4710]);
    expect(label(daily())).toMatch(/4,?042/);
    expect(label(daily())).toMatch(/4,?710/);
    expect(label(daily())).not.toMatch(/5,?066/);
  });

  it("AC-1 a series shorter than a Mars year starts at its first sol", async () => {
    openWeatherRange({}); // the default fixture: sols 100-108
    await readyTable();
    await expectRange(100, 108);
  });
});

describe("AC-2 presets count from source_max_sol: the 'Last 100 sols' defect", () => {
  it("AC-2 the presets are 'Last Mars year', 'Last 120 sols' and 'Whole published data'; the old picker is gone", async () => {
    await openCuriosity();
    const group = screen.getByRole("group", { name: /range presets/i });
    const names = within(group)
      .getAllByRole("button")
      .map((b) => b.textContent?.trim() ?? "");
    expect(names).toEqual([
      expect.stringMatching(/Last Mars year/),
      expect.stringMatching(/Last 120 sols/),
      expect.stringMatching(/Whole published data/),
    ]);
    expect(screen.queryByText(/Last 100 sols/)).toBeNull();
    expect(screen.queryByLabelText("Sols shown")).toBeNull();
  });

  it("AC-2 'Last 120 sols' -> [4591, 4710]: the table and the chart are not empty", async () => {
    await openCuriosity();
    fireEvent.click(preset(/Last 120 sols/));
    await expectRange(4591, 4710);
    const sols = solsInTable();
    expect(sols.length).toBeGreaterThan(0);
    expect(sols[0]).toBe(4710);
    expect(label(daily())).toMatch(/4,?591/);
    expect(label(daily())).not.toMatch(/no data in this range/i);
    expect(screen.queryByText(/no sols with data/i)).toBeNull();
  });

  it("AC-2 'Whole published data' -> [first sol, 4710]", async () => {
    await openCuriosity();
    fireEvent.click(preset(/Whole published data/));
    await expectRange(CUR_FIRST, CUR_SOURCE_MAX);
    expect(handleNow(startHandle())).toBe(CUR_FIRST);
  });

  it("AC-2 'Last Mars year' brings the default back", async () => {
    await openCuriosity();
    fireEvent.click(preset(/Last 120 sols/));
    await expectRange(4591, 4710);
    fireEvent.click(preset(/Last Mars year/));
    await expectRange(4042, 4710);
  });
});

describe("AC-3 the two handles of the range slider", () => {
  const start = () => startHandle();
  const end = () => endHandle();

  it("AC-3 both handles are role=slider with a name, aria-valuemin/max/now and a text value ('sol 1,191')", async () => {
    await openPerseverance();
    for (const h of [start(), end()]) {
      expect(h.getAttribute("aria-valuemin")).not.toBeNull();
      expect(h.getAttribute("aria-valuemax")).not.toBeNull();
      expect(Number(h.getAttribute("aria-valuemin"))).toBeLessThanOrEqual(handleNow(h));
      expect(Number(h.getAttribute("aria-valuemax"))).toBeGreaterThanOrEqual(handleNow(h));
      expect(h.getAttribute("tabindex")).not.toBeNull(); // reachable with Tab
    }
    expect(start().getAttribute("aria-valuetext")).toMatch(/sol 1,?191/i);
    expect(end().getAttribute("aria-valuetext")).toMatch(/sol 1,?859/i);
  });

  it("AC-3 arrow keys move a handle by one sol, and the text value follows", async () => {
    await openPerseverance();
    fireEvent.keyDown(start(), { key: "ArrowRight" });
    await expectRange(1192, 1859);
    expect(handleNow(start())).toBe(1192);
    expect(start().getAttribute("aria-valuetext")).toMatch(/sol 1,?192/i);
    fireEvent.keyDown(start(), { key: "ArrowLeft" });
    fireEvent.keyDown(start(), { key: "ArrowLeft" });
    await expectRange(1190, 1859);
    fireEvent.keyDown(end(), { key: "ArrowLeft" });
    await expectRange(1190, 1858);
    expect(end().getAttribute("aria-valuetext")).toMatch(/sol 1,?858/i);
  });

  it("AC-3 PageUp / PageDown move a handle by ten sols", async () => {
    await openPerseverance();
    fireEvent.keyDown(start(), { key: "PageUp" });
    await expectRange(1201, 1859);
    fireEvent.keyDown(start(), { key: "PageDown" });
    fireEvent.keyDown(start(), { key: "PageDown" });
    await expectRange(1181, 1859);
    fireEvent.keyDown(end(), { key: "PageDown" });
    await expectRange(1181, 1849);
  });

  it("AC-3 Home and End go to the limits: the first sol of the data and the rover's current sol", async () => {
    await openPerseverance();
    fireEvent.keyDown(start(), { key: "Home" });
    await expectRange(PER_FIRST, PER_SOURCE_MAX);
    fireEvent.keyDown(end(), { key: "End" });
    await expectRange(PER_FIRST, MAX_SOL_PER);
    expect(handleNow(end())).toBe(MAX_SOL_PER);
  });

  it("AC-3 the handles do not cross: the start stops short of the end, the width is at least 6 sols", async () => {
    await openPerseverance();
    fireEvent.keyDown(start(), { key: "End" });
    await waitFor(() => expect(rangeInFields()[0]).toBeGreaterThan(1859 - 100));
    let [from, to] = rangeInFields();
    expect(to).toBe(1859);
    expect(from).toBeLessThan(to);
    expect(to - from + 1).toBeGreaterThanOrEqual(6);
    // more presses to the right do not push through
    for (let i = 0; i < 12; i++) fireEvent.keyDown(start(), { key: "ArrowRight" });
    [from, to] = rangeInFields();
    expect(to - from + 1).toBeGreaterThanOrEqual(6);
    expect(from).toBeLessThan(to);
  });

  it("AC-3 the end handle stops short of the start from the other side", async () => {
    await openPerseverance();
    fireEvent.keyDown(end(), { key: "Home" });
    await waitFor(() => expect(rangeInFields()[1]).toBeLessThan(1191 + 100));
    for (let i = 0; i < 12; i++) fireEvent.keyDown(end(), { key: "ArrowLeft" });
    const [from, to] = rangeInFields();
    expect(from).toBe(1191);
    expect(to - from + 1).toBeGreaterThanOrEqual(6);
  });

  it("AC-3 moving a handle redraws the chart and the table", async () => {
    await openPerseverance();
    const before = label(daily());
    fireEvent.keyDown(end(), { key: "PageDown" });
    await expectRange(1191, 1849);
    await waitFor(() => expect(label(daily())).not.toBe(before));
    expect(solsInTable()[0]).toBe(1849);
  });
});

describe("AC-4 the fields 'From sol' and 'To sol'", () => {
  it("AC-4 'To sol' = 'From sol' + 3 is refused: aria-invalid, a text of the error, the range stays", async () => {
    await openPerseverance();
    typeInto(toInput(), String(1191 + 3));
    pressEnter(toInput());
    await waitFor(() => expect(toInput().getAttribute("aria-invalid")).toBe("true"));
    await waitFor(() => expect(alertText()).not.toBe(""));
    expect(handleNow(startHandle())).toBe(1191);
    expect(handleNow(endHandle())).toBe(1859);
  });

  it("AC-4 text like 'abc' is refused", async () => {
    await openPerseverance();
    typeInto(fromInput(), "abc");
    pressEnter(fromInput());
    await waitFor(() => expect(fromInput().getAttribute("aria-invalid")).toBe("true"));
    await waitFor(() => expect(alertText()).not.toBe(""));
    expect(handleNow(startHandle())).toBe(1191);
    expect(handleNow(endHandle())).toBe(1859);
  });

  it("AC-4 a sol before the first sol of the data is refused", async () => {
    await openPerseverance();
    typeInto(fromInput(), String(PER_FIRST - 1));
    pressEnter(fromInput());
    await waitFor(() => expect(fromInput().getAttribute("aria-invalid")).toBe("true"));
    expect(alertText()).not.toBe("");
    expect(handleNow(startHandle())).toBe(1191);
  });

  it("AC-4 a sol beyond the rover's current sol is refused", async () => {
    await openPerseverance();
    typeInto(toInput(), String(MAX_SOL_PER + 1));
    pressEnter(toInput());
    await waitFor(() => expect(toInput().getAttribute("aria-invalid")).toBe("true"));
    expect(alertText()).not.toBe("");
    expect(handleNow(endHandle())).toBe(1859);
  });

  it("AC-4 'From sol' above 'To sol' is refused", async () => {
    await openPerseverance();
    typeInto(fromInput(), "1900");
    pressEnter(fromInput());
    await waitFor(() => expect(fromInput().getAttribute("aria-invalid")).toBe("true"));
    expect(handleNow(startHandle())).toBe(1191);
    expect(handleNow(endHandle())).toBe(1859);
  });

  it("AC-4 a valid value is applied on Enter; the error goes away", async () => {
    await openPerseverance();
    typeInto(fromInput(), "abc");
    pressEnter(fromInput());
    await waitFor(() => expect(fromInput().getAttribute("aria-invalid")).toBe("true"));
    typeInto(fromInput(), "1500");
    pressEnter(fromInput());
    await waitFor(() => expect(handleNow(startHandle())).toBe(1500));
    expect(fromInput().getAttribute("aria-invalid")).not.toBe("true");
    expect(alertText()).toBe("");
    expect(label(daily())).toMatch(/1,?500/);
  });

  it("AC-4 a valid value is applied when the field loses focus", async () => {
    await openPerseverance();
    fromInput().focus();
    fireEvent.input(toInput(), { target: { value: "1700" } });
    fireEvent.change(toInput(), { target: { value: "1700" } });
    fireEvent.blur(toInput());
    fireEvent.focusOut(toInput());
    await waitFor(() => expect(handleNow(endHandle())).toBe(1700));
    expect(solsInTable()[0]).toBe(1700);
  });

  it("AC-4 the fields have visible labels and show the range the handles show", async () => {
    await openPerseverance();
    expect(fromInput().value).toBe("1191");
    expect(toInput().value).toBe("1859");
    fireEvent.keyDown(startHandle(), { key: "PageUp" });
    await waitFor(() => expect(fromInput().value).toBe("1201"));
  });
});

describe("AC-5 the chart follows the range", () => {
  it("AC-5 'Last 120 sols' of Curiosity -> the chart is named for sols 4591 to 4710 and its sol axis stays in them", async () => {
    await openCuriosity();
    fireEvent.click(preset(/Last 120 sols/));
    await expectRange(4591, 4710);
    const chart = daily()!;
    expect(label(chart)).toMatch(/4,?591/);
    expect(label(chart)).toMatch(/4,?710/);
    const sols = axisNumbers(chart).filter((n) => n >= 1000);
    expect(sols.length).toBeGreaterThanOrEqual(2);
    for (const n of sols) {
      expect(n).toBeGreaterThanOrEqual(4591);
      expect(n).toBeLessThanOrEqual(4710);
    }
  });

  it("AC-5 points outside the range are not drawn: no path or dot lies outside the chart's width", async () => {
    await openCuriosity();
    fireEvent.click(preset(/Last 120 sols/));
    await expectRange(4591, 4710);
    const chart = daily()!;
    const clip = chart.querySelector("clipPath rect")!;
    const xs: number[] = [];
    for (const p of chart.querySelectorAll("path[d]")) {
      for (const m of (p.getAttribute("d") ?? "").matchAll(
        /[ML]\s*(-?\d+(?:\.\d+)?)[ ,]\s*(-?\d+(?:\.\d+)?)/g,
      )) {
        xs.push(Number(m[1]));
      }
    }
    for (const c of chart.querySelectorAll("circle[cx]")) xs.push(num(c, "cx"));
    expect(xs.length).toBeGreaterThan(0);
    // nothing is drawn left or right of the plot area: points outside the range are not drawn at all
    for (const x of xs) {
      expect(x).toBeGreaterThanOrEqual(num(clip, "x") - 0.5);
      expect(x).toBeLessThanOrEqual(num(clip, "x") + num(clip, "width") + 0.5);
    }
  });

  it("AC-5 a new range gives a new axis", async () => {
    await openPerseverance();
    await setRange(1000, 1100);
    const sols = axisNumbers(daily()!).filter((n) => n >= 500);
    expect(sols.length).toBeGreaterThanOrEqual(2);
    for (const n of sols) {
      expect(n).toBeGreaterThanOrEqual(1000);
      expect(n).toBeLessThanOrEqual(1100);
    }
  });

  it("AC-5 a range with no data says so and does not break", async () => {
    // Curiosity has no data in 2331-2352
    await openCuriosity();
    await setRange(2335, 2345);
    expect(alertText()).toBe("");
    expect(daily()).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/NaN|undefined/);
  });
});

describe("AC-6 the 'not published yet' zone stays inside the chart", () => {
  const clipOf = (svg: Element) => svg.querySelector("clipPath rect")!;

  it("AC-6 range [1800, 2000] -> the zone lies inside the clip rectangle of the plot area", async () => {
    await openPerseverance();
    await setRange(1800, 2000);
    const chart = daily()!;
    const zone = zoneOf(chart);
    expect(zone, "grey zone").toBeTruthy();
    const clip = clipOf(chart);
    expect(clip, "a clipPath of the plot area").toBeTruthy();
    expect(zone!.closest("[clip-path]"), "the zone is drawn under the clip").toBeTruthy();
    const left = num(clip, "x");
    const right = left + num(clip, "width");
    expect(num(zone!, "x")).toBeGreaterThanOrEqual(left - 0.01);
    expect(num(zone!, "x") + num(zone!, "width")).toBeLessThanOrEqual(right + 0.01);
    // the zone starts half a sol after source_max_sol (1859.5) on the sol axis of the plot area
    const at = left + ((1859.5 - 1800) / (2000 - 1800)) * (right - left);
    expect(Math.abs(num(zone!, "x") - at)).toBeLessThan(1.5);
    expect(Math.abs(num(zone!, "x") + num(zone!, "width") - right)).toBeLessThan(0.5);
  });

  it("AC-6 a range that starts after the published data: the zone does not begin left of the plot area", async () => {
    await openPerseverance();
    await setRange(1900, MAX_SOL_PER);
    const chart = daily()!;
    const zone = zoneOf(chart);
    expect(zone, "grey zone").toBeTruthy();
    expect(num(zone!, "x")).toBeGreaterThanOrEqual(num(clipOf(chart), "x") - 0.01);
  });

  it("AC-6 the zone is named in words", async () => {
    await openPerseverance();
    await setRange(1700, MAX_SOL_PER);
    expect(daily()!.textContent + label(daily())).toMatch(/not published yet/i);
  });

  it("AC-6 a range wholly inside the published data has no zone", async () => {
    await openPerseverance();
    fireEvent.click(preset(/Last 120 sols/));
    await expectRange(1740, 1859);
    expect(zoneOf(daily()!)).toBeNull();
  });
});

describe("AC-7 the table follows the range: pages of 50, sorting", () => {
  it("AC-7 120 sols -> 3 pages of 50, 50 and 20 rows", async () => {
    await openPerseverance();
    await setRange(1600, 1719); // 120 sols, no gap
    const table = tableEl();
    expect(bodyRows(table)).toHaveLength(50);
    expect(within(pagerEl()).getByRole("button", { name: /page 3/i })).toBeTruthy();
    expect(within(pagerEl()).queryByRole("button", { name: /page 4/i })).toBeNull();
    fireEvent.click(nextPage());
    await waitFor(() => expect(currentPage()).toBe(2));
    expect(bodyRows()).toHaveLength(50);
    fireEvent.click(nextPage());
    await waitFor(() => expect(currentPage()).toBe(3));
    expect(bodyRows()).toHaveLength(20);
    expect((nextPage() as HTMLButtonElement).disabled).toBe(true);
  });

  it("AC-7 only sols of the range are listed, and only those with data", async () => {
    await openPerseverance();
    fireEvent.click(preset(/Last 120 sols/));
    await expectRange(1740, 1859);
    const sols: number[] = [];
    for (;;) {
      sols.push(...solsInTable());
      if ((nextPage() as HTMLButtonElement).disabled) break;
      const page = currentPage();
      fireEvent.click(nextPage());
      await waitFor(() => expect(currentPage()).toBe(page + 1));
    }
    expect(sols.length).toBe(120 - (PER_GAP[1] - PER_GAP[0] + 1)); // 1800-1805 have no rows
    for (const s of sols) {
      expect(s).toBeGreaterThanOrEqual(1740);
      expect(s).toBeLessThanOrEqual(1859);
      expect(s < PER_GAP[0] || s > PER_GAP[1]).toBe(true);
    }
  });

  it("AC-7 by default the newest sol is first: 'Sol' is sorted descending", async () => {
    await openPerseverance();
    expect(headerOf(/^Sol/).getAttribute("aria-sort")).toBe("descending");
    const sols = solsInTable();
    expect(sols).toEqual([...sols].sort((a, b) => b - a));
    expect(sols).toHaveLength(50);
  });

  it("AC-7 a click on a header changes aria-sort: ascending, then descending again", async () => {
    await openPerseverance();
    await setRange(1600, 1719);
    const solHead = () => headerOf(/^Sol/);
    fireEvent.click(within(solHead()).getByRole("button"));
    await waitFor(() => expect(solHead().getAttribute("aria-sort")).toBe("ascending"));
    expect(solsInTable()[0]).toBe(1600);
    fireEvent.click(within(solHead()).getByRole("button"));
    await waitFor(() => expect(solHead().getAttribute("aria-sort")).toBe("descending"));
    expect(solsInTable()[0]).toBe(1719);
  });

  it("AC-7 sorting by another column moves aria-sort to it and orders the rows", async () => {
    await openPerseverance();
    await setRange(1600, 1719);
    const date = () => headerOf(/date/i);
    fireEvent.click(within(date()).getByRole("button"));
    await waitFor(() => expect(date().getAttribute("aria-sort")).toMatch(/ascending|descending/));
    expect(headerOf(/^Sol/).getAttribute("aria-sort")).toBe("none");
    // the date moves with the sol in the fixture, so the order of the sols shows the order of the dates
    const sols = solsInTable();
    const asc = date().getAttribute("aria-sort") === "ascending";
    expect(sols).toEqual([...sols].sort((a, b) => (asc ? a - b : b - a)));
    fireEvent.click(within(date()).getByRole("button"));
    await waitFor(() =>
      expect(date().getAttribute("aria-sort")).toBe(asc ? "descending" : "ascending"),
    );
  });

  it("AC-7 exactly one column is the current order", async () => {
    await openPerseverance();
    const heads = within(tableEl()).getAllByRole("columnheader");
    const sorted = heads.filter((h) => h.getAttribute("aria-sort") === "descending");
    expect(sorted).toHaveLength(1); // exactly one column is the current order
  });

  it("AC-7 a new range returns the table to page 1", async () => {
    await openPerseverance();
    await setRange(1600, 1719);
    fireEvent.click(nextPage());
    await waitFor(() => expect(currentPage()).toBe(2));
    fireEvent.click(preset(/Whole published data/));
    await expectRange(PER_FIRST, PER_SOURCE_MAX);
    await waitFor(() => expect(currentPage()).toBe(1));
    expect(solsInTable()[0]).toBe(PER_SOURCE_MAX);
  });

  it("AC-7 the pager can be used with the keyboard: its buttons are real buttons with names", async () => {
    await openPerseverance();
    for (const b of within(pagerEl()).getAllByRole("button")) {
      expect(b.tagName).toBe("BUTTON");
      expect(b.textContent?.trim() || b.getAttribute("aria-label")).toBeTruthy();
    }
  });
});

describe("AC-8 'Jump to sol'", () => {
  async function zoomed() {
    await openPerseverance();
    fireEvent.click(preset(/Last 120 sols/));
    await expectRange(1740, 1859);
  }
  const jump = (sol: number) => {
    typeInto(jumpInput(), String(sol));
    pressEnter(jumpInput());
  };

  it("AC-8 a sol with data on another page: that page, the row is highlighted and has the focus", async () => {
    await zoomed();
    expect(rowFor(1745), "1745 is not on page 1").toBeUndefined();
    jump(1745);
    await waitFor(() => expect(rowFor(1745)).toBeTruthy());
    expect(currentPage()).toBe(3);
    const row = rowFor(1745)!;
    expect(isHighlighted(row), "highlighted row").toBe(true);
    await waitFor(() => expect(row.contains(document.activeElement)).toBe(true));
  });

  it("AC-8 a sol without data: the nearest sol with data, with an explanation", async () => {
    await zoomed();
    jump(1802); // 1800-1805 have no data; 1799 is the nearest
    await waitFor(() => expect(statusText()).toMatch(/no data for sol 1,?802/i));
    expect(statusText()).toMatch(/1,?799/);
    const row = rowFor(1799)!;
    expect(row).toBeTruthy();
    expect(isHighlighted(row)).toBe(true);
    await waitFor(() => expect(row.contains(document.activeElement)).toBe(true));
  });

  it("AC-8 a sol outside the range: an explanation and 'Widen the range'; after the click the sol is in the range", async () => {
    await zoomed();
    jump(1500);
    const widen = await screen.findByRole("button", { name: /widen the range/i });
    fireEvent.click(widen);
    await waitFor(() => expect(rangeInFields()[0]).toBeLessThanOrEqual(1500));
    expect(rangeInFields()[1]).toBeGreaterThanOrEqual(1859);
    await waitFor(() => expect(rowFor(1500)).toBeTruthy());
    expect(isHighlighted(rowFor(1500)!)).toBe(true);
    await waitFor(() => expect(rowFor(1500)!.contains(document.activeElement)).toBe(true));
  });

  it("AC-8 a sol after source_max_sol says 'not published yet'", async () => {
    await zoomed();
    jump(1900); // the rover is at sol 2031, the archive ends at 1859
    await waitFor(() => expect(statusText()).toMatch(/not published yet/i));
  });

  it("AC-8 something that is not a number does not break the page", async () => {
    await zoomed();
    typeInto(jumpInput(), "abc");
    pressEnter(jumpInput());
    expect(document.body.textContent).not.toMatch(/NaN|undefined/);
    expect(bodyRows().length).toBeGreaterThan(0);
  });
});

describe("AC-9 a click on the chart finds the sol in the table", () => {
  it("AC-9 click near the middle of the chart -> a row near the middle of the range is highlighted and focused", async () => {
    await openPerseverance();
    fireEvent.click(preset(/Last 120 sols/));
    await expectRange(1740, 1859);
    const chart = daily()!;
    const w = viewWidth(chart);
    const h = Number((chart.getAttribute("viewBox") ?? "0 0 0 290").split(/\s+/)[3]);
    // jsdom has no layout: give the svg the box the browser would give it
    chart.getBoundingClientRect = () =>
      ({
        x: 0,
        y: 0,
        left: 0,
        top: 0,
        right: w,
        bottom: h,
        width: w,
        height: h,
        toJSON() {},
      }) as DOMRect;
    fireEvent.click(chart, { clientX: w / 2, clientY: h / 2 });
    await waitFor(() => expect(bodyRows().some(isHighlighted)).toBe(true));
    const row = bodyRows().find(isHighlighted)!;
    expect(solOfRow(row)).toBeGreaterThanOrEqual(1780);
    expect(solOfRow(row)).toBeLessThanOrEqual(1820);
    await waitFor(() => expect(row.contains(document.activeElement)).toBe(true));
  });
});

describe("AC-12 the range in the address", () => {
  it("AC-12 ?from=1591&to=1710 is restored on load", async () => {
    await openPerseverance("/data/weather?from=1591&to=1710");
    await expectRange(1591, 1710);
    expect(label(daily())).toMatch(/1,?591/);
    expect(solsInTable()[0]).toBe(1710);
  });

  it("AC-12 a change of the range is written to the address without a new history entry", async () => {
    await openPerseverance();
    const entries = window.history.length;
    fireEvent.click(preset(/Last 120 sols/));
    await expectRange(1740, 1859);
    await waitFor(() => {
      const q = new URLSearchParams(window.location.search);
      expect([q.get("from"), q.get("to")]).toEqual(["1740", "1859"]);
    });
    expect(window.history.length).toBe(entries);
    expect(window.location.pathname).toBe("/data/weather");
  });

  it("AC-12 the rover is in the address: a Curiosity reload keeps Curiosity and its range", async () => {
    openWeatherRange({ cur: curLong }, "/data/weather?rover=curiosity&from=4591&to=4710");
    await readyTable();
    expect((screen.getByRole("radio", { name: /Curiosity/ }) as HTMLInputElement).checked).toBe(
      true,
    );
    await expectRange(4591, 4710);
    expect(solsInTable()[0]).toBe(4710);
    expect(new URLSearchParams(window.location.search).get("rover")).toBe("curiosity");
  });

  it("AC-12 without ?rover the page opens Perseverance", async () => {
    await openPerseverance("/data/weather?from=1591&to=1710");
    expect((screen.getByRole("radio", { name: /Perseverance/ }) as HTMLInputElement).checked).toBe(
      true,
    );
  });

  it("AC-12 choosing a rover writes it to the address and resets the range to that rover's default", async () => {
    openWeatherRange({ per: perLong, cur: curLong });
    await readyTable();
    fireEvent.click(preset(/Last 120 sols/));
    await expectRange(1740, 1859);
    fireEvent.click(screen.getByRole("radio", { name: /Curiosity/ }));
    await expectRange(CUR_SOURCE_MAX - 668, CUR_SOURCE_MAX);
    await waitFor(() => {
      const q = new URLSearchParams(window.location.search);
      expect(q.get("rover")).toBe("curiosity");
      // the range of the other rover is gone: no range in the address, or the new default
      expect([q.get("from") ?? "4042", q.get("to") ?? "4710"]).toEqual(["4042", "4710"]);
    });
    fireEvent.click(screen.getByRole("radio", { name: /Perseverance/ }));
    await expectRange(PER_SOURCE_MAX - 668, PER_SOURCE_MAX);
    await waitFor(() => {
      const q = new URLSearchParams(window.location.search);
      expect([q.get("from") ?? "1191", q.get("to") ?? "1859"]).toEqual(["1191", "1859"]);
      expect(q.get("rover") ?? "perseverance").toBe("perseverance");
    });
  });

  for (const [rover, q] of [
    ["perseverance", "?from=1&to=2"],
    ["perseverance", "?from=3000&to=4000"],
    ["curiosity", "?rover=curiosity&from=1&to=2"],
    ["curiosity", "?rover=curiosity&from=6000&to=7000"],
  ] as const) {
    it(`AC-12 ${rover} ${q}: a range that misses the rover's data and limits -> the default range`, async () => {
      openWeatherRange({ per: perLong, cur: curLong }, `/data/weather${q}`);
      await readyTable();
      const last = rover === "curiosity" ? CUR_SOURCE_MAX : PER_SOURCE_MAX;
      await expectRange(last - 668, last);
      expect(alertText()).toBe("");
    });
  }

  for (const bad of [
    "?from=abc&to=1710",
    "?from=1591",
    "?to=1710",
    "?from=1710&to=1591",
    "?from=&to=",
    "?from=1591.5&to=1710",
  ]) {
    it(`AC-12 ${bad} -> the default range, no error`, async () => {
      await openPerseverance(`/data/weather${bad}`);
      await expectRange(PER_SOURCE_MAX - 668, PER_SOURCE_MAX);
      expect(alertText()).toBe("");
      expect(fromInput().getAttribute("aria-invalid")).not.toBe("true");
    });
  }
});

describe("AC-13 every row links to the photos of its sol", () => {
  it("AC-13 Perseverance: the row of sol 1859 has 'Photos' -> /rovers/perseverance?sol=1859", async () => {
    await openPerseverance();
    const link = within(rowFor(PER_SOURCE_MAX)!).getByRole("link", { name: /photos/i });
    expect(link.getAttribute("href")).toBe(`/rovers/perseverance?sol=${PER_SOURCE_MAX}`);
  });

  it("AC-13 every row of the page has one", async () => {
    await openPerseverance();
    for (const row of bodyRows()) {
      const link = within(row).getByRole("link", { name: /photos/i });
      expect(link.getAttribute("href")).toBe(`/rovers/perseverance?sol=${solOfRow(row)}`);
    }
  });

  it("AC-13 Curiosity: /rovers/curiosity?sol=4710", async () => {
    await openCuriosity();
    const link = within(rowFor(CUR_SOURCE_MAX)!).getByRole("link", { name: /photos/i });
    expect(link.getAttribute("href")).toBe(`/rovers/curiosity?sol=${CUR_SOURCE_MAX}`);
  });
});

describe("AC-14 states: loading, 503, no data", () => {
  it("AC-14 loading: a skeleton with a status, no slider, no table", async () => {
    openWeatherRange({ over: { [WX("perseverance")]: () => new Promise(() => {}) } });
    expect(await screen.findByRole("status")).toBeTruthy();
    expect(screen.queryByRole("slider")).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByLabelText("From sol")).toBeNull();
  });

  it("AC-14 503: the error with a retry button that works", async () => {
    let n = 0;
    openWeatherRange({
      per: perLong,
      over: {
        [WX("perseverance")]: () =>
          ++n === 1
            ? apiError(503, "db_unavailable", "Database is unavailable")
            : weatherList("perseverance", perLong),
      },
    });
    const alert = await screen.findByRole("alert");
    expect(screen.queryByRole("slider")).toBeNull();
    fireEvent.click(within(alert).getByRole("button", { name: /try again|retry/i }));
    await readyTable();
    await expectRange(PER_SOURCE_MAX - 668, PER_SOURCE_MAX);
  });

  it("AC-14 items [] -> 'not loaded yet', no slider, no table", async () => {
    openWeatherRange({ per: [], over: { [WX("perseverance")]: weatherList("perseverance", []) } });
    expect(await screen.findByText(/not loaded yet|no data yet/i)).toBeTruthy();
    expect(screen.queryByRole("slider")).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByLabelText("From sol")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
