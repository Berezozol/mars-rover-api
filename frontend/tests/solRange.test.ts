import { describe, expect, it } from "vitest";
import {
  MARS_YEAR_SOLS,
  MIN_RANGE_SOLS,
  clampRange,
  defaultRange,
  parseRangeQuery,
  presetRange,
} from "../src/lib/solRange";

// Stage 22 phase A, docs/proposals/sol-conditions.md section 2 and 3 (decision 2): the sol range logic
// of the weather and radiation pages. A range is [from, to], both ends included.

const count = ([a, b]: [number, number]) => b - a + 1;

describe("AC-1 default range: the last Mars year before source_max_sol", () => {
  it("AC-1 the constants of the spec", () => {
    expect(MARS_YEAR_SOLS).toBe(669);
    expect(MIN_RANGE_SOLS).toBe(6);
  });

  it("AC-1 first 9, source_max 4710 -> [4042, 4710] (669 sols up to source_max_sol)", () => {
    const r = defaultRange({ first: 9, sourceMax: 4710 });
    expect(r).toEqual([4042, 4710]);
    expect(count(r)).toBe(MARS_YEAR_SOLS);
  });

  it("AC-1 when sourceMax - 668 is before the first sol, the range starts at the first sol", () => {
    expect(defaultRange({ first: 100, sourceMax: 108 })).toEqual([100, 108]);
    expect(defaultRange({ first: 9, sourceMax: 600 })).toEqual([9, 600]);
    expect(defaultRange({ first: 101, sourceMax: 768 })).toEqual([101, 768]);
  });

  it("AC-1 the edge: exactly 669 sols fit, so the start is sourceMax - 668", () => {
    expect(defaultRange({ first: 100, sourceMax: 768 })).toEqual([100, 768]);
    expect(defaultRange({ first: 99, sourceMax: 768 })).toEqual([100, 768]);
  });

  it("AC-1 the end is source_max_sol, never the rover's current sol", () => {
    // Curiosity is at sol 5066 while the archive ends at 4710: the range must not reach past 4710
    const r = defaultRange({ first: 9, sourceMax: 4710 });
    expect(r[1]).toBe(4710);
  });
});

describe("AC-2 presets count from source_max_sol", () => {
  const base = { first: 9, sourceMax: 4710 };

  it("AC-2 'Last 120 sols' -> [4591, 4710] (not from max_sol 5066: the old 'Last 100 sols' was empty)", () => {
    expect(presetRange("zoom", base)).toEqual([4591, 4710]);
  });

  it("AC-2 'Whole published data' -> [first, 4710]", () => {
    expect(presetRange("all", base)).toEqual([9, 4710]);
  });

  it("AC-2 'Last Mars year' is the default range", () => {
    expect(presetRange("year", base)).toEqual(defaultRange(base));
  });

  it("AC-2 a series shorter than 120 sols gives the whole series", () => {
    expect(presetRange("zoom", { first: 100, sourceMax: 108 })).toEqual([100, 108]);
  });
});

describe("clampRange keeps a range inside the bounds and at least MIN_RANGE_SOLS wide", () => {
  const bounds = { first: 3, max: 2031 };

  it("AC-3 a range inside the bounds is unchanged", () => {
    expect(clampRange([100, 400], bounds)).toEqual([100, 400]);
  });

  it("AC-3 an end beyond the bounds is pulled back", () => {
    expect(clampRange([-5, 100], bounds)).toEqual([3, 100]);
    expect(clampRange([1900, 5000], bounds)).toEqual([1900, 2031]);
  });

  it("AC-3 a range narrower than MIN_RANGE_SOLS is widened, and still lies inside the bounds", () => {
    for (const r of [
      [100, 101],
      [100, 100],
      [2030, 2031],
      [3, 4],
    ] as [number, number][]) {
      const out = clampRange(r, bounds);
      expect(count(out), String(r)).toBeGreaterThanOrEqual(MIN_RANGE_SOLS);
      expect(out[0], String(r)).toBeGreaterThanOrEqual(bounds.first);
      expect(out[1], String(r)).toBeLessThanOrEqual(bounds.max);
      expect(out[0], String(r)).toBeLessThan(out[1]);
    }
  });
});

describe("AC-12 the range in the address", () => {
  it("AC-12 ?from=4591&to=4710 -> [4591, 4710]", () => {
    expect(parseRangeQuery("?from=4591&to=4710")).toEqual([4591, 4710]);
  });

  it("AC-12 works without the question mark and next to other parameters", () => {
    expect(parseRangeQuery("from=4591&to=4710")).toEqual([4591, 4710]);
    expect(parseRangeQuery("?x=1&to=4710&from=4591")).toEqual([4591, 4710]);
  });

  it("AC-12 incomplete, wrong or reversed values are not a range", () => {
    for (const q of [
      "",
      "?",
      "?from=4591",
      "?to=4710",
      "?from=&to=",
      "?from=abc&to=4710",
      "?from=4591&to=xyz",
      "?from=4591.5&to=4710",
      "?from=-5&to=100",
      "?from=4710&to=4591",
    ]) {
      expect(parseRangeQuery(q) ?? null, q).toBeNull();
    }
  });
});
