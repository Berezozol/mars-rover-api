import { fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../src/App";
import { copyText, feedPath, feedUrl, hasFeed, setFeedLink } from "../src/lib/feed";
import {
  curiosity,
  detail,
  ingenuity,
  mockApi,
  page,
  perseverance,
  photo,
  spirit,
} from "./helpers";

function open(path: string) {
  window.history.replaceState(null, "", path);
  return render(<App />);
}

const alternates = () =>
  [
    ...document.head.querySelectorAll('link[rel="alternate"][data-sol-index-feed]'),
  ] as HTMLLinkElement[];

describe("feed URL building", () => {
  it("omits defaults and upper-cases the camera", () => {
    expect(feedPath("perseverance")).toBe("/api/v2/rovers/perseverance/feed.xml");
    expect(feedPath("perseverance", { camera: "navcam_left", sample_type: "full" })).toBe(
      "/api/v2/rovers/perseverance/feed.xml?camera=NAVCAM_LEFT&sample_type=full",
    );
    expect(feedPath("curiosity", { group: "sol" })).toBe("/api/v2/rovers/curiosity/feed.xml");
    expect(feedPath("curiosity", { group: "photo" })).toBe(
      "/api/v2/rovers/curiosity/feed.xml?group=photo",
    );
  });

  it("is absolute, from the current origin", () => {
    expect(feedUrl("curiosity", { camera: "MAST_LEFT" })).toBe(
      `${window.location.origin}/api/v2/rovers/curiosity/feed.xml?camera=MAST_LEFT`,
    );
  });

  it("only sync rovers have a feed", () => {
    expect(hasFeed(perseverance)).toBe(true);
    expect(hasFeed(curiosity)).toBe(true);
    expect(hasFeed(spirit)).toBe(false);
    expect(hasFeed(ingenuity)).toBe(false);
    expect(hasFeed({ data_source: null })).toBe(false);
  });
});

describe("copyText", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("uses the clipboard when available", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    expect(await copyText("x", null)).toBe("copied");
    expect(writeText).toHaveBeenCalledWith("x");
  });

  it("selects the field when the clipboard is unavailable or refused", async () => {
    const input = document.createElement("input");
    document.body.appendChild(input);
    const select = vi.spyOn(input, "select");
    vi.stubGlobal("navigator", {
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error("no")) },
    });
    expect(await copyText("x", input)).toBe("selected");
    expect(select).toHaveBeenCalled();
    vi.stubGlobal("navigator", {});
    expect(await copyText("x", input)).toBe("selected");
    input.remove();
  });
});

describe("head link", () => {
  it("replaces and removes the page-specific alternate link", () => {
    const remove1 = setFeedLink("/a.xml", "A");
    const remove2 = setFeedLink("/b.xml", "B");
    expect(alternates().map((l) => l.getAttribute("href"))).toEqual(["/b.xml"]);
    remove1();
    expect(alternates()).toHaveLength(1);
    remove2();
    expect(alternates()).toHaveLength(0);
  });
});

describe("static head links", () => {
  it("hides the static link of the same rover while the page link exists", () => {
    const stat = (r: string) => {
      const l = document.createElement("link");
      l.rel = "alternate";
      l.setAttribute("data-static-feed", r);
      document.head.appendChild(l);
      return l;
    };
    const p = stat("perseverance");
    const c = stat("curiosity");
    const remove = setFeedLink("/p.xml", "P", "perseverance");
    expect(p.isConnected).toBe(false);
    expect(c.isConnected).toBe(true);
    remove();
    expect(p.isConnected).toBe(true);
    p.remove();
    c.remove();
  });
});

describe("Subscribe on the photos page", () => {
  const routes = (rovers = [perseverance, spirit]) => ({
    "/api/v2/rovers": { items: rovers },
    "/api/v2/rovers/perseverance/photos": page([photo(1)]),
    "/api/v2/rovers/perseverance/cameras": { items: [] },
    "/api/v2/rovers/spirit/photos": page([photo(2, { rover: "spirit" })]),
    "/api/v2/rovers/spirit/cameras": { items: [] },
  });

  it("follows the filters, switches grouping, copies and closes on Esc", async () => {
    mockApi(routes());
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    open("/rovers/perseverance?camera=navcam_left&sample_type=full");
    const button = await screen.findByRole("button", { name: /Subscribe \(RSS\)/ });
    expect(button.getAttribute("aria-expanded")).toBe("false");
    await waitFor(() => expect(alternates()).toHaveLength(1));
    expect(alternates()[0]?.getAttribute("href")).toBe(
      `${window.location.origin}/api/v2/rovers/perseverance/feed.xml?camera=NAVCAM_LEFT&sample_type=full`,
    );

    fireEvent.click(button);
    const field = (await screen.findByLabelText("Feed link")) as HTMLInputElement;
    expect(field.readOnly).toBe(true);
    expect(field.value).toBe(
      `${window.location.origin}/api/v2/rovers/perseverance/feed.xml?camera=NAVCAM_LEFT&sample_type=full`,
    );
    expect(document.activeElement).toBe(field);
    expect(screen.getByText("RSS: Perseverance · NAVCAM_LEFT · full")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("One per photo"));
    expect(field.value).toContain("&group=photo");

    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() =>
      expect(document.getElementById("sub-status")?.textContent).toMatch(/Copied/),
    );
    expect(writeText).toHaveBeenCalledWith(field.value);

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByLabelText("Feed link")).toBeNull());
    expect(document.activeElement).toBe(button);
  });

  it("shows the fallback hint when the clipboard is unavailable", async () => {
    mockApi(routes());
    vi.stubGlobal("navigator", {});
    open("/rovers/perseverance");
    fireEvent.click(await screen.findByRole("button", { name: /Subscribe \(RSS\)/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Copy" }));
    await waitFor(() =>
      expect(document.getElementById("sub-status")?.textContent).toMatch(/Ctrl\+C/),
    );
  });

  it("closes on outside click", async () => {
    mockApi(routes());
    open("/rovers/perseverance");
    fireEvent.click(await screen.findByRole("button", { name: /Subscribe \(RSS\)/ }));
    await screen.findByLabelText("Feed link");
    fireEvent.click(document.body);
    await waitFor(() => expect(screen.queryByLabelText("Feed link")).toBeNull());
  });

  it("is not rendered for archive rovers, and the head link goes away on leaving", async () => {
    mockApi(routes());
    const view = open("/rovers/perseverance");
    await screen.findByRole("button", { name: /Subscribe \(RSS\)/ });
    await waitFor(() => expect(alternates()).toHaveLength(1));
    view.unmount();
    expect(alternates()).toHaveLength(0);

    open("/rovers/spirit");
    await screen.findByRole("heading", { name: "Spirit photos" });
    expect(screen.queryByRole("button", { name: /Subscribe/ })).toBeNull();
    expect(alternates()).toHaveLength(0);
  });
});

describe("Subscribe to this camera on the photo card", () => {
  it("links to the camera feed for sync rovers only", async () => {
    mockApi({
      "/api/v2/photos/10": detail(10),
      "/api/v2/rovers/perseverance/photos": page([photo(10)]),
      "/api/v2/photos/20": detail(20, { rover: "spirit" }),
    });
    const view = open("/photos/10");
    const link = await screen.findByRole("link", { name: /Subscribe to this camera/ });
    expect(link.getAttribute("href")).toBe(
      "/api/v2/rovers/perseverance/feed.xml?camera=SUPERCAM_RMI",
    );
    view.unmount();

    open("/photos/20");
    await screen.findByText("Credit & source");
    expect(screen.queryByRole("link", { name: /Subscribe to this camera/ })).toBeNull();
  });
});
