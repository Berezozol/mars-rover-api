// Print daily weather of a rover and the hourly air temperature of one sol.
// Node 18+ (global fetch), no packages.
// Usage: node examples/weather.mjs [BASE_URL] [ROVER] [SOL_MIN] [SOL_MAX]
const [
  base = "http://127.0.0.1:8000",
  rover = "perseverance",
  solMin = "100",
  solMax = "105",
] = process.argv.slice(2);

const root = base.replace(/\/+$/, "");
const fmt = (value) => (value == null ? "-" : value.toFixed(1));

async function get(path) {
  const response = await fetch(`${root}${path}`);
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${(await response.text()).slice(0, 200)}`);
  }
  return response.json();
}

const data = await get(`/api/v2/rovers/${rover}/weather?sol_min=${solMin}&sol_max=${solMax}`);

// `credit` must be shown with the data.
console.log(`Credit: ${data.credit}`);
console.log(`Latest sol we have: ${data.source_max_sol} (the archive lags by 6-8 months)`);
const groundQuality = data.parameters.ground_temp_c?.quality ?? "n/a";
for (const item of data.items) {
  const air = item.air_temp_c ?? {};
  // Curiosity has no "wind" key; Perseverance has wind: null outside sols 36-313.
  const gust = item.wind?.gust_ms;
  // Perseverance's ground_temp_c is an estimate (about +-3 K); see parameters.ground_temp_c.quality.
  const ground = item.ground_temp_c?.mean;
  const flags = item.flags.map((flag) => flag.code).join(",") || "-";
  console.log(
    `sol ${item.sol}: air ${fmt(air.min)} / ${fmt(air.mean)} / ${fmt(air.max)} C, gust ${fmt(gust)} m/s, ground ${fmt(ground)} C (${groundQuality}), flags ${flags}`,
  );
}

if (data.items.length > 0) {
  const sol = data.items[0].sol;
  const detail = await get(`/api/v2/rovers/${rover}/weather/${sol}`);
  console.log(`\nHourly air temperature, sol ${sol} (LMST hours; - = no readings)`);
  console.log(detail.hours.map((h) => `${String(h.hour).padStart(2, "0")}:${fmt(h.air_temp_c)}`).join(" "));
  // Each hour also has air_temp_c_min / air_temp_c_max; Perseverance has wind_gust_ms (99th percentile).
  console.log("hour  air min/max (C)  wind gust (m/s)");
  for (const h of detail.hours) {
    console.log(
      `${String(h.hour).padStart(2, "0")}    ${fmt(h.air_temp_c_min).padStart(6)} ${fmt(h.air_temp_c_max).padStart(6)}  ${fmt(h.wind_gust_ms).padStart(15)}`,
    );
  }
}
