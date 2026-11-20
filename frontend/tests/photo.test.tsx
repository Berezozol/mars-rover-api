import { fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../src/App";
import { apiError, detail, mockApi, page, perseverance, photo } from "./helpers";
import { dimensionOf, listContext, neighborsFromInSol, positionText } from "../src/pages/Photo";

describe("Photo card", () => {
  it("shows metadata, credit, source link and the image via the image endpoint", async () => {
    mockApi({
      "/api/v2/rovers": { items: [perseverance] },
      "/api/v2/photos/81234": detail(81234),
      "/api/v2/rovers/perseverance/photos": page([photo(81234), photo(81233)], 2),
    });
    window.history.replaceState(null, "", "/photos/81234");
    render(<App />);
    expect(await screen.findByRole("heading", { level: 1 })).toBeTruthy();
    expect(screen.getAllByText("NASA/JPL-Caltech/LANL/CNES/IRAP").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("SRC_81234")).toBeTruthy();
    const src = screen.getByRole("link", { name: /View on the mission/ });
    expect(src.getAttribute("href")).toBe("https://source.example/81234");
    expect(src.getAttribute("rel")).toContain("noopener");
    const main = screen.getByAltText(
      /SuperCam Remote Micro Imager frame from Perseverance, sol 2031, 12:16:25 UTC/,
    );
    // shown straight from images.* (no extra request through our API)
    expect(main.getAttribute("src")).toBe("https://img.example/81234_800.jpg");
    expect(screen.getByRole("link", { name: /Open image/ }).getAttribute("href")).toBe(
      "/api/v2/photos/81234/image?size=medium",
    );
    // switching the size goes through the endpoint too
    fireEvent.click(screen.getByLabelText("Large"));
    await waitFor(() =>
      expect(screen.getByAltText(/frame from Perseverance/).getAttribute("src")).toBe(
        "https://img.example/81234_1200.jpg",
      ),
    );
    fireEvent.click(screen.getByLabelText("Full"));
    await waitFor(() =>
      expect(screen.getByAltText(/frame from Perseverance/).getAttribute("width")).toBe("1648"),
    ); // reserved from width/height
    expect(screen.getByAltText(/frame from Perseverance/).getAttribute("height")).toBe("1200");
  });

  it("disables unavailable sizes for Curiosity (full only)", async () => {
    mockApi({
      "/api/v2/rovers": { items: [perseverance] },
      "/api/v2/photos/5": detail(5, {
        rover: "curiosity",
        images: { small: null, medium: null, large: null, full: "https://img.example/c.jpg" },
        extra: {},
        caption: null,
      }),
    });
    window.history.replaceState(null, "", "/photos/5");
    render(<App />);
    await screen.findByRole("heading", { level: 1 });
    expect((screen.getByLabelText("Small") as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText("Full") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText(/published in one size only/)).toBeTruthy();
  });

  it("steps through the sol with in_sol, passing the list's camera and sample type", async () => {
    const m = mockApi({
      "/api/v2/rovers": { items: [perseverance] },
      "/api/v2/photos/2": detail(2, {
        in_sol: { position: 17, total: 52, newer_id: 3, older_id: 1 },
      }),
      "/api/v2/rovers/perseverance/photos": page([], 0),
    });
    const from = "/rovers/perseverance?sol=2031&camera=supercam_rmi&sample_type=full&page=3";
    window.history.replaceState(null, "", "/photos/2?from=" + encodeURIComponent(from));
    render(<App />);
    const older = await screen.findByRole("link", { name: /Older/ });
    expect(older.getAttribute("href")).toContain("/photos/1?from=");
    expect(screen.getByRole("link", { name: /Newer/ }).getAttribute("href")).toContain(
      "/photos/3?from=",
    );
    expect(screen.getByText("17 of 52 on sol 2031 · SUPERCAM_RMI · full")).toBeTruthy();
    const call = m.last("/api/v2/photos/2");
    expect(call?.searchParams.get("camera")).toBe("SUPERCAM_RMI");
    expect(call?.searchParams.get("sample_type")).toBe("full");
    // no page walking for a sol list
    expect(
      m.calls.some((c) => c.pathname.endsWith("/photos") && c.pathname.includes("rovers")),
    ).toBe(false);
  });

  it("disables the edge buttons when in_sol has no neighbour there", async () => {
    mockApi({
      "/api/v2/rovers": { items: [perseverance] },
      "/api/v2/photos/2": detail(2, {
        in_sol: { position: 1, total: 52, newer_id: null, older_id: 1 },
      }),
      "/api/v2/rovers/perseverance/photos": page([], 0),
    });
    window.history.replaceState(null, "", "/photos/2");
    render(<App />);
    expect(await screen.findByRole("link", { name: /Older/ })).toBeTruthy();
    expect(screen.queryByRole("link", { name: /Newer/ })).toBeNull();
    expect(screen.getByText("1 of 52 on sol 2031")).toBeTruthy();
  });

  it("falls back to an unfiltered card when the list's camera is rejected (422)", async () => {
    let calls = 0;
    mockApi({
      "/api/v2/rovers": { items: [perseverance] },
      "/api/v2/photos/2": (url) => {
        calls += 1;
        return url.searchParams.get("camera")
          ? apiError(422, "validation_error", "bad", [{ field: "camera", message: "Unknown" }])
          : detail(2, { in_sol: { position: 2, total: 5, newer_id: 3, older_id: 1 } });
      },
      "/api/v2/rovers/perseverance/photos": page([], 0),
    });
    window.history.replaceState(
      null,
      "",
      "/photos/2?from=" + encodeURIComponent("/rovers/perseverance?sol=2031&camera=NOPE"),
    );
    render(<App />);
    // exact text: the rejected filter is not shown, and the neighbour links do not carry it
    expect(await screen.findByText("2 of 5 on sol 2031")).toBeTruthy();
    expect(calls).toBe(2);
    const href = screen.getByRole("link", { name: /Older/ }).getAttribute("href")!;
    expect(href).toContain("/photos/1?from=");
    expect(decodeURIComponent(href)).not.toContain("camera");
  });

  it("ignores a list of another rover (no filters, no from in the links)", async () => {
    const m = mockApi({
      "/api/v2/rovers": { items: [perseverance] },
      "/api/v2/photos/2": detail(2, {
        in_sol: { position: 3, total: 9, newer_id: 3, older_id: 1 },
      }),
      "/api/v2/rovers/perseverance/photos": page([], 0),
    });
    window.history.replaceState(
      null,
      "",
      "/photos/2?from=" +
        encodeURIComponent("/rovers/curiosity?sol=5&camera=NAVCAM_LEFT&sample_type=full"),
    );
    render(<App />);
    expect(await screen.findByText("3 of 9 on sol 2031")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Older/ }).getAttribute("href")).toBe("/photos/1");
    expect(m.last("/api/v2/photos/2")?.searchParams.get("camera")).toBeNull();
  });

  it("walks list pages only for Earth-date lists", async () => {
    const m = mockApi({
      "/api/v2/rovers": { items: [perseverance] },
      "/api/v2/photos/2": detail(2, {
        in_sol: { position: 1, total: 9, newer_id: null, older_id: null },
      }),
      "/api/v2/rovers/perseverance/photos": page([photo(3), photo(2), photo(1)], 3),
    });
    window.history.replaceState(
      null,
      "",
      "/photos/2?from=" + encodeURIComponent("/rovers/perseverance?earth_date=2026-11-06"),
    );
    render(<App />);
    expect(await screen.findByText("2 of 3 in this list")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Older/ }).getAttribute("href")).toContain("/photos/1");
    expect(m.last("/api/v2/photos/2")?.searchParams.get("camera")).toBeNull();
  });

  it("ignores a foreign 'from' address (still steps through the sol by in_sol)", async () => {
    const m = mockApi({
      "/api/v2/rovers": { items: [perseverance] },
      "/api/v2/photos/2": detail(2, {
        in_sol: { position: 4, total: 6, newer_id: 3, older_id: 1 },
      }),
      "/api/v2/rovers/perseverance/photos": page([photo(2)], 1),
    });
    window.history.replaceState(
      null,
      "",
      "/photos/2?from=" + encodeURIComponent("//evil.example/x"),
    );
    render(<App />);
    const older = await screen.findByRole("link", { name: /Older/ });
    expect(older.getAttribute("href")).toBe("/photos/1");
    expect(m.last("/api/v2/photos/2")?.searchParams.get("camera")).toBeNull();
  });

  it("shows the local mars time, hidden when null", async () => {
    mockApi({
      "/api/v2/rovers": { items: [perseverance] },
      "/api/v2/photos/2": detail(2, { local_time: { time: "11:25:43", kind: "lmst" } }),
      "/api/v2/photos/3": detail(3, { local_time: { time: "09:01:02", kind: "ltst" } }),
      "/api/v2/photos/4": detail(4),
      "/api/v2/rovers/perseverance/photos": page([], 0),
    });
    window.history.replaceState(null, "", "/photos/2");
    const { unmount } = render(<App />);
    expect(await screen.findByText("Local time")).toBeTruthy();
    expect(screen.getByText("LMST")).toBeTruthy();
    expect(screen.getByText(/11:25:43/)).toBeTruthy();
    unmount();
    window.history.replaceState(null, "", "/photos/3");
    const second = render(<App />);
    expect(await screen.findByText("LTST")).toBeTruthy();
    second.unmount();
    window.history.replaceState(null, "", "/photos/4");
    render(<App />);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByText("Local time")).toBeNull();
  });
});

describe("Photo list context helpers", () => {
  it("listContext reads sol lists, Earth-date lists and rejects foreign addresses", () => {
    expect(listContext("/rovers/perseverance?sol=1&camera=navcam_left&sample_type=bogus")).toEqual({
      from: "/rovers/perseverance?sol=1&camera=navcam_left&sample_type=bogus",
      byDate: false,
      filters: { camera: "NAVCAM_LEFT", sample_type: undefined },
    });
    expect(listContext("/rovers/perseverance?earth_date=2026-11-06").byDate).toBe(true);
    expect(listContext("//evil.example")).toEqual({ from: null, byDate: false, filters: {} });
    expect(listContext(null).from).toBeNull();
  });

  it("neighborsFromInSol maps newer/older and keeps a null position", () => {
    const n = neighborsFromInSol(
      { position: null, total: 4, newer_id: 9, older_id: null },
      "/rovers/x?sol=1",
    );
    expect(n.newer).toEqual({ id: 9, from: "/rovers/x?sol=1" });
    expect(n.older).toBeNull();
    expect(positionText(n, 12, "CAM", "")).toBe("Sol 12 · outside the filter of this list");
    expect(positionText(null, 12, "CAM", "")).toBe("Sol 12 · CAM");
  });

  it("dimensionOf needs both width and height", () => {
    expect(dimensionOf({ width: 1648, height: 1200 })).toEqual({ w: 1648, h: 1200 });
    expect(dimensionOf({ width: null, height: null })).toBeNull();
    expect(dimensionOf({})).toBeNull();
  });
});

describe("Photo card errors", () => {
  it("shows photo_not_found", async () => {
    mockApi({
      "/api/v2/rovers": { items: [] },
      "/api/v2/photos/9": () => apiError(404, "photo_not_found", "Photo 9 not found"),
    });
    window.history.replaceState(null, "", "/photos/9");
    render(<App />);
    expect(await screen.findByText("Photo not found")).toBeTruthy();
  });
});

describe("Spirit/Opportunity full frames (converted on demand)", () => {
  const mer = (over = {}) =>
    detail(77, {
      rover: "spirit",
      title: null,
      extra: {},
      images: {
        small: "https://pds.example/77_thumb.jpg",
        medium: null,
        large: null,
        full: "http://localhost:3000/api/v2/photos/77/image?size=full",
      },
      source: { id: "S77", link: "https://pds.example/77.IMG" },
      ...over,
    });
  const base = {
    "/api/v2/rovers": { items: [perseverance] },
    "/api/v2/rovers/spirit/photos": page([], 0),
  };

  // jsdom has no object URLs; install spies and restore the originals afterwards.
  const original = { create: URL.createObjectURL, revoke: URL.revokeObjectURL };
  afterEach(() => {
    URL.createObjectURL = original.create;
    URL.revokeObjectURL = original.revoke;
  });
  function stubObjectUrls() {
    const create = vi.fn(() => "blob:full");
    const revoke = vi.fn();
    URL.createObjectURL = create;
    URL.revokeObjectURL = revoke;
    return { create, revoke };
  }

  it("starts with the preview, waits for the converter, retries after busy, and frees the blob", async () => {
    let busy = true;
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => (release = r));
    mockApi({
      ...base,
      "/api/v2/photos/77": mer(),
      "/api/v2/photos/77/image": async () => {
        await gate;
        return busy
          ? apiError(503, "converter_busy", "busy", null, { "Retry-After": "10" })
          : new Response(new Blob(["x"]), { status: 200 });
      },
    });
    const urls = stubObjectUrls();
    window.history.replaceState(null, "", "/photos/77");
    render(<App />);
    await screen.findByRole("heading", { level: 1 });
    expect((screen.getByLabelText("Small") as HTMLInputElement).checked).toBe(true); // preview first
    expect(screen.getByRole("link", { name: /Original data file/ })).toBeTruthy();

    fireEvent.click(screen.getByLabelText("Full"));
    expect(await screen.findByText("Preparing the full-size frame…")).toBeTruthy();
    release();
    expect(await screen.findByText("The image converter is busy")).toBeTruthy();
    expect(screen.getByText(/10 s/)).toBeTruthy();

    busy = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() =>
      expect(screen.getByAltText(/Spirit/).getAttribute("src")).toBe("blob:full"),
    );
    expect(urls.revoke).not.toHaveBeenCalled();

    // leaving the full size frees the object URL
    fireEvent.click(screen.getByLabelText("Small"));
    await waitFor(() => expect(urls.revoke).toHaveBeenCalledWith("blob:full"));
  });

  it("frees the blob when the visitor leaves the page", async () => {
    mockApi({
      ...base,
      "/api/v2/photos/77": mer(),
      "/api/v2/photos/77/image": () => new Response(new Blob(["x"]), { status: 200 }),
    });
    const urls = stubObjectUrls();
    window.history.replaceState(null, "", "/photos/77");
    const { unmount } = render(<App />);
    await screen.findByRole("heading", { level: 1 });
    fireEvent.click(screen.getByLabelText("Full"));
    await waitFor(() =>
      expect(screen.getByAltText(/Spirit/).getAttribute("src")).toBe("blob:full"),
    );
    unmount();
    expect(urls.revoke).toHaveBeenCalledWith("blob:full");
  });

  it("shows a clear error when the conversion times out", async () => {
    mockApi({
      ...base,
      "/api/v2/photos/77": mer(),
      "/api/v2/photos/77/image": () => apiError(504, "upstream_timeout", "timeout"),
    });
    window.history.replaceState(null, "", "/photos/77");
    render(<App />);
    await screen.findByRole("heading", { level: 1 });
    fireEvent.click(screen.getByLabelText("Full"));
    expect(await screen.findByText("Converting the image took too long")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  });
});

describe("Photo card safety", () => {
  const routes = (d: ReturnType<typeof detail>) => ({
    "/api/v2/rovers": { items: [perseverance] },
    "/api/v2/photos/3": d,
    "/api/v2/rovers/perseverance/photos": page([], 0),
  });

  it("does not render a non-http(s) source link", async () => {
    mockApi(routes(detail(3, { source: { id: "X", link: "javascript:alert(1)" } })));
    window.history.replaceState(null, "", "/photos/3");
    render(<App />);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByRole("link", { name: /raw-image page|Original data/ })).toBeNull();
    expect(document.querySelector('a[href^="javascript:"]')).toBeNull();
  });

  it("survives a broken percent-encoding in 'from' (no blank screen)", async () => {
    mockApi(routes(detail(3)));
    window.history.replaceState(null, "", "/photos/3?from=%2Frovers%2F%25E0");
    render(<App />);
    expect(await screen.findByRole("heading", { level: 1 })).toBeTruthy();
  });
});
