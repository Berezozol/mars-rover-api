import { cleanup, fireEvent, screen, waitFor } from "@testing-library/preact";
import { describe, expect, it } from "vitest";
import { clearApiCache } from "../src/api/client";
import {
  SPIKE_DOSE,
  SPIKE_RATE,
  SPIKE_SOL,
  radLong,
  radManySpikes,
  radRateSpiked,
  radSpiked,
} from "./fixtures/range";
import { chartByName, openRadiationRange, readyTable } from "./range-helpers";

// Small fixes 2026-11-26 (docs/proposals/small-fixes-2026-11-26.md), AC-4 ... AC-7: the radiation charts leave a
// solar-event spike off the scale and say so, in words, next to the chart and in its accessible name.
// The range 4000-4300 has 301 sols (the "Auto" scale is fitted below 400 sols) and about 300 points per series.

const FIT_RANGE = "/data/radiation?from=4000&to=4300";
const dose = () => chartByName(/dose/i)!;
const rateChart = () => chartByName(/count rate/i)!;

/** The numbers of the vertical axis: the numeric labels above the row of sol numbers at the bottom. */
function yAxisNumbers(svg: Element): number[] {
  const labels = [...svg.querySelectorAll("text")]
    .map((t) => ({
      y: Number(t.getAttribute("y")),
      n: (t.textContent ?? "").replace(/,/g, "").trim(),
    }))
    .filter((t) => /^-?\d+(\.\d+)?$/.test(t.n));
  const bottom = Math.max(...labels.map((t) => t.y));
  return labels.filter((t) => t.y < bottom).map((t) => Number(t.n));
}
const highest = (svg: Element) => Math.max(...yAxisNumbers(svg));

/** Visible text (not the accessible name) that says a sol is off the scale. */
const offScaleTexts = () =>
  screen.queryAllByText((_, el) => {
    if (!el || /^(script|style)$/i.test(el.tagName)) return false;
    // own text only: a parent must not match because of its children
    const own = [...el.childNodes]
      .filter((n) => n.nodeType === Node.TEXT_NODE)
      .map((n) => n.textContent ?? "")
      .join("");
    return /off scale/i.test(own);
  });

async function open(items: unknown[], path = FIT_RANGE) {
  openRadiationRange(items, {}, path);
  await readyTable();
  await waitFor(() => expect(dose()).toBeTruthy());
}

function chooseScale(name: RegExp) {
  const select = screen.getByLabelText(/vertical scale/i) as HTMLSelectElement;
  const option = [...select.options].find((o) => name.test(o.textContent ?? ""));
  expect(option, `scale option ${name}`).toBeTruthy();
  fireEvent.change(select, { target: { value: option!.value } });
}

describe("AC-4 a spike in the fitted scale is shown at the top and named", () => {
  it("AC-4 Auto on a range under 400 sols: the top follows the quiet values, not the spike", async () => {
    await open(radSpiked);
    expect(highest(dose())).toBeLessThan(1000);
    expect(highest(dose())).toBeGreaterThan(200);
  });

  it("AC-4 the visible label names the sol, the value and the unit", async () => {
    await open(radSpiked);
    const texts = offScaleTexts().map((e) => e.textContent ?? "");
    expect(texts.length, "one visible off-scale label for the dose chart").toBe(1);
    expect(texts[0]).toMatch(
      new RegExp(
        `Sol ${SPIKE_SOL}: ${String(SPIKE_DOSE).replace(/(\d)(\d{3})/, "$1,?$2")}(\\.\\d)? µGy/day, off scale`,
      ),
    );
  });

  it("AC-4 the accessible name of the chart has the same sentence", async () => {
    await open(radSpiked);
    expect(dose().getAttribute("aria-label")).toMatch(
      /Sol 4190: 2,?342(\.\d)? µGy\/day, off scale/,
    );
    // the other chart has nothing to say
    expect(rateChart().getAttribute("aria-label") ?? "").not.toMatch(/off scale/i);
  });

  it("AC-4 the default view (669 sols, Auto starts from zero) clips the spike and shows the note", async () => {
    await open(radSpiked, "/data/radiation");
    expect(highest(dose())).toBeLessThan(1000);
    expect(Math.min(...yAxisNumbers(dose()))).toBe(0);
    expect(dose().getAttribute("aria-label")).toMatch(
      /Sol 4190: 2,?342(\.\d)? µGy\/day, off scale/,
    );
    const notes = screen.getAllByTestId("off-scale-note");
    expect(notes).toHaveLength(1);
    expect(notes[0]!.textContent).toMatch(/Sol 4190: 2,?342(\.\d)? µGy\/day, off scale/);
  });

  it("AC-4 'Fit to the range' on a long range also leaves the spike off the scale", async () => {
    await open(radSpiked, "/data/radiation");
    chooseScale(/fit to the range/i);
    await waitFor(() => expect(Math.min(...yAxisNumbers(dose()))).toBeGreaterThan(0));
    expect(highest(dose())).toBeLessThan(1000);
    expect(dose().getAttribute("aria-label")).toMatch(/off scale/i);
  });

  it("AC-4 several spikes: one sentence with the count and the highest sol", async () => {
    await open(radManySpikes, "/data/radiation?from=4100&to=4400");
    const texts = offScaleTexts().map((e) => e.textContent ?? "");
    expect(texts).toHaveLength(1);
    expect(texts[0]).toMatch(/3 sols off scale, highest sol 4300: 2,?100(\.\d)? µGy\/day/);
    expect(dose().getAttribute("aria-label")).toMatch(
      /3 sols off scale, highest sol 4300: 2,?100(\.\d)? µGy\/day/,
    );
    expect(highest(dose())).toBeLessThan(1000);
  });
});

describe("AC-5 the scale from zero shows the whole series", () => {
  it("AC-5 'From zero' on the spiked range: the top is not below the maximum and there is no label", async () => {
    await open(radSpiked);
    chooseScale(/from zero/i);
    await waitFor(() => expect(highest(dose())).toBeGreaterThanOrEqual(SPIKE_DOSE));
    expect(offScaleTexts()).toHaveLength(0);
    expect(dose().getAttribute("aria-label") ?? "").not.toMatch(/off scale/i);
  });

  it("AC-5 'From zero' on the default long range shows the spike, no note", async () => {
    await open(radSpiked, "/data/radiation");
    chooseScale(/from zero/i);
    await waitFor(() => expect(highest(dose())).toBeGreaterThanOrEqual(SPIKE_DOSE));
    expect(screen.queryAllByTestId("off-scale-note")).toHaveLength(0);
    expect(offScaleTexts()).toHaveLength(0);
  });
});

describe("AC-6 a range without outliers is drawn as before", () => {
  it("AC-6 sols 1000-1100: no label, the same axis as without the spike elsewhere in the data", async () => {
    await open(radSpiked, "/data/radiation?from=1000&to=1300");
    const withSpike = yAxisNumbers(dose());
    expect(offScaleTexts()).toHaveLength(0);
    expect(dose().getAttribute("aria-label") ?? "").not.toMatch(/off scale/i);
    cleanup();
    clearApiCache();
    await open(radLong, "/data/radiation?from=1000&to=1300");
    expect(yAxisNumbers(dose())).toEqual(withSpike);
    expect(offScaleTexts()).toHaveLength(0);
  });

  it("AC-6 a range with fewer than 20 points has no clipping, even with the spike inside", async () => {
    await open(radSpiked, "/data/radiation?from=4186&to=4194"); // 9 sols: E and B make 18 points, under 20
    expect(highest(dose())).toBeGreaterThanOrEqual(SPIKE_DOSE);
    expect(offScaleTexts()).toHaveLength(0);
  });
});

describe("AC-7 the count rate chart follows the same rule, in events/s", () => {
  it("AC-7 a count-rate spike: the top follows the quiet values, the label is in events/s", async () => {
    await open(radRateSpiked);
    expect(highest(rateChart())).toBeLessThan(20);
    const texts = offScaleTexts().map((e) => e.textContent ?? "");
    expect(texts).toHaveLength(1);
    expect(texts[0]).toMatch(
      new RegExp(`Sol ${SPIKE_SOL}: ${SPIKE_RATE}(\\.\\d+)? events/s, off scale`),
    );
    expect(rateChart().getAttribute("aria-label")).toMatch(/events\/s, off scale/);
    // the dose chart is quiet
    expect(dose().getAttribute("aria-label") ?? "").not.toMatch(/off scale/i);
  });

  it("AC-7 'From zero': the count-rate chart is not clipped", async () => {
    await open(radRateSpiked);
    chooseScale(/from zero/i);
    await waitFor(() => expect(highest(rateChart())).toBeGreaterThanOrEqual(SPIKE_RATE));
    expect(offScaleTexts()).toHaveLength(0);
  });
});
