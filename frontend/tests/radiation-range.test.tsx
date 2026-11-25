import { fireEvent, screen, waitFor, within } from "@testing-library/preact";
import { describe, expect, it } from "vitest";
import { apiError } from "./helpers";
import { RAD_FIRST, RAD_SOURCE_MAX, radLong } from "./fixtures/range";
import { radiationResponse } from "./fixtures/radiation";
import {
  RAD,
  alertText,
  axisNumbers,
  bodyRows,
  chartByName,
  endHandle,
  expectRange,
  fromInput,
  handleNow,
  jumpInput,
  openRadiationRange,
  pressEnter,
  preset,
  readyTable,
  rowFor,
  solOfRow,
  solsInTable,
  startHandle,
  statusText,
  toInput,
  typeInto,
} from "./range-helpers";

// Stage 22 phase A, radiation (docs/proposals/sol-conditions.md AC-11; mockup
// design/mockups/radiation-range.html). Curiosity RAD: sols 15-4843 published (a gap at 4800-4805),
// the rover is at sol 5066.

const dose = () => chartByName(/dose/i)!;

async function openRad() {
  const m = openRadiationRange(radLong);
  await readyTable();
  await waitFor(() => expect(document.body.textContent).toMatch(/223 sols/)); // 5066 - 4843, the rover card is in
  return m;
}

/** The lowest number printed on the axes of a chart: 0 when the vertical scale starts from zero. */
const lowestTick = (svg: Element) => Math.min(...axisNumbers(svg));

/** The vertical-scale control: a select (the mockup) or a set of radios or toggle buttons. */
function scaleControl() {
  const select = screen.queryByLabelText(/vertical scale/i);
  if (select instanceof HTMLSelectElement) {
    return {
      current: () => select.selectedOptions[0]?.textContent ?? "",
      choose(name: RegExp) {
        const o = [...select.options].find((x) => name.test(x.textContent ?? ""));
        expect(o, `scale option ${name}`).toBeTruthy();
        fireEvent.change(select, { target: { value: o!.value } });
      },
    };
  }
  const group = screen.getByRole("group", { name: /vertical scale|scale/i });
  const items = () => [
    ...within(group).queryAllByRole("radio"),
    ...within(group).queryAllByRole("button"),
  ];
  return {
    current: () =>
      items().find(
        (i) => (i as HTMLInputElement).checked || i.getAttribute("aria-pressed") === "true",
      )?.textContent ?? "",
    choose(name: RegExp) {
      const el = items().find((i) =>
        name.test(i.textContent ?? i.getAttribute("aria-label") ?? ""),
      );
      expect(el, `scale toggle ${name}`).toBeTruthy();
      fireEvent.click(el!);
    },
  };
}

describe("AC-11 radiation: the same range slider and the same table", () => {
  it("AC-11 the default range is the last Mars year before the newest published sol", async () => {
    await openRad();
    await expectRange(RAD_SOURCE_MAX - 668, RAD_SOURCE_MAX);
    expect(handleNow(startHandle())).toBe(RAD_SOURCE_MAX - 668);
    expect(handleNow(endHandle())).toBe(RAD_SOURCE_MAX);
    expect(dose().getAttribute("aria-label")).toMatch(/4,?175/);
  });

  it("AC-11 presets, handles and fields work like on the weather page", async () => {
    await openRad();
    fireEvent.click(preset(/Last 120 sols/));
    await expectRange(RAD_SOURCE_MAX - 119, RAD_SOURCE_MAX);
    fireEvent.keyDown(startHandle(), { key: "PageUp" });
    await expectRange(RAD_SOURCE_MAX - 109, RAD_SOURCE_MAX);
    typeInto(toInput(), "4800");
    pressEnter(toInput());
    await waitFor(() => expect(handleNow(endHandle())).toBe(4800));
    fireEvent.click(preset(/Whole published data/));
    await expectRange(RAD_FIRST, RAD_SOURCE_MAX);
  });

  it("AC-11 the table lists the sols of the range, newest first, 50 to a page", async () => {
    await openRad();
    fireEvent.click(preset(/Last 120 sols/));
    await expectRange(RAD_SOURCE_MAX - 119, RAD_SOURCE_MAX);
    const table = await screen.findByRole("table");
    const heads = within(table)
      .getAllByRole("columnheader")
      .map((h) => h.textContent ?? "");
    expect(heads.some((h) => /^Sol/i.test(h))).toBe(true);
    expect(heads.some((h) => /dose E|^E\b/i.test(h))).toBe(true);
    expect(heads.some((h) => /dose B|^B\b/i.test(h))).toBe(true);
    expect(heads.some((h) => /count rate|events\/s/i.test(h))).toBe(true);
    expect(bodyRows()).toHaveLength(50);
    expect(solsInTable()[0]).toBe(RAD_SOURCE_MAX);
    expect(table.textContent).toMatch(/µGy\/day|μGy\/day/);
  });

  it("AC-11 'Jump to sol' finds a sol of another page", async () => {
    await openRad();
    fireEvent.click(preset(/Last 120 sols/));
    await expectRange(RAD_SOURCE_MAX - 119, RAD_SOURCE_MAX);
    typeInto(jumpInput(), "4730");
    pressEnter(jumpInput());
    await waitFor(() => expect(rowFor(4730)).toBeTruthy());
    await waitFor(() => expect(rowFor(4730)!.contains(document.activeElement)).toBe(true));
    // 4800-4805 have no data: the nearest sol with data is named
    typeInto(jumpInput(), "4802");
    pressEnter(jumpInput());
    await waitFor(() => expect(statusText()).toMatch(/4,?802/));
    expect(statusText()).toMatch(/4,?799/);
  });

  it("AC-11 each row links to the photos of its sol", async () => {
    await openRad();
    const row = bodyRows()[0]!;
    const link = within(row).getByRole("link", { name: /photos/i });
    expect(link.getAttribute("href")).toBe(`/rovers/curiosity?sol=${solOfRow(row)}`);
  });

  it("AC-11 the range is kept in the address and restored (AC-12 on this page)", async () => {
    openRadiationRange(radLong, {}, "/data/radiation?from=4400&to=4600");
    await readyTable();
    await expectRange(4400, 4600);
    fireEvent.click(preset(/Last 120 sols/));
    await expectRange(4724, RAD_SOURCE_MAX);
    await waitFor(() => {
      const q = new URLSearchParams(window.location.search);
      expect([q.get("from"), q.get("to")]).toEqual(["4724", "4843"]);
    });
  });

  it("AC-11 a bad address gives the default range and no error", async () => {
    openRadiationRange(radLong, {}, "/data/radiation?from=zzz&to=4600");
    await readyTable();
    await expectRange(RAD_SOURCE_MAX - 668, RAD_SOURCE_MAX);
    expect(alertText()).toBe("");
  });

  it("AC-11 the grey zone of the dose chart stays inside the plot area", async () => {
    await openRad();
    typeInto(toInput(), "5000");
    pressEnter(toInput());
    await waitFor(() => expect(handleNow(endHandle())).toBe(5000));
    const svg = dose();
    const zone = svg.querySelector('[data-testid="not-published-zone"], rect.gap');
    expect(zone, "grey zone").toBeTruthy();
    expect(zone!.closest("[clip-path]")).toBeTruthy();
    const clip = svg.querySelector("clipPath rect")!;
    const left = Number(clip.getAttribute("x"));
    const right = left + Number(clip.getAttribute("width"));
    expect(Number(zone!.getAttribute("x"))).toBeGreaterThanOrEqual(left - 0.01);
    expect(
      Number(zone!.getAttribute("x")) + Number(zone!.getAttribute("width")),
    ).toBeLessThanOrEqual(right + 0.01);
    // it starts half a sol after the newest published sol (4843.5), range 4175-5000
    const at = left + ((4843.5 - 4175) / (5000 - 4175)) * (right - left);
    expect(Math.abs(Number(zone!.getAttribute("x")) - at)).toBeLessThan(1.5);
  });
});

describe("AC-11 the vertical scale: Auto, From zero, Fit to the range", () => {
  it("AC-11 'Auto' is the default", async () => {
    await openRad();
    expect(scaleControl().current()).toMatch(/auto/i);
  });

  it("AC-11 Auto, a range of 400 sols or more -> the scale starts from zero", async () => {
    await openRad(); // the default range is 669 sols
    expect(lowestTick(dose())).toBe(0);
    await expectRange(RAD_SOURCE_MAX - 668, RAD_SOURCE_MAX);
    typeInto(fromInput(), String(RAD_SOURCE_MAX - 399)); // exactly 400 sols
    pressEnter(fromInput());
    await expectRange(RAD_SOURCE_MAX - 399, RAD_SOURCE_MAX);
    expect(lowestTick(dose())).toBe(0);
  });

  it("AC-11 Auto, a range under 400 sols -> the scale is fitted to the data", async () => {
    await openRad();
    typeInto(fromInput(), String(RAD_SOURCE_MAX - 398)); // 399 sols
    pressEnter(fromInput());
    await expectRange(RAD_SOURCE_MAX - 398, RAD_SOURCE_MAX);
    expect(lowestTick(dose())).toBeGreaterThan(0);
    fireEvent.click(preset(/Last 120 sols/));
    await expectRange(RAD_SOURCE_MAX - 119, RAD_SOURCE_MAX);
    expect(lowestTick(dose())).toBeGreaterThan(0);
  });

  it("AC-11 'Fit to the range' on a long range lifts the scale off zero; 'From zero' puts it back", async () => {
    await openRad();
    expect(lowestTick(dose())).toBe(0);
    scaleControl().choose(/fit to the range/i);
    await waitFor(() => expect(lowestTick(dose())).toBeGreaterThan(0));
    expect(scaleControl().current()).toMatch(/fit/i);
    scaleControl().choose(/from zero/i);
    await waitFor(() => expect(lowestTick(dose())).toBe(0));
    expect(scaleControl().current()).toMatch(/zero/i);
  });

  it("AC-11 'From zero' on a short range starts at zero", async () => {
    await openRad();
    fireEvent.click(preset(/Last 120 sols/));
    await expectRange(RAD_SOURCE_MAX - 119, RAD_SOURCE_MAX);
    expect(lowestTick(dose())).toBeGreaterThan(0);
    scaleControl().choose(/from zero/i);
    await waitFor(() => expect(lowestTick(dose())).toBe(0));
  });

  it("AC-11 a manual choice stays when the range changes", async () => {
    await openRad();
    scaleControl().choose(/fit to the range/i);
    await waitFor(() => expect(lowestTick(dose())).toBeGreaterThan(0));
    fireEvent.click(preset(/Whole published data/)); // 4829 sols: Auto would be from zero
    await expectRange(RAD_FIRST, RAD_SOURCE_MAX);
    expect(lowestTick(dose())).toBeGreaterThan(0);
  });
});

describe("AC-11 radiation: states (AC-14 on this page)", () => {
  it("AC-14 loading: no slider, no table", async () => {
    openRadiationRange(undefined, { [RAD]: () => new Promise(() => {}) });
    expect(await screen.findByRole("status")).toBeTruthy();
    expect(screen.queryByRole("slider")).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("AC-14 503: the error with a button that retries", async () => {
    let n = 0;
    openRadiationRange(undefined, {
      [RAD]: () =>
        ++n === 1
          ? apiError(503, "db_unavailable", "Database is unavailable")
          : radiationResponse(radLong),
    });
    const alert = await screen.findByRole("alert");
    fireEvent.click(within(alert).getByRole("button", { name: /try again|retry/i }));
    await readyTable();
    await expectRange(RAD_SOURCE_MAX - 668, RAD_SOURCE_MAX);
  });

  it("AC-14 items [] -> 'not loaded yet', no slider", async () => {
    openRadiationRange([]);
    expect(await screen.findByText(/not loaded yet|no data yet/i)).toBeTruthy();
    expect(screen.queryByRole("slider")).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
  });
});
