import { describe, expect, it } from "vitest";
import { expectedRiseSet } from "./fixtures/sunref";
import { FALLBACK_LAT, HEMISPHERE, seasonOf, sunTimes } from "../src/lib/marsSeason";

// AC-26: the season comes from Ls and the hemisphere of the rover, sunrise and sunset from Ls and the latitude
// (docs/proposals/sol-conditions.md, ADR-5). The functions are small, so the unit tests pin the physics, not the
// wording: the season is found by its name in whatever the function returns.

const words = (x: unknown) => JSON.stringify(x ?? "").toLowerCase();

/** Sunrise and sunset in LMST hours, whatever the object is called: the first two numbers, in order. */
function riseSet(ls: number, lat: number): [number, number] | null {
  const r = sunTimes(ls, lat) as unknown;
  if (r === null || r === undefined) return null;
  const nums = (Array.isArray(r) ? r : Object.values(r as object)).filter(
    (v): v is number => typeof v === "number" && Number.isFinite(v),
  );
  return nums.length >= 2 ? [nums[0]!, nums[1]!] : null;
}

describe("AC-26 marsSeason: the hemisphere of each rover", () => {
  it("AC-26 Curiosity (south of the equator) and Perseverance (north) have different hemispheres", () => {
    expect(HEMISPHERE.curiosity).toBeDefined();
    expect(HEMISPHERE.perseverance).toBeDefined();
    expect(HEMISPHERE.curiosity).not.toEqual(HEMISPHERE.perseverance);
  });

  it("AC-26 the fallback latitudes are the rover latitudes: about -4.6 and 18.4", () => {
    expect(FALLBACK_LAT.curiosity).toBeCloseTo(-4.6, 0);
    expect(FALLBACK_LAT.perseverance).toBeCloseTo(18.4, 0);
  });
});

describe("AC-26 seasonOf", () => {
  const south = HEMISPHERE.curiosity;
  const north = HEMISPHERE.perseverance;

  it("AC-26 Ls 356.7 in the south is summer (the case of Curiosity sol 344)", () => {
    expect(words(seasonOf(356.7, south))).toContain("summer");
  });

  it("AC-26 the same Ls in the north is winter", () => {
    expect(words(seasonOf(356.7, north))).toContain("winter");
  });

  it.each([
    [0, "spring", "autumn"],
    [45, "spring", "autumn"],
    [90, "summer", "winter"],
    [135, "summer", "winter"],
    [180, "autumn", "spring"],
    [225, "autumn", "spring"],
    [270, "winter", "summer"],
    [315, "winter", "summer"],
  ])("AC-26 Ls %d: %s in the north, %s in the south", (ls, northName, southName) => {
    expect(words(seasonOf(ls, north))).toContain(northName);
    expect(words(seasonOf(ls, south))).toContain(southName);
  });

  it("AC-26 Ls 360 is the same as Ls 0 (a new Mars year)", () => {
    expect(words(seasonOf(360, north))).toBe(words(seasonOf(0, north)));
  });
});

describe("AC-26 sunTimes", () => {
  // sunTimes is LMST = LTST - EOT(Ls), EOT of Allison & McEwen (2000); the numbers are the reference of
  // fixtures/sunref.ts (EOT -41.4, +39.5 and +8.3 min), computed apart from the page code.
  it.each([
    [0, 6.689, 18.689], // 6:41 and 18:41; EOT is -41 min
    [180, 5.341, 17.341], // EOT is +39.5 min: the other sign
    [250, 5.861, 17.861], // EOT is +8.3 min, near the perihelion
  ])("AC-26 on the equator at Ls %d sunrise is %f and sunset %f LMST", (ls, rise, set) => {
    const t = riseSet(ls, 0);
    expect(t, `Ls ${ls}`).not.toBeNull();
    expect(Math.abs(t![0] - rise)).toBeLessThan(0.1);
    expect(Math.abs(t![1] - set)).toBeLessThan(0.1);
    // and the reference of this file agrees with the literals above
    expect(Math.abs(expectedRiseSet(ls, 0)[0] - rise)).toBeLessThan(0.01);
  });

  it("AC-26 the day is centred on LTST noon, which is 12 - EOT in LMST, at both rover latitudes", () => {
    for (const lat of [-4.6, 18.4]) {
      for (const ls of [0, 60, 120, 180, 250, 300]) {
        const t = riseSet(ls, lat)!;
        const want = expectedRiseSet(ls, lat);
        expect(Math.abs(t[0] - want[0]), `rise Ls ${ls} lat ${lat}`).toBeLessThan(0.2);
        expect(Math.abs(t[1] - want[1]), `set Ls ${ls} lat ${lat}`).toBeLessThan(0.2);
      }
    }
  });

  it("AC-26 at 18.4 N the day is long in northern summer (Ls 90) and short in northern winter (Ls 270)", () => {
    const summer = riseSet(90, 18.4)!;
    const winter = riseSet(270, 18.4)!;
    expect(summer[1] - summer[0]).toBeGreaterThan(12.5);
    expect(winter[1] - winter[0]).toBeLessThan(11.5);
  });

  it("AC-26 the south has the opposite seasons: at -4.6 the day is longer at Ls 270 than at Ls 90", () => {
    const a = riseSet(270, -4.6)!;
    const b = riseSet(90, -4.6)!;
    expect(a[1] - a[0]).toBeGreaterThan(b[1] - b[0]);
  });

  it("AC-26 the sun is up at local noon: sunrise is before 12 and sunset after 12", () => {
    for (const lat of [-4.6, 18.4]) {
      for (const ls of [0, 60, 120, 180, 240, 300]) {
        const t = riseSet(ls, lat)!;
        expect(t[0]).toBeLessThan(12);
        expect(t[1]).toBeGreaterThan(12);
      }
    }
  });
});
