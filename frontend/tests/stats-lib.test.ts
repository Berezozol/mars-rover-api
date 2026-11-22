// Stage 19, `src/lib/stats.ts`: `fillPeriods` adds zero rows for periods the API skips (AC-20).
// `fillPeriods(groups)` fills every rover on its own, between its first and last period only.
import { describe, expect, it } from "vitest";
import { fillPeriods } from "../src/lib/stats";

type Row = { rover: string; period: string; total_photos: number; sols_with_photos: number };
const row = (period: string, total_photos: number): Row => ({
  rover: "perseverance",
  period,
  total_photos,
  sols_with_photos: total_photos ? 1 : 0,
});
const fill = fillPeriods as unknown as (rows: Row[]) => Row[];

describe("fillPeriods", () => {
  it("AC-20: fills missing months with zeros", () => {
    const out = fill([row("2021-02", 5), row("2021-05", 7)]);
    expect(out.map((r) => r.period)).toEqual(["2021-02", "2021-03", "2021-04", "2021-05"]);
    expect(out.map((r) => r.total_photos)).toEqual([5, 0, 0, 7]);
  });

  it("AC-20: crosses the year boundary", () => {
    const out = fill([row("2021-11", 1), row("2022-02", 2)]);
    expect(out.map((r) => r.period)).toEqual(["2021-11", "2021-12", "2022-01", "2022-02"]);
    expect(out.map((r) => r.total_photos)).toEqual([1, 0, 0, 2]);
  });

  it("AC-20: fills missing years", () => {
    const out = fill([row("2020", 3), row("2023", 4)]);
    expect(out.map((r) => r.period)).toEqual(["2020", "2021", "2022", "2023"]);
    expect(out.map((r) => r.total_photos)).toEqual([3, 0, 0, 4]);
  });

  it("AC-20: leaves a continuous series unchanged", () => {
    const input = [row("2021-01", 1), row("2021-02", 2), row("2021-03", 3)];
    expect(fill(input).map((r) => r.total_photos)).toEqual([1, 2, 3]);
  });

  it("AC-20: a single period and an empty list stay as they are", () => {
    expect(fill([row("2021-06", 9)])).toHaveLength(1);
    expect(fill([])).toEqual([]);
  });

  it("AC-20: does not change its input", () => {
    const input = [row("2021-02", 5), row("2021-05", 7)];
    const copy = JSON.parse(JSON.stringify(input)) as Row[];
    fill(input);
    expect(input).toEqual(copy);
  });

  it("AC-20: filled rows keep the rover and count no sols", () => {
    const filled = fill([row("2021-02", 5), row("2021-04", 7)])[1]!;
    expect(filled.rover).toBe("perseverance");
    expect(filled.sols_with_photos).toBe(0);
  });

  it("AC-20: with several rovers each one is filled within its own bounds", () => {
    const r = (rover: string, period: string, n: number): Row => ({ ...row(period, n), rover });
    const out = fill([
      r("perseverance", "2021-02", 1),
      r("perseverance", "2021-04", 2),
      r("curiosity", "2012-08", 3),
      r("curiosity", "2012-11", 4),
    ]);
    const of = (rover: string) => out.filter((x) => x.rover === rover).map((x) => x.period);
    expect(of("perseverance")).toEqual(["2021-02", "2021-03", "2021-04"]);
    expect(of("curiosity")).toEqual(["2012-08", "2012-09", "2012-10", "2012-11"]);
  });
});
