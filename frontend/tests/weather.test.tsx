import { fireEvent, screen, waitFor, within } from "@testing-library/preact";
import { describe, expect, it } from "vitest";
import { apiError } from "./helpers";
import {
  CUR_CREDIT,
  CUR_PARAMETERS,
  CUR_SOURCE_URL,
  PER_CREDIT,
  PER_PARAMETERS,
  PER_SOURCE_URL,
  curItems,
  curSol,
  perItems,
  perSol,
  weatherList,
} from "./fixtures/weather";
import { perLong } from "./fixtures/range";
import {
  WX,
  callsTo,
  chip,
  dailyChart,
  hasChip,
  openWeather,
  panelOf,
  readyPanel,
  readyPanelWithRover,
  rowOf,
} from "./weather-helpers";

// Tests of the "Weather by sol" section of the Data page (stage 17), written from docs/API.md and the
// mockup design/mockups/data.html#weather. Test names carry the stage-17 criteria AC-41 ... AC-46
// (the Data page of stage 21 numbers its own criteria in data.test.tsx).

const label = (el: Element | undefined) => el?.getAttribute("aria-label") ?? "";

describe("Weather AC-41: the section and the daily chart of Perseverance", () => {
  it("AC-41 the /data overview asks nothing; /data/weather loads the list", async () => {
    const m = openWeather({}, "/data");
    await screen.findByRole("heading", { level: 1, name: "Data" });
    expect(callsTo(m, WX("perseverance"))).toHaveLength(0);
    fireEvent.click(
      within(screen.getByRole("main"))
        .getAllByRole("link")
        .find((a) => a.getAttribute("href") === "/data/weather")!,
    );
    await waitFor(() => expect(callsTo(m, WX("perseverance"))).toHaveLength(1));
    expect(window.location.pathname).toBe("/data/weather");
  });

  it("AC-41 /data/weather asks for the Perseverance list once, without parameters, and for the rover card", async () => {
    const m = openWeather();
    const panel = await readyPanel();
    expect(panel).toBeTruthy();
    const list = callsTo(m, WX("perseverance"));
    expect(list).toHaveLength(1);
    expect(list[0]!.search).toBe("");
    expect(callsTo(m, "/api/v2/rovers/perseverance").length).toBeGreaterThan(0); // max_sol
    expect(callsTo(m, WX("curiosity"))).toHaveLength(0);
  });

  it("AC-41 Perseverance (MEDA) is the default rover in a labelled radio group", async () => {
    openWeather();
    const panel = await readyPanel();
    const group = within(panel).getByRole("radiogroup", { name: "Rover" });
    const per = within(group).getByRole("radio", { name: /Perseverance/ }) as HTMLInputElement;
    const cur = within(group).getByRole("radio", { name: /Curiosity/ }) as HTMLInputElement;
    expect(per.checked).toBe(true);
    expect(cur.checked).toBe(false);
    expect(within(group).getByText(/MEDA/)).toBeTruthy();
    expect(within(group).getByText(/REMS/)).toBeTruthy();
  });

  it("AC-41 the daily chart has a text alternative: parameter, min-max band, mean, sols", async () => {
    openWeather();
    const panel = await readyPanel();
    const chart = dailyChart(panel)!;
    expect(chart, "daily chart with a text alternative").toBeTruthy();
    expect(label(chart)).toMatch(/air temp/i);
    expect(label(chart)).toMatch(/min.*max/i);
    expect(label(chart)).toMatch(/mean/i);
    expect(label(chart)).toMatch(/sols? /i);
    expect(label(chart)).toMatch(/Perseverance/);
  });

  it("AC-41 the grey zone after the newest published sol says it is not published yet", async () => {
    openWeather();
    const panel = await readyPanel();
    expect(panel.textContent).toMatch(/not published yet/i);
  });

  it("AC-41 the summary: lag in words (Rover.max_sol minus source_max_sol), update date, coverage", async () => {
    openWeather();
    const panel = await readyPanelWithRover();
    const t = panel.textContent ?? "";
    // Perseverance is at sol 2031, the newest published sol is 108: 1923 sols behind
    expect(t).toMatch(/1923 sols/);
    expect(t).toMatch(/behind/i);
    expect(t).toMatch(/2026-11-24/);
    expect(t).toMatch(/sols? 100\s*[–-]\s*108/);
  });

  it("AC-41 the credit is always text and the archive is linked; no NASA/JPL logos, no endorsement", async () => {
    openWeather();
    const panel = await readyPanel();
    expect(panel.textContent).toContain(PER_CREDIT);
    const link = within(panel)
      .getAllByRole("link")
      .find((a) => a.getAttribute("href") === PER_SOURCE_URL);
    expect(link, "link to the archive (source_url)").toBeTruthy();
    expect(panel.textContent).not.toMatch(/approved by NASA|endorsed by NASA(?!\s*or)/i);
    expect(
      panel.querySelector('img[src*="nasa" i], img[alt*="NASA" i], img[alt*="JPL" i]'),
    ).toBeNull();
  });

  it("AC-41 parameter chips come from the response header and show which one is chosen", async () => {
    openWeather();
    const panel = await readyPanel();
    const group = within(panel).getByRole("group", { name: /Parameter/ });
    const names = within(group)
      .getAllByRole("button")
      .map((b) => b.textContent?.trim());
    expect(names).toEqual([
      expect.stringMatching(/^Air temp/),
      expect.stringMatching(/^Pressure/),
      expect.stringMatching(/^Humidity/),
      expect.stringMatching(/^Wind/),
    ]);
    expect(chip(panel, /^Air temp/).getAttribute("aria-pressed")).toBe("true");
    expect(chip(panel, /^Pressure/).getAttribute("aria-pressed")).toBe("false");
  });

  it("AC-41 choosing a parameter redraws the chart without a new request", async () => {
    const m = openWeather();
    const panel = await readyPanelWithRover();
    const before = m.calls.length;
    fireEvent.click(chip(panel, /^Pressure/));
    await waitFor(() => expect(label(dailyChart(panel))).toMatch(/pressure/i));
    expect(chip(panel, /^Pressure/).getAttribute("aria-pressed")).toBe("true");
    expect(chip(panel, /^Air temp/).getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(chip(panel, /^Wind/));
    await waitFor(() => expect(label(dailyChart(panel))).toMatch(/wind/i));
    expect(label(dailyChart(panel))).toMatch(/gust/i);
    expect(m.calls.length).toBe(before);
  });

  it("AC-41 the range presets redraw the chart against source_max_sol, with no new list request", async () => {
    const m = openWeather({ per: perLong });
    const panel = await readyPanelWithRover();
    const before =
      callsTo(m, WX("perseverance")).length + callsTo(m, "/api/v2/rovers/perseverance").length;
    // the default range ends at the newest published sol (1859), not at the rover's current sol (2031)
    expect(label(dailyChart(panel))).toMatch(/1,?859/);
    expect(label(dailyChart(panel))).not.toMatch(/2,?031/);
    expect(within(panel).queryByLabelText("Sols shown")).toBeNull();
    fireEvent.click(
      within(within(panel).getByRole("group", { name: /range presets/i })).getByRole("button", {
        name: /Last 120 sols/,
      }),
    );
    await waitFor(() => expect(label(dailyChart(panel))).toMatch(/1,?740/));
    expect(
      callsTo(m, WX("perseverance")).length + callsTo(m, "/api/v2/rovers/perseverance").length,
    ).toBe(before);
  });

  it("AC-41 the table lists the sols of the range, newest first, with the date and the flags", async () => {
    const many = Array.from({ length: 12 }, (_, i) => perSol(200 + i));
    openWeather({ per: many });
    const panel = await readyPanel();
    const table = within(panel).getByRole("table");
    const heads = within(table)
      .getAllByRole("columnheader")
      .map((h) => h.textContent ?? "");
    for (const h of [/^Sol/, /Date/, /Air/, /Pressure/, /Humidity/, /Wind/, /Hours/, /Flags/]) {
      expect(
        heads.some((x) => h.test(x)),
        String(h),
      ).toBe(true);
    }
    // stage 22: all 12 sols of the range (they fit one page), not the 8 newest
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(12);
    const sols = rows.map((r) => (r.firstElementChild?.textContent ?? "").replace(/\D/g, ""));
    expect(sols).toEqual([
      "211",
      "210",
      "209",
      "208",
      "207",
      "206",
      "205",
      "204",
      "203",
      "202",
      "201",
      "200",
    ]);
    expect(rows[0]!.textContent).toMatch(/2021-06-01/);
    expect(rows[0]!.textContent).toMatch(/24\s*\/\s*24/);
  });
});

describe("Weather AC-42: Perseverance and Curiosity differ in the parameters they have", () => {
  it("AC-42 Perseverance has Wind and no Ground temp or UV", async () => {
    openWeather();
    const panel = await readyPanel();
    expect(hasChip(panel, /^Wind/)).toBe(true);
    expect(hasChip(panel, /^Ground temp/)).toBe(false);
    expect(hasChip(panel, /^UV/)).toBe(false);
    const heads = within(within(panel).getByRole("table")).getAllByRole("columnheader");
    expect(heads.some((h) => /Wind/.test(h.textContent ?? ""))).toBe(true);
    expect(heads.some((h) => /Ground|UV/.test(h.textContent ?? ""))).toBe(false);
  });

  it("AC-42 switching to Curiosity asks for /rovers/curiosity/weather and its rover card", async () => {
    const m = openWeather();
    const panel = await readyPanel();
    fireEvent.click(within(panel).getByRole("radio", { name: /Curiosity/ }));
    await waitFor(() => expect(callsTo(m, WX("curiosity"))).toHaveLength(1));
    expect(callsTo(m, WX("curiosity"))[0]!.search).toBe("");
    await waitFor(() => expect(callsTo(m, "/api/v2/rovers/curiosity").length).toBeGreaterThan(0));
    const now = await readyPanel();
    expect(
      (within(now).getByRole("radio", { name: /Curiosity/ }) as HTMLInputElement).checked,
    ).toBe(true);
    expect(now.textContent).toContain(CUR_CREDIT);
    expect(
      within(now)
        .getAllByRole("link")
        .some((a) => a.getAttribute("href") === CUR_SOURCE_URL),
    ).toBe(true);
    // Rover.max_sol 5066 against the newest published sol 15
    await waitFor(() => expect(now.textContent).toMatch(/5051 sols/));
  });

  it("AC-42 Curiosity has Ground temp and UV, and no Wind option at all", async () => {
    openWeather();
    const panel = await readyPanel();
    fireEvent.click(within(panel).getByRole("radio", { name: /Curiosity/ }));
    await waitFor(() => expect(hasChip(panel, /^Ground temp/)).toBe(true));
    expect(hasChip(panel, /^UV/)).toBe(true);
    expect(hasChip(panel, /^Wind/)).toBe(false);
    const heads = within(within(panel).getByRole("table")).getAllByRole("columnheader");
    expect(heads.some((h) => /Wind/.test(h.textContent ?? ""))).toBe(false);
    expect(heads.some((h) => /Ground/.test(h.textContent ?? ""))).toBe(true);
    expect(heads.some((h) => /UV/.test(h.textContent ?? ""))).toBe(true);
  });

  it("AC-42 a chosen parameter that the other rover lacks falls back to Air temp", async () => {
    openWeather();
    const panel = await readyPanel();
    fireEvent.click(chip(panel, /^Wind/));
    fireEvent.click(within(panel).getByRole("radio", { name: /Curiosity/ }));
    await waitFor(() => expect(hasChip(panel, /^Ground temp/)).toBe(true));
    expect(chip(panel, /^Air temp/).getAttribute("aria-pressed")).toBe("true");
    await waitFor(() => expect(label(dailyChart(panel))).toMatch(/air temp/i));
  });

  it("AC-42 going back does not ask for the same rover again", async () => {
    const m = openWeather();
    const panel = await readyPanel();
    fireEvent.click(within(panel).getByRole("radio", { name: /Curiosity/ }));
    await waitFor(() => expect(hasChip(panel, /^UV/)).toBe(true));
    fireEvent.click(within(panel).getByRole("radio", { name: /Perseverance/ }));
    await waitFor(() => expect(hasChip(panel, /^Wind/)).toBe(true));
    expect(callsTo(m, WX("perseverance"))).toHaveLength(1);
    expect(callsTo(m, WX("curiosity"))).toHaveLength(1);
  });

  it("AC-42 the radio group is reachable and operable with the keyboard (native radios)", async () => {
    openWeather();
    const panel = await readyPanel();
    const per = within(panel).getByRole("radio", { name: /Perseverance/ }) as HTMLInputElement;
    expect(per.type).toBe("radio");
    expect(per.disabled).toBe(false);
    expect(per.name).toBeTruthy();
    expect((within(panel).getByRole("radio", { name: /Curiosity/ }) as HTMLInputElement).name).toBe(
      per.name,
    );
  });

  it("AC-42 parameters follow the header, not the rover name: 'estimate' gets its own badge", async () => {
    const withGround = perItems.map((i) => ({
      ...i,
      ground_temp_c: { min: -60.0, max: -10.0, mean: -35.0 },
    }));
    openWeather({
      per: withGround,
      over: {
        [WX("perseverance")]: weatherList("perseverance", withGround, {
          parameters: { ...PER_PARAMETERS, ground_temp_c: { unit: "°C", quality: "estimate" } },
        }),
      },
    });
    const panel = await readyPanel();
    const ground = chip(panel, /^Ground temp/);
    expect(ground.textContent).toMatch(/estimate/i);
    expect(ground.textContent).not.toMatch(/preliminary/i);
    expect(chip(panel, /^Humidity/).textContent).toMatch(/preliminary/i);
    expect(chip(panel, /^Air temp/).textContent).not.toMatch(/preliminary|estimate/i);
    fireEvent.click(ground);
    await waitFor(() => expect(label(dailyChart(panel))).toMatch(/ground/i));
  });

  it("AC-42 a parameter missing from the header has no chip, an unknown one does not break the section", async () => {
    openWeather({
      over: {
        [WX("perseverance")]: weatherList("perseverance", perItems, {
          parameters: {
            air_temp_c: PER_PARAMETERS.air_temp_c,
            pressure_pa: PER_PARAMETERS.pressure_pa,
            soil_moisture: { unit: "%", quality: "measured" },
          },
        }),
      },
    });
    const panel = await readyPanel();
    expect(hasChip(panel, /^Humidity/)).toBe(false);
    expect(hasChip(panel, /^Wind/)).toBe(false);
    expect(hasChip(panel, /^Air temp/)).toBe(true);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("AC-42 a null means 'sensor exists, no data' and is written as a word; an absent key has no column", async () => {
    openWeather();
    const panel = await readyPanel();
    // Perseverance sol 108: wind is null
    expect(rowOf(panel, 108)!.textContent).toMatch(/null|no data/i);
    expect(rowOf(panel, 107)!.textContent).not.toMatch(/null/i);
    fireEvent.click(within(panel).getByRole("radio", { name: /Curiosity/ }));
    await waitFor(() => expect(hasChip(panel, /^UV/)).toBe(true));
    // Curiosity sol 15: UV is null
    await waitFor(() => expect(rowOf(panel, 15)).toBeTruthy());
    expect(rowOf(panel, 15)!.textContent).toMatch(/null|no data/i);
    expect(rowOf(panel, 14)!.textContent).not.toMatch(/null/i);
  });

  it("AC-42 Curiosity data without a humidity minimum and a gap in sols does not break the chart", async () => {
    openWeather({ cur: [curSol(2), curSol(9), curSol(12)] });
    const panel = await readyPanel();
    fireEvent.click(within(panel).getByRole("radio", { name: /Curiosity/ }));
    await waitFor(() => expect(hasChip(panel, /^UV/)).toBe(true));
    fireEvent.click(chip(panel, /^Humidity/));
    await waitFor(() => expect(label(dailyChart(panel))).toMatch(/humidity/i));
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("Weather AC-43: flags", () => {
  it("AC-43 flags are named in words in the table", async () => {
    openWeather();
    const panel = await readyPanel();
    expect(rowOf(panel, 105)!.textContent).toMatch(/partial coverage/i);
    expect(rowOf(panel, 106)!.textContent).toMatch(/humidity above 100/i);
    expect(rowOf(panel, 107)!.textContent).toMatch(/out of range/i);
    expect(rowOf(panel, 100)!.textContent).toMatch(/none/i);
  });

  it("AC-43 an unknown flag code gets a neutral label and does not break the row", async () => {
    const odd = perSol(109, {
      flags: [{ code: "sensor_dust", parameter: "air_temp_c", count: 3 }],
      brand_new_field: { nested: [1, 2] },
    });
    openWeather({ per: [...perItems, odd] });
    const panel = await readyPanel();
    const row = rowOf(panel, 109)!;
    expect(row, "row of sol 109").toBeTruthy();
    expect(row.textContent).not.toMatch(/none/i);
    expect(row.textContent).toMatch(/sensor_dust|other|note|flag/i);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("AC-43 a flag never hides a value: humidity above 100 % is shown as published", async () => {
    openWeather();
    const panel = await readyPanel();
    expect(rowOf(panel, 106)!.textContent).toMatch(/137\.5/);
  });

  it("AC-43 markers on the chart belong to the chosen parameter (humidity: diamond and cross)", async () => {
    openWeather();
    const panel = await readyPanel();
    // air temperature: the flags concern humidity, so no flagged-sol marker there
    expect(within(panel).queryAllByTestId("weather-flag-marker")).toHaveLength(0);
    fireEvent.click(chip(panel, /^Humidity/));
    await waitFor(() =>
      expect(within(panel).queryAllByTestId("weather-flag-marker")).toHaveLength(2),
    );
    const markers = within(panel).getAllByTestId("weather-flag-marker");
    const byCode = Object.fromEntries(
      markers.map((m) => [m.getAttribute("data-code"), m.getAttribute("data-sol")]),
    );
    expect(byCode).toEqual({ humidity_over_100: "106", out_of_range: "107" });
    // partial_coverage has no parameter, it is not a marker
  });

  it("AC-43 humidity above 100 % keeps a 100 % line and the legend explains the marker", async () => {
    openWeather();
    const panel = await readyPanel();
    fireEvent.click(chip(panel, /^Humidity/));
    await waitFor(() => expect(label(dailyChart(panel))).toMatch(/humidity/i));
    expect(panel.textContent).toMatch(/100\s?%/);
    expect(panel.textContent).toMatch(/above 100/i);
    expect(panel.textContent).toMatch(/left out/i);
    // the axis reaches the published maximum rather than cutting the data at 100
    expect(label(dailyChart(panel))).toMatch(/137\.5/);
  });

  it("AC-43 a partial-coverage sol is drawn differently, not hidden", async () => {
    openWeather();
    const panel = await readyPanel();
    // the default range is the 9 loaded sols, so the dots have room
    await waitFor(() =>
      expect(within(panel).queryAllByTestId("weather-mean-partial").length).toBeGreaterThan(0),
    );
    const partial = within(panel).getAllByTestId("weather-mean-partial");
    expect(partial.some((e) => e.getAttribute("data-sol") === "105")).toBe(true);
    // the legend explains it
    expect(panel.textContent).toMatch(/partial coverage \(under 18 of 24 hours\)|under 18 of 24/i);
  });
});

describe("Weather AC-8: the old inspector is gone", () => {
  it("AC-8 no 'Inspect a sol' slider, no readout, no hourly charts, no anomaly buttons on the weather page", async () => {
    openWeather();
    const panel = await readyPanel();
    expect(
      within(panel).queryByRole("slider", { name: /inspect a sol|next loaded sol/i }),
    ).toBeNull();
    expect(panel.textContent).not.toMatch(/Inspect a sol/i);
    expect(within(panel).queryAllByRole("img", { name: /by local hour/i })).toHaveLength(0);
    expect(within(panel).queryByRole("button", { name: /anomaly/i })).toBeNull();
    // only the two handles of the sol range remain
    expect(within(panel).getAllByRole("slider")).toHaveLength(2);
  });

  it("AC-8 the page asks for the list only: no /weather/{sol} request until the visitor opens a report", async () => {
    const m = openWeather();
    await readyPanel();
    expect(m.calls.filter((c) => /\/weather\/\d+$/.test(c.pathname))).toEqual([]);
  });

  it("AC-8 flags are shown as pills in the table rows (FlagPill stays)", async () => {
    openWeather();
    const panel = await readyPanel();
    const row = rowOf(panel, 106)!;
    expect(row.textContent).toMatch(/humidity above 100/i);
  });

  it("AC-8 a sol has a link to its report from the table", async () => {
    openWeather();
    const panel = await readyPanel();
    const link = within(rowOf(panel, 100)!).getByRole("link", { name: /sol report/i });
    expect(link.getAttribute("href")).toBe("/rovers/perseverance/sols/100/conditions");
  });
});

describe("Weather AC-45: states", () => {
  it("AC-45 loading: a status with skeletons, the rover switch stays, no slider yet", async () => {
    openWeather({ over: { [WX("perseverance")]: () => new Promise(() => {}) } });
    const panel = await panelOf();
    expect(await within(panel).findByRole("status")).toBeTruthy();
    expect(within(panel).getByRole("radiogroup", { name: "Rover" })).toBeTruthy();
    expect(within(panel).queryByRole("slider")).toBeNull();
    expect(within(panel).queryByRole("table")).toBeNull();
  });

  it("AC-45 items [] and updated_at null: 'Weather data not loaded yet', credit stays, no chart", async () => {
    openWeather({
      per: [],
      over: { [WX("perseverance")]: weatherList("perseverance", []) },
    });
    expect(await screen.findByText("Weather data not loaded yet")).toBeTruthy();
    const panel = await panelOf();
    expect(within(panel).queryByRole("slider")).toBeNull();
    expect(within(panel).queryByRole("table")).toBeNull();
    expect(dailyChart(panel)).toBeUndefined();
    expect(panel.textContent).toContain(PER_CREDIT);
    expect(within(panel).getByRole("radiogroup", { name: "Rover" })).toBeTruthy();
    // the other rover is one click away
    fireEvent.click(within(panel).getByRole("radio", { name: /Curiosity/ }));
    expect((await within(panel).findAllByRole("slider")).length).toBeGreaterThan(0);
  });

  it("AC-45 error 503: message and 'Try again' that works, the rover switch stays", async () => {
    let n = 0;
    const m = openWeather({
      over: {
        [WX("perseverance")]: () =>
          ++n === 1
            ? apiError(503, "db_unavailable", "Database is unavailable")
            : weatherList("perseverance"),
      },
    });
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/temporarily unavailable|could not be loaded/i);
    const panel = await panelOf();
    expect(within(panel).getByRole("radiogroup", { name: "Rover" })).toBeTruthy();
    fireEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    expect((await within(panel).findAllByRole("slider")).length).toBeGreaterThan(0);
    expect(callsTo(m, WX("perseverance"))).toHaveLength(2);
  });

  it("AC-45 error: the other rover can be chosen from the error state", async () => {
    openWeather({
      over: { [WX("perseverance")]: apiError(503, "db_unavailable", "Database is unavailable") },
    });
    await screen.findByRole("alert");
    const panel = await panelOf();
    fireEvent.click(within(panel).getByRole("radio", { name: /Curiosity/ }));
    expect((await within(panel).findAllByRole("slider")).length).toBeGreaterThan(0);
    expect(within(panel).queryByRole("alert")).toBeNull();
  });

  it("AC-45 429 with Retry-After: 60 shows the message and 'Try again', no silent retry", async () => {
    const m = openWeather({
      over: {
        [WX("perseverance")]: apiError(429, "rate_limited", "Too many requests", null, {
          "Retry-After": "60",
        }),
      },
    });
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByRole("button", { name: "Try again" })).toBeTruthy();
    expect(callsTo(m, WX("perseverance"))).toHaveLength(1);
  });

  it("AC-45 network error shows an error with 'Try again'", async () => {
    openWeather({
      over: {
        [WX("perseverance")]: () => {
          throw new TypeError("Failed to fetch");
        },
      },
    });
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByRole("button", { name: "Try again" })).toBeTruthy();
  });

  it("AC-45 the section works when the rover card cannot be loaded (no lag text, no crash)", async () => {
    openWeather({
      over: {
        "/api/v2/rovers/perseverance": apiError(503, "db_unavailable", "Database is unavailable"),
      },
    });
    const panel = await readyPanel();
    expect(dailyChart(panel)).toBeTruthy();
    expect(panel.textContent).toContain(PER_CREDIT);
  });

  it("AC-45 unexpected nulls and extra fields in the list and in a sol do not break the section", async () => {
    const odd = [
      perSol(100, { air_temp_c: null, surprise: { nested: true }, ls: null }),
      perSol(101, { humidity_pct: null, pressure_pa: null, earth_date: null }),
      perSol(102, { flags: [{ code: "new_code", parameter: null, count: null, extra: 1 }] }),
      perSol(103, { wind: null }),
    ];
    openWeather({ per: odd });
    const panel = await readyPanel();
    expect(screen.queryByRole("alert")).toBeNull();
    for (const p of [/^Pressure/, /^Humidity/, /^Wind/, /^Air temp/]) {
      fireEvent.click(chip(panel, p));
      await waitFor(() => expect(chip(panel, p).getAttribute("aria-pressed")).toBe("true"));
    }
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("AC-45 a rover without max_sol (null) draws no grey zone and does not crash", async () => {
    openWeather({
      over: {
        "/api/v2/rovers/perseverance": {
          id: "perseverance",
          name: "Perseverance",
          mission: "mars2020",
          launch_date: "2020-07-30",
          landing_date: "2021-02-18",
          end_date: null,
          status: "active",
          max_sol: null,
          max_date: null,
          total_photos: 0,
          cameras_count: 0,
          last_synced_at: null,
          data_source: "sync",
        },
      },
    });
    const panel = await readyPanel();
    expect(dailyChart(panel)).toBeTruthy();
    expect(panel.textContent).not.toMatch(/NaN|undefined/);
  });

  it("AC-45 the credit of the chosen rover is visible in every state that has the summary", async () => {
    openWeather({ cur: [], over: { [WX("curiosity")]: weatherList("curiosity", []) } });
    const panel = await readyPanel();
    fireEvent.click(within(panel).getByRole("radio", { name: /Curiosity/ }));
    expect(await screen.findByText("Weather data not loaded yet")).toBeTruthy();
    expect(panel.textContent).toContain(CUR_CREDIT);
  });

  it("AC-45 Curiosity header has no wind key: the section does not invent one", async () => {
    openWeather({
      over: {
        [WX("curiosity")]: weatherList("curiosity", curItems, { parameters: CUR_PARAMETERS }),
      },
    });
    const panel = await readyPanel();
    fireEvent.click(within(panel).getByRole("radio", { name: /Curiosity/ }));
    await waitFor(() => expect(hasChip(panel, /^UV/)).toBe(true));
    expect(hasChip(panel, /wind/i)).toBe(false);
  });
});
