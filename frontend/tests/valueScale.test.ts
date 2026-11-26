import { describe, expect, it } from "vitest";
import { valueScale } from "../src/lib/solRange";

// Small fixes 2026-11-26 (docs/proposals/small-fixes-2026-11-26.md), section 3: the outlier rule of the value
// axis. At least 20 finite points; m = the median; outliers are values above 3 x m; no
// clipping when the scale starts from zero. Points are [sol, value].

/** `n` sols from 1000 with values 190..229 (a quiet series), as [sol, value]. */
const quiet = (n: number): [number, number][] =>
  Array.from({ length: n }, (_, i) => [1000 + i, 190 + (i % 40)]);

const withSpike = (n: number, at: number, value: number): [number, number][] =>
  quiet(n).map(([s, v]) => (s === at ? [s, value] : [s, v]));

describe("AC-4 an outlier is left out of the scale in the fitted mode", () => {
  it("AC-4 one spike: the top follows the quiet values, the spike is returned as [sol, value]", () => {
    const pts = withSpike(300, 1050, 2342);
    const s = valueScale(pts, false);
    expect(s.outliers).toEqual([[1050, 2342]]);
    expect(s.top).toBeLessThan(400);
    expect(s.ticks[s.ticks.length - 1]).toBe(s.top);
  });

  it("AC-4 the scale equals the scale of the series without the spike", () => {
    const pts = withSpike(300, 1050, 2342);
    const without = pts.filter(([s]) => s !== 1050);
    const a = valueScale(pts, false);
    const b = valueScale(without, false);
    expect([a.bottom, a.top, a.ticks]).toEqual([b.bottom, b.top, b.ticks]);
  });

  it("AC-4 several spikes: all are returned, ordered by sol", () => {
    const pts = quiet(600).map(([s, v]): [number, number] =>
      s === 1150 ? [s, 1800] : s === 1020 ? [s, 2100] : s === 1100 ? [s, 1500] : [s, v],
    );
    const s = valueScale(pts, false);
    expect(s.outliers).toEqual([
      [1020, 2100],
      [1100, 1500],
      [1150, 1800],
    ]);
    expect(s.top).toBeLessThan(400);
  });

  it("AC-4 the exact edge: a value of exactly 3 x median is not an outlier, just above it is", () => {
    // 100 points of 100 and one more: the median is 100
    const base = Array.from({ length: 100 }, (_, i): [number, number] => [i, 100]);
    expect(valueScale([...base, [500, 300]], false).outliers).toEqual([]);
    expect(valueScale([...base, [500, 300.5]], false).outliers).toEqual([[500, 300.5]]);
  });
});

describe("AC-5 the scale from zero is never clipped", () => {
  it("AC-5 zero mode: no outliers, the top reaches the highest value", () => {
    const s = valueScale(withSpike(300, 1050, 2342), true);
    expect(s.outliers).toEqual([]);
    expect(s.bottom).toBe(0);
    expect(s.top).toBeGreaterThanOrEqual(2342);
  });
});

describe("AC-6 a series without outliers is scaled as before", () => {
  it("AC-6 no outliers in a quiet series: empty list", () => {
    const s = valueScale(quiet(300), false);
    expect(s.outliers).toEqual([]);
    expect(s.bottom).toBeGreaterThan(0);
    expect(s.top).toBeGreaterThanOrEqual(229);
    expect(s.ticks[0]).toBe(s.bottom);
  });

  it("AC-6 a series with some noise below 2 x p is not clipped", () => {
    const pts = quiet(300).map(([s, v]): [number, number] => (s === 1010 ? [s, 400] : [s, v]));
    expect(valueScale(pts, false).outliers).toEqual([]);
    expect(valueScale(pts, false).top).toBeGreaterThanOrEqual(400);
  });

  it("AC-6 non-finite values are ignored and never reported", () => {
    const pts = [...quiet(50), [2000, Number.NaN] as [number, number]];
    const s = valueScale(pts, false);
    expect(s.outliers).toEqual([]);
    expect(Number.isFinite(s.top)).toBe(true);
  });

  it("AC-6 an empty series still gives a scale and no outliers", () => {
    const s = valueScale([], false);
    expect(s.outliers).toEqual([]);
    expect(s.top).toBeGreaterThan(s.bottom);
  });
});

describe("AC-4 fewer than 20 points: no clipping", () => {
  it("AC-4 19 points with a spike: the spike stays on the scale", () => {
    const s = valueScale(withSpike(19, 1005, 2342), false);
    expect(s.outliers).toEqual([]);
    expect(s.top).toBeGreaterThanOrEqual(2342);
  });

  it("AC-4 a lone spike in a short series (21 and 100 points) is clipped", () => {
    for (const n of [21, 50, 100]) {
      const s = valueScale(withSpike(n, 1005, 2342), false);
      expect(s.outliers, `${n} points`).toEqual([[1005, 2342]]);
      expect(s.top, `${n} points`).toBeLessThan(400);
    }
  });
});

describe("AC-7 the same rule for any series (the count rate)", () => {
  it("AC-7 a count rate series of 7-8 events/s with one value of 70", () => {
    const rate = Array.from({ length: 300 }, (_, i): [number, number] => [
      4100 + i,
      i === 90 ? 70 : 7 + (i % 9) / 10,
    ]);
    const s = valueScale(rate, false);
    expect(s.outliers).toEqual([[4190, 70]]);
    expect(s.top).toBeLessThan(20);
    expect(valueScale(rate, true).outliers).toEqual([]);
  });
});
