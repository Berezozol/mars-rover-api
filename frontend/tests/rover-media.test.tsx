import { fireEvent, render, screen, within } from "@testing-library/preact";
import { describe, expect, it, vi } from "vitest";
import { App } from "../src/App";
import {
  curiosity,
  ingenuity,
  mockApi,
  page,
  pending,
  perseverance,
  photo,
  spirit,
} from "./helpers";

// jsdom must not load the real web component; the page imports it lazily on demand.
vi.mock("@google/model-viewer", () => ({}));

const ALL = [perseverance, curiosity, spirit, ingenuity];
const CREDITS: Record<string, string> = {
  curiosity: "NASA/JPL-Caltech/MSSS",
  perseverance: "NASA/JPL-Caltech/MSSS",
  ingenuity: "NASA/JPL-Caltech/ASU",
};
const GLB: Record<string, string> = {
  curiosity: "24584_Curiosity_static.glb",
  perseverance: "25042_Perseverance.glb",
  ingenuity: "25043_Ingenuity_v3.glb",
  spirit: "24883_MER_static.glb",
  opportunity: "24883_MER_static.glb",
};
const FORBIDDEN = /(?<!un)official|endorsed by nasa|approved by nasa/i;

function mockHome() {
  return mockApi({
    "/api/v2/rovers": { items: [...ALL, pending] },
    "/api/v2/rovers/perseverance/photos/latest": { sol: 2031, ...page([photo(1)]) },
    "/api/v2/rovers/ingenuity/flights": { items: [] },
  });
}

function mockRoverPage(id: string) {
  const rover = [...ALL, pending].find((r) => r.id === id)!;
  return mockApi({
    "/api/v2/rovers": { items: [...ALL, pending] },
    [`/api/v2/rovers/${id}/cameras`]: { items: [] },
    [`/api/v2/rovers/${id}/photos`]: page([photo(1, { rover: id })], 1),
    [`/api/v2/rovers/${id}/photos/latest`]: { sol: rover.max_sol, ...page([]) },
  });
}

function open(path: string) {
  window.history.replaceState(null, "", path);
  return render(<App />);
}

describe("Home rover cards: photo and credit", () => {
  for (const id of ["curiosity", "perseverance", "ingenuity"]) {
    it(`${id}: image, lazy loading, alt and exact credit`, async () => {
      mockHome();
      open("/");
      const name = id[0]!.toUpperCase() + id.slice(1);
      const card = (await screen.findByRole("heading", { level: 2, name })).closest("li")!;
      const img = within(card).getByRole("img", { name: new RegExp(name, "i") });
      expect(img.tagName).toBe("IMG");
      const src = img.getAttribute("src") ?? "";
      expect(src).toContain(id);
      expect(src.endsWith(".webp")).toBe(true);
      expect(img.getAttribute("alt")?.trim()).toBeTruthy();
      expect(img.getAttribute("loading")).toBe("lazy");
      expect(
        within(card).getByText(
          (text, el) => el?.children.length === 0 && text.trim().endsWith(CREDITS[id]!),
        ),
      ).toBeTruthy();
    });
  }

  it("uses no forbidden wording on the home page", async () => {
    mockHome();
    const { container } = open("/");
    await screen.findByRole("heading", { level: 2, name: "Curiosity" });
    expect(container.querySelector("main")?.textContent ?? "").not.toMatch(FORBIDDEN);
  });
});

describe("Rover page: 3D model", () => {
  for (const id of ["curiosity", "perseverance", "ingenuity", "spirit", "opportunity"]) {
    it(`${id}: loads the viewer only after the click`, async () => {
      const m = mockRoverPage(id);
      const { container } = open(`/rovers/${id}`);
      const button = await screen.findByRole("button", { name: /^Show 3D model/ });
      expect(container.querySelector("model-viewer")).toBeNull();
      expect(m.calls.some((c) => c.pathname.endsWith(".glb"))).toBe(false);

      fireEvent.click(button);
      const viewer = await vi.waitFor(() => {
        const el = container.querySelector("model-viewer");
        expect(el).not.toBeNull();
        return el!;
      });
      expect(viewer.getAttribute("src")?.endsWith(GLB[id]!)).toBe(true);
    });
  }

  it("shows the 3D model credit with a link to science.nasa.gov", async () => {
    mockRoverPage("curiosity");
    open("/rovers/curiosity");
    await screen.findByRole("button", { name: /^Show 3D model/ });
    expect(screen.getByText(/3D model: NASA\/JPL-Caltech/)).toBeTruthy();
    const links = screen
      .getAllByRole("link")
      .filter(
        (a) => new URL(a.getAttribute("href") ?? "", "http://x").hostname === "science.nasa.gov",
      );
    expect(links.length).toBeGreaterThan(0);
  });

  it("shows an error with a link to the source page when the model fails to load", async () => {
    mockRoverPage("perseverance");
    const { container } = open("/rovers/perseverance");
    fireEvent.click(await screen.findByRole("button", { name: /^Show 3D model/ }));
    const viewer = await vi.waitFor(() => {
      const el = container.querySelector("model-viewer");
      expect(el).not.toBeNull();
      return el!;
    });
    fireEvent(viewer, new Event("error"));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/3D model|model/i);
    const link = within(alert).getByRole("link");
    expect(link.getAttribute("href")).toMatch(/^https:\/\/science\.nasa\.gov\//);
  });

  it("uses no forbidden wording on the rover page", async () => {
    mockRoverPage("curiosity");
    const { container } = open("/rovers/curiosity");
    await screen.findByRole("button", { name: /^Show 3D model/ });
    expect(container.querySelector("main")?.textContent ?? "").not.toMatch(FORBIDDEN);
  });
});
