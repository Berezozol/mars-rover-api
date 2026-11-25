// Mars seasons and approximate sunrise/sunset for the sol report. Computed in the browser: the API gives
// only Ls (solar longitude); the hemisphere is a constant of the rover and the latitude comes from the
// rover's latest stop (`/location?latest=true`), with a fallback here.

export type Hemisphere = "north" | "south";

/** Hemisphere of each rover that has weather data (rovers never change hemisphere). */
export const HEMISPHERE = {
  curiosity: "south",
  perseverance: "north",
} as const satisfies Record<string, Hemisphere>;

/** Latitude (degrees, north positive) used when the rover's location is not available. */
export const FALLBACK_LAT = {
  curiosity: -4.6,
  perseverance: 18.4,
} as const satisfies Record<string, number>;

/** Hemisphere of a rover by id; `undefined` for a rover without weather data. */
export const hemisphereOf = (rover: string): Hemisphere | undefined =>
  (HEMISPHERE as Record<string, Hemisphere | undefined>)[rover];

/** Fallback latitude of a rover by id (0 for an unknown one). */
export const fallbackLat = (rover: string): number =>
  (FALLBACK_LAT as Record<string, number | undefined>)[rover] ?? 0;

/** Axial tilt of Mars, degrees. */
const TILT_DEG = 25.19;

const SEASONS_NORTH = ["spring", "summer", "autumn", "winter"] as const;
const SEASONS_SOUTH = ["autumn", "winter", "spring", "summer"] as const;

/**
 * "Late southern summer": the season of the hemisphere for a solar longitude (0° = northern spring
 * equinox, quarters of 90°), split into early / mid / late thirds. `null` when Ls is unknown.
 */
export function seasonOf(ls: number | null | undefined, hemisphere: Hemisphere): string | null {
  if (typeof ls !== "number" || !Number.isFinite(ls)) return null;
  const l = ((ls % 360) + 360) % 360;
  const q = Math.floor(l / 90) % 4;
  const names = hemisphere === "north" ? SEASONS_NORTH : SEASONS_SOUTH;
  const within = l % 90;
  const part = within < 30 ? "early" : within >= 60 ? "late" : "mid";
  const text = `${part} ${hemisphere === "north" ? "northern" : "southern"} ${names[q]}`;
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Eccentricity of the orbit of Mars and Ls of its perihelion, degrees. */
const ECC = 0.0934;
const LS_PERIHELION = 251.0;

/**
 * Equation of time in degrees (true minus mean solar time; 1 degree = 4 minutes) at solar longitude Ls,
 * after Allison and McEwen (2000): EOT = 2.861 sin 2Ls - 0.071 sin 4Ls + 0.002 sin 6Ls - (v - M),
 * where v - M is the equation of centre. The true anomaly is v = Ls - Ls(perihelion), M follows from v.
 */
export function equationOfTimeDeg(ls: number): number {
  const rad = Math.PI / 180;
  const v = (ls - LS_PERIHELION) * rad;
  const ecc = 2 * Math.atan(Math.sqrt((1 - ECC) / (1 + ECC)) * Math.tan(v / 2));
  const m = ecc - ECC * Math.sin(ecc);
  let eoc = (v - m) / rad;
  eoc = ((((eoc + 180) % 360) + 360) % 360) - 180;
  return (
    2.861 * Math.sin(2 * ls * rad) -
    0.071 * Math.sin(4 * ls * rad) +
    0.002 * Math.sin(6 * ls * rad) -
    eoc
  );
}

/**
 * Approximate sunrise and sunset in local MEAN solar time (LMST) hours: the flat-horizon formula gives
 * true solar time (12 +- half the day), which is moved to LMST with LMST = LTST - EOT(Ls). Good to a few
 * minutes at best, so it is shown as approximate. `null` when Ls is unknown.
 */
export function sunTimes(
  ls: number | null | undefined,
  latDeg: number,
): [sunrise: number, sunset: number] | null {
  if (typeof ls !== "number" || !Number.isFinite(ls)) return null;
  const rad = Math.PI / 180;
  const dec = Math.asin(Math.sin(TILT_DEG * rad) * Math.sin(ls * rad));
  const c = Math.min(1, Math.max(-1, -Math.tan(latDeg * rad) * Math.tan(dec)));
  const half = (Math.acos(c) * 12) / Math.PI;
  const eot = equationOfTimeDeg(ls) / 15; // hours
  return [12 - half - eot, 12 + half - eot];
}

/** `5.2` -> `05:12`. */
export function hhmm(hours: number): string {
  const m = Math.round(hours * 60);
  return `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}
