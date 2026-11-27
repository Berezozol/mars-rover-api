// Package 4 of the frontend review: the shell and navigation (docs/proposals/frontend-shell-nav.md, AC-1..AC-4).
// Written against the specification, not against the implementation. Browser-only checks (375 px strip, focus
// outline, footer link height, axe, no flash on reload) live in e2e/shell-nav.spec.ts. Tests are named `shell AC-N`.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../src/App";
import { EmptyState } from "../src/components/States";
import {
  getThemeChoice,
  onSystemThemeChange,
  setThemeChoice,
  type ThemeChoice,
} from "../src/lib/theme";
import { roverViews } from "../src/lib/views";
import { matchRoute, navigate } from "../src/router";
import {
  apiError,
  curiosity,
  ingenuity,
  mockApi,
  page,
  pending,
  perseverance,
  spirit,
  manifest,
} from "./helpers";

const ROVERS = [perseverance, curiosity, spirit, pending, ingenuity];

/** Every endpoint a rover page may ask for, empty but well-formed, so that the tabs can be checked on any page. */
function roverRoutes(): Record<string, object> {
  const routes: Record<string, object> = {
    "/api/v2/rovers": { items: ROVERS },
    "/api/v2/photos": page([]),
  };
  for (const r of ROVERS) {
    const base = `/api/v2/rovers/${r.id}`;
    routes[base] = r;
    routes[`${base}/photos`] = page([]);
    routes[`${base}/photos/latest`] = { sol: null, ...page([]) };
    routes[`${base}/cameras`] = { items: [] };
    routes[`${base}/manifest`] = manifest(r, []);
    routes[`${base}/traverse`] = {
      type: "FeatureCollection",
      rover: r.id,
      available: false,
      updated_at: null,
      source_max_sol: null,
      features: [],
    };
    routes[`${base}/location`] = {
      rover: r.id,
      available: false,
      updated_at: null,
      source_max_sol: null,
      items: [],
    };
  }
  routes["/api/v2/rovers/ingenuity/flights"] = { items: [] };
  routes["/api/v2/rovers/perseverance/samples"] = {
    rover: "perseverance",
    source_url: "https://example.invalid/samples",
    source_updated_at: null,
    updated_at: null,
    items: [],
  };
  return routes;
}

type Routes = Parameters<typeof mockApi>[0];

function open(path: string, extra: Routes = {}) {
  const m = mockApi({ ...roverRoutes(), ...extra });
  window.history.replaceState(null, "", path);
  render(<App />);
  return m;
}

const mainNav = () => screen.getByRole("navigation", { name: "Main" });
const names = (root: HTMLElement) =>
  within(root)
    .getAllByRole("link")
    .map((a) => (a.textContent ?? "").trim());

// ----- AC-1: the header and the rover's row of tabs -----

describe("shell AC-1 the header", () => {
  it("shell AC-1 has exactly Rovers, Search, Data (menu), Stats, Status, API docs, in this order", async () => {
    open("/no-such-page");
    await screen.findByRole("heading", { name: "Page not found" });
    const nav = mainNav();
    const items = [...nav.querySelectorAll<HTMLElement>("a, button")]
      .filter((el) => !el.closest("ul[hidden], [hidden]"))
      .map((el) => (el.textContent ?? "").trim());
    expect(items).toHaveLength(6);
    expect(items[0]).toBe("Rovers");
    expect(items[1]).toBe("Search");
    expect(items[2]).toMatch(/^Data/);
    expect(items[3]).toBe("Stats");
    expect(items[4]).toBe("Status");
    expect(items[5]).toBe("API docs");
    expect(within(nav).getByRole("button", { name: /^Data/ })).toBeTruthy();
  });

  it("shell AC-1 has no Photos, Timeline, Route map or Flights item, whatever the rovers are", async () => {
    open("/no-such-page");
    await screen.findByRole("heading", { name: "Page not found" });
    const nav = mainNav();
    for (const gone of ["Photos", "Timeline", "Route map", "Flights"]) {
      expect(within(nav).queryByRole("link", { name: gone })).toBeNull();
    }
  });

  it("shell AC-1 the items lead to /, /search, /stats, /status and /docs", async () => {
    open("/no-such-page");
    await screen.findByRole("heading", { name: "Page not found" });
    const nav = mainNav();
    const href = (name: string) => within(nav).getByRole("link", { name }).getAttribute("href");
    expect(href("Rovers")).toBe("/");
    expect(href("Search")).toBe("/search");
    expect(href("Stats")).toBe("/stats");
    expect(href("Status")).toBe("/status");
    expect(href("API docs")).toBe("/docs");
  });

  it("shell AC-1 the header has no 'Photos' item to follow after /data/weather (no silent jump to another rover)", async () => {
    open("/data/weather", {
      "/api/v2/rovers/curiosity/weather": { items: [] },
      "/api/v2/rovers/perseverance/weather": { items: [] },
    });
    await screen.findByRole("navigation", { name: "Main" });
    expect(within(mainNav()).queryByRole("link", { name: "Photos" })).toBeNull();
    expect(screen.queryByTestId("view-tabs")).toBeNull();
  });

  it("shell AC-1 the current page is marked in the header (Status on /status)", async () => {
    open("/status", { "/health/sync": { status: "ok", rovers: [] } });
    await screen.findByTestId("status-overall");
    expect(
      within(mainNav()).getByRole("link", { name: "Status" }).getAttribute("aria-current"),
    ).toBe("page");
  });

  it("shell AC-1 the old 'Status' link is gone from the footer; For developers stays there", async () => {
    open("/no-such-page");
    await screen.findByRole("heading", { name: "Page not found" });
    expect(screen.queryByTestId("footer-status-link")).toBeNull();
    const footer = document.querySelector("footer")!;
    expect(within(footer).getByRole("link", { name: "For developers" })).toBeTruthy();
    expect(within(footer).queryByRole("link", { name: "Status" })).toBeNull();
  });

  it("shell AC-1 the footer has two labelled groups: Project and API keys", async () => {
    open("/no-such-page");
    await screen.findByRole("heading", { name: "Page not found" });
    const project = screen.getByTestId("footer-group-project");
    const keys = screen.getByTestId("footer-group-keys");
    expect(project.textContent).toContain("Project");
    expect(names(project).filter((n) => n !== "Project")).toEqual(
      expect.arrayContaining(["For developers", "API docs"]),
    );
    expect(keys.textContent).toContain("API keys");
    expect(names(keys)).toEqual(
      expect.arrayContaining(["Get an API key", "My keys", "Delete my data"]),
    );
    expect(within(keys).getByRole("link", { name: "My keys" }).getAttribute("href")).toBe(
      "/keys/manage",
    );
  });
});

describe("shell AC-1 roverViews", () => {
  it.each([
    ["curiosity", ["photos", "timeline", "map"]],
    ["perseverance", ["photos", "timeline", "map", "samples"]],
    ["ingenuity", ["photos", "timeline", "flights"]],
    ["spirit", ["photos", "timeline"]],
    ["opportunity", ["photos", "timeline"]],
  ])("shell AC-1 %s has %j", (id, views) => {
    expect(roverViews(id)).toEqual(views);
  });
});

describe("shell AC-1 the row of views is the same on every page of a rover", () => {
  type Case = {
    rover: string;
    labels: string[];
    pages: { path: string; current: string }[];
  };
  const cases: Case[] = [
    {
      rover: "curiosity",
      labels: ["Photos", "Timeline", "Route map"],
      pages: [
        { path: "/rovers/curiosity", current: "Photos" },
        { path: "/rovers/curiosity/timeline", current: "Timeline" },
        { path: "/rovers/curiosity/map", current: "Route map" },
      ],
    },
    {
      rover: "perseverance",
      labels: ["Photos", "Timeline", "Route map", "Samples"],
      pages: [
        { path: "/rovers/perseverance", current: "Photos" },
        { path: "/rovers/perseverance/timeline", current: "Timeline" },
        { path: "/rovers/perseverance/map", current: "Route map" },
        { path: "/rovers/perseverance/samples", current: "Samples" },
      ],
    },
    {
      rover: "ingenuity",
      labels: ["Photos", "Timeline", "Flights"],
      pages: [
        { path: "/rovers/ingenuity", current: "Photos" },
        { path: "/rovers/ingenuity/timeline", current: "Timeline" },
        { path: "/rovers/ingenuity/flights", current: "Flights" },
      ],
    },
    {
      rover: "spirit",
      labels: ["Photos", "Timeline"],
      pages: [
        { path: "/rovers/spirit", current: "Photos" },
        { path: "/rovers/spirit/timeline", current: "Timeline" },
      ],
    },
  ];
  const HREF: Record<string, string> = {
    Photos: "",
    Timeline: "/timeline",
    "Route map": "/map",
    Samples: "/samples",
    Flights: "/flights",
  };

  for (const c of cases) {
    for (const p of c.pages) {
      it(`shell AC-1 ${p.path}: ${c.labels.join(", ")}; ${p.current} is current; every tab stays on ${c.rover}`, async () => {
        open(p.path);
        const tabs = await screen.findByTestId("view-tabs");
        expect(tabs.tagName).toBe("NAV");
        expect(screen.getByRole("navigation", { name: /^Views of / })).toBe(tabs);
        expect(names(tabs)).toEqual(c.labels);
        for (const label of c.labels) {
          const link = within(tabs).getByRole("link", { name: label });
          expect(link.getAttribute("href")).toBe(`/rovers/${c.rover}${HREF[label]}`);
          expect(link.getAttribute("aria-current")).toBe(label === p.current ? "page" : null);
        }
      });
    }
  }

  it("shell AC-1 the row is named after its rover", async () => {
    open("/rovers/curiosity/timeline");
    const tabs = await screen.findByTestId("view-tabs");
    expect(tabs.getAttribute("aria-label")).toBe("Views of Curiosity");
  });

  it("shell AC-1 a view the rover does not have (/rovers/curiosity/samples) offers no tab for it", async () => {
    open("/rovers/curiosity/samples");
    await screen.findByText("Curiosity has no sample list");
    const tabs = screen.queryByTestId("view-tabs");
    expect(tabs).toBeNull();
  });

  it("shell AC-1 a view the rover does not have (/rovers/ingenuity/map) offers no Route map tab", async () => {
    open("/rovers/ingenuity/map");
    await screen.findByText("No location data for Ingenuity");
    const tabs = screen.queryByTestId("view-tabs");
    // the page keeps Ingenuity's own row (tests/ingenuity.test.tsx relies on it); it just has no Route map tab
    expect(tabs).not.toBeNull();
    expect(within(tabs!).queryByRole("link", { name: "Route map" })).toBeNull();
  });

  it("shell AC-1 there is one copy of the row per page (the pages no longer draw their own)", async () => {
    open("/rovers/perseverance/map");
    await screen.findByTestId("view-tabs");
    expect(screen.getAllByRole("navigation", { name: "Views of Perseverance" })).toHaveLength(1);
    expect(document.querySelectorAll("[data-testid=view-tabs]")).toHaveLength(1);
  });

  it("shell AC-1 Perseverance has no Flights tab (it used to lead to Ingenuity)", async () => {
    open("/rovers/perseverance/map");
    const tabs = await screen.findByTestId("view-tabs");
    expect(within(tabs).queryByRole("link", { name: "Flights" })).toBeNull();
  });
});

// ----- AC-3: headings, /photos/abc, focus after a link -----

describe("shell AC-3 EmptyState level", () => {
  it.each([
    [undefined, 3],
    [1, 1],
    [2, 2],
    [3, 3],
  ] as const)("shell AC-3 level %s renders an h%s", (level, expected) => {
    render(<EmptyState title="Nothing here" level={level} />);
    expect(screen.getByRole("heading", { name: "Nothing here", level: expected })).toBeTruthy();
  });
});

describe("shell AC-3 /photos/<id> accepts only a positive whole number", () => {
  it.each(["abc", "-1", "0", "01", "1.5", "12345678901", "1e3", "%20"])(
    "shell AC-3 /photos/%s is not found",
    (id) => {
      expect(matchRoute(`/photos/${id}`)).toEqual({ name: "notfound" });
    },
  );
  it.each(["1", "81234", "9999999999"])("shell AC-3 /photos/%s is a photo", (id) => {
    expect(matchRoute(`/photos/${id}`)).toEqual({ name: "photo", id });
  });
});

describe("shell AC-3 one h1 on a placeholder page", () => {
  const nosuch = (path: string) => `/api/v2/rovers/nosuch/${path}`;
  const gone = () => apiError(404, "not_found", "no such rover");

  it.each(["/no-such-page", "/photos/abc", "/photos/-1"])(
    "shell AC-3 %s: exactly one h1, 'Page not found'",
    async (path) => {
      const m = open(path);
      expect(await screen.findByRole("heading", { level: 1, name: "Page not found" })).toBeTruthy();
      expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
      expect(m.calls.filter((c) => c.pathname.startsWith("/api/v2/photos"))).toEqual([]);
    },
  );

  it.each(["", "/timeline", "/map", "/samples", "/flights"])(
    "shell AC-3 /rovers/nosuch%s: exactly one h1, 'Rover not found'",
    async (suffix) => {
      open(`/rovers/nosuch${suffix}`, {
        "/api/v2/rovers/nosuch": gone,
        [nosuch("photos")]: gone,
        [nosuch("photos/latest")]: gone,
        [nosuch("cameras")]: gone,
        [nosuch("manifest")]: gone,
        [nosuch("traverse")]: gone,
        [nosuch("location")]: gone,
        [nosuch("samples")]: gone,
        [nosuch("flights")]: gone,
      });
      expect(
        await screen.findByRole("heading", { level: 1, name: "Rover not found" }),
      ).toBeTruthy();
      expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    },
  );
});

describe("shell AC-3 focus after a navigation", () => {
  it("shell AC-3 a link to a page moves focus to its h1 (tabindex -1)", async () => {
    open("/no-such-page");
    await screen.findByRole("heading", { level: 1, name: "Page not found" });
    fireEvent.click(within(mainNav()).getByRole("link", { name: "Search" }));
    const h1 = await screen.findByRole("heading", { level: 1 });
    expect(h1.textContent).not.toBe("Page not found");
    await waitFor(() => expect(document.activeElement).toBe(h1));
    expect(h1.getAttribute("tabindex")).toBe("-1");
  });

  it("shell AC-3 a page whose h1 comes later: focus waits on <main>, then moves to the h1 that appears", async () => {
    let release: (r: Response) => void = () => {};
    const slow = new Promise<Response>((resolve) => (release = resolve));
    open("/no-such-page", { "/api/v2/photos/777": () => slow });
    await screen.findByRole("heading", { level: 1, name: "Page not found" });
    navigate("/photos/777");
    await waitFor(() => expect(screen.queryByRole("heading", { level: 1 })).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(document.querySelector("main")));
    release(
      new Response(JSON.stringify({ ...detailFor(777) }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const h1 = await screen.findByRole("heading", { level: 1 });
    await waitFor(() => expect(document.activeElement).toBe(h1));
  });

  it("shell AC-3 if the visitor moved focus away while the page loaded, the late h1 does not take it back", async () => {
    let release: (r: Response) => void = () => {};
    const slow = new Promise<Response>((resolve) => (release = resolve));
    open("/no-such-page", { "/api/v2/photos/778": () => slow });
    await screen.findByRole("heading", { level: 1, name: "Page not found" });
    navigate("/photos/778");
    await waitFor(() => expect(document.activeElement).toBe(document.querySelector("main")));
    const select = screen.getByLabelText("Theme") as HTMLSelectElement;
    select.focus();
    expect(document.activeElement).toBe(select);
    release(
      new Response(JSON.stringify({ ...detailFor(778) }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await screen.findByRole("heading", { level: 1 });
    expect(document.activeElement).toBe(select);
  });

  it("shell AC-3 the focus watcher stops after 2 s: an h1 that comes later leaves focus on <main>", async () => {
    let release: (r: Response) => void = () => {};
    const slow = new Promise<Response>((resolve) => (release = resolve));
    open("/no-such-page", { "/api/v2/photos/779": () => slow });
    await screen.findByRole("heading", { level: 1, name: "Page not found" });
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    try {
      navigate("/photos/779");
      await vi.advanceTimersByTimeAsync(2500);
      expect(document.activeElement).toBe(document.querySelector("main"));
      release(
        new Response(JSON.stringify(detailFor(779)), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
      await vi.advanceTimersByTimeAsync(50);
    } finally {
      vi.useRealTimers();
    }
    await screen.findByRole("heading", { level: 1 });
    expect(document.activeElement).toBe(document.querySelector("main"));
  });

  it("shell AC-3 Back does not move focus to the h1", async () => {
    open("/no-such-page");
    await screen.findByRole("heading", { level: 1, name: "Page not found" });
    fireEvent.click(within(mainNav()).getByRole("link", { name: "Search" }));
    const h1 = await screen.findByRole("heading", { level: 1 });
    await waitFor(() => expect(document.activeElement).toBe(h1));
    window.history.back();
    const back = await screen.findByRole("heading", { level: 1, name: "Page not found" });
    await waitFor(() => expect(window.location.pathname).toBe("/no-such-page"));
    expect(document.activeElement).not.toBe(back);
  });
});

function detailFor(id: number) {
  return {
    id,
    rover: "perseverance",
    sol: 10,
    earth_date: "2021-03-01",
    taken_at: "2021-03-01T12:00:00Z",
    camera: { code: "NAVCAM_LEFT", name: "Navigation Camera - Left" },
    sample_type: "full",
    title: `Photo ${id}`,
    img_src: `https://img.example/${id}.png`,
    images: {
      small: `https://img.example/${id}_320.jpg`,
      medium: `https://img.example/${id}_800.jpg`,
      large: `https://img.example/${id}_1200.jpg`,
      full: `https://img.example/${id}.png`,
    },
    credit: "NASA/JPL-Caltech",
    source: { id: `SRC_${id}`, link: `https://source.example/${id}` },
    width: 1024,
    height: 768,
    caption: null,
    received_at: "2021-03-01T13:00:00Z",
    site: null,
    drive: null,
    extra: {},
    location: null,
    local_time: null,
    in_sol: null,
  };
}

// ----- AC-4: the theme -----

const KEY = "sol-index-theme";

/** A controllable `prefers-color-scheme` (jsdom has no matchMedia). */
function stubSystemTheme(dark: boolean) {
  let isDark = dark;
  const listeners = new Set<(e: MediaQueryListEvent) => void>();
  const forDark = (q: string) => /prefers-color-scheme:\s*dark/.test(q);
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      get matches() {
        return forDark(query) ? isDark : false;
      },
      media: query,
      onchange: null,
      addEventListener: (_: string, cb: (e: MediaQueryListEvent) => void) => {
        if (forDark(query)) listeners.add(cb);
      },
      removeEventListener: (_: string, cb: (e: MediaQueryListEvent) => void) => {
        listeners.delete(cb);
      },
      addListener: (cb: (e: MediaQueryListEvent) => void) => {
        if (forDark(query)) listeners.add(cb);
      },
      removeListener: (cb: (e: MediaQueryListEvent) => void) => {
        listeners.delete(cb);
      },
      dispatchEvent: () => true,
    })),
  );
  return {
    set(next: boolean) {
      isDark = next;
      for (const cb of [...listeners]) {
        cb({ matches: next, media: "(prefers-color-scheme: dark)" } as MediaQueryListEvent);
      }
    },
    count: () => listeners.size,
  };
}

const html = () => document.documentElement;

describe("shell AC-4 the theme choice (library)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    html().removeAttribute("data-theme");
    stubSystemTheme(false);
  });

  it("shell AC-4 nothing saved: Auto", () => {
    expect(getThemeChoice()).toBe("auto");
  });

  it.each(["light", "dark"] as ThemeChoice[])(
    "shell AC-4 setThemeChoice(%s) sets html[data-theme] and the key",
    (choice) => {
      setThemeChoice(choice);
      expect(html().getAttribute("data-theme")).toBe(choice);
      expect(window.localStorage.getItem(KEY)).toBe(choice);
      expect(getThemeChoice()).toBe(choice);
    },
  );

  it("shell AC-4 setThemeChoice('auto') removes the attribute and the key", () => {
    setThemeChoice("dark");
    setThemeChoice("auto");
    expect(html().hasAttribute("data-theme")).toBe(false);
    expect(window.localStorage.getItem(KEY)).toBeNull();
    expect(getThemeChoice()).toBe("auto");
  });

  it("shell AC-4 a saved value that is neither light nor dark counts as Auto", () => {
    window.localStorage.setItem(KEY, "purple");
    expect(getThemeChoice()).toBe("auto");
  });

  it("shell AC-4 onSystemThemeChange calls back when the system theme changes", () => {
    const system = stubSystemTheme(false);
    const cb = vi.fn();
    onSystemThemeChange(cb);
    expect(system.count()).toBeGreaterThan(0);
    system.set(true);
    expect(cb).toHaveBeenCalled();
  });
});

describe("shell AC-4 the theme select", () => {
  let system: ReturnType<typeof stubSystemTheme>;
  beforeEach(() => {
    window.localStorage.clear();
    html().removeAttribute("data-theme");
    system = stubSystemTheme(false);
  });

  const select = () => screen.getByLabelText("Theme") as HTMLSelectElement;
  const optionValue = (label: string) =>
    [...select().options].find((o) => o.textContent === label)!.value;
  const chosen = () => select().selectedOptions[0]?.textContent;
  async function ready() {
    await screen.findByRole("heading", { level: 1, name: "Page not found" });
  }
  const pick = (label: string) =>
    fireEvent.change(select(), { target: { value: optionValue(label) } });

  it("shell AC-4 is a select#theme-select labelled Theme with Auto, Light and Dark; the old button is gone", async () => {
    open("/no-such-page");
    await ready();
    expect(select().id).toBe("theme-select");
    expect(select().tagName).toBe("SELECT");
    expect([...select().options].map((o) => o.textContent)).toEqual(["Auto", "Light", "Dark"]);
    expect(document.querySelector(".theme-toggle")).toBeNull();
    expect(screen.queryByRole("button", { name: /^Switch to/ })).toBeNull();
  });

  it("shell AC-4 shows Auto when nothing is saved", async () => {
    open("/no-such-page");
    await ready();
    expect(chosen()).toBe("Auto");
  });

  it.each(["Light", "Dark"])(
    "shell AC-4 a saved %s is shown as the current choice",
    async (label) => {
      window.localStorage.setItem(KEY, label.toLowerCase());
      open("/no-such-page");
      await ready();
      expect(chosen()).toBe(label);
    },
  );

  it("shell AC-4 Dark and Light set html[data-theme] and the saved key", async () => {
    open("/no-such-page");
    await ready();
    pick("Dark");
    expect(html().getAttribute("data-theme")).toBe("dark");
    expect(window.localStorage.getItem(KEY)).toBe("dark");
    expect(chosen()).toBe("Dark");
    pick("Light");
    expect(html().getAttribute("data-theme")).toBe("light");
    expect(window.localStorage.getItem(KEY)).toBe("light");
  });

  it("shell AC-4 Auto removes the attribute and the key (back to the system theme)", async () => {
    open("/no-such-page");
    await ready();
    pick("Dark");
    pick("Auto");
    expect(html().hasAttribute("data-theme")).toBe(false);
    expect(window.localStorage.getItem(KEY)).toBeNull();
    expect(chosen()).toBe("Auto");
  });

  it("shell AC-4 under Auto a change of the system theme is followed and the choice stays Auto", async () => {
    open("/no-such-page");
    await ready();
    system.set(true);
    await waitFor(() => expect(chosen()).toBe("Auto"));
    expect(html().hasAttribute("data-theme")).toBe(false);
    expect(window.localStorage.getItem(KEY)).toBeNull();
    system.set(false);
    expect(chosen()).toBe("Auto");
  });

  it("shell AC-4 under an explicit choice the system theme does not override it", async () => {
    open("/no-such-page");
    await ready();
    pick("Light");
    system.set(true);
    expect(html().getAttribute("data-theme")).toBe("light");
    expect(chosen()).toBe("Light");
  });

  it("shell AC-4 a choice made in another tab (storage event) is applied here", async () => {
    open("/no-such-page");
    await ready();
    window.localStorage.setItem(KEY, "dark");
    window.dispatchEvent(
      new StorageEvent("storage", { key: KEY, newValue: "dark", oldValue: null }),
    );
    await waitFor(() => expect(html().getAttribute("data-theme")).toBe("dark"));
    await waitFor(() => expect(chosen()).toBe("Dark"));
    window.localStorage.removeItem(KEY);
    window.dispatchEvent(
      new StorageEvent("storage", { key: KEY, newValue: null, oldValue: "dark" }),
    );
    await waitFor(() => expect(html().hasAttribute("data-theme")).toBe(false));
    await waitFor(() => expect(chosen()).toBe("Auto"));
  });

  it("shell AC-4 a storage event for another key changes nothing", async () => {
    open("/no-such-page");
    await ready();
    window.dispatchEvent(new StorageEvent("storage", { key: "other", newValue: "dark" }));
    expect(chosen()).toBe("Auto");
    expect(html().hasAttribute("data-theme")).toBe(false);
  });

  it("shell AC-4 the select has a visible-to-assistive-tech label (a 'Theme' label bound to it)", async () => {
    open("/no-such-page");
    await ready();
    const label = document.querySelector('label[for="theme-select"]');
    expect(label?.textContent?.trim()).toBe("Theme");
  });
});
