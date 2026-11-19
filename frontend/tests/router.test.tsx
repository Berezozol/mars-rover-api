import { fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { describe, expect, it } from "vitest";
import { App } from "../src/App";
import { matchRoute } from "../src/router";
import { curiosity, mockApi, perseverance } from "./helpers";

describe("matchRoute", () => {
  it("matches the interface addresses", () => {
    expect(matchRoute("/")).toEqual({ name: "home" });
    expect(matchRoute("/rovers/Curiosity")).toEqual({ name: "photos", rover: "curiosity" });
    expect(matchRoute("/rovers/curiosity/")).toEqual({ name: "photos", rover: "curiosity" });
    expect(matchRoute("/rovers/curiosity/timeline")).toEqual({
      name: "timeline",
      rover: "curiosity",
    });
    expect(matchRoute("/photos/81234")).toEqual({ name: "photo", id: "81234" });
  });

  it("sends everything else to not found", () => {
    expect(matchRoute("/nope")).toEqual({ name: "notfound" });
    expect(matchRoute("/rovers")).toEqual({ name: "notfound" });
    expect(matchRoute("/rovers/x/timeline/extra")).toEqual({ name: "notfound" });
    expect(matchRoute("/photos")).toEqual({ name: "notfound" });
  });
});

describe("navigation", () => {
  it("shows the not-found page inside the interface", async () => {
    mockApi({ "/api/v2/rovers": { items: [perseverance] } });
    window.history.replaceState(null, "", "/no/such/page");
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeTruthy();
    expect(screen.getByText(/not affiliated with or endorsed by NASA or JPL/)).toBeTruthy();
  });

  it("navigates without a page load when a link is clicked", async () => {
    mockApi({
      "/api/v2/rovers": { items: [perseverance, curiosity] },
      "/api/v2/rovers/perseverance/photos/latest": {
        sol: 2031,
        items: [],
        page: 1,
        per_page: 6,
        total: 0,
      },
      "/api/v2/rovers/curiosity/photos": { items: [], page: 1, per_page: 24, total: 0 },
      "/api/v2/rovers/curiosity/cameras": { items: [] },
      "/api/v2/rovers/curiosity/manifest": { rover: curiosity, sols: [] },
    });
    render(<App />);
    const link = await screen.findByRole("link", { name: "Latest sol 5066" });
    fireEvent.click(link);
    await waitFor(() => expect(window.location.pathname).toBe("/rovers/curiosity"));
    expect(await screen.findByRole("heading", { name: "Curiosity photos" })).toBeTruthy();
  });
});
