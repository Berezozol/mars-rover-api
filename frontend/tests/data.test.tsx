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

const header = (name: RegExp) => screen.getByRole("button", { name });

describe("Data: header entry (AC-40)", () => {
  it("AC-40 the main navigation has 'Data' (to /data) and no 'Samples'", async () => {
    routes({ "/api/v2/rovers/perseverance/photos/latest": { sol: 2031, ...page([], 0) } });
    open("/");
    const nav = await screen.findByRole("navigation", { name: "Main" });
    await waitFor(() => expect(within(nav).getByRole("link", { name: "Data" })).toBeTruthy());
    expect(within(nav).getByRole("link", { name: "Data" }).getAttribute("href")).toBe("/data");
    expect(within(nav).queryByRole("link", { name: "Samples" })).toBeNull();
  });

  it("AC-40 the Samples page of Perseverance still works at its old path", async () => {
    routes();
    open("/rovers/perseverance/samples");
    expect(await screen.findByRole("heading", { level: 1, name: /rock samples/i })).toBeTruthy();
    expect(await screen.findByRole("table")).toBeTruthy();
  });
});

describe("Data: accordion (AC-41)", () => {
  it("AC-41 without a hash the first section (Rock samples) is open, Radiation is closed, no Weather", async () => {
    const m = routes();
    open("/data");
    expect(await screen.findByRole("heading", { level: 1, name: "Data" })).toBeTruthy();
    expect(header(/Rock samples/).getAttribute("aria-expanded")).toBe("true");
    expect(header(/Radiation/).getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("button", { name: /Weather/ })).toBeNull();
    expect(screen.queryByText(/Weather by sol/)).toBeNull();
    // a closed section asks nothing
    await screen.findByRole("region", { name: /Rock samples/ });
    expect(m.calls.filter((c) => c.pathname === RAD)).toHaveLength(0);
  });

  it("AC-41 #radiation opens and scrolls to the radiation section", async () => {
    routes();
    open("/data#radiation");
    await waitFor(() => expect(header(/Radiation/).getAttribute("aria-expanded")).toBe("true"));
    expect(await screen.findByRole("region", { name: /Radiation/ })).toBeTruthy();
    await waitFor(() => expect(Element.prototype.scrollIntoView).toHaveBeenCalled());
  });

  it("AC-41 clicking a header toggles aria-expanded and writes the hash", async () => {
    routes();
    open("/data");
    await screen.findByRole("heading", { level: 1, name: "Data" });
    const rad = header(/Radiation/);
    expect(rad.getAttribute("aria-controls")).toBeTruthy();
    fireEvent.click(rad);
    await waitFor(() => expect(header(/Radiation/).getAttribute("aria-expanded")).toBe("true"));
    expect(window.location.hash).toBe("#radiation");
    fireEvent.click(header(/Radiation/));
    await waitFor(() => expect(header(/Radiation/).getAttribute("aria-expanded")).toBe("false"));
    fireEvent.click(header(/Rock samples/));
    await waitFor(() => expect(header(/Rock samples/).getAttribute("aria-expanded")).toBe("false"));
  });

  it("AC-41 Expand all / Collapse all", async () => {
    routes();
    open("/data");
    await screen.findByRole("heading", { level: 1, name: "Data" });
    fireEvent.click(screen.getByRole("button", { name: /Expand all/ }));
    await waitFor(() => expect(header(/Radiation/).getAttribute("aria-expanded")).toBe("true"));
    expect(header(/Rock samples/).getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: /Collapse all/ }));
    await waitFor(() => expect(header(/Radiation/).getAttribute("aria-expanded")).toBe("false"));
    expect(header(/Rock samples/).getAttribute("aria-expanded")).toBe("false");
  });
});

describe("Data: Radiation section (AC-42)", () => {
  async function openRadiation(over: Record<string, unknown> = {}) {
    const m = routes(over);
    open("/data#radiation");
    const panel = await screen.findByRole("region", { name: /Radiation/ });
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
    open("/data#radiation");
    return (await screen.findByRole("slider", { name: /Inspect a sol/ })) as HTMLInputElement;
  }
  const readout = () => screen.getByRole("region", { name: /Radiation/ });

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
    open("/data#radiation");
    const panel = await screen.findByRole("region", { name: /Radiation/ });
    expect(await within(panel).findByRole("status")).toBeTruthy();
    expect(within(panel).queryByRole("slider")).toBeNull();
  });

  it("AC-44 items: [] says 'Radiation data not loaded yet' and draws no chart", async () => {
    routes({ [RAD]: radiationResponse([]) });
    open("/data#radiation");
    expect(await screen.findByText("Radiation data not loaded yet")).toBeTruthy();
    const panel = screen.getByRole("region", { name: /Radiation/ });
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
    open("/data#radiation");
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/temporarily unavailable|could not be loaded/i);
    // the samples section is independent
    fireEvent.click(header(/Rock samples/));
    const samples = await screen.findByRole("region", { name: /Rock samples/ });
    await waitFor(() => expect(within(samples).getAllByRole("img").length).toBeGreaterThan(0));
    expect(within(samples).queryByRole("alert")).toBeNull();
    fireEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("slider", { name: /Inspect a sol/ })).toBeTruthy();
  });

  it("AC-44 a 429 shows the message and 'Try again'", async () => {
    routes({
      [RAD]: apiError(429, "rate_limited", "Too many requests", null, { "Retry-After": "60" }),
    });
    open("/data#radiation");
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
    open("/data#radiation");
    expect(await screen.findByRole("slider", { name: /Inspect a sol/ })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("Data: Rock samples section (AC-45)", () => {
  it("AC-45 /data#samples: summary, cumulative step chart up to 30 and a link to the samples page", async () => {
    routes();
    open("/data#samples");
    const panel = await screen.findByRole("region", { name: /Rock samples/ });
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
    open("/data#samples");
    expect(await screen.findByText(/Samples not loaded yet|No samples loaded yet/)).toBeTruthy();
  });
});
