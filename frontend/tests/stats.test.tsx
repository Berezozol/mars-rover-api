// Stage 19, screen Stats (`/stats`, GET /api/v2/photos/stats). Written against
// docs/proposals/stage-19-frontend.md, docs/API.md and design/mockups/stats.html.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { describe, expect, it, vi } from "vitest";
import { App } from "../src/App";
import { apiError, curiosity, ingenuity, mockApi, perseverance, spirit } from "./helpers";

const STATS = "/api/v2/photos/stats";

interface G {
  rover: string;
  camera?: string | null;
  period?: string | null;
  total_photos: number;
  sols_with_photos?: number;
  first_sol?: number;
  last_sol?: number;
  first_date?: string;
  last_date?: string;
}
const group = (g: G) => ({
  camera: null,
  period: null,
  sols_with_photos: 10,
  first_sol: 1,
  last_sol: 100,
  first_date: "2021-01-01",
  last_date: "2021-12-31",
  ...g,
});
const body = (group_by: string, groups: ReturnType<typeof group>[]) => ({
  group_by,
  total_photos: groups.reduce((a, g) => a + g.total_photos, 0),
  sols_with_photos: groups.reduce((a, g) => a + g.sols_with_photos, 0),
  first_date: groups.length ? "2012-08-06" : null,
  last_date: groups.length ? "2026-11-06" : null,
  groups,
});

const BY_ROVER = body("rover", [
  group({ rover: "perseverance", total_photos: 579518 }),
  group({ rover: "curiosity", total_photos: 1184530 }),
]);
// Perseverance skips 2021-03; Curiosity skips 2012-09.
const BY_MONTH = body("month", [
  group({ rover: "perseverance", period: "2021-02", total_photos: 1111 }),
  group({ rover: "perseverance", period: "2021-04", total_photos: 2222 }),
  group({ rover: "curiosity", period: "2012-08", total_photos: 3333 }),
  group({ rover: "curiosity", period: "2012-10", total_photos: 4444 }),
]);
const BY_YEAR = body("year", [
  group({ rover: "perseverance", period: "2021", total_photos: 5555 }),
  group({ rover: "curiosity", period: "2012", total_photos: 6666 }),
]);
const BY_CAMERA = body("camera", [
  group({ rover: "perseverance", camera: "NAVCAM_LEFT", total_photos: 96210 }),
  group({ rover: "curiosity", camera: "MAST_LEFT", total_photos: 70000 }),
  group({ rover: "curiosity", camera: "MAHLI", total_photos: 88431 }),
]);

type Handler = Parameters<typeof mockApi>[0][string];

function setup(statsHandler?: Handler, start = "/stats") {
  const m = mockApi({
    "/api/v2/photos": { items: [], page: 1, per_page: 24, total: 0, nearest_sols: null },
    "/api/v2/rovers": { items: [perseverance, curiosity, spirit, ingenuity] },
    "/api/v2/rovers/perseverance/cameras": {
      items: [{ code: "NAVCAM_LEFT", name: "Navcam L", total_photos: 1 }],
    },
    "/api/v2/rovers/curiosity/cameras": {
      items: [{ code: "MAST_LEFT", name: "Mast L", total_photos: 1 }],
    },
    "/api/v2/rovers/spirit/cameras": { items: [] },
    "/api/v2/rovers/ingenuity/cameras": { items: [] },
    [STATS]:
      statsHandler ??
      ((url: URL) => {
        switch (url.searchParams.get("group_by")) {
          case "month":
            return BY_MONTH;
          case "year":
            return BY_YEAR;
          case "camera":
            return BY_CAMERA;
          default:
            return BY_ROVER;
        }
      }),
  });
  window.history.replaceState(null, "", start);
  render(<App />);
  return m;
}

const stats = (m: ReturnType<typeof mockApi>) => m.calls.filter((c) => c.pathname === STATS);
const groupBys = (m: ReturnType<typeof mockApi>) =>
  stats(m).map((c) => c.searchParams.get("group_by") ?? "rover");
const showStats = () => fireEvent.click(screen.getByRole("button", { name: "Show stats" }));
const toggleTable = async () => {
  const host = await screen.findByTestId("stats-table-toggle");
  const control = host.matches("input, button")
    ? host
    : (within(host).getByLabelText(/table/i) ?? host);
  fireEvent.click(control);
};

describe("Stats", () => {
  it("AC-19: asks group_by=rover for the summary and group_by=month for the default tab", async () => {
    const m = setup();
    const summary = await screen.findByTestId("stats-summary");
    await waitFor(() => expect(groupBys(m)).toEqual(expect.arrayContaining(["rover", "month"])));
    expect(groupBys(m)).not.toContain("year");
    expect(groupBys(m)).not.toContain("camera");
    await waitFor(() => expect(summary.textContent).toContain("1,764,048")); // 579,518 + 1,184,530
    expect(await screen.findByRole("heading", { name: "Curiosity" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Perseverance" })).toBeTruthy();
    expect(screen.getByTestId("stats-tab-month").getAttribute("aria-selected")).toBe("true");
  });

  it("AC-20: month tab gives each rover its own chart", async () => {
    setup();
    const charts = await screen.findAllByTestId("stats-chart");
    expect(charts).toHaveLength(2); // Perseverance and Curiosity
    const names = charts.map((c) => c.closest("section, article, div")?.textContent ?? "");
    expect(names.join(" ")).toMatch(/Perseverance/);
    expect(names.join(" ")).toMatch(/Curiosity/);
  });

  it("AC-20: skipped months are drawn as zeros (the chart summary counts filled months)", async () => {
    setup();
    const charts = await screen.findAllByTestId("stats-chart");
    // each rover has 2 months with photos and 1 skipped month between them
    for (const c of charts) {
      const label =
        c.getAttribute("aria-label") ??
        c.querySelector("[aria-label*='shown']")?.getAttribute("aria-label") ??
        "";
      expect(label).toMatch(/\b3 months shown, 2 with photos/);
    }
  });

  it("AC-20: the year tab asks group_by=year and also draws one chart per rover", async () => {
    const m = setup();
    await screen.findAllByTestId("stats-chart");
    fireEvent.click(screen.getByTestId("stats-tab-year"));
    await waitFor(() => expect(groupBys(m)).toContain("year"));
    await waitFor(() =>
      expect(screen.getByTestId("stats-tab-year").getAttribute("aria-selected")).toBe("true"),
    );
    expect(await screen.findAllByTestId("stats-chart")).toHaveLength(2);
  });

  it("AC-21: a chart is an image with a text summary", async () => {
    setup();
    const [chart] = await screen.findAllByTestId("stats-chart");
    const img = chart!.getAttribute("role") === "img" ? chart! : chart!.querySelector("[role=img]");
    expect(img).toBeTruthy();
    const text = (img!.getAttribute("aria-label") ?? "") + (chart!.textContent ?? "");
    expect(text.trim().length).toBeGreaterThan(10);
  });

  it("AC-21: Chart / Table switch shows a table with the same numbers", async () => {
    setup();
    await screen.findAllByTestId("stats-chart");
    await toggleTable();
    const table = await screen.findByTestId("stats-table");
    for (const n of ["1,111", "2,222", "3,333", "4,444"]) expect(table.textContent).toContain(n);
    expect(within(table).getAllByRole("columnheader").length).toBeGreaterThan(2);
  });

  it("AC-22: the camera tab lists cameras by rover with a Search link to /search", async () => {
    const m = setup();
    await screen.findAllByTestId("stats-chart");
    fireEvent.click(screen.getByTestId("stats-tab-camera"));
    await waitFor(() => expect(groupBys(m)).toContain("camera"));
    const table = await screen.findByTestId("stats-table");
    expect(table.textContent).toContain("NAVCAM_LEFT");
    expect(table.textContent).toContain("MAHLI");
    expect(table.textContent).toContain("88,431");
    const link = within(table).getByRole("link", { name: /Search.*MAST_LEFT/i });
    const u = new URL(link.getAttribute("href")!, "http://localhost");
    expect(u.pathname).toBe("/search");
    expect(u.searchParams.get("rovers")).toBe("curiosity");
    expect(u.searchParams.get("camera")).toBe("MAST_LEFT");
  });

  it("AC-22: following the Search link opens /search", async () => {
    setup();
    await screen.findAllByTestId("stats-chart");
    fireEvent.click(screen.getByTestId("stats-tab-camera"));
    const table = await screen.findByTestId("stats-table");
    fireEvent.click(within(table).getByRole("link", { name: /Search.*NAVCAM_LEFT/i }));
    await waitFor(() => expect(window.location.pathname).toBe("/search"));
    expect(window.location.search).toContain("camera=NAVCAM_LEFT");
  });

  it("AC-23: 'Full frames only' sends sample_type=full", async () => {
    const m = setup();
    await screen.findAllByTestId("stats-chart");
    expect(stats(m).every((c) => !c.searchParams.has("sample_type"))).toBe(true);
    fireEvent.click(screen.getByLabelText("Full frames only"));
    showStats();
    await waitFor(() => expect(stats(m).at(-1)!.searchParams.get("sample_type")).toBe("full"));
    const last2 = stats(m).slice(-2);
    for (const c of last2) expect(c.searchParams.get("sample_type")).toBe("full");
  });

  it("AC-23: a sol range is sent as sol_min / sol_max, an Earth date as date_min / date_max, never both", async () => {
    const m = setup();
    await screen.findAllByTestId("stats-chart");
    fireEvent.input(screen.getByLabelText("From sol"), { target: { value: "100" } });
    fireEvent.input(screen.getByLabelText("To sol"), { target: { value: "200" } });
    showStats();
    await waitFor(() => expect(stats(m).at(-1)!.searchParams.get("sol_min")).toBe("100"));
    expect(stats(m).at(-1)!.searchParams.get("sol_max")).toBe("200");
    fireEvent.click(screen.getByLabelText("Earth date"));
    fireEvent.input(screen.getByLabelText("From date"), { target: { value: "2021-03-01" } });
    showStats();
    await waitFor(() => expect(stats(m).at(-1)!.searchParams.get("date_min")).toBe("2021-03-01"));
    const q = stats(m).at(-1)!.searchParams;
    expect(q.has("sol_min") || q.has("sol_max")).toBe(false);
  });

  it("AC-23: an empty selection (groups: []) shows the empty state", async () => {
    setup((url: URL) => body(url.searchParams.get("group_by") ?? "rover", []));
    expect(await screen.findByTestId("stats-empty")).toBeTruthy();
    expect(screen.queryByTestId("stats-chart")).toBeNull();
  });

  it("AC-23: 422 shows an error with the code", async () => {
    setup(() =>
      apiError(422, "validation_error", "Invalid query parameters", [
        { field: "date_min", message: "date_min must not be after date_max" },
      ]),
    );
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("validation_error");
  });

  it("AC-23: 503 shows the unavailable state", async () => {
    setup(() => apiError(503, "db_unavailable", "down"));
    expect(await screen.findByText("Service temporarily unavailable")).toBeTruthy();
  });

  it("shows a loading state while the stats are coming", async () => {
    setup(() => new Promise(() => {}));
    expect(await screen.findByText(/Loading/)).toBeTruthy();
  });

  it("AC-24: on a narrow screen the stats filters start collapsed behind 'Filters'", async () => {
    vi.stubGlobal(
      "matchMedia",
      (query: string) =>
        ({
          matches: false,
          media: query,
          addEventListener: () => {},
          removeEventListener: () => {},
          addListener: () => {},
          removeListener: () => {},
        }) as unknown as MediaQueryList,
    );
    setup();
    const form = await screen.findByTestId("stats-form");
    const toggle = within(form).getByRole("button", { name: /filters/i });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
  });

  it("rover card 'Search these photos' keeps only that rover's cameras, with period and sample type", async () => {
    setup(
      undefined,
      "/stats?rovers=perseverance,curiosity&camera=NAVCAM_LEFT,MAST_LEFT&sol_min=100&sol_max=200&sample_type=full",
    );
    const summary = await screen.findByTestId("stats-summary");
    const link = (name: string) =>
      within(summary).getByRole("link", { name: new RegExp(`Search ${name}`) });
    const u = new URL(link("Curiosity").getAttribute("href")!, "http://localhost");
    expect(u.pathname).toBe("/search");
    expect(u.searchParams.get("rovers")).toBe("curiosity");
    expect(u.searchParams.get("camera")).toBe("MAST_LEFT");
    expect(u.searchParams.get("sol_min")).toBe("100");
    expect(u.searchParams.get("sol_max")).toBe("200");
    expect(u.searchParams.get("sample_type")).toBe("full");
    const p = new URL(link("Perseverance").getAttribute("href")!, "http://localhost");
    expect(p.searchParams.get("rovers")).toBe("perseverance");
    expect(p.searchParams.get("camera")).toBe("NAVCAM_LEFT");
  });

  it("first load never has more than 4 API requests in flight", async () => {
    let active = 0;
    let peak = 0;
    const track = (v: object | ((u: URL) => object)) => async (u: URL) => {
      active++;
      peak = Math.max(peak, active);
      await new Promise((r) => setTimeout(r, 15));
      active--;
      return typeof v === "function" ? v(u) : v;
    };
    mockApi({
      "/api/v2/rovers": track({ items: [perseverance, curiosity, spirit, ingenuity] }),
      "/api/v2/rovers/perseverance/cameras": track({ items: [] }),
      "/api/v2/rovers/curiosity/cameras": track({ items: [] }),
      "/api/v2/rovers/spirit/cameras": track({ items: [] }),
      "/api/v2/rovers/ingenuity/cameras": track({ items: [] }),
      [STATS]: track((u: URL) =>
        u.searchParams.get("group_by") === "month" ? BY_MONTH : BY_ROVER,
      ),
    });
    window.history.replaceState(null, "", "/stats");
    render(<App />);
    await screen.findAllByTestId("stats-chart");
    await new Promise((r) => setTimeout(r, 60)); // let any late requests start
    expect(peak).toBeLessThanOrEqual(4);
  });

  it("AC-24: the header has Search and Stats links; filters collapse behind a 'Filters' button", async () => {
    setup();
    await screen.findByTestId("stats-summary");
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Search" }).getAttribute("href")).toBe("/search");
    expect(within(nav).getByRole("link", { name: "Stats" }).getAttribute("href")).toBe("/stats");
  });

  it("credit/notice rules: no NASA or JPL logos and no endorsement wording", async () => {
    setup();
    await screen.findByTestId("stats-summary");
    expect(document.querySelector("img[src*='nasa' i], img[src*='jpl' i]")).toBeNull();
    expect(document.body.textContent).not.toMatch(
      /(?<!not affiliated with or )endorsed by NASA|approved by NASA|official NASA/i,
    );
  });
});
