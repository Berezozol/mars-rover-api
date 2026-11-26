import { fireEvent, screen, waitFor, within } from "@testing-library/preact";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiError } from "./helpers";
import { radDetail } from "./fixtures/radhours";
import { radiationResponse } from "./fixtures/radiation";
import { CUR_SOL, PER_SOL, reportDetail } from "./fixtures/solreport";
import {
  RADH,
  WXD,
  charts,
  hourSlider,
  inspectHour,
  mainEl,
  mainText,
  oneChart,
  openReport,
  reportHeading,
  reportPath,
} from "./solreport-helpers";

// docs/proposals/sol-report-polish.md (editorial 1, 2026-11-26): component tests of AC-1 ... AC-8, AC-10, AC-11.
// AC-3, AC-4, AC-9 and AC-12 need real layout and are in e2e/sol-report-polish.spec.ts. Written from the
// spec: the shared names are the class `is-idle`, the button `Clear hour`, the text `No hour selected`,
// `data-testid="sol-summary"` and the h2 `Weather by hour`. Everything else is matched loosely.

const CUR = "curiosity";
const PER = "perseverance";

type Over = Parameters<typeof openReport>[1];

async function openCuriosity(over: Over = {}, sol = CUR_SOL) {
  const m = openReport(reportPath(CUR, sol), over);
  await reportHeading("Curiosity", sol);
  await waitFor(() => expect(charts(/air temperature/i)).toHaveLength(1));
  return m;
}
async function openPerseverance(over: Over = {}, sol = PER_SOL) {
  const m = openReport(reportPath(PER, sol), over);
  await reportHeading("Perseverance", sol);
  await waitFor(() => expect(charts(/air temperature/i)).toHaveLength(1));
  return m;
}

const hourOutput = () => mainEl().querySelector("output") as HTMLOutputElement;
const clearButton = () => screen.queryByRole("button", { name: /^clear hour$/i });
const norm = (s: string | null | undefined) => (s ?? "").replace(/−/g, "-").replace(/\s+/g, " ");
const cursors = () => document.querySelectorAll('[data-testid="hour-cursor"]');

/** The shared hour is shown on screen: the output has a clock time. */
const shownHour = () => /(\d\d):00/.exec(hourOutput().textContent ?? "")?.[1] ?? null;

beforeEach(() => {
  // jsdom has no pointer capture; the chart may ask for it on a touch (decision 5 of the spec)
  const proto = SVGElement.prototype as unknown as Record<string, unknown>;
  proto.setPointerCapture = vi.fn();
  proto.releasePointerCapture = vi.fn();
  proto.hasPointerCapture = vi.fn(() => true);
});

describe("polish AC-1 no hour selected is told truthfully", () => {
  it("polish AC-1 a fresh page says 'No hour selected', not 'none selected'", async () => {
    await openCuriosity();
    expect(hourOutput().textContent?.trim()).toBe("No hour selected");
    expect(mainText()).not.toMatch(/none selected/i);
  });

  it("polish AC-1 the thumb of the slider is muted: the slider has the class is-idle", async () => {
    await openCuriosity();
    expect(hourSlider().classList.contains("is-idle")).toBe(true);
  });

  it("polish AC-1 no chart shows the values of an hour, and there is no 'Clear hour' button", async () => {
    await openCuriosity();
    expect(cursors()).toHaveLength(0);
    expect(clearButton()).toBeNull();
    // the readout under every chart is the hint, not an hour
    const card = screen.getByTestId("hour-chart-air");
    expect(card.textContent).not.toMatch(/\d\d:00.*LMST/);
  });

  it("polish AC-1 the same on Perseverance", async () => {
    await openPerseverance();
    expect(hourOutput().textContent?.trim()).toBe("No hour selected");
    expect(hourSlider().classList.contains("is-idle")).toBe(true);
    expect(clearButton()).toBeNull();
  });
});

describe("polish AC-2 choosing an hour and clearing it", () => {
  it("polish AC-2 the slider shows the hour in the output, drops is-idle and offers 'Clear hour'", async () => {
    await openCuriosity();
    inspectHour(13);
    await waitFor(() => expect(hourOutput().textContent).toMatch(/13:00/));
    expect(hourSlider().classList.contains("is-idle")).toBe(false);
    expect(clearButton()).toBeTruthy();
    expect(cursors().length).toBeGreaterThan(0);
  });

  it("polish AC-2 'Clear hour' returns to the idle state and moves the focus to the slider", async () => {
    await openCuriosity();
    inspectHour(9);
    const clear = await screen.findByRole("button", { name: /^clear hour$/i });
    fireEvent.click(clear);
    await waitFor(() => expect(hourOutput().textContent?.trim()).toBe("No hour selected"));
    expect(hourSlider().classList.contains("is-idle")).toBe(true);
    expect(clearButton()).toBeNull();
    expect(cursors()).toHaveLength(0);
    await waitFor(() => expect(document.activeElement).toBe(hourSlider()));
  });

  it("polish AC-2 a touch on a chart also selects the hour and shows the button", async () => {
    await openCuriosity();
    const svg = oneChart(/air temperature/i);
    fireEvent.pointerDown(svg, {
      clientX: 200,
      clientY: 40,
      pointerType: "touch",
      pointerId: 5,
      isPrimary: true,
    });
    await waitFor(() => expect(shownHour()).not.toBeNull());
    expect(clearButton()).toBeTruthy();
    fireEvent.click(clearButton()!);
    await waitFor(() => expect(hourOutput().textContent?.trim()).toBe("No hour selected"));
    expect(cursors()).toHaveLength(0);
  });

  it("polish AC-2 the mouse leaving a chart still clears the hover (the old behaviour stays for the mouse)", async () => {
    await openCuriosity();
    const svg = oneChart(/air temperature/i);
    fireEvent.pointerMove(svg, { clientX: 200, clientY: 40, pointerType: "mouse" });
    await waitFor(() => expect(shownHour()).not.toBeNull());
    fireEvent.pointerLeave(svg, { pointerType: "mouse" });
    await waitFor(() => expect(hourOutput().textContent?.trim()).toBe("No hour selected"));
  });
});

describe("polish AC-5 the sol summary under the heading", () => {
  const summary = () => screen.getByTestId("sol-summary");
  // `lo ... hi unit` in any of the usual separators; numbers are matched as whole numbers (digits are optional)
  const range = (lo: string, hi: string, unit: string) =>
    new RegExp(
      `(?<![\\d.])${lo}(?:\\.0)?\\s*(?:…|\\.{3}|–|—|\\bto\\b|\\s-\\s)\\s*${hi}(?:\\.0)?\\s*${unit}`,
    );

  it("polish AC-5 Curiosity: air min…max, pressure mean, UV max and the daily dose E", async () => {
    await openCuriosity();
    const text = norm(summary().textContent);
    expect(text).toMatch(range("-75", "-5", "°C"));
    expect(text).toMatch(/(?<![\d.])745\.5\s*Pa/);
    expect(text).toMatch(/(?<![\d.])19\.20?\s*W\/m(²|2)/);
    await waitFor(() => expect(norm(summary().textContent)).toMatch(/218\.7\s*µGy\/day/));
    expect(text).not.toMatch(/null|NaN|undefined/);
  });

  it("polish AC-5 the summary is under the h1 and before the charts", async () => {
    await openCuriosity();
    const h1 = screen.getByRole("heading", { level: 1 });
    expect(h1.compareDocumentPosition(summary()) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const firstChart = charts(/air temperature/i)[0]!;
    expect(
      summary().compareDocumentPosition(firstChart) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("polish AC-5 the numbers are the daily fields of the answer, not constants", async () => {
    const detail = {
      ...reportDetail(CUR, CUR_SOL, { ls: 356.7 }),
      air_temp_c: { min: -61.3, max: -2.9, mean: -40.1 },
      pressure_pa: { min: 700, max: 790, mean: 733.2 },
      uv_wm2: { max: 12.7, mean: 3.3 },
    };
    await openCuriosity({ [WXD(CUR, CUR_SOL)]: detail });
    const text = norm(summary().textContent);
    expect(text).toMatch(range("-61\\.3", "-2\\.9", "°C"));
    expect(text).toMatch(/733\.2\s*Pa/);
    expect(text).toMatch(/12\.70?\s*W\/m(²|2)/);
    expect(text).not.toMatch(/745\.5|19\.2/);
  });

  it("polish AC-5 a value that is null is a dash, the other values stay", async () => {
    const detail = { ...reportDetail(CUR, CUR_SOL, { ls: 356.7 }), uv_wm2: null };
    await openCuriosity({ [WXD(CUR, CUR_SOL)]: detail });
    const text = norm(summary().textContent);
    expect(text).toMatch(/UV/i);
    expect(text).toContain("—");
    expect(text).toMatch(/745\.5\s*Pa/);
    expect(text).not.toMatch(/null|NaN|undefined/);
  });

  it("polish AC-5 Curiosity without radiation for the sol: three figures, no dose", async () => {
    await openCuriosity({
      [RADH(CUR_SOL)]: apiError(404, "sol_not_found", "No radiation data for sol 344", null),
      "/api/v2/rovers/curiosity/radiation": radiationResponse([]),
    });
    // give the radiation requests the time to end
    await waitFor(() =>
      expect(screen.queryAllByRole("status", { name: /loading/i })).toHaveLength(0),
    );
    const text = norm(summary().textContent);
    expect(text).not.toMatch(/µGy/);
    expect(text).toMatch(/745\.5\s*Pa/);
  });

  it("polish AC-5 Perseverance: air, pressure and ground temperature; no UV, no dose", async () => {
    const detail = {
      ...reportDetail(PER, PER_SOL),
      ground_temp_c: { min: -70.5, max: -12.3, mean: -40.2 },
    };
    await openPerseverance({ [WXD(PER, PER_SOL)]: detail });
    const text = norm(summary().textContent);
    expect(text).toMatch(range("-82\\.4", "-18\\.9", "°C"));
    expect(text).toMatch(/(?<![\d.])726(?:\.0)?\s*Pa/);
    expect(text).toMatch(range("-70\\.5", "-12\\.3", "°C"));
    expect(text).toMatch(/ground|soil/i);
    expect(text).not.toMatch(/UV|µGy/);
  });
});

describe("polish AC-6 the facts block: the secondary row", () => {
  const term = (re: RegExp) =>
    [...mainEl().querySelectorAll("dt")].find((el) => re.test(el.textContent ?? ""));

  /** The smallest element that holds all the given nodes. */
  const commonAncestor = (nodes: Element[]) => {
    let a: Element | null = nodes[0]!;
    while (a && !nodes.every((n) => a!.contains(n))) a = a.parentElement;
    return a;
  };

  it("polish AC-6 Coverage, Hours with readings and Flags are there and sit together, apart from the main facts", async () => {
    await openCuriosity();
    const cov = term(/^\s*coverage/i);
    const hrs = term(/hours with readings/i);
    const flags = term(/^\s*flags/i);
    expect(cov, "Coverage").toBeTruthy();
    expect(hrs, "Hours with readings").toBeTruthy();
    expect(flags, "Flags").toBeTruthy();
    const group = commonAncestor([cov!, hrs!, flags!])!;
    const earth = term(/earth date/i)!;
    expect(earth, "Earth date stays").toBeTruthy();
    expect(group.contains(earth), "the secondary row holds the three, not the main facts").toBe(
      false,
    );
    expect(group.contains(screen.getByTestId("sol-summary"))).toBe(false);
  });

  it("polish AC-6 the other facts stay: earth date, Ls, season, sunrise and sunset", async () => {
    await openCuriosity();
    expect(term(/earth date/i)).toBeTruthy();
    expect(term(/solar longitude|\bLs\b/i)).toBeTruthy();
    expect(term(/season/i)).toBeTruthy();
    expect(term(/sunrise/i)).toBeTruthy();
    expect(mainText()).toContain("2013-07-15");
    expect(mainText()).toMatch(/50\s*%/);
    expect(mainText()).toMatch(/24\s*of\s*24/);
  });
});

describe("polish AC-7 the notes of quality and approximation are written out", () => {
  /** Text of a card without its badges: the explanation must be more than the word of the badge. */
  const textWithoutBadges = (el: Element) => {
    const copy = el.cloneNode(true) as Element;
    copy.querySelectorAll('[class*="badge"]').forEach((b) => b.remove());
    return norm(copy.textContent);
  };

  it("polish AC-7 a 'preliminary' chart (humidity) has a visible line that says why", async () => {
    await openCuriosity();
    const card = screen.getByTestId("hour-chart-humidity");
    expect(card.textContent).toMatch(/preliminary/i);
    expect(textWithoutBadges(card)).toMatch(
      /calibrat|revis|not final|may (still )?change|subject to/i,
    );
  });

  it("polish AC-7 an 'estimate' chart (ground temperature of Perseverance) has a visible line that says what it is", async () => {
    await openPerseverance();
    const card = screen.getByTestId("hour-chart-ground");
    expect(card.textContent).toMatch(/estimate/i);
    expect(textWithoutBadges(card)).toMatch(
      /estimat|infrared|brightness|derived|inferred|calculated|model/i,
    );
  });

  /** The text shows at once, or after a click on a tip button (a button works by touch and keyboard). */
  function explained(re: RegExp): boolean {
    if (re.test(mainText())) return true;
    const tips = screen
      .queryAllByRole("button")
      .filter(
        (b) =>
          /approx|optional|explain|about|info|why|what|help|tip|sunrise|shade/i.test(
            b.getAttribute("aria-label") ?? b.textContent ?? "",
          ) && !/clear hour/i.test(b.textContent ?? ""),
      );
    for (const tip of tips) {
      fireEvent.click(tip);
      if (re.test(mainText())) return true;
    }
    return false;
  }

  it("polish AC-7 'optional' at sunrise and sunset is explained in text or behind a tip button", async () => {
    await openCuriosity();
    expect(
      explained(/not in the api|computed|calculated|from Ls|latitude|few minutes/i),
      "an explanation of the sunrise and sunset",
    ).toBe(true);
  });

  it("polish AC-7 'approx.' of 'Shade night' is explained in text or behind a tip button", async () => {
    await openCuriosity();
    expect(screen.getByLabelText(/shade night/i)).toBeTruthy();
    expect(
      explained(
        /shad\w+.*(computed|approximate|latitude|estimated|sunrise)|night.*(computed|approximate|latitude)/i,
      ),
      "an explanation of the night shading",
    ).toBe(true);
  });
});

describe("polish AC-8 the table of hours", () => {
  /** The table of the details block 'Hourly values as a table'. */
  async function table(): Promise<HTMLTableElement> {
    const summary = await screen.findByText(/hourly values as a table/i);
    const t = summary.closest("details")!.querySelector("table");
    expect(t, "a table in the details block").toBeTruthy();
    return t as HTMLTableElement;
  }
  const headers = (t: HTMLTableElement) =>
    [...t.querySelectorAll("thead th")].map((th) => norm(th.textContent));
  const rowOf = (t: HTMLTableElement, hour: number) =>
    [...t.querySelectorAll("tbody tr")].find((tr) =>
      new RegExp(`^${String(hour).padStart(2, "0")}:00`).test(
        tr.querySelector("th")?.textContent ?? "",
      ),
    )!;

  it("polish AC-8 Curiosity: every parameter has the mean, min and max columns", async () => {
    await openCuriosity();
    const heads = headers(await table());
    for (const name of [
      /air temperature/i,
      /pressure/i,
      /humidity/i,
      /ground temperature/i,
      /ultraviolet|\bUV\b/i,
    ]) {
      const mine = heads.filter((h) => name.test(h));
      expect(
        mine.some((h) => /mean|avg|average/i.test(h)),
        `${name} mean in ${mine}`,
      ).toBe(true);
      expect(
        mine.some((h) => /\bmin\b|minimum|lowest/i.test(h)),
        `${name} min in ${mine}`,
      ).toBe(true);
      expect(
        mine.some((h) => /\bmax\b|maximum|highest/i.test(h)),
        `${name} max in ${mine}`,
      ).toBe(true);
    }
  });

  it("polish AC-8 the cells of hour 13 hold the mean, min and max of the fixtures", async () => {
    await openCuriosity();
    const row = rowOf(await table(), 13);
    const cells = [...row.querySelectorAll("td")].map((td) => norm(td.textContent));
    // air temperature -38 +- 2, pressure 853 +- 1.5
    for (const v of ["-38.0", "-40.0", "-36.0", "853.0", "851.5", "854.5"]) {
      expect(cells, `cell ${v}`).toContain(v);
    }
  });

  it("polish AC-8 Perseverance: the same three columns for air temperature and pressure", async () => {
    await openPerseverance();
    const heads = headers(await table());
    for (const name of [/air temperature/i, /pressure/i, /humidity/i]) {
      const mine = heads.filter((h) => name.test(h));
      expect(
        mine.some((h) => /mean|avg|average/i.test(h)),
        `${name} mean`,
      ).toBe(true);
      expect(
        mine.some((h) => /\bmin\b|minimum|lowest/i.test(h)),
        `${name} min`,
      ).toBe(true);
      expect(
        mine.some((h) => /\bmax\b|maximum|highest/i.test(h)),
        `${name} max`,
      ).toBe(true);
    }
  });

  it("polish AC-8 Curiosity with hourly radiation: dose E and B in µGy/h and the coverage", async () => {
    await openCuriosity();
    await waitFor(() => expect(charts(/dose rate/i)).toHaveLength(1));
    const heads = headers(await table());
    expect(
      heads.some((h) => /dose.*\bE\b|\bE\b.*dose/i.test(h) && /µGy\s*\/\s*h/.test(h)),
      `${heads}`,
    ).toBe(true);
    expect(
      heads.some((h) => /dose.*\bB\b|\bB\b.*dose/i.test(h) && /µGy\s*\/\s*h/.test(h)),
      `${heads}`,
    ).toBe(true);
    expect(
      heads.some((h) => /coverage/i.test(h)),
      `coverage in ${heads}`,
    ).toBe(true);
  });

  it("polish AC-8 the coverage is in percent everywhere, never 0.92", async () => {
    await openCuriosity();
    await waitFor(() => expect(charts(/dose rate/i)).toHaveLength(1));
    const t = await table();
    const idx = headers(t)
      .map((h, i) => (/coverage/i.test(h) ? i : -1))
      .filter((i) => i >= 0);
    expect(idx.length).toBeGreaterThan(0);
    for (const tr of t.querySelectorAll("tbody tr")) {
      const cells = [...tr.children].map((c) => norm(c.textContent));
      for (const i of idx) expect(cells[i], `row ${cells[0]}`).toMatch(/^(\d{1,3}\s?%|[–-])$/);
    }
    // the coverage of an hour in the fixtures is 0.5
    expect(norm(rowOf(t, 13).textContent)).toMatch(/50\s?%/);
  });

  it("polish AC-8 hours of the radiation without observations are dashes, not zeros or NaN", async () => {
    await openCuriosity({ [RADH(CUR_SOL)]: radDetail(CUR_SOL, { missing: [5] }) });
    await waitFor(() => expect(charts(/dose rate/i)).toHaveLength(1));
    const t = await table();
    expect(norm(rowOf(t, 5).textContent)).not.toMatch(/NaN|null|undefined/);
    expect(norm(t.textContent)).not.toMatch(/NaN|undefined/);
  });
});

describe("polish AC-10 the headings of the report", () => {
  const levels = () =>
    screen.getAllByRole("heading").map((h) => ({
      level: Number(h.tagName.slice(1)),
      text: norm(h.textContent),
      el: h,
    }));

  it.each([
    ["Curiosity", CUR, CUR_SOL],
    ["Perseverance", PER, PER_SOL],
  ])(
    "polish AC-10 %s: h1, then h2 'Weather by hour', then the h3 of the charts",
    async (_n, rover, sol) => {
      if (rover === CUR) await openCuriosity();
      else await openPerseverance();
      const hs = levels();
      expect(hs.filter((h) => h.level === 1)).toHaveLength(1);
      const weather = hs.find((h) => h.level === 2 && /^weather by hour$/i.test(h.text));
      expect(weather, "h2 Weather by hour").toBeTruthy();
      const air = hs.find((h) => h.level === 3 && /air temperature/i.test(h.text))!;
      expect(air, "h3 of the air temperature chart").toBeTruthy();
      expect(
        weather!.el.compareDocumentPosition(air.el) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
      expect(sol).toBeGreaterThan(0);
    },
  );

  it.each([
    ["Curiosity", CUR],
    ["Perseverance", PER],
  ])("polish AC-10 %s: no heading skips a level (the rule heading-order)", async (_n, rover) => {
    if (rover === CUR) {
      await openCuriosity();
      await waitFor(() => expect(charts(/dose rate/i)).toHaveLength(1));
    } else await openPerseverance();
    let prev = 0;
    for (const h of levels()) {
      expect(h.level - prev, `"${h.text}" (h${h.level}) after h${prev}`).toBeLessThanOrEqual(1);
      prev = h.level;
    }
  });
});

describe("polish AC-11 the finger moves the cursor along a chart", () => {
  const touch = { pointerType: "touch", pointerId: 7, isPrimary: true } as const;

  it("polish AC-11 a touch captures the pointer; a horizontal drag moves the hour with the finger", async () => {
    await openCuriosity();
    const svg = oneChart(/air temperature/i);
    fireEvent.pointerDown(svg, { ...touch, clientX: 100, clientY: 60, buttons: 1 });
    expect(
      (svg as unknown as { setPointerCapture: ReturnType<typeof vi.fn> }).setPointerCapture,
    ).toHaveBeenCalledWith(7);
    await waitFor(() => expect(shownHour()).not.toBeNull());
    const first = Number(shownHour());

    fireEvent.pointerMove(svg, { ...touch, clientX: 160, clientY: 62, buttons: 1 });
    fireEvent.pointerMove(svg, { ...touch, clientX: 250, clientY: 64, buttons: 1 });
    await waitFor(() => expect(Number(shownHour())).toBeGreaterThan(first));
    const mid = Number(shownHour());

    // and back to the left
    fireEvent.pointerMove(svg, { ...touch, clientX: 120, clientY: 64, buttons: 1 });
    await waitFor(() => expect(Number(shownHour())).toBeLessThan(mid));
    // every chart follows: the cursor is in more than one of them
    expect(cursors().length).toBeGreaterThan(1);
  });

  it("polish AC-11 the readout of every chart names the same hour as the output", async () => {
    await openCuriosity();
    const svg = oneChart(/air temperature/i);
    fireEvent.pointerDown(svg, { ...touch, clientX: 100, clientY: 60, buttons: 1 });
    fireEvent.pointerMove(svg, { ...touch, clientX: 230, clientY: 60, buttons: 1 });
    await waitFor(() => expect(shownHour()).not.toBeNull());
    const hh = shownHour()!;
    for (const id of ["air", "pressure"]) {
      expect(screen.getByTestId(`hour-chart-${id}`).textContent).toContain(`${hh}:00`);
    }
  });

  it("polish AC-11 pointercancel ends the drag: a later move without a pressed finger changes nothing", async () => {
    await openCuriosity();
    const svg = oneChart(/air temperature/i);
    fireEvent.pointerDown(svg, { ...touch, clientX: 100, clientY: 60, buttons: 1 });
    fireEvent.pointerMove(svg, { ...touch, clientX: 200, clientY: 60, buttons: 1 });
    await waitFor(() => expect(shownHour()).not.toBeNull());
    const before = shownHour();
    fireEvent.pointerCancel(svg, { ...touch });
    fireEvent.pointerMove(svg, { ...touch, clientX: 280, clientY: 60, buttons: 0 });
    // let preact flush: a changed hour would show up in the next tick
    await waitFor(() => expect(shownHour()).toBe(before));
    expect(shownHour()).toBe(before);
  });

  it("polish AC-11 pointerup and a lost capture end the drag the same way", async () => {
    await openCuriosity();
    const svg = oneChart(/air temperature/i);
    for (const end of ["pointerUp", "lostPointerCapture"] as const) {
      fireEvent.pointerDown(svg, { ...touch, clientX: 100, clientY: 60, buttons: 1 });
      fireEvent.pointerMove(svg, { ...touch, clientX: 180, clientY: 60, buttons: 1 });
      await waitFor(() => expect(shownHour()).not.toBeNull());
      const before = shownHour();
      fireEvent[end](svg, { ...touch });
      fireEvent.pointerMove(svg, { ...touch, clientX: 290, clientY: 60, buttons: 0 });
      await waitFor(() => expect(shownHour()).toBe(before));
      expect(shownHour(), end).toBe(before);
    }
  });

  it("polish AC-11 a touch move with no touch down before it (a stray event) does not select an hour", async () => {
    await openCuriosity();
    const svg = oneChart(/air temperature/i);
    fireEvent.pointerMove(svg, { ...touch, clientX: 200, clientY: 60, buttons: 0 });
    expect(hourOutput().textContent?.trim()).toBe("No hour selected");
    expect(cursors()).toHaveLength(0);
  });

  it("polish AC-11 the slider and 'Clear hour' work after a drag", async () => {
    await openCuriosity();
    const svg = oneChart(/air temperature/i);
    fireEvent.pointerDown(svg, { ...touch, clientX: 100, clientY: 60, buttons: 1 });
    fireEvent.pointerCancel(svg, { ...touch });
    inspectHour(20);
    await waitFor(() => expect(hourOutput().textContent).toMatch(/20:00/));
    fireEvent.click(await screen.findByRole("button", { name: /^clear hour$/i }));
    await waitFor(() => expect(hourOutput().textContent?.trim()).toBe("No hour selected"));
    expect(within(mainEl()).queryAllByTestId("hour-cursor")).toHaveLength(0);
  });
});
