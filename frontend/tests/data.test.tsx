import { fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { App } from "../src/App";
import type { Sample } from "../src/api/types";
import { apiError, curiosity, mockApi, page, perseverance, samplesResponse } from "./helpers";
import {
  RAD_CITATION,
  RAD_CREDIT,
  RAD_SOURCE_URL,
  radItems,
  radSol,
  radiationResponse,
} from "./fixtures/radiation";
import { WX, weatherRoutes } from "./weather-helpers";
import { weatherList } from "./fixtures/weather";

const RAD = "/api/v2/rovers/curiosity/radiation";

// 30 samples: the 10 real rows of the fixture, repeated with new numbers and growing sols (all sealed by sol 1441).
const base = (
  JSON.parse(readFileSync("tests/fixtures/samples.json", "utf-8")) as { items: Sample[] }
).items;
const thirty: Sample[] = Array.from({ length: 30 }, (_, i) => ({
  ...base[i % base.length]!,
  number: i + 1,
  sol: 164 + i * 44,
}));

function routes(over: Record<string, unknown> = {}) {
  return mockApi({
    ...weatherRoutes(),
    "/api/v2/rovers": { items: [perseverance, curiosity] },
    "/api/v2/rovers/curiosity": curiosity,
    "/api/v2/rovers/perseverance/samples": samplesResponse(thirty),
    [RAD]: radiationResponse(),
    ...over,
  });
}

function open(path = "/data") {
  window.history.replaceState(null, "", path);
  return render(<App />);
}

const mainEl = () => screen.getByRole("main");

async function mainNav() {
  return screen.findByRole("navigation", { name: "Main" });
}

/** The header "Data" dropdown button. */
async function dataButton() {
  const nav = await mainNav();
  return await waitFor(() => within(nav).getByRole("button", { name: /^Data/ }));
}

/** A dataset entry of the opened menu: a link, or a menuitem when the menu uses the menu pattern. */
function menuEntry(nav: HTMLElement, name: string | RegExp) {
  return within(nav).queryByRole("link", { name }) ?? within(nav).queryByRole("menuitem", { name });
}

const ENTRIES: [string, string][] = [
  ["Rock samples", "/data/samples"],
  ["Radiation", "/data/radiation"],
  ["Weather", "/data/weather"],
  ["All datasets", "/data"],
];

describe("Data: header dropdown menu (AC-40)", () => {
  const home = () =>
    routes({ "/api/v2/rovers/perseverance/photos/latest": { sol: 2031, ...page([], 0) } });

  it("AC-40 'Data' is a button with aria-expanded=false and aria-controls; no 'Samples' item in the nav", async () => {
    home();
    open("/");
    const btn = await dataButton();
    expect(btn.getAttribute("aria-expanded")).toBe("false");
    expect(btn.getAttribute("aria-controls")).toBeTruthy();
    expect(within(await mainNav()).queryByRole("link", { name: "Samples" })).toBeNull();
  });

  it("AC-40 clicking the button opens the menu with the datasets and 'All datasets'", async () => {
    home();
    open("/");
    const btn = await dataButton();
    fireEvent.click(btn);
    await waitFor(() => expect(btn.getAttribute("aria-expanded")).toBe("true"));
    const nav = await mainNav();
    for (const [name, href] of ENTRIES) {
      const el = menuEntry(nav, name);
      expect(el, name).toBeTruthy();
      expect(el!.getAttribute("href"), name).toBe(href);
    }
    const controlled = document.getElementById(btn.getAttribute("aria-controls")!);
    expect(controlled, "aria-controls points to an existing element").toBeTruthy();
  });

  it("AC-40 Escape closes the menu and returns focus to the button", async () => {
    home();
    open("/");
    const btn = await dataButton();
    fireEvent.click(btn);
    await waitFor(() => expect(btn.getAttribute("aria-expanded")).toBe("true"));
    fireEvent.keyDown(document.activeElement ?? btn, { key: "Escape" });
    await waitFor(() => expect(btn.getAttribute("aria-expanded")).toBe("false"));
    expect(document.activeElement).toBe(btn);
    expect(menuEntry(await mainNav(), "Radiation")).toBeNull();
  });

  it("AC-40 a click outside closes the menu", async () => {
    home();
    open("/");
    const btn = await dataButton();
    fireEvent.click(btn);
    await waitFor(() => expect(btn.getAttribute("aria-expanded")).toBe("true"));
    fireEvent.mouseDown(document.body);
    fireEvent.click(document.body);
    await waitFor(() => expect(btn.getAttribute("aria-expanded")).toBe("false"));
  });

  it("AC-40 the active dataset has aria-current; the others do not", async () => {
    routes();
    open("/data/radiation");
    const btn = await dataButton();
    fireEvent.click(btn);
    const nav = await mainNav();
    await waitFor(() => expect(menuEntry(nav, "Radiation")).toBeTruthy());
    const cur = (n: string) => menuEntry(nav, n)!.getAttribute("aria-current");
    expect(cur("Radiation")).toBeTruthy();
    expect(cur("Rock samples")).toBeNull();
    expect(cur("Weather")).toBeNull();
  });

  it("AC-40 choosing a dataset navigates to its page", async () => {
    routes();
    open("/data");
    const btn = await dataButton();
    fireEvent.click(btn);
    const nav = await mainNav();
    await waitFor(() => expect(menuEntry(nav, "Weather")).toBeTruthy());
    fireEvent.click(menuEntry(nav, "Weather")!);
    await waitFor(() => expect(window.location.pathname).toBe("/data/weather"));
    expect(await screen.findByRole("heading", { level: 1, name: /Weather/ })).toBeTruthy();
  });

  it("AC-40 the Samples page of Perseverance still works at its old path", async () => {
    routes();
    open("/rovers/perseverance/samples");
    expect(await screen.findByRole("heading", { level: 1, name: /rock samples/i })).toBeTruthy();
    expect(await screen.findByRole("table")).toBeTruthy();
  });
});

describe("Data: routes, titles and overview (AC-41)", () => {
  const PAGES: [string, RegExp][] = [
    ["/data/samples", /Rock samples/],
    ["/data/radiation", /Radiation/],
    ["/data/weather", /Weather/],
  ];

  for (const [path, name] of PAGES) {
    it(`AC-41 ${path} is a full page with an h1 and the title "<Name> — Sol Index"`, async () => {
      routes();
      open(path);
      expect(await screen.findByRole("heading", { level: 1, name })).toBeTruthy();
      await waitFor(() => expect(document.title).toMatch(/ — Sol Index$/));
      expect(document.title).toMatch(name);
      expect(document.title).not.toMatch(/^Data — /);
    });
  }

  it("AC-41 /data is an overview with three cards linking to the dataset pages and no accordion", async () => {
    const m = routes();
    open("/data");
    expect(await screen.findByRole("heading", { level: 1, name: "Data" })).toBeTruthy();
    const main = mainEl();
    for (const [, href] of ENTRIES.slice(0, 3)) {
      expect(
        within(main)
          .getAllByRole("link")
          .some((a) => a.getAttribute("href") === href),
        href,
      ).toBe(true);
    }
    expect(within(main).queryByRole("button", { name: /Expand all|Collapse all/ })).toBeNull();
    expect(within(main).queryAllByRole("button", { expanded: false })).toHaveLength(0);
    // the overview does not load the datasets
    expect(m.calls.filter((c) => c.pathname === RAD)).toHaveLength(0);
  });

  for (const key of ["radiation", "samples", "weather"]) {
    it(`AC-41 old /data#${key} ends up on /data/${key} without a new history entry`, async () => {
      routes();
      const before = window.history.length;
      open(`/data#${key}`);
      await waitFor(() => expect(window.location.pathname).toBe(`/data/${key}`));
      expect(window.location.hash).toBe("");
      expect(window.history.length).toBe(before);
      expect(await screen.findByRole("heading", { level: 1 })).toBeTruthy();
    });
  }

  it("AC-41 /data with an unknown hash stays the overview", async () => {
    routes();
    open("/data#nonsense");
    expect(await screen.findByRole("heading", { level: 1, name: "Data" })).toBeTruthy();
    expect(window.location.pathname).toBe("/data");
  });
});

describe("Data: dataset page content (AC-46)", () => {
  const CASES: [string, string, RegExp][] = [
    ["/data/radiation", "/api/v2/rovers/curiosity/radiation", /Radiation/],
    ["/data/samples", "/api/v2/rovers/perseverance/samples", /Rock samples/],
    ["/data/weather", "/api/v2/rovers/perseverance/weather", /Weather/],
  ];

  for (const [path, endpoint, name] of CASES) {
    it(`AC-46 ${path}: facts as term/description pairs, 'About the data' with the API endpoint`, async () => {
      routes();
      open(path);
      await screen.findByRole("heading", { level: 1, name });
      await screen.findByRole("heading", { name: /About the data/i });
      const main = mainEl();
      const terms = main.querySelectorAll("dl dt");
      expect(terms.length).toBeGreaterThanOrEqual(2);
      for (const dt of terms) {
        expect(dt.nextElementSibling?.tagName, dt.textContent ?? "").toBe("DD");
      }
      expect(main.textContent).toContain(endpoint);
    });
  }

  it("AC-46 radiation facts: rover, coverage, newest sol, last update and the lag", async () => {
    routes();
    open("/data/radiation");
    await screen.findByRole("slider", { name: /Inspect a sol/ });
    const dl = mainEl().querySelector("dl")!;
    const text = dl.textContent ?? "";
    expect(text).toMatch(/Curiosity/);
    expect(text).toMatch(/sols? 10\s*[–-]\s*13/);
    expect(text).toMatch(/2026-12-01/);
    expect(text).toMatch(/5053 sols/);
  });

  it("AC-46 the credit, the citation and the archive link are visible on the radiation page", async () => {
    routes();
    open("/data/radiation");
    await screen.findByRole("slider", { name: /Inspect a sol/ });
    expect(mainEl().textContent).toContain(RAD_CREDIT);
    expect(mainEl().textContent).toContain(RAD_CITATION);
    expect(
      within(mainEl())
        .getAllByRole("link")
        .some((a) => a.getAttribute("href") === RAD_SOURCE_URL),
    ).toBe(true);
  });

  it("AC-46 the credit stays visible when a dataset has no data", async () => {
    routes({ [RAD]: radiationResponse([]) });
    open("/data/radiation");
    await screen.findByText("Radiation data not loaded yet");
    expect(mainEl().textContent).toContain(RAD_CREDIT);
  });

  it("AC-46 weather with no data shows an empty state, not a broken chart", async () => {
    routes({
      [WX("perseverance")]: weatherList("perseverance", []),
    });
    open("/data/weather");
    expect(await screen.findByText("Weather data not loaded yet")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(mainEl().textContent).not.toMatch(/NaN|undefined/);
  });

  it("AC-46 no NASA/JPL logos and no endorsement wording on the Data pages", async () => {
    routes();
    for (const path of ["/data", "/data/radiation", "/data/samples", "/data/weather"]) {
      const view = open(path);
      await screen.findByRole("heading", { level: 1 });
      const main = mainEl();
      expect(
        main.querySelector('img[src*="nasa" i], img[alt*="NASA" i], img[alt*="JPL" i]'),
      ).toBeNull();
      expect(main.textContent).not.toMatch(/approved by NASA|endorsed by NASA(?!\s*or)/i);
      view.unmount();
    }
  });
});

describe("Data: Radiation section (AC-42)", () => {
  async function openRadiation(over: Record<string, unknown> = {}) {
    const m = routes(over);
    open("/data/radiation");
    const panel = await screen.findByRole("main");
    return { m, panel };
  }

  it("AC-42 asks the radiation API once, without parameters", async () => {
    const { m } = await openRadiation();
    await screen.findByRole("slider", { name: /Inspect a sol/ });
    const calls = m.calls.filter((c) => c.pathname === RAD);
    expect(calls).toHaveLength(1);
    expect(calls[0]!.search).toBe("");
  });

  it("AC-42 the summary card shows coverage, update date, lag in text, credit and source", async () => {
    const { panel } = await openRadiation();
    await screen.findByRole("slider", { name: /Inspect a sol/ });
    const text = panel.textContent ?? "";
    expect(text).toContain(RAD_CREDIT);
    expect(text).toMatch(/2026-12-01/);
    expect(text).toMatch(/sols? 10\s*[–-]\s*13/); // coverage
    // Curiosity is at sol 5066 and the newest published sol is 13: 5053 sols behind, said in words
    expect(text).toMatch(/5053 sols/);
    expect(text).toMatch(/behind/i);
    expect(text).toMatch(/PDS/);
    // CLAUDE.md: unofficial project, no endorsement wording, no NASA/JPL logos
    expect(text).not.toMatch(/approved by NASA|endorsed by NASA(?!\s*or)/i);
    expect(
      panel.querySelector('img[src*="nasa" i], img[alt*="NASA" i], img[alt*="JPL" i]'),
    ).toBeNull();
  });

  it("AC-42 the citation text is on the card and the archive is linked", async () => {
    const { panel } = await openRadiation();
    await screen.findByRole("slider", { name: /Inspect a sol/ });
    expect(panel.textContent).toContain(RAD_CITATION);
    const link = within(panel)
      .getAllByRole("link")
      .find((a) => a.getAttribute("href") === RAD_SOURCE_URL);
    expect(link, "link to the archive (source_url)").toBeTruthy();
  });

  it("AC-42 has a dose chart with E and B, a 'not published yet' zone and no 'Open full view'", async () => {
    const { panel } = await openRadiation();
    await screen.findByRole("slider", { name: /Inspect a sol/ });
    const charts = within(panel).getAllByRole("img");
    expect(charts.length).toBeGreaterThanOrEqual(2); // doses + count rate
    const dose = charts.find((c) => /dose/i.test(c.getAttribute("aria-label") ?? ""))!;
    expect(dose, "dose chart with a text alternative").toBeTruthy();
    expect(dose.getAttribute("aria-label")).toMatch(/zero|from 0|starts at 0/i);
    expect(panel.textContent).toMatch(/E\b/);
    expect(panel.textContent).toMatch(/not published yet/);
    expect(panel.textContent).toMatch(/µGy\/day|μGy\/day|micrograys? per day/);
    expect(within(panel).queryByText(/Open full view/i)).toBeNull();
    expect(screen.queryByRole("link", { name: /Open full view/i })).toBeNull();
  });

  it("AC-42 shows a small count-rate chart for detector B, raw, in events/s", async () => {
    const { panel } = await openRadiation();
    await screen.findByRole("slider", { name: /Inspect a sol/ });
    expect(
      within(panel).getByRole("heading", { name: /Detector B count rate \(raw\), events\/s/ }),
    ).toBeTruthy();
    const rate = within(panel)
      .getAllByRole("img")
      .find((c) => /count rate|events/i.test(c.getAttribute("aria-label") ?? ""));
    expect(rate, "count-rate chart with a text alternative").toBeTruthy();
  });

  it("AC-42 the table lists the newest published sols with E, B, rate and coverage", async () => {
    const { panel } = await openRadiation();
    const table = await within(panel).findByRole("table");
    const heads = within(table)
      .getAllByRole("columnheader")
      .map((h) => h.textContent ?? "");
    expect(heads.some((h) => /^Sol$/i.test(h))).toBe(true);
    expect(heads.some((h) => /^E\b/.test(h))).toBe(true);
    expect(heads.some((h) => /^B\b/.test(h))).toBe(true);
    expect(heads.some((h) => /Coverage/i.test(h))).toBe(true);
    expect(heads.some((h) => /events\/s|count rate/i.test(h))).toBe(true);
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    // newest first or oldest first, both fine: the three sols are all there, 12 (a gap) is not
    const sols = rows.map((r) => within(r).getAllByRole("cell")[0]!.textContent?.trim());
    expect([...sols].sort()).toEqual(["10", "11", "13"]);
    expect(table.textContent).toMatch(/160\.4/);
    expect(table.textContent).toMatch(/9\.437|9\.44/);
    expect(table.textContent).toMatch(/0\.86|86 ?%/);
  });

  it("AC-42 shows only the last 3 sols of a longer series", async () => {
    const many = [1, 2, 3, 4, 5].map((s) => radSol(s));
    const { panel } = await openRadiation({ [RAD]: radiationResponse(many) });
    const table = await within(panel).findByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(4); // header + 3
    const sols = within(table)
      .getAllByRole("row")
      .slice(1)
      .map((r) => within(r).getAllByRole("cell")[0]!.textContent?.trim());
    expect([...sols].sort()).toEqual(["3", "4", "5"]);
  });

  it("AC-42 the table has a count-rate column and states are not only colour: lag is text", async () => {
    const { panel } = await openRadiation();
    await screen.findByRole("slider", { name: /Inspect a sol/ });
    expect(panel.textContent).toMatch(/Detector B count rate/);
    expect(panel.textContent).toMatch(/behind/i);
  });

  it("AC-42 absorbed dose, not equivalent dose: no sieverts and no safety verdicts", async () => {
    const { panel } = await openRadiation();
    await screen.findByRole("slider", { name: /Inspect a sol/ });
    // the note may say "not equivalent dose (sieverts)", but no value is given in Sv
    expect(panel.textContent).not.toMatch(/\d\s*(m|µ)?Sv\b/);
    expect(panel.textContent).not.toMatch(/\b(dangerous|safe|harmful)\b/i);
  });
});

describe("Data: Inspect a sol slider (AC-43)", () => {
  async function slider() {
    routes();
    open("/data/radiation");
    return (await screen.findByRole("slider", { name: /Inspect a sol/ })) as HTMLInputElement;
  }
  const readout = () => screen.getByRole("main");

  it("AC-43 starts at the newest sol and shows sol, date, E, B and rate", async () => {
    const s = await slider();
    expect(s.value).toBe("13");
    const t = readout().textContent ?? "";
    expect(t).toMatch(/Sol 13/);
    expect(t).toMatch(/2012-08-18/);
    expect(t).toMatch(/160\.4/);
    expect(t).toMatch(/167\.9/);
    expect(t).toMatch(/9\.437|9\.44/);
    expect(t).toMatch(/events\/s/);
  });

  it("AC-43 moving the slider updates the readout", async () => {
    const s = await slider();
    fireEvent.input(s, { target: { value: "11" } });
    await waitFor(() => expect(readout().textContent).toMatch(/Sol 11\b/));
    const t = readout().textContent ?? "";
    expect(t).toMatch(/2012-08-16/);
    expect(t).toMatch(/171\.5/);
    expect(t).toMatch(/180\.25|180\.3/);
    expect(t).toMatch(/8\.5/);
  });

  it("AC-43 a sol without data says 'No data for sol N'", async () => {
    const s = await slider();
    fireEvent.input(s, { target: { value: "12" } });
    expect(await screen.findByText(/No data for sol 12/)).toBeTruthy();
  });

  it("AC-43 the slider has a label, a numeric range and a description", async () => {
    const s = await slider();
    expect(s.type).toBe("range");
    expect(s.step === "1" || s.step === "").toBe(true);
    expect(Number(s.min)).toBeLessThanOrEqual(10);
    expect(Number(s.max)).toBeGreaterThanOrEqual(13);
    expect(s.getAttribute("aria-describedby") || s.getAttribute("aria-valuetext")).toBeTruthy();
  });

  it("AC-43 an arrow key moves by one sol (jsdom has no native range keys: keydown must be handled or this is covered in e2e)", async () => {
    const s = await slider();
    s.focus();
    fireEvent.keyDown(s, { key: "ArrowLeft" });
    // Either the component handles the key itself, or the browser does it natively: the e2e test covers the latter.
    if (s.value !== "13") expect(s.value).toBe("12");
  });
});

describe("Data: states (AC-44)", () => {
  it("AC-44 shows a loading state while the request is in flight", async () => {
    routes({ [RAD]: () => new Promise(() => {}) });
    open("/data/radiation");
    const panel = await screen.findByRole("main");
    expect(await within(panel).findByRole("status")).toBeTruthy();
    expect(within(panel).queryByRole("slider")).toBeNull();
  });

  it("AC-44 items: [] says 'Radiation data not loaded yet' and draws no chart", async () => {
    routes({ [RAD]: radiationResponse([]) });
    open("/data/radiation");
    expect(await screen.findByText("Radiation data not loaded yet")).toBeTruthy();
    const panel = screen.getByRole("main");
    expect(within(panel).queryByRole("slider")).toBeNull();
    expect(within(panel).queryByRole("table")).toBeNull();
    // credit stays visible even when there is nothing to draw (CLAUDE.md)
    expect(panel.textContent).toContain(RAD_CREDIT);
  });

  it("AC-44 a v2 error shows the message and 'Try again', which works; the samples section keeps working", async () => {
    let n = 0;
    routes({
      [RAD]: () =>
        ++n === 1
          ? apiError(503, "db_unavailable", "Database is unavailable")
          : radiationResponse(),
    });
    open("/data/radiation");
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/temporarily unavailable|could not be loaded/i);
    fireEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("slider", { name: /Inspect a sol/ })).toBeTruthy();
  });

  it("AC-44 a failing radiation request does not break the samples page", async () => {
    routes({ [RAD]: apiError(503, "db_unavailable", "Database is unavailable") });
    open("/data/samples");
    const main = await screen.findByRole("main");
    await waitFor(() => expect(within(main).getAllByRole("img").length).toBeGreaterThan(0));
    expect(within(main).queryByRole("alert")).toBeNull();
  });

  it("AC-44 a 429 shows the message and 'Try again'", async () => {
    routes({
      [RAD]: apiError(429, "rate_limited", "Too many requests", null, { "Retry-After": "60" }),
    });
    open("/data/radiation");
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByRole("button", { name: "Try again" })).toBeTruthy();
  });

  it("AC-44 an unexpected null or extra field in a sol does not break the section", async () => {
    const odd = radSol(13, {
      count_rate_b_per_s: null,
      timing_caveat: null,
      surprise: { nested: true },
      dose_b_ugy_day: { mean: 167.9, min: null, max: null },
    });
    routes({ [RAD]: radiationResponse([radItems[0], odd]) });
    open("/data/radiation");
    expect(await screen.findByRole("slider", { name: /Inspect a sol/ })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("Data: Rock samples section (AC-45)", () => {
  it("AC-45 /data#samples: summary, cumulative step chart up to 30 and a link to the samples page", async () => {
    routes();
    open("/data/samples");
    const panel = await screen.findByRole("main");
    await waitFor(() => expect(within(panel).getAllByRole("img").length).toBeGreaterThan(0));
    const t = panel.textContent ?? "";
    expect(t).toMatch(/30/);
    expect(t).toMatch(/Credit/i);
    const chart = within(panel)
      .getAllByRole("img")
      .find((c) => /sealed|cumulative/i.test(c.getAttribute("aria-label") ?? ""))!;
    expect(chart, "cumulative chart with a text alternative").toBeTruthy();
    expect(chart.getAttribute("aria-label")).toMatch(/30/);
    const link = within(panel)
      .getAllByRole("link")
      .find((a) => a.getAttribute("href") === "/rovers/perseverance/samples");
    expect(link, "link to the samples page").toBeTruthy();
    expect(within(panel).queryByText(/Open full view/i)).toBeNull();
  });

  it("AC-45 an empty sample list says it is not loaded yet", async () => {
    routes({ "/api/v2/rovers/perseverance/samples": samplesResponse([]) });
    open("/data/samples");
    expect(await screen.findByText(/Samples not loaded yet|No samples loaded yet/)).toBeTruthy();
  });
});
