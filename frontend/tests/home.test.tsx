import { render, screen } from "@testing-library/preact";
import { describe, expect, it } from "vitest";
import { App } from "../src/App";
import {
  apiError,
  curiosity,
  mockApi,
  page,
  pending,
  perseverance,
  photo,
  spirit,
} from "./helpers";

const latest = {
  sol: 2031,
  ...page([photo(1), photo(2, { camera: { code: "NAVCAM_LEFT", name: "Navcam" } })]),
  total: 52,
};

describe("Home", () => {
  it("shows rovers with data (active first), the latest strip and credit", async () => {
    mockApi({
      "/api/v2/rovers": { items: [curiosity, pending, perseverance, spirit] },
      "/api/v2/rovers/perseverance/photos/latest": latest,
    });
    render(<App />);
    await screen.findByRole("link", { name: "Latest sol 5066" });
    const headings = screen.getAllByRole("heading", { level: 2 });
    const names = headings.map((h) => h.textContent);
    expect(names.indexOf("Perseverance")).toBeLessThan(names.indexOf("Curiosity"));
    expect(names.indexOf("Curiosity")).toBeLessThan(names.indexOf("Spirit"));
    expect(names).not.toContain("Opportunity"); // max_sol = null: hidden
    expect(screen.getByText("Mission complete")).toBeTruthy();
    expect(screen.getByText("More rovers appear when their archives are imported.")).toBeTruthy();
    expect(await screen.findByText("All 52 photos of sol 2031 →")).toBeTruthy();
    expect(screen.getAllByText("NASA/JPL-Caltech/LANL/CNES/IRAP").length).toBeGreaterThan(0);
    // a rover card leads to the latest sol (no sol in the address)
    const open = screen.getByRole("link", { name: "Latest sol 5066" });
    expect(open.getAttribute("href")).toBe("/rovers/curiosity");
  });

  it("hides the hint when all rovers have data", async () => {
    mockApi({
      "/api/v2/rovers": { items: [perseverance, curiosity] },
      "/api/v2/rovers/perseverance/photos/latest": latest,
    });
    render(<App />);
    await screen.findByText("All 52 photos of sol 2031 →");
    expect(screen.queryByText(/More rovers appear/)).toBeNull();
  });

  it("links to the server-rendered developer page and API docs as plain links", async () => {
    mockApi({
      "/api/v2/rovers": { items: [perseverance] },
      "/api/v2/rovers/perseverance/photos/latest": latest,
    });
    render(<App />);
    await screen.findByText("All 52 photos of sol 2031 →");
    expect(screen.getByRole("link", { name: "Developer guide" }).getAttribute("href")).toBe(
      "/developers",
    );
    expect(screen.getAllByRole("link", { name: "API docs" })[0]?.getAttribute("href")).toBe(
      "/docs",
    );
  });

  it("shows a loading state, then the 503 state with retry", async () => {
    mockApi({
      "/api/v2/rovers": () => apiError(503, "db_unavailable", "Database unavailable"),
    });
    render(<App />);
    expect(screen.getByText("Loading rovers…")).toBeTruthy();
    expect(await screen.findByText("Service temporarily unavailable")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  });
});
