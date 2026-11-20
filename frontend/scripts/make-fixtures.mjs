/* global fetch, process, console, URL */
// Derives the small test fixtures in tests/fixtures/ from a live API (decimated real data):
//   node scripts/make-fixtures.mjs https://your-deployment.example
// Traverse: at most ~250 features (every Kth, plus the last), each with its first and last point
// plus every Nth point; stops: ~100 evenly spread,
// always including the first and the last. Output is minified (see .prettierignore).
import { mkdirSync, writeFileSync } from "node:fs";

const base = (process.argv[2] ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
const out = new URL("../tests/fixtures/", import.meta.url);
const TARGET_POINTS = 1200;
const MAX_FEATURES = 250;
const TARGET_STOPS = 100;

async function get(path) {
  const res = await fetch(base + path);
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}

function thinTraverse(fc) {
  const total = fc.features.reduce((n, f) => n + f.geometry.coordinates.length, 0);
  const step = Math.max(1, Math.ceil(total / TARGET_POINTS));
  // Very many tiny features (Curiosity): keep every Kth one, and always the last.
  const k = Math.max(1, Math.ceil(fc.features.length / MAX_FEATURES));
  const kept = fc.features.filter((_, i) => i % k === 0 || i === fc.features.length - 1);
  const features = kept.map((f) => {
    const c = f.geometry.coordinates;
    const keep = c.filter((_, i) => i === 0 || i === c.length - 1 || i % step === 0);
    return { ...f, geometry: { ...f.geometry, coordinates: keep } };
  });
  return { ...fc, features };
}

function thinStops(loc) {
  const n = loc.items.length;
  const step = Math.max(1, Math.ceil(n / TARGET_STOPS));
  const items = loc.items.filter((_, i) => i === 0 || i === n - 1 || i % step === 0);
  return { ...loc, items };
}

const save = (name, data) => {
  writeFileSync(new URL(name, out), JSON.stringify(data));
  console.log(name, JSON.stringify(data).length, "bytes");
};

mkdirSync(out, { recursive: true });
save("rovers.json", await get("/api/v2/rovers"));
for (const rover of ["perseverance", "curiosity"]) {
  save(`${rover}-traverse.json`, thinTraverse(await get(`/api/v2/rovers/${rover}/traverse`)));
  save(`${rover}-location.json`, thinStops(await get(`/api/v2/rovers/${rover}/location`)));
}
