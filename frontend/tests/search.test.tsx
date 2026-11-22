// Stage 19, screen Search (`/search`, GET /api/v2/photos). Written against docs/proposals/stage-19-frontend.md,
// docs/API.md and design/mockups/search.html, not against the implementation.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { describe, expect, it, vi } from "vitest";
import { App } from "../src/App";
import {
  apiError,
  curiosity,
  ingenuity,
  mockApi,
  page,
  perseverance,
  photo,
  spirit,
} from "./helpers";

const PHOTOS = "/api/v2/photos";
const CREDIT = "NASA/JPL-Caltech/LANL/CNES/IRAP";

const cam = (code: string, n = 100) => ({ code, name: code.replaceAll("_", " "), total_photos: n });
const CURIOSITY_CAMS = [
  "CHEMCAM_RMI",
  "FHAZ_LEFT_A",
  "FHAZ_RIGHT_A",
  "MAHLI",
  "MARDI",
  "MAST_LEFT",
  "MAST_RIGHT",
  "NAVCAM_LEFT",
  "NAVCAM_RIGHT",
  "RHAZ_LEFT_A",
  "RHAZ_RIGHT_A",
  "CHEMCAM_RMI_2",
].map((c) => cam(c));
const PERSEVERANCE_CAMS = ["NAVCAM_LEFT", "SKYCAM", "SUPERCAM_RMI"].map((c) => cam(c));

type Handler = Parameters<typeof mockApi>[0][string];

function setup(photosHandler: Handler = page([photo(10), photo(9)], 2), start = "/search") {
  const m = mockApi({
    "/api/v2/rovers": { items: [perseverance, curiosity, spirit, ingenuity] },
    "/api/v2/rovers/perseverance/cameras": { items: PERSEVERANCE_CAMS },
    "/api/v2/rovers/curiosity/cameras": { items: CURIOSITY_CAMS },
    "/api/v2/rovers/spirit/cameras": { items: [cam("NAVCAM"), cam("PANCAM")] },
    "/api/v2/rovers/ingenuity/cameras": { items: [cam("HELI_NAV"), cam("HELI_RTE")] },
    [PHOTOS]: photosHandler,
  });
  window.history.replaceState(null, "", start);
  render(<App />);
  return m;
}

/** jsdom has no layout: `(min-width: 760px)` is false, so the page behaves like a phone. */
function narrowScreen() {
  vi.stubGlobal(
    "matchMedia",
    (query: string) =>
      ({
        matches: false,
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
        addListener: () => {},
        removeListener: () => {},
        onchange: null,
        dispatchEvent: () => false,
      }) as unknown as MediaQueryList,
  );
  window.matchMedia = globalThis.matchMedia;
}

const searchCalls = (m: ReturnType<typeof mockApi>) => m.calls.filter((c) => c.pathname === PHOTOS);
const lastSearch = (m: ReturnType<typeof mockApi>) => m.last(PHOTOS);
const submit = () => fireEvent.click(screen.getByTestId("search-submit"));
const roverChip = (name: string) => screen.getByRole("button", { name: new RegExp(`^${name}`) });
const camChip = (code: string) => screen.getByRole("button", { name: new RegExp(`^${code}\\b`) });
const typeInto = (label: string, value: string) =>
  fireEvent.input(screen.getByLabelText(label), { target: { value } });

describe("Search: request and results", () => {
  it("AC-1: opens without parameters, asks newest-first for all rovers and shows credit on every tile", async () => {
    const m = setup();
    await waitFor(() => expect(screen.getAllByText(CREDIT)).toHaveLength(2));
    const q = lastSearch(m)!.searchParams;
    expect(q.get("sort")).toBe("-taken_at");
    expect(q.get("per_page")).toBeTruthy();
    expect(q.has("rovers")).toBe(false);
    expect(within(screen.getByTestId("search-results")).getAllByRole("img").length).toBeGreaterThan(
      0,
    );
  });

  it("AC-2: restores the filters from the address and sends the same parameters", async () => {
    const m = setup(
      page([photo(10)], 1),
      "/search?rovers=curiosity&camera=MAST_LEFT&golden_hour=true",
    );
    await screen.findByTestId("search-form");
    await waitFor(() => expect(lastSearch(m)).toBeTruthy());
    const q = lastSearch(m)!.searchParams;
    expect(q.get("rovers")).toBe("curiosity");
    expect(q.get("camera")).toBe("MAST_LEFT");
    expect(q.get("golden_hour")).toBe("true");
    expect(roverChip("Curiosity").getAttribute("aria-pressed")).toBe("true");
    expect(roverChip("Perseverance").getAttribute("aria-pressed")).toBe("false");
    expect(
      (await screen.findByRole("button", { name: /^MAST_LEFT/ })).getAttribute("aria-pressed"),
    ).toBe("true");
    expect((screen.getByLabelText("Golden hour") as HTMLInputElement).checked).toBe(true);
  });

  it("AC-3: chip clicks send nothing; 'Search' sends one request and updates the address", async () => {
    const m = setup();
    await screen.findByTestId("search-form");
    await waitFor(() => expect(searchCalls(m)).toHaveLength(1));
    fireEvent.click(roverChip("Spirit")); // toggles Spirit off
    // both Curiosity and Perseverance have NAVCAM_LEFT: pick the first chip
    fireEvent.click((await screen.findAllByRole("button", { name: /^NAVCAM_LEFT\b/ }))[0]!);
    typeInto("From sol", "100");
    await Promise.resolve();
    expect(searchCalls(m)).toHaveLength(1);
    expect(window.location.search).not.toContain("sol");
    submit();
    await waitFor(() => expect(searchCalls(m)).toHaveLength(2));
    await waitFor(() => expect(window.location.search).toContain("sol_min=100"));
    expect(lastSearch(m)!.searchParams.get("sol_min")).toBe("100");
    expect(lastSearch(m)!.searchParams.get("camera")).toBe("NAVCAM_LEFT");
    expect(window.location.pathname).toBe("/search");
  });

  it("AC-4: with every rover selected `rovers` is not sent; the last rover cannot be unselected", async () => {
    const m = setup();
    await screen.findByTestId("search-form");
    await waitFor(() => expect(searchCalls(m)).toHaveLength(1));
    for (const r of ["Perseverance", "Spirit", "Ingenuity"]) fireEvent.click(roverChip(r));
    submit();
    await waitFor(() => expect(searchCalls(m)).toHaveLength(2));
    expect(lastSearch(m)!.searchParams.get("rovers")).toBe("curiosity");
    fireEvent.click(roverChip("Curiosity")); // the last one: refused
    expect(roverChip("Curiosity").getAttribute("aria-pressed")).toBe("true");
    // select everything again: back to no `rovers`
    for (const r of ["Perseverance", "Spirit", "Ingenuity"]) fireEvent.click(roverChip(r));
    submit();
    await waitFor(() => expect(searchCalls(m)).toHaveLength(3));
    expect(lastSearch(m)!.searchParams.has("rovers")).toBe(false);
  });

  it("AC-5: at most 10 cameras; the 11th is blocked", async () => {
    setup(page([photo(1)], 1), "/search?rovers=curiosity");
    await screen.findByRole("button", { name: /^CHEMCAM_RMI\b/ });
    const codes = CURIOSITY_CAMS.map((c) => c.code);
    for (const c of codes.slice(0, 10)) fireEvent.click(camChip(c));
    for (const c of codes.slice(0, 10)) {
      expect(camChip(c).getAttribute("aria-pressed")).toBe("true");
    }
    const eleventh = camChip(codes[10]!);
    const blocked =
      eleventh.getAttribute("aria-disabled") === "true" || (eleventh as HTMLButtonElement).disabled;
    expect(blocked).toBe(true);
    fireEvent.click(eleventh);
    expect(camChip(codes[10]!).getAttribute("aria-pressed")).toBe("false");
  });

  it("AC-5: removing a rover drops its cameras from the choice and from the request", async () => {
    const m = setup(page([photo(1)], 1), "/search?rovers=curiosity&camera=MAHLI");
    await screen.findByRole("button", { name: /^MAHLI( |$)/ });
    await waitFor(() => expect(searchCalls(m)).toHaveLength(1));
    fireEvent.click(roverChip("Perseverance"));
    fireEvent.click(await screen.findByRole("button", { name: /^SKYCAM( |$)/ }));
    submit();
    await waitFor(() => expect(searchCalls(m)).toHaveLength(2));
    expect(lastSearch(m)!.searchParams.get("camera")).toContain("SKYCAM");
    fireEvent.click(roverChip("Perseverance"));
    await waitFor(() => expect(screen.queryByRole("button", { name: /^SKYCAM( |$)/ })).toBeNull());
    submit();
    await waitFor(() => expect(searchCalls(m)).toHaveLength(3));
    const cams = lastSearch(m)!.searchParams.get("camera") ?? "";
    expect(cams).not.toContain("SKYCAM");
    expect(cams).toContain("MAHLI");
  });

  it("AC-6: Golden hour and Custom window are exclusive in the request", async () => {
    const m = setup();
    await screen.findByTestId("search-form");
    await waitFor(() => expect(searchCalls(m)).toHaveLength(1));
    fireEvent.click(screen.getByLabelText("Custom window"));
    typeInto("From (local)", "06:00");
    typeInto("Until (local)", "07:30");
    submit();
    await waitFor(() => expect(searchCalls(m)).toHaveLength(2));
    let q = lastSearch(m)!.searchParams;
    expect(q.get("local_time_min")).toBe("06:00");
    expect(q.get("local_time_max")).toBe("07:30");
    expect(q.has("golden_hour")).toBe(false);
    fireEvent.click(screen.getByLabelText("Golden hour"));
    submit();
    await waitFor(() => expect(searchCalls(m)).toHaveLength(3));
    q = lastSearch(m)!.searchParams;
    expect(q.get("golden_hour")).toBe("true");
    expect(q.has("local_time_min")).toBe(false);
    expect(q.has("local_time_max")).toBe(false);
  });

  it("AC-6: a window with min = max is an error at the field and sends no request", async () => {
    const m = setup();
    await screen.findByTestId("search-form");
    await waitFor(() => expect(searchCalls(m)).toHaveLength(1));
    fireEvent.click(screen.getByLabelText("Custom window"));
    typeInto("From (local)", "06:00");
    typeInto("Until (local)", "06:00");
    submit();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/differ|same|equal/i);
    expect(searchCalls(m)).toHaveLength(1);
    const invalid = [screen.getByLabelText("From (local)"), screen.getByLabelText("Until (local)")];
    expect(invalid.some((i) => i.getAttribute("aria-invalid") === "true")).toBe(true);
  });

  it("AC-7: the same sol in 'from' and 'to' is sent as one `sol`", async () => {
    const m = setup();
    await screen.findByTestId("search-form");
    await waitFor(() => expect(searchCalls(m)).toHaveLength(1));
    typeInto("From sol", "1500");
    typeInto("To sol", "1500");
    submit();
    await waitFor(() => expect(searchCalls(m)).toHaveLength(2));
    const q = lastSearch(m)!.searchParams;
    expect(q.get("sol")).toBe("1500");
    expect(q.has("sol_min")).toBe(false);
    expect(q.has("sol_max")).toBe(false);
  });

  it("AC-7: a sol and an Earth date are never sent together", async () => {
    const m = setup();
    await screen.findByTestId("search-form");
    await waitFor(() => expect(searchCalls(m)).toHaveLength(1));
    typeInto("From sol", "10");
    fireEvent.click(screen.getByLabelText("Earth date"));
    typeInto("From date", "2024-01-02");
    submit();
    await waitFor(() => expect(searchCalls(m)).toHaveLength(2));
    const q = lastSearch(m)!.searchParams;
    expect(q.get("date_min")).toBe("2024-01-02");
    for (const k of ["sol", "sol_min", "sol_max"]) expect(q.has(k)).toBe(false);
  });

  it("AC-8: a camera without a sol shows the hint to pick a sol; a sol removes it", async () => {
    setup(page([photo(1)], 1), "/search?rovers=curiosity&camera=MAST_LEFT");
    expect(await screen.findByTestId("search-sol-hint")).toBeTruthy();
    typeInto("From sol", "1000");
    await waitFor(() => expect(screen.queryByTestId("search-sol-hint")).toBeNull());
  });

  it("AC-8: no camera, no hint", async () => {
    setup();
    await screen.findByTestId("search-form");
    expect(screen.queryByTestId("search-sol-hint")).toBeNull();
  });
});

describe("Search: states", () => {
  const busy = () =>
    apiError(503, "service_busy", "Server is busy, retry later", null, { "Retry-After": "5" });

  it("AC-9: 503 service_busy shows the countdown from Retry-After, disabled retry and advice", async () => {
    setup(busy);
    const box = await screen.findByTestId("search-busy");
    expect(box.textContent).toMatch(/\b5\b/);
    expect(box.textContent).toMatch(/narrow|single sol|fewer cameras/i);
    expect((screen.getByTestId("search-retry") as HTMLButtonElement).disabled).toBe(true);
  });

  it("AC-9: 'Try again' becomes available after the countdown and repeats the same request", async () => {
    let first = true;
    const m = setup(
      () =>
        first
          ? ((first = false), apiError(503, "service_busy", "busy", null, { "Retry-After": "1" }))
          : page([photo(1)], 1),
      "/search?rovers=curiosity&sol=1000",
    );
    await screen.findByTestId("search-busy");
    const retry = screen.getByTestId("search-retry") as HTMLButtonElement;
    expect(retry.disabled).toBe(true);
    await waitFor(() => expect(retry.disabled).toBe(false), { timeout: 4000 });
    const before = searchCalls(m);
    fireEvent.click(retry);
    await waitFor(() => expect(searchCalls(m)).toHaveLength(before.length + 1));
    expect(searchCalls(m).at(-1)!.search).toBe(before.at(-1)!.search);
    expect(await screen.findByText(CREDIT)).toBeTruthy();
    expect(screen.queryByTestId("search-busy")).toBeNull();
  });

  it("AC-10: 422 shows human text by field and the raw response under a disclosure", async () => {
    setup(() =>
      apiError(422, "validation_error", "Invalid query parameters", [
        { field: "local_time_max", message: "local_time_min and local_time_max must differ" },
      ]),
    );
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/time/i);
    expect(alert.textContent).toContain("validation_error");
    expect(within(alert).getByText("Response details")).toBeTruthy();
    expect(alert.querySelector("pre")?.textContent).toContain("local_time_max");
  });

  it("AC-11: an empty answer shows 'nothing found' with hints for the active filters", async () => {
    setup(page([], 0), "/search?rovers=curiosity&golden_hour=true");
    const empty = await screen.findByTestId("search-empty");
    expect(empty.textContent).toMatch(/local time|time of day|time-of-day/i);
    expect(screen.queryByText(CREDIT)).toBeNull();
  });

  it("AC-12: total above 10 000 caps the pages at 10000 / per_page and shows the limit note", async () => {
    const m = setup(page([photo(1)], 50000, 1, 24), "/search?per_page=24");
    expect(await screen.findByTestId("search-limit-note")).toBeTruthy();
    const nav = await screen.findByRole("navigation", { name: "Pagination" });
    expect(nav.textContent).toMatch(/Page 1 of 416\b/);
    expect(within(nav).queryByRole("link", { name: "Page 417" })).toBeNull();
    void m;
  });

  it("AC-12: below the limit there is no note", async () => {
    setup(page([photo(1)], 100, 1, 24));
    await screen.findByText(CREDIT);
    expect(screen.queryByTestId("search-limit-note")).toBeNull();
  });

  it("a slow older response never replaces a newer one (race)", async () => {
    const resolvers: Record<string, (r: Response | object) => void> = {};
    const m = setup(
      (url: URL) => new Promise((res) => (resolvers[url.searchParams.get("sol") ?? "none"] = res)),
    );
    await screen.findByTestId("search-form");
    await waitFor(() => expect(searchCalls(m)).toHaveLength(1));
    typeInto("From sol", "10");
    typeInto("To sol", "10");
    submit();
    await waitFor(() => expect(searchCalls(m)).toHaveLength(2));
    // the newer request (sol=10) answers first, then the older (no sol) arrives late
    resolvers["10"]!(page([photo(1, { credit: "NEW-CREDIT" })], 1));
    expect(await screen.findByText("NEW-CREDIT")).toBeTruthy();
    resolvers["none"]!(page([photo(2, { credit: "OLD-CREDIT" })], 1));
    await Promise.resolve();
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("OLD-CREDIT")).toBeNull();
    expect(screen.getByText("NEW-CREDIT")).toBeTruthy();
  });

  it("shows a loading state while waiting", async () => {
    setup(() => new Promise(() => {}));
    await screen.findByTestId("search-form");
    expect(screen.getByText(/Loading|Searching/)).toBeTruthy();
  });

  it("a network error is shown, not a blank screen", async () => {
    setup(() => {
      throw new TypeError("fetch failed");
    });
    expect(await screen.findByRole("alert")).toBeTruthy();
  });
});

describe("Search: navigation and layout", () => {
  it("AC-24: the header has Search and Stats links", async () => {
    setup();
    await screen.findByTestId("search-form");
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Search" }).getAttribute("href")).toBe("/search");
    expect(within(nav).getByRole("link", { name: "Stats" }).getAttribute("href")).toBe("/stats");
  });

  it("AC-24: on a narrow screen the filters start collapsed behind 'Filters'", async () => {
    narrowScreen();
    setup();
    const form = await screen.findByTestId("search-form");
    const toggle = within(form).getByRole("button", { name: /filters/i });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
  });

  it("AC-24: the filters collapse into a 'Filters' button with aria-expanded", async () => {
    setup();
    const form = await screen.findByTestId("search-form");
    const toggle = within(form).getByRole("button", { name: /filters/i });
    expect(toggle.getAttribute("aria-expanded")).toMatch(/true|false/);
    const was = toggle.getAttribute("aria-expanded");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).not.toBe(was);
  });

  // The horizontal-scroll and the real-data scenarios need a browser and a local server; Playwright is not set up yet.
  it.todo(
    "AC-24: at 360 px there is no horizontal page scroll (needs e2e; Playwright not installed)",
  );
  it.todo(
    "AC-25: Stats -> By camera -> Search by camera -> open a photo on the built frontend (needs e2e)",
  );
});

describe("Search: load", () => {
  it("first load never has more than 4 API requests in flight", async () => {
    let active = 0;
    let peak = 0;
    const track =
      <T,>(v: T) =>
      async () => {
        active++;
        peak = Math.max(peak, active);
        await new Promise((r) => setTimeout(r, 15));
        active--;
        return v;
      };
    mockApi({
      "/api/v2/rovers": track({ items: [perseverance, curiosity, spirit, ingenuity] }),
      "/api/v2/rovers/perseverance/cameras": track({ items: PERSEVERANCE_CAMS }),
      "/api/v2/rovers/curiosity/cameras": track({ items: CURIOSITY_CAMS }),
      "/api/v2/rovers/spirit/cameras": track({ items: [cam("NAVCAM")] }),
      "/api/v2/rovers/ingenuity/cameras": track({ items: [cam("HELI_NAV")] }),
      [PHOTOS]: track(page([photo(1)], 1)),
    });
    window.history.replaceState(null, "", "/search");
    render(<App />);
    await screen.findByText(CREDIT);
    await new Promise((r) => setTimeout(r, 60));
    expect(peak).toBeLessThanOrEqual(4);
  });
});
