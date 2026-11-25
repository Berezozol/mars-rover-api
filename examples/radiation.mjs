// Print daily radiation of Curiosity (RAD) and the hourly dose of one sol.
// Node 18+ (global fetch), no packages.
// Usage: node examples/radiation.mjs [BASE_URL] [SOL_MIN] [SOL_MAX]
const [base = "http://127.0.0.1:8000", solMin = "1600", solMax = "1605"] = process.argv.slice(2);

const root = base.replace(/\/+$/, "");
const fmt = (value) => (value == null ? "-" : value.toFixed(2));

async function get(path) {
  const response = await fetch(`${root}${path}`);
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${(await response.text()).slice(0, 200)}`);
  }
  return response.json();
}

const data = await get(`/api/v2/rovers/curiosity/radiation?sol_min=${solMin}&sol_max=${solMax}`);

// `credit` and `citation` must be shown with the data.
console.log(`Credit: ${data.credit}`);
console.log(`Citation: ${data.citation}`);
console.log(`Latest sol we have: ${data.source_max_sol} (the dataset lags by months)`);
// Daily fields are in microgray per DAY (the hourly ones below are per HOUR).
for (const item of data.items) {
  console.log(
    `sol ${item.sol}: dose E ${fmt(item.dose_e_ugy_day.mean)}, dose B ${fmt(item.dose_b_ugy_day.mean)} uGy/day, coverage ${fmt(item.coverage)}, used ${item.observations.used}`,
  );
}

if (data.items.length > 0) {
  const sol = data.items[0].sol;
  const detail = await get(`/api/v2/rovers/curiosity/radiation/${sol}`);
  if (detail.timing_caveat) {
    console.log(`\nSol ${sol}: the dataset lists timing errors for this sol; hours may be shifted.`);
  }
  if (detail.hours == null) {
    // Hours were not computed, or the observation times of the sol contradict each other.
    console.log(`\nSol ${sol}: no hourly values (hours is null)`);
  } else {
    // Hourly fields are in microgray per HOUR; `hour` is LMST computed from UTC.
    console.log(`\nHourly dose, sol ${sol} (LMST hours; - = no observations)`);
    console.log("hour  coverage  obs  dose E (uGy/h)  dose B (uGy/h)");
    for (const h of detail.hours) {
      console.log(
        `${String(h.hour).padStart(2, "0")}    ${fmt(h.coverage).padStart(8)}  ${String(h.observations).padStart(3)}  ${fmt(h.dose_e_ugy_h).padStart(14)}  ${fmt(h.dose_b_ugy_h).padStart(14)}`,
      );
    }
  }
}
