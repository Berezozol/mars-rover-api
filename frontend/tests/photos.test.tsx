import { fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { describe, expect, it } from "vitest";
import { App } from "../src/App";
import { apiError, curiosity, mockApi, page, perseverance, photo } from "./helpers";

const cameras = {
  items: [
    { code: "NAVCAM_LEFT", name: "Left Navcam", total_photos: 96210 },
    { code: "SUPERCAM_RMI", name: "SuperCam RMI", total_photos: 13087 },
    { code: "SKYCAM", name: "Skycam", total_photos: 7820 },
  ],
};
// `/cameras?sol=N`: counts for that sol (SKYCAM did not shoot); without `sol`: the whole mission.
const solCameras = {
  items: [
    { code: "NAVCAM_LEFT", name: "Left Navcam", total_photos: 30 },
    { code: "SUPERCAM_RMI", name: "SuperCam RMI", total_photos: 22 },
    { code: "SKYCAM", name: "Skycam", total_photos: 0 },
  ],
};

function routes(photosHandler: Parameters<typeof mockApi>[0][string]) {
  return mockApi({
    "/api/v2/rovers": { items: [perseverance, curiosity] },
    "/api/v2/rovers/perseverance/cameras": (url: URL) =>
      url.searchParams.get("sol") ? solCameras : cameras,
    "/api/v2/rovers/perseverance/photos": photosHandler,
  });
}

describe("Photos browser", () => {
  it("opens the latest sol by default and renders tiles with credit and reserved image size", async () => {
    const m = routes(page([photo(10), photo(9, { sample_type: "mixed" })], 52));
    window.history.replaceState(null, "", "/rovers/perseverance");
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Sol 2031" })).toBeTruthy();
    await waitFor(() =>
      expect(screen.getAllByText("NASA/JPL-Caltech/LANL/CNES/IRAP")).toHaveLength(2),
    );
    expect(m.last("/api/v2/rovers/perseverance/photos")?.searchParams.get("sol")).toBe("2031");
    const img = screen.getAllByRole("img")[0] as HTMLImageElement;
    expect(img.getAttribute("src")).toBe("https://img.example/10_320.jpg"); // small first
    expect(img.getAttribute("loading")).toBe("lazy");
    expect(img.getAttribute("width")).toBe("320");
    expect(screen.getByText("mixed").className).toContain("other"); // non-filter sample type badge
    expect(screen.getByText(/1–2 of 52 photos/)).toBeTruthy();
    expect(screen.getByText(/GET \/api\/v2\/rovers\/perseverance\/photos\?sol=2031/)).toBeTruthy();
  });

  it("reserves each tile's proportions from width/height (4:3 when unknown)", async () => {
    routes(
      page([photo(10, { width: 2000, height: 1000 }), photo(9, { width: null, height: null })], 2),
    );
    window.history.replaceState(null, "", "/rovers/perseverance");
    render(<App />);
    await screen.findAllByRole("img");
    const boxes = Array.from(document.querySelectorAll<HTMLElement>(".thumb"));
    expect(boxes[0]?.style.aspectRatio).toBe("2 / 1");
    expect(boxes[1]?.style.aspectRatio).toBe(""); // CSS default 4 / 3
    expect(boxes[0]?.querySelector("img")?.getAttribute("height")).toBe("160");
  });

  it("falls back from small to medium to full when an image fails", async () => {
    routes(page([photo(10)], 1));
    window.history.replaceState(null, "", "/rovers/perseverance");
    render(<App />);
    const img = (await screen.findAllByRole("img"))[0] as HTMLImageElement;
    fireEvent.error(img);
    await waitFor(() =>
      expect((screen.getAllByRole("img")[0] as HTMLImageElement).src).toContain("_800.jpg"),
    );
    fireEvent.error(screen.getAllByRole("img")[0]!);
    await waitFor(() =>
      expect((screen.getAllByRole("img")[0] as HTMLImageElement).src).toBe(
        "https://img.example/10.png",
      ),
    );
  });

  it("uses full for Curiosity (small/medium are null)", async () => {
    mockApi({
      "/api/v2/rovers": { items: [curiosity] },
      "/api/v2/rovers/curiosity/cameras": { items: [] },
      "/api/v2/rovers/curiosity/photos": page([
        photo(5, {
          rover: "curiosity",
          images: { small: null, medium: null, large: null, full: "https://img.example/c.jpg" },
        }),
      ]),
    });
    window.history.replaceState(null, "", "/rovers/curiosity?sol=1000");
    render(<App />);
    const img = (await screen.findAllByRole("img"))[0] as HTMLImageElement;
    expect(img.src).toBe("https://img.example/c.jpg");
  });

  it("filters by camera chip and sample type through the address", async () => {
    const m = routes(page([photo(10)], 1));
    window.history.replaceState(null, "", "/rovers/perseverance");
    render(<App />);
    const chip = await screen.findByRole("button", { name: /SUPERCAM_RMI/ });
    fireEvent.click(chip);
    await waitFor(() => expect(window.location.search).toContain("camera=SUPERCAM_RMI"));
    await waitFor(() =>
      expect(m.last("/api/v2/rovers/perseverance/photos")?.searchParams.get("camera")).toBe(
        "SUPERCAM_RMI",
      ),
    );
    fireEvent.click(screen.getByLabelText("Subframe"));
    await waitFor(() => expect(window.location.search).toContain("sample_type=subframe"));
    await waitFor(() =>
      expect(m.last("/api/v2/rovers/perseverance/photos")?.searchParams.get("sample_type")).toBe(
        "subframe",
      ),
    );
    // cameras without photos on this sol sit under the disclosure
    expect(screen.getByText(/Other cameras \(1, no photos on this sol\)/)).toBeTruthy();
  });

  it("shows camera counts for the viewed sol (/cameras?sol=), not the whole mission", async () => {
    const m = routes(page([photo(10)], 52));
    window.history.replaceState(null, "", "/rovers/perseverance?sol=2031");
    render(<App />);
    const chip = await screen.findByRole("button", { name: /SUPERCAM_RMI/ });
    expect(chip.textContent).toContain("22");
    expect(chip.getAttribute("title")).toContain("22 photos on this sol");
    expect(screen.getByText(/counts are for this sol/)).toBeTruthy();
    expect(screen.getByText("52 on this sol")).toBeTruthy();
    expect(
      m.calls.some((c) => c.pathname.endsWith("/cameras") && c.searchParams.get("sol") === "2031"),
    ).toBe(true);
    expect(m.calls.some((c) => c.pathname.endsWith("/manifest"))).toBe(false);
  });

  it("an Earth-date search shows mission-wide camera counts (no ?sol=)", async () => {
    const m = routes(page([photo(10)], 1));
    window.history.replaceState(null, "", "/rovers/perseverance?earth_date=2026-11-06");
    render(<App />);
    const chip = await screen.findByRole("button", { name: /SUPERCAM_RMI/ });
    expect(chip.getAttribute("title")).toContain("across all sols");
    expect(screen.getByText(/counts are across all sols/)).toBeTruthy();
    const cams = m.calls.filter((c) => c.pathname.endsWith("/cameras"));
    expect(cams.length).toBeGreaterThan(0);
    for (const c of cams) expect(c.searchParams.get("sol")).toBeNull();
  });

  it("explains an empty sol and offers the nearest sols from nearest_sols and the timeline", async () => {
    routes(page([], 0, 1, 24, { before: 2028, after: 2030 }));
    window.history.replaceState(null, "", "/rovers/perseverance?sol=2029");
    render(<App />);
    expect(await screen.findByRole("heading", { name: "No photos on sol 2029" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "‹ Sol 2028" }).getAttribute("href")).toBe(
      "/rovers/perseverance?sol=2028",
    );
    expect(screen.getByRole("link", { name: "Sol 2030 ›" }).getAttribute("href")).toBe(
      "/rovers/perseverance?sol=2030",
    );
    expect(screen.getByRole("link", { name: "Open timeline" }).getAttribute("href")).toBe(
      "/rovers/perseverance/timeline",
    );
  });

  it("offers only the side that exists, also for an Earth-date search", async () => {
    routes(page([], 0, 1, 24, { before: null, after: 15 }));
    window.history.replaceState(null, "", "/rovers/perseverance?earth_date=2021-02-01");
    render(<App />);
    expect(await screen.findByRole("heading", { name: "No photos on 2021-02-01" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Sol 15 ›" }).getAttribute("href")).toBe(
      "/rovers/perseverance?sol=15",
    );
    expect(screen.queryByRole("link", { name: /^‹ Sol/ })).toBeNull();
  });

  it("offers a filter reset when filters hide everything", async () => {
    routes(page([], 0));
    window.history.replaceState(null, "", "/rovers/perseverance?sol=2031&camera=SKYCAM");
    render(<App />);
    expect(await screen.findByText("No photos match these filters")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Reset filters" }));
    await waitFor(() => expect(window.location.search).not.toContain("camera"));
  });

  it("shows 422 problems by field, including the list of valid cameras", async () => {
    routes(() =>
      apiError(422, "validation_error", "Invalid query parameters", [
        { field: "camera", message: "Unknown camera 'XX'. Valid cameras: NAVCAM_LEFT, SKYCAM" },
      ]),
    );
    window.history.replaceState(null, "", "/rovers/perseverance?camera=xx");
    render(<App />);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Camera");
    expect(alert.textContent).toContain("Valid cameras: NAVCAM_LEFT, SKYCAM");
    expect(alert.textContent).toContain("validation_error");
    expect(screen.getByRole("link", { name: "Go to latest sol" }).getAttribute("href")).toBe(
      "/rovers/perseverance",
    );
  });

  it("shows the 503 state with a retry that reloads", async () => {
    let fail = true;
    routes(() => (fail ? apiError(503, "db_unavailable", "down") : page([photo(1)], 1)));
    window.history.replaceState(null, "", "/rovers/perseverance");
    render(<App />);
    expect(await screen.findByText("Service temporarily unavailable")).toBeTruthy();
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("NASA/JPL-Caltech/LANL/CNES/IRAP")).toBeTruthy();
  });

  it("shows a loading skeleton while waiting", async () => {
    routes(() => new Promise(() => {}));
    window.history.replaceState(null, "", "/rovers/perseverance");
    render(<App />);
    await screen.findByRole("heading", { name: "Sol 2031" });
    expect(screen.getByText("Loading…")).toBeTruthy();
    expect(document.querySelector("#results")?.getAttribute("aria-busy")).toBe("true");
  });

  it("paginates with links that keep the filters", async () => {
    routes(page([photo(10)], 60));
    window.history.replaceState(null, "", "/rovers/perseverance?sol=2031&camera=SKYCAM");
    render(<App />);
    const next = await screen.findByRole("link", { name: "Next page" });
    expect(next.getAttribute("href")).toBe("/rovers/perseverance?sol=2031&camera=SKYCAM&page=2");
  });

  it("shows a message for an unknown rover", async () => {
    routes(page([]));
    window.history.replaceState(null, "", "/rovers/zzz");
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Rover not found" })).toBeTruthy();
  });
});
