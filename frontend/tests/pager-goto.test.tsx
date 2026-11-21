import { fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { describe, expect, it } from "vitest";
import { App } from "../src/App";
import { curiosity, mockApi, page, perseverance, photo } from "./helpers";

const PHOTOS = "/api/v2/rovers/perseverance/photos";

// 240 photos at 24 per page = 10 pages. The mock echoes the requested page.
function setup(total = 240, start = "/rovers/perseverance?sol=2031&camera=SKYCAM") {
  const m = mockApi({
    "/api/v2/rovers": { items: [perseverance, curiosity] },
    "/api/v2/rovers/perseverance/cameras": { items: [] },
    [PHOTOS]: (url: URL) => page([photo(10)], total, Number(url.searchParams.get("page") ?? 1)),
  });
  window.history.replaceState(null, "", start);
  render(<App />);
  return m;
}

// Assumed accessible name: a text input labelled "Go to page" and a button named "Go".
const input = () => screen.findByLabelText(/go to page/i) as Promise<HTMLInputElement>;
// The sol form already has a "Go" button, so scope the pager's one to its navigation landmark.
const pagerNav = () => screen.getByRole("navigation", { name: "Pagination" });
const goButton = () => within(pagerNav()).getByRole("button", { name: /^go$/i });

describe("Pager: go to page", () => {
  it("jumps to a middle page with the button, keeping filters and requesting that page", async () => {
    const m = setup();
    const field = await input();
    fireEvent.input(field, { target: { value: "7" } });
    fireEvent.click(goButton());
    await waitFor(() => expect(window.location.search).toContain("page=7"));
    expect(window.location.search).toContain("camera=SKYCAM");
    expect(window.location.search).toContain("sol=2031");
    await waitFor(() => expect(m.last(PHOTOS)?.searchParams.get("page")).toBe("7"));
    expect(await screen.findByText(/Page 7 of 10/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Page 7" }).getAttribute("aria-current")).toBe("page");
  });

  it("jumps with Enter in the field", async () => {
    const m = setup();
    const field = await input();
    fireEvent.input(field, { target: { value: "4" } });
    fireEvent.keyDown(field, { key: "Enter" });
    fireEvent.submit(field.closest("form") ?? field);
    await waitFor(() => expect(window.location.search).toContain("page=4"));
    await waitFor(() => expect(m.last(PHOTOS)?.searchParams.get("page")).toBe("4"));
  });

  it("shows the current page and total", async () => {
    setup(240, "/rovers/perseverance?sol=2031&page=3");
    expect(await screen.findByText(/Page 3 of 10/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Page 3" }).getAttribute("aria-current")).toBe("page");
  });

  it.each([["0"], ["11"], ["-2"], ["abc"], [""], ["2.5"]])(
    "does not request or navigate to a bogus page for input %j",
    async (value) => {
      const m = setup(240, "/rovers/perseverance?sol=2031&page=2");
      await screen.findByText(/Page 2 of 10/);
      const field = await input();
      const callsBefore = m.calls.filter((c) => c.pathname === PHOTOS).length;
      fireEvent.input(field, { target: { value } });
      fireEvent.click(goButton());
      // Either an inline error is shown or the value is clamped; never a jump to an invalid page.
      const valid = (n: number) => Number.isInteger(n) && n >= 1 && n <= 10;
      await waitFor(() => {
        const n = Number(new URLSearchParams(window.location.search).get("page") ?? 1);
        expect(valid(n)).toBe(true);
      });
      for (const c of m.calls.filter((x) => x.pathname === PHOTOS).slice(callsBefore)) {
        expect(valid(Number(c.searchParams.get("page") ?? 1))).toBe(true);
      }
      expect(screen.queryByText(/Page \d+ of 10/)).toBeTruthy();
    },
  );

  it("is not shown when there is a single page", async () => {
    setup(10, "/rovers/perseverance?sol=2031");
    await screen.findByText(/of 10 photos/);
    expect(screen.queryByLabelText(/go to page/i)).toBeNull();
    expect(screen.queryByRole("navigation", { name: "Pagination" })).toBeNull();
  });

  it("is reachable by keyboard: a focusable labelled input with a numeric range", async () => {
    setup();
    const field = await input();
    field.focus();
    expect(document.activeElement).toBe(field);
    if (field.type === "number") {
      expect(field.min).toBe("1");
      expect(field.max).toBe("10");
    }
  });
});
