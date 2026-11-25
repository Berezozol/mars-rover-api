/**
 * Reference sunrise and sunset in LMST, computed independently of the page code (AC-26): the flat-horizon day
 * length from the declination, centred on LTST noon, then LMST = LTST - EOT. The equation of time is the one of
 * Allison & McEwen (2000): EOT = 2.861 sin 2Ls - 0.071 sin 4Ls + 0.002 sin 6Ls - (equation of centre).
 * Checked by hand: EOT is -41.4 min at Ls 0, +14.9 at Ls 90, +39.5 at Ls 180, +8.3 at Ls 250, -13.1 at Ls 270.
 */
const rad = (d: number) => (d * Math.PI) / 180;

export function eotHours(ls: number): number {
  const nu = (((ls - 250.99) % 360) + 360) % 360;
  let m = nu;
  const centre = (x: number) =>
    10.691 * Math.sin(rad(x)) +
    0.623 * Math.sin(rad(2 * x)) +
    0.05 * Math.sin(rad(3 * x)) +
    0.005 * Math.sin(rad(4 * x)) +
    0.0005 * Math.sin(rad(5 * x));
  for (let i = 0; i < 50; i++) m = nu - centre(m);
  const eot =
    2.861 * Math.sin(rad(2 * ls)) -
    0.071 * Math.sin(rad(4 * ls)) +
    0.002 * Math.sin(rad(6 * ls)) -
    (nu - m);
  return eot / 15;
}

export function expectedRiseSet(ls: number, lat: number): [number, number] {
  const dec = Math.asin(Math.sin(rad(25.19)) * Math.sin(rad(ls)));
  const h = (Math.acos(-Math.tan(rad(lat)) * Math.tan(dec)) * 180) / Math.PI / 15;
  const eot = eotHours(ls);
  return [12 - h - eot, 12 + h - eot];
}
