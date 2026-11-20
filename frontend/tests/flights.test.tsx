import { fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { App } from "../src/App";
import type { Flight } from "../src/api/types";
import { defaultDir, hms, mmss, recordsOf, sortFlights, summarize } from "../src/lib/flights";
import { apiError, ingenuity, mockApi, page, perseverance, photo } from "./helpers";

// A few real rows of the flight log (flights 1, 9, 25, 59, 68); photo counts are made up.
const fixture = JSON.parse(readFileSync("tests/fixtures/ingenuity-flights.json", "utf-8")) as {
  items: Flight[];
};
const flights = fixture.items;

function open(path = "/rovers/ingenuity/flights") {
  window.history.replaceState(null, "", path);
  return render(<App />);
}

function routes(over: Record<string, unknown> = {}) {
  return mockApi({
    "/api/v2/rovers": { items: [perseverance, ingenuity] },
    "/api/v2/rovers/ingenuity/flights": fixture,
    ...over,
  });
}

/** Flight numbers in the order of the table rows. */
function order(): number[] {
  return screen
    .getAllByRole("row")
    .slice(1)
    .map((r) => Number(r.id.replace("flight-", "")));
}

describe("flight helpers", () => {
  it("summarizes totals on the client", () => {
    const s = summarize(flights)!;
    expect(s.count).toBe(5);
    expect([s.firstSol, s.lastSol]).toEqual([58, 1003]);
    expect(s.distanceM).toBe(0 + 625 + 704 + 0 + 702);
    expect(s.airSeconds).toBeCloseTo(39.1 + 166.4 + 161.3 + 142.6 + 131.1, 5);
    expect(s.top.flight).toBe(59); // 20 m
    expect(summarize([])).toBeNull();
  });

  it("formats durations", () => {
    expect(mmss(166.4)).toBe("2:46");
    expect(mmss(39.1)).toBe("0:39");
    expect(mmss(59.6)).toBe("1:00");
    expect(hms(3725)).toEqual({ h: 1, m: 2, s: 5 });
  });

  it("sorts with the flight number as a tie-break and flags records", () => {
    expect(sortFlights(flights, "distance", "desc").map((f) => f.flight)).toEqual([
      25, 68, 9, 1, 59,
    ]);
    expect(sortFlights(flights, "flight", "desc").map((f) => f.flight)).toEqual([68, 59, 25, 9, 1]);
    expect(defaultDir("flight")).toBe("asc");
    expect(defaultDir("distance")).toBe("desc");
    const rec = recordsOf(flights);
    expect(rec.get(59)).toEqual(["highest"]);
    expect(rec.get(25)).toEqual(["longest"]);
    expect(rec.get(68)).toEqual(["fastest"]);
    expect(rec.has(9)).toBe(false);
    // all-zero columns award no record
    expect(
      recordsOf([{ ...flights[0]!, max_altitude_m: 0, distance_m: 0, max_groundspeed_mps: 0 }])
        .size,
    ).toBe(0);
  });
});

describe("Flights page", () => {
  it("shows loading, then tiles computed from the list and every flight", async () => {
    const m = routes();
    open();
    expect(await screen.findByRole("status", { name: "Loading flights" })).toBeTruthy();
    const tiles = await screen.findByRole("region", { name: "Flight summary" });
    expect(within(tiles).getByText("5")).toBeTruthy(); // flights
    expect(within(tiles).getByText("sol 58 – 1003")).toBeTruthy();
    expect(within(tiles).getByText("2.03")).toBeTruthy(); // km: 2031 m
    expect(within(tiles).getByText("flight 59, sol 915")).toBeTruthy();
    expect(within(tiles).getByText(/^64[01] s in the air$/)).toBeTruthy();
    expect(order()).toEqual([1, 9, 25, 59, 68]);
    expect(m.calls.filter((c) => c.pathname.endsWith("/flights"))).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1, name: "Ingenuity flights" })).toBeTruthy();
    expect(screen.getByText("Complete")).toBeTruthy();
    expect(screen.getByText("Imported")).toBeTruthy();
  });

  it("links the photo count to the sol and marks a missing landing site", async () => {
    routes();
    open();
    const link = await screen.findByRole("link", { name: "479 photos of sol 133 (flight 9)" });
    expect(link.getAttribute("href")).toBe("/rovers/ingenuity?sol=133");
    const row = document.getElementById("flight-1")!;
    expect(within(row).getByTitle("Landing site not named in the flight log")).toBeTruthy();
    expect(document.getElementById("flight-9")!.textContent).toContain("Airfield F");
    const dur = row.querySelector("time[datetime^=PT]")!;
    expect(dur.getAttribute("datetime")).toBe("PT39.1S");
    expect(dur.textContent).toBe("0:39");
    expect(within(row).getByText("(39.1 seconds)")).toBeTruthy();
    // plain heading for the unsortable column, real sort buttons elsewhere
    expect(
      screen.getByRole("columnheader", { name: "From → to" }).querySelector("button"),
    ).toBeNull();
    expect(screen.getAllByRole("columnheader")).toHaveLength(9);
  });

  it("sorts by a heading (and reverses on a second click) and by the select", async () => {
    routes();
    open();
    await screen.findByRole("table");
    fireEvent.click(screen.getByRole("button", { name: "Distance" }));
    expect(order()).toEqual([25, 68, 9, 1, 59]);
    expect(screen.getByRole("columnheader", { name: "Distance" }).getAttribute("aria-sort")).toBe(
      "descending",
    );
    fireEvent.click(screen.getByRole("button", { name: "Distance" }));
    expect(order()).toEqual([1, 59, 9, 68, 25]);
    expect(screen.getByRole("columnheader", { name: "Distance" }).getAttribute("aria-sort")).toBe(
      "ascending",
    );
    fireEvent.change(screen.getByLabelText("Sort by"), { target: { value: "photos:desc" } });
    expect(order()).toEqual([9, 59, 68, 25, 1]); // 479, 456, 413, 242, 139
    fireEvent.change(screen.getByLabelText("Sort by"), { target: { value: "flight:desc" } });
    expect(order()).toEqual([68, 59, 25, 9, 1]);
  });

  it("scrolls to and highlights the #flight-N anchor once the rows exist", async () => {
    routes();
    const spy = vi.fn();
    Element.prototype.scrollIntoView = spy;
    open("/rovers/ingenuity/flights#flight-25");
    await screen.findByRole("table");
    await waitFor(() =>
      expect(document.getElementById("flight-25")!.className).toContain("target"),
    );
    expect(spy).toHaveBeenCalled();
    expect(spy.mock.contexts[0]).toBe(document.getElementById("flight-25"));
  });

  it("explains a 503 and offers Try again", async () => {
    let calls = 0;
    routes({
      "/api/v2/rovers/ingenuity/flights": () =>
        ++calls === 1 ? apiError(503, "db_unavailable", "Database is unavailable") : fixture,
    });
    open();
    expect(await screen.findByText("Service temporarily unavailable")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("table")).toBeTruthy();
  });

  it("shows an empty state for an empty log", async () => {
    routes({ "/api/v2/rovers/ingenuity/flights": { items: [] } });
    open();
    expect(await screen.findByText("No flights yet")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("other rovers have no flights, and the nav item appears only when Ingenuity is listed", async () => {
    routes();
    open("/rovers/perseverance/flights");
    expect(await screen.findByText("Perseverance has no flights")).toBeTruthy();
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Flights" }).getAttribute("href")).toBe(
      "/rovers/ingenuity/flights",
    );
  });

  it("has no Flights nav item when the API does not list Ingenuity", async () => {
    mockApi({
      "/api/v2/rovers": { items: [perseverance] },
      "/api/v2/rovers/perseverance/photos": page([photo(1)]),
      "/api/v2/rovers/perseverance/cameras": { items: [] },
    });
    open("/rovers/perseverance");
    await screen.findByRole("heading", { name: /Perseverance photos/ });
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).queryByRole("link", { name: "Flights" })).toBeNull();
  });
});
