import { fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { describe, expect, it, vi } from "vitest";
import { App, ErrorBoundary } from "../src/App";
import { isOwnApiUrl, safeHref, thumbSources } from "../src/lib/format";
import {
  apiError,
  curiosity,
  manifest,
  mockApi,
  page,
  perseverance,
  photo,
  spirit,
} from "./helpers";

describe("thumbnails never trigger our own converter", () => {
  const merImages = {
    small: "https://pds.example/s.jpg",
    medium: null,
    large: null,
    full: "http://localhost:8000/api/v2/photos/9/image?size=full",
  };

  it("thumbSources drops URLs inside /api/", () => {
    expect(thumbSources(merImages)).toEqual(["https://pds.example/s.jpg"]);
    expect(isOwnApiUrl("/api/v2/photos/1/image")).toBe(true);
    expect(isOwnApiUrl("https://img.example/api.jpg")).toBe(false);
  });

  it("a failed preview shows the placeholder instead of falling back to the converter", async () => {
    mockApi({
      "/api/v2/rovers": { items: [spirit] },
      "/api/v2/rovers/spirit/cameras": { items: [] },
      "/api/v2/rovers/spirit/manifest": manifest(spirit, []),
      "/api/v2/rovers/spirit/photos": page([photo(9, { rover: "spirit", images: merImages })]),
    });
    window.history.replaceState(null, "", "/rovers/spirit?sol=100");
    render(<App />);
    const img = (await screen.findAllByRole("img"))[0] as HTMLImageElement;
    fireEvent.error(img);
    expect(await screen.findByText("Preview unavailable")).toBeTruthy();
    for (const el of document.querySelectorAll("img")) {
      expect(el.getAttribute("src")).not.toContain("/api/");
    }
  });
});

describe("safeHref", () => {
  it("allows only http(s)", () => {
    expect(safeHref("https://a.example/x")).toBe("https://a.example/x");
    expect(safeHref("javascript:alert(1)")).toBeNull();
    expect(safeHref("data:text/html,x")).toBeNull();
    expect(safeHref(null)).toBeNull();
  });
});

describe("error boundary", () => {
  it("shows a message and a way home instead of a blank page", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const Boom = () => {
      throw new Error("boom");
    };
    render(
      <ErrorBoundary resetKey="/x">
        <Boom />
      </ErrorBoundary>,
    );
    expect(screen.getByText("Something went wrong")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Go to the rovers" }).getAttribute("href")).toBe("/");
  });
});

describe("stale responses", () => {
  it("does not render an outdated answer after the filters changed", async () => {
    let releaseSlow: () => void = () => {};
    const slow = new Promise<void>((r) => (releaseSlow = r));
    mockApi({
      "/api/v2/rovers": { items: [perseverance] },
      "/api/v2/rovers/perseverance/cameras": {
        items: [{ code: "SKYCAM", name: "Skycam", total_photos: 5 }],
      },
      "/api/v2/rovers/perseverance/manifest": manifest(perseverance, [
        { sol: 2031, earth_date: "2026-11-06", total_photos: 2, cameras: ["SKYCAM"] },
      ]),
      "/api/v2/rovers/perseverance/photos": async (url: URL) => {
        if (url.searchParams.get("camera") === "SKYCAM") {
          return page([photo(2, { credit: "FAST-RESULT" })], 1);
        }
        await slow; // the first, unfiltered request answers last
        return page([photo(1, { credit: "SLOW-STALE" })], 1);
      },
    });
    window.history.replaceState(null, "", "/rovers/perseverance");
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /SKYCAM/ }));
    expect(await screen.findByText("FAST-RESULT")).toBeTruthy();
    releaseSlow();
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByText("SLOW-STALE")).toBeNull();
    expect(screen.getByText("FAST-RESULT")).toBeTruthy();
  });
});

describe("network and rate-limit errors in the UI", () => {
  it("shows a network error with retry", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    render(<App />);
    expect((await screen.findAllByText("Can’t reach the server")).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: "Try again" }).length).toBeGreaterThan(0);
  });

  it("shows a 429 with the wait time", async () => {
    mockApi({
      "/api/v2/rovers": () =>
        apiError(429, "rate_limited", "Rate limit exceeded", null, { "Retry-After": "12" }),
    });
    render(<App />);
    expect((await screen.findAllByText("Too many requests")).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Try again in 12 s/).length).toBeGreaterThan(0);
  });
});

describe("timeline heat strip keyboard", () => {
  it("has a single tab stop and moves with the arrow keys", async () => {
    mockApi({
      "/api/v2/rovers": { items: [curiosity] },
      "/api/v2/rovers/curiosity/manifest": manifest(curiosity, [
        { sol: 5066, earth_date: "2026-11-07", total_photos: 10, cameras: [] },
      ]),
    });
    window.history.replaceState(null, "", "/rovers/curiosity/timeline");
    render(<App />);
    await screen.findByText("Sols with photos");
    const cells = () => [...document.querySelectorAll<HTMLButtonElement>(".heat-cells button")];
    expect(cells().filter((b) => b.tabIndex === 0)).toHaveLength(1);
    const first = cells().find((b) => b.tabIndex === 0)!;
    first.focus();
    fireEvent.keyDown(first.closest(".heat")!, { key: "ArrowLeft" });
    await waitFor(() =>
      expect(document.activeElement?.getAttribute("data-bin")).toBe(
        String(Number(first.dataset.bin) - 1),
      ),
    );
    expect(cells().filter((b) => b.tabIndex === 0)).toHaveLength(1);
  });
});
