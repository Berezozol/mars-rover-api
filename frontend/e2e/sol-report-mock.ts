import type { Page } from "@playwright/test";
import { CUR_NOW, PER_NOW, weatherList } from "../tests/fixtures/weather";
import { radiationResponse } from "../tests/fixtures/radiation";
import { radDetail } from "../tests/fixtures/radhours";
import { curLong, perLong } from "../tests/fixtures/range";
import { latestLocation, radAround, reportDetail } from "../tests/fixtures/solreport";

// The intercepted API of the sol report for the polish spec (docs/proposals/sol-report-polish.md).

const rover = (id: string, name: string, max: number) => ({
  id,
  name,
  mission: id === "curiosity" ? "msl" : "mars2020",
  launch_date: "2020-07-30",
  landing_date: "2021-02-18",
  end_date: null,
  status: "active",
  max_sol: max,
  max_date: "2026-11-06",
  total_photos: 1,
  cameras_count: 1,
  last_synced_at: "2026-11-17T06:00:12Z",
  data_source: "sync",
});
const PER = rover("perseverance", "Perseverance", PER_NOW);
const CUR = rover("curiosity", "Curiosity", CUR_NOW);

export const json = (body: unknown, status = 200) => ({
  status,
  contentType: "application/json",
  body: JSON.stringify(body),
});

export const CUR_URL = "/rovers/curiosity/sols/344/conditions";
export const PER_URL = "/rovers/perseverance/sols/100/conditions";

/** The whole API the report asks for, with hourly radiation for Curiosity. */
export async function mockReportApi(page: Page): Promise<void> {
  await page.route("**/api/v2/rovers", (r) => r.fulfill(json({ items: [PER, CUR] })));
  await page.route("**/api/v2/rovers/perseverance", (r) => r.fulfill(json(PER)));
  await page.route("**/api/v2/rovers/curiosity", (r) => r.fulfill(json(CUR)));
  await page.route("**/api/v2/rovers/*/location**", (r) => {
    const id = r.request().url().includes("/curiosity/") ? "curiosity" : "perseverance";
    return r.fulfill(json(latestLocation(id)));
  });
  await page.route("**/api/v2/rovers/curiosity/radiation**", (r) => {
    const url = new URL(r.request().url());
    const one = url.pathname.match(/\/radiation\/(\d+)$/);
    if (one) return r.fulfill(json(radDetail(Number(one[1]))));
    const lo = Number(url.searchParams.get("sol_min") ?? 0);
    const hi = Number(url.searchParams.get("sol_max") ?? 1e9);
    const items = url.searchParams.has("sol_min")
      ? radAround.filter((x) => x.sol >= lo && x.sol <= hi)
      : radAround;
    return r.fulfill(json(radiationResponse(items)));
  });
  await page.route("**/api/v2/rovers/*/weather**", (route) => {
    const url = new URL(route.request().url());
    const m = url.pathname.match(/\/rovers\/(perseverance|curiosity)\/weather(?:\/(\d+))?$/);
    if (!m)
      return route.fulfill(
        json({ error: { code: "not_found", message: "x", details: null } }, 404),
      );
    const id = m[1] as "perseverance" | "curiosity";
    if (m[2] === undefined) {
      return route.fulfill(json(weatherList(id, id === "perseverance" ? perLong : curLong)));
    }
    return route.fulfill(
      json({
        ...reportDetail(id, Number(m[2]), { ls: id === "curiosity" ? 356.7 : 52.4 }),
        // the daily ground temperature of Perseverance, for the summary
        ...(id === "perseverance"
          ? { ground_temp_c: { min: -70.5, max: -12.3, mean: -40.2 } }
          : {}),
      }),
    );
  });
}
