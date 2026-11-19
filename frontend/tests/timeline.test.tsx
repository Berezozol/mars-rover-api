import { fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { describe, expect, it } from "vitest";
import { App } from "../src/App";
import { binCounts, levelFn } from "../src/pages/Timeline";
import { apiError, manifest, mockApi, perseverance } from "./helpers";

const sols = [
  {
    sol: 2031,
    earth_date: "2026-11-06",
    total_photos: 52,
    cameras: ["NAVCAM_LEFT", "SUPERCAM_RMI"],
  },
  { sol: 2030, earth_date: "2026-11-05", total_photos: 40, cameras: ["NAVCAM_LEFT"] },
  { sol: 2000, earth_date: "2026-10-06", total_photos: 300, cameras: ["MCZ_LEFT"] },
  { sol: 5, earth_date: "2021-02-23", total_photos: 900, cameras: ["NAVCAM_LEFT"] },
];

function setup(path = "/rovers/perseverance/timeline") {
  const m = mockApi({
    "/api/v2/rovers": { items: [perseverance] },
    "/api/v2/rovers/perseverance/manifest": manifest(perseverance, sols),
  });
  window.history.replaceState(null, "", path);
  render(<App />);
  return m;
}

describe("binCounts / levelFn", () => {
  it("sums per 10 sols and ranks non-empty bins into quartile levels", () => {
    const bins = binCounts(sols, 2031);
    expect(bins).toHaveLength(204);
    expect(bins[0]).toBe(900);
    expect(bins[203]).toBe(92);
    const level = levelFn(bins);
    expect(level(0)).toBe(0);
    expect(level(900)).toBe(4);
    expect(level(92)).toBe(1);
  });
});

describe("Timeline", () => {
  it("renders the summary, the heat strip and the default 100-sol table (empty sols absent)", async () => {
    const m = setup();
    expect(
      await screen.findByRole("heading", { name: "Perseverance mission timeline" }),
    ).toBeTruthy();
    expect(await screen.findByText("Sols with photos")).toBeTruthy();
    expect(screen.getByText("of 2,032")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Sols 0–9: 900 photos" })).toBeTruthy();
    expect(screen.getByText("Sols 1932–2031 · 2026-10-06 – 2026-11-06 · 392 photos")).toBeTruthy();
    const rows = screen.getAllByRole("row");
    expect(rows).toHaveLength(1 + 3); // header + sols 2031, 2030, 2000
    expect(m.calls.filter((c) => c.pathname.endsWith("/manifest"))).toHaveLength(1); // loaded once, whole mission
  });

  it("links a sol to the photo browser", async () => {
    setup();
    await screen.findByText("Sols with photos");
    // the table row link and the "Latest sol" summary link both lead to a browser
    for (const link of screen.getAllByRole("link", { name: "2031" })) {
      expect(link.getAttribute("href")).toMatch(/^\/rovers\/perseverance(\?sol=2031)?$/);
    }
    expect(
      screen.getByRole("link", { name: /Sol 2030, 2026-11-05: 40 photos/ }).getAttribute("href"),
    ).toBe("/rovers/perseverance?sol=2030");
  });

  it("zooms to a clicked heat cell through the address", async () => {
    setup();
    fireEvent.click(await screen.findByRole("button", { name: "Sols 0–9: 900 photos" }));
    await waitFor(() => expect(window.location.search).toBe("?from_sol=0&to_sol=99"));
    expect(
      await screen.findByText(/Sols 0–99 · 2021-02-23 – 2021-02-23 · 900 photos/),
    ).toBeTruthy();
  });

  it("rejects from > to before any request", async () => {
    const m = setup("/rovers/perseverance/timeline?from_sol=50&to_sol=10");
    expect(await screen.findByText("Invalid range")).toBeTruthy();
    expect(screen.getByText(/must not be greater than/)).toBeTruthy();
    expect(m.calls.filter((c) => c.pathname.endsWith("/manifest"))).toHaveLength(1); // only the one whole-mission load
  });

  it("shows the 503 state", async () => {
    mockApi({
      "/api/v2/rovers": { items: [perseverance] },
      "/api/v2/rovers/perseverance/manifest": () => apiError(503, "db_unavailable", "down"),
    });
    window.history.replaceState(null, "", "/rovers/perseverance/timeline");
    render(<App />);
    expect(await screen.findByText("Service temporarily unavailable")).toBeTruthy();
  });
});
