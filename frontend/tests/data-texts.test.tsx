import { fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../src/App";
import { apiError, curiosity, ingenuity, mockApi, page, perseverance, photo } from "./helpers";
import { RAD_CREDIT, radiationResponse } from "./fixtures/radiation";
import { CUR_CREDIT, PER_CREDIT } from "./fixtures/weather";
import { CUR_SOL } from "./fixtures/solreport";
import { fromInput, openRadiationRange } from "./range-helpers";
import { openWeather, readyPanel } from "./weather-helpers";
import {
  charts,
  hourSlider,
  inspectHour,
  mainEl,
  oneChart,
  openReport,
  reportHeading,
  reportPath,
} from "./solreport-helpers";

// docs/proposals/frontend-data-texts.md (revision 1, 2026-11-26): the component tests of AC-2 ... AC-5 and AC-10.
// AC-1, AC-7, AC-8, AC-9 need real layout and are in e2e/data-texts.spec.ts; AC-6 is in data-texts-errors.test.ts.

const FOLLOWING = Node.DOCUMENT_POSITION_FOLLOWING;
const follows = (a: Node, b: Node) => (a.compareDocumentPosition(b) & FOLLOWING) !== 0;

describe("data-texts AC-2 the credit of the radiation page sits next to the data", () => {
  it("data-texts AC-2 a 'Credit:' line under the charts, before the range controls", async () => {
    openRadiationRange();
    await screen.findByRole("table");
    const line = await screen.findByTestId("data-credit-chart");
    expect(line.textContent).toMatch(/^\s*Credit:/);
    expect(line.textContent).toContain(RAD_CREDIT);
    expect(line.classList.contains("credit")).toBe(true);
    const firstChart = within(mainEl())
      .getAllByRole("img")
      .find((el) => el.tagName.toLowerCase() === "svg")!;
    expect(follows(firstChart, line)).toBe(true);
    expect(follows(line, fromInput())).toBe(true);
  });

  it("data-texts AC-2 a 'Credit:' line under the table of sols", async () => {
    openRadiationRange();
    const table = await screen.findByRole("table");
    const line = await screen.findByTestId("data-credit-table");
    expect(line.textContent).toMatch(/^\s*Credit:/);
    expect(line.textContent).toContain(RAD_CREDIT);
    expect(follows(table, line)).toBe(true);
  });

  it("data-texts AC-2 the text comes from the `credit` of the answer, not from a constant", async () => {
    openRadiationRange(undefined, {
      "/api/v2/rovers/curiosity/radiation": radiationResponse(undefined, {
        credit: "Some other team / Some archive",
      }),
    });
    await screen.findByRole("table");
    expect((await screen.findByTestId("data-credit-chart")).textContent).toContain(
      "Some other team / Some archive",
    );
    expect(screen.getByTestId("data-credit-table").textContent).toContain(
      "Some other team / Some archive",
    );
  });
});

describe("data-texts AC-3 the credit of the weather page sits next to the data", () => {
  it("data-texts AC-3 Perseverance: the lines under the chart and under the table", async () => {
    openWeather();
    const panel = await readyPanel();
    const chartLine = await within(panel).findByTestId("data-credit-chart");
    const tableLine = within(panel).getByTestId("data-credit-table");
    for (const line of [chartLine, tableLine]) {
      expect(line.textContent).toMatch(/^\s*Credit:/);
      expect(line.textContent).toContain(PER_CREDIT);
    }
    expect(follows(within(panel).getByRole("table"), tableLine)).toBe(true);
    expect(follows(chartLine, fromInput())).toBe(true);
  });

  it("data-texts AC-3 switching to Curiosity shows the credit of the new answer in both places", async () => {
    openWeather();
    const panel = await readyPanel();
    fireEvent.click(within(panel).getByRole("radio", { name: /Curiosity/ }));
    await waitFor(() =>
      expect(within(panel).getByTestId("data-credit-chart").textContent).toContain(CUR_CREDIT),
    );
    expect(within(panel).getByTestId("data-credit-table").textContent).toContain(CUR_CREDIT);
    expect(within(panel).getByTestId("data-credit-chart").textContent).not.toContain(PER_CREDIT);
  });
});

describe("data-texts AC-4 the Search link of a camera row keeps the page filters", () => {
  const STATS = "/api/v2/photos/stats";
  const group = (rover: string, camera: string, total: number) => ({
    rover,
    camera,
    period: null,
    total_photos: total,
    sols_with_photos: 10,
    first_sol: 300,
    last_sol: 400,
    first_date: "2021-01-01",
    last_date: "2021-12-31",
  });
  const BY_CAMERA = {
    group_by: "camera",
    total_photos: 1230,
    sols_with_photos: 20,
    first_date: "2012-08-06",
    last_date: "2026-11-06",
    groups: [group("curiosity", "CHEMCAM_RMI", 1170), group("perseverance", "NAVCAM_LEFT", 60)],
  };

  async function openCameraRows(path: string) {
    mockApi({
      "/api/v2/photos": { items: [], page: 1, per_page: 24, total: 0, nearest_sols: null },
      "/api/v2/rovers": { items: [perseverance, curiosity] },
      "/api/v2/rovers/perseverance/cameras": {
        items: [{ code: "NAVCAM_LEFT", name: "Navcam L", total_photos: 1 }],
      },
      "/api/v2/rovers/curiosity/cameras": {
        items: [{ code: "CHEMCAM_RMI", name: "ChemCam RMI", total_photos: 1 }],
      },
      [STATS]: (url: URL) => {
        const by = url.searchParams.get("group_by") ?? "rover";
        if (by === "camera") return BY_CAMERA;
        return {
          ...BY_CAMERA,
          group_by: by,
          groups: BY_CAMERA.groups.map((g) => ({
            ...g,
            camera: null,
            period: by === "rover" ? null : "2021",
          })),
        };
      },
    });
    window.history.replaceState(null, "", path);
    render(<App />);
    fireEvent.click(await screen.findByTestId("stats-tab-camera"));
    const table = await screen.findByTestId("stats-table");
    const link = within(table).getByRole("link", { name: /Search.*CHEMCAM_RMI/i });
    return new URL(link.getAttribute("href")!, "http://localhost");
  }

  it("data-texts AC-4 sol period and full frames are carried over, with this row's rover and camera", async () => {
    const u = await openCameraRows(
      "/stats?rovers=curiosity,perseverance&sol_min=300&sol_max=400&sample_type=full",
    );
    expect(u.pathname).toBe("/search");
    expect(u.searchParams.get("rovers")).toBe("curiosity");
    expect(u.searchParams.get("camera")).toBe("CHEMCAM_RMI");
    expect(u.searchParams.get("sol_min")).toBe("300");
    expect(u.searchParams.get("sol_max")).toBe("400");
    expect(u.searchParams.get("sample_type")).toBe("full");
  });

  it("data-texts AC-4 a date period is carried over as dates", async () => {
    const u = await openCameraRows(
      "/stats?rovers=curiosity,perseverance&date_min=2021-03-01&date_max=2021-06-30&sample_type=full",
    );
    expect(u.searchParams.get("camera")).toBe("CHEMCAM_RMI");
    expect(u.searchParams.get("date_min")).toBe("2021-03-01");
    expect(u.searchParams.get("date_max")).toBe("2021-06-30");
    expect(u.searchParams.has("sol_min")).toBe(false);
  });

  it("data-texts AC-4 with 'Full frames only' off there is no sample_type", async () => {
    const u = await openCameraRows("/stats?rovers=curiosity,perseverance&sol_min=300&sol_max=400");
    expect(u.searchParams.get("sol_min")).toBe("300");
    expect(u.searchParams.has("sample_type")).toBe(false);
  });
});

describe("data-texts AC-5 the sol arrows of the photo page keep the filters", () => {
  function open(path: string) {
    const m = mockApi({
      "/api/v2/rovers": { items: [perseverance, curiosity, ingenuity] },
      "/api/v2/rovers/curiosity/cameras": {
        items: [{ code: "NAV_LEFT_B", name: "Navcam Left B", total_photos: 10 }],
      },
      "/api/v2/rovers/curiosity/photos": page(
        [photo(1, { rover: "curiosity", sol: 344 })],
        1,
        1,
        48,
      ),
    });
    window.history.replaceState(null, "", path);
    render(<App />);
    return m;
  }
  const START = "/rovers/curiosity?sol=344&camera=NAV_LEFT_B&sample_type=full&per_page=48";
  const params = (href: string) => {
    const u = new URL(href, "http://localhost");
    return { path: u.pathname, q: Object.fromEntries(u.searchParams) };
  };
  const expected = (sol: number) => ({
    path: "/rovers/curiosity",
    q: { sol: String(sol), camera: "NAV_LEFT_B", sample_type: "full", per_page: "48" },
  });

  it("data-texts AC-5 'Next sol' links to sol+1 with camera, sample type and page size", async () => {
    open(START);
    const next = await screen.findByRole("link", { name: "Next sol" });
    expect(params(next.getAttribute("href")!)).toEqual(expected(345));
  });

  it("data-texts AC-5 'Previous sol' links to sol-1 with the same filters", async () => {
    open(START);
    const prev = await screen.findByRole("link", { name: "Previous sol" });
    expect(params(prev.getAttribute("href")!)).toEqual(expected(343));
  });

  it("data-texts AC-5 the page number is dropped", async () => {
    open(`${START}&page=3`);
    const next = await screen.findByRole("link", { name: "Next sol" });
    const { q } = params(next.getAttribute("href")!);
    expect(q).not.toHaveProperty("page");
    expect(q).not.toHaveProperty("earth_date");
  });

  it("data-texts AC-5 after the click the address has the filters and the focus is on the results title", async () => {
    const m = open(START);
    fireEvent.click(await screen.findByRole("link", { name: "Next sol" }));
    await waitFor(() => expect(window.location.search).toContain("sol=345"));
    expect(params(window.location.pathname + window.location.search)).toEqual(expected(345));
    await waitFor(() => expect(document.activeElement?.id).toBe("result-title"));
    await waitFor(() =>
      expect(m.last("/api/v2/rovers/curiosity/photos")?.searchParams.get("camera")).toBe(
        "NAV_LEFT_B",
      ),
    );
  });
});

describe("data-texts AC-10 the pinned hour of the sol report", () => {
  const CUR = "curiosity";
  const hourOutput = () => mainEl().querySelector("output") as HTMLOutputElement;
  const clearButton = () => screen.queryByRole("button", { name: /^clear hour$/i });
  const shownHour = () => /(\d\d):00/.exec(hourOutput().textContent ?? "")?.[1] ?? null;
  const X_15 = 243; // x of the middle of hour 15 on a 360 px chart in jsdom (zero-size rect)

  beforeEach(() => {
    const proto = SVGElement.prototype as unknown as Record<string, unknown>;
    proto.setPointerCapture = vi.fn();
    proto.releasePointerCapture = vi.fn();
    proto.hasPointerCapture = vi.fn(() => true);
  });

  async function openCuriosity() {
    openReport(reportPath(CUR, CUR_SOL));
    await reportHeading("Curiosity", CUR_SOL);
    await waitFor(() => expect(charts(/air temperature/i)).toHaveLength(1));
  }

  it("data-texts AC-10 (a) the slider hour comes back after the mouse passes over a chart", async () => {
    await openCuriosity();
    inspectHour(4);
    await waitFor(() => expect(shownHour()).toBe("04"));
    const svg = oneChart(/air temperature/i);
    fireEvent.pointerMove(svg, { clientX: X_15, clientY: 40, pointerType: "mouse" });
    fireEvent.pointerLeave(svg, { pointerType: "mouse" });
    await waitFor(() => expect(shownHour()).toBe("04"));
    expect(clearButton()).toBeTruthy();
    expect(hourSlider().classList.contains("is-idle")).toBe(false);
  });

  it("data-texts AC-10 (b) with no hour selected the mouse leaving still gives 'No hour selected'", async () => {
    await openCuriosity();
    const svg = oneChart(/air temperature/i);
    fireEvent.pointerMove(svg, { clientX: X_15, clientY: 40, pointerType: "mouse" });
    await waitFor(() => expect(shownHour()).not.toBeNull());
    fireEvent.pointerLeave(svg, { pointerType: "mouse" });
    await waitFor(() => expect(hourOutput().textContent?.trim()).toBe("No hour selected"));
    expect(clearButton()).toBeNull();
  });

  it("data-texts AC-10 (c) a mouse click on a chart pins the hour, leaving does not unpin it", async () => {
    await openCuriosity();
    const svg = oneChart(/air temperature/i);
    fireEvent.pointerMove(svg, { clientX: X_15, clientY: 40, pointerType: "mouse" });
    fireEvent.pointerDown(svg, { clientX: X_15, clientY: 40, pointerType: "mouse", buttons: 1 });
    fireEvent.pointerUp(svg, { clientX: X_15, clientY: 40, pointerType: "mouse" });
    await waitFor(() => expect(shownHour()).toBe("15"));
    fireEvent.pointerLeave(svg, { pointerType: "mouse" });
    await waitFor(() => expect(shownHour()).toBe("15"));
    expect(clearButton()).toBeTruthy();
    // a later pass previews another hour, but leaving returns to the pinned 15:00
    fireEvent.pointerMove(svg, { clientX: 100, clientY: 40, pointerType: "mouse" });
    fireEvent.pointerLeave(svg, { pointerType: "mouse" });
    await waitFor(() => expect(shownHour()).toBe("15"));
  });

  it("data-texts AC-10 (d) 'Clear hour' after a slider hour with a mouse pass gives 'No hour selected'", async () => {
    await openCuriosity();
    inspectHour(4);
    const svg = oneChart(/air temperature/i);
    fireEvent.pointerMove(svg, { clientX: X_15, clientY: 40, pointerType: "mouse" });
    fireEvent.pointerLeave(svg, { pointerType: "mouse" });
    fireEvent.click(await screen.findByRole("button", { name: /^clear hour$/i }));
    await waitFor(() => expect(hourOutput().textContent?.trim()).toBe("No hour selected"));
    expect(clearButton()).toBeNull();
    expect(document.querySelectorAll('[data-testid="hour-cursor"]')).toHaveLength(0);
  });

  it("data-texts AC-10 (d) 'Clear hour' after a mouse-pinned hour gives 'No hour selected'", async () => {
    await openCuriosity();
    const svg = oneChart(/air temperature/i);
    fireEvent.pointerDown(svg, { clientX: X_15, clientY: 40, pointerType: "mouse", buttons: 1 });
    fireEvent.pointerUp(svg, { clientX: X_15, clientY: 40, pointerType: "mouse" });
    fireEvent.pointerLeave(svg, { pointerType: "mouse" });
    fireEvent.click(await screen.findByRole("button", { name: /^clear hour$/i }));
    await waitFor(() => expect(hourOutput().textContent?.trim()).toBe("No hour selected"));
  });
});

describe("data-texts AC-6/AC-7 the range order error is shown once", () => {
  it("data-texts AC-7 one backend issue for sol_min: the alert has the text once, without a repeated label", async () => {
    mockApi({
      "/api/v2/photos": { items: [], page: 1, per_page: 24, total: 0, nearest_sols: null },
      "/api/v2/rovers": { items: [perseverance, curiosity] },
      "/api/v2/rovers/curiosity/cameras": { items: [] },
      "/api/v2/photos/stats": () =>
        apiError(422, "validation_error", "Invalid query parameters", [
          { field: "query.sol_min", message: "sol_min must be less than or equal to sol_max" },
        ]),
    });
    window.history.replaceState(null, "", "/stats?rovers=curiosity&sol_min=500&sol_max=100");
    render(<App />);
    const alert = await screen.findByRole("alert");
    const text = alert.textContent ?? "";
    expect(text).toContain("“From sol” must not be greater than “To sol”");
    expect(text.split("must not be greater than")).toHaveLength(2);
    expect(text).not.toMatch(/From sol\W*:\s*“From sol”/);
    expect((screen.getByLabelText("From sol") as HTMLElement).getAttribute("aria-invalid")).toBe(
      "true",
    );
    expect((screen.getByLabelText("To sol") as HTMLElement).getAttribute("aria-invalid")).toBe(
      "true",
    );
  });
});
