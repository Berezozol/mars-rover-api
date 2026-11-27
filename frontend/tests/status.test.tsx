// Stage 19, screen Status (`/status`, GET /health/sync). Written against
// docs/proposals/stage-19-status-page.md and docs/API.md, not against the implementation.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../src/App";
import { json, mockApi } from "./helpers";

const HEALTH = "/health/sync";

interface RoverHealth {
  rover: string;
  last_success_at: string | null;
  newest_photo_date: string | null;
  sync_stale: boolean;
  photos_stale: boolean;
}
const curiosityOk: RoverHealth = {
  rover: "curiosity",
  last_success_at: "2026-11-18T20:15:03Z",
  newest_photo_date: "2026-11-17",
  sync_stale: false,
  photos_stale: false,
};
const perseveranceOk: RoverHealth = {
  rover: "perseverance",
  last_success_at: "2026-11-18T06:00:12Z",
  newest_photo_date: "2026-11-06",
  sync_stale: false,
  photos_stale: false,
};
const OK = { status: "ok", rovers: [curiosityOk, perseveranceOk] };

function setup(handler: Parameters<typeof mockApi>[0][string], start = "/status") {
  const m = mockApi({ [HEALTH]: handler });
  window.history.replaceState(null, "", start);
  render(<App />);
  return m;
}
const healthCalls = (m: ReturnType<typeof mockApi>) => m.calls.filter((c) => c.pathname === HEALTH);

function setVisibility(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
  document.dispatchEvent(new Event("visibilitychange"));
}

const tick = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

afterEach(async () => {
  // Unmount while the fake clock is still installed so the page's interval and
  // Preact's pending after-paint timers are cleared on the same clock, then
  // flush what is left and only then restore real timers.
  cleanup();
  if (vi.isFakeTimers()) await vi.runOnlyPendingTimersAsync();
  vi.useRealTimers();
  setVisibility("visible");
  // Let any real after-paint timer queued by the last render fire before teardown.
  await new Promise((resolve) => requestAnimationFrame(() => resolve(null)));
});

describe("Status", () => {
  it("AC-1: ok shows the overall status and one card per rover with both dates", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-19T12:00:00Z"));
    setup({
      status: "ok",
      rovers: [{ ...curiosityOk, last_success_at: "2026-11-19T09:00:00Z" }, perseveranceOk],
    });
    const overall = await screen.findByTestId("status-overall");
    expect(overall.textContent).toContain("All systems normal");
    const cur = screen.getByTestId("status-rover-curiosity");
    expect(cur.textContent).toContain("2026-11-19"); // exact UTC date of last_success_at
    expect(cur.textContent).toContain("UTC");
    expect(cur.textContent).toMatch(/3 hours ago/);
    expect(cur.textContent).toContain("2026-11-17"); // newest_photo_date
    const per = screen.getByTestId("status-rover-perseverance");
    expect(per.textContent).toContain("2026-11-06");
    expect(per.textContent).toContain("UTC");
    expect(per.textContent).toMatch(/ago|just now/);
    expect(screen.queryByTestId("status-sync-late")).toBeNull();
    expect(screen.queryByTestId("status-photos-late")).toBeNull();
  });

  it("AC-2: stale (503 with a body) is not a load error and flags the late rover", async () => {
    setup(
      json(
        {
          status: "stale",
          rovers: [{ ...curiosityOk, sync_stale: true }, perseveranceOk],
        },
        { status: 503 },
      ),
    );
    const overall = await screen.findByTestId("status-overall");
    expect(overall.textContent).toContain("Data may be behind");
    const late = screen.getByTestId("status-sync-late");
    expect(late.textContent).toContain("Sync is late");
    expect(late.textContent).toContain("24");
    expect(
      within(screen.getByTestId("status-rover-curiosity")).getByTestId("status-sync-late"),
    ).toBe(late);
    expect(screen.getAllByTestId("status-sync-late")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });

  it("AC-3: photos_stale shows the note with the reason for the delay", async () => {
    setup(
      json(
        { status: "stale", rovers: [curiosityOk, { ...perseveranceOk, photos_stale: true }] },
        { status: 503 },
      ),
    );
    const note = await screen.findByTestId("status-photos-late");
    expect(note.textContent).toContain("No new photos for 21+ days");
    expect(
      within(screen.getByTestId("status-rover-perseverance")).getByTestId("status-photos-late"),
    ).toBe(note);
    expect(screen.getAllByTestId("status-photos-late")).toHaveLength(1);
    expect(note.textContent).toMatch(/JPL/);
    expect(note.textContent).toMatch(/delay/i);
    expect(note.textContent).toMatch(/Sun/i);
  });

  it("AC-4: degraded (503 db error) says the database is unavailable and shows no cards", async () => {
    setup(json({ status: "degraded", db: "error" }, { status: 503 }));
    const overall = await screen.findByTestId("status-overall");
    expect(overall.textContent).toContain("Service degraded: database unavailable");
    expect(screen.queryByTestId("status-rover-curiosity")).toBeNull();
    expect(screen.queryByTestId("status-rover-perseverance")).toBeNull();
  });

  it("AC-5: a network error shows an error state and Try again re-requests", async () => {
    let n = 0;
    const m = setup(() => {
      n += 1;
      if (n === 1) throw new TypeError("Failed to fetch");
      return OK;
    });
    const retry = await screen.findByRole("button", { name: "Try again" });
    expect(screen.queryByTestId("status-rover-curiosity")).toBeNull();
    fireEvent.click(retry);
    expect((await screen.findByTestId("status-overall")).textContent).toContain(
      "All systems normal",
    );
    expect(healthCalls(m)).toHaveLength(2);
  });

  it("AC-5: a response that is not JSON shows an error state with Try again", async () => {
    setup(
      new Response("<html>Bad gateway</html>", {
        status: 502,
        headers: { "Content-Type": "text/html" },
      }),
    );
    expect(await screen.findByRole("button", { name: "Try again" })).toBeTruthy();
    expect(screen.queryByTestId("status-overall")).toBeNull();
  });

  it("AC-6: null last_success_at and newest_photo_date read as never / no photos", async () => {
    setup(
      json(
        {
          status: "stale",
          rovers: [
            {
              rover: "curiosity",
              last_success_at: null,
              newest_photo_date: null,
              sync_stale: true,
              photos_stale: true,
            },
          ],
        },
        { status: 503 },
      ),
    );
    const card = await screen.findByTestId("status-rover-curiosity");
    expect(card.textContent).toMatch(/never/i);
    expect(card.textContent).toMatch(/no photos/i);
    expect(card.textContent).not.toMatch(/null|undefined|Invalid/);
  });

  it("AC-7: polls every 60 s while visible, not while hidden, and fetches at once on return", async () => {
    vi.useFakeTimers({
      toFake: ["setTimeout", "setInterval", "clearTimeout", "clearInterval", "Date"],
    });
    vi.setSystemTime(new Date("2026-11-19T12:00:00Z"));
    const m = setup(OK);
    await tick(0);
    expect(healthCalls(m)).toHaveLength(1);
    await tick(60_000);
    await tick(60_000);
    expect(healthCalls(m)).toHaveLength(3); // exactly +2 in 120 s
    setVisibility("hidden");
    await tick(180_000);
    expect(healthCalls(m)).toHaveLength(3);
    setVisibility("visible"); // hidden for >= 60 s: refresh immediately
    await tick(0);
    expect(healthCalls(m)).toHaveLength(4);
  });

  it("AC-7: Refresh requests at once and restarts the 60 s interval", async () => {
    vi.useFakeTimers({
      toFake: ["setTimeout", "setInterval", "clearTimeout", "clearInterval", "Date"],
    });
    vi.setSystemTime(new Date("2026-11-19T12:00:00Z"));
    const m = setup(OK);
    await tick(0);
    await tick(40_000);
    fireEvent.click(screen.getByTestId("status-refresh"));
    await tick(0);
    expect(healthCalls(m)).toHaveLength(2);
    await tick(59_000); // 59 s after Refresh: the old interval must not fire at 60 s total
    expect(healthCalls(m)).toHaveLength(2);
    await tick(1_000);
    expect(healthCalls(m)).toHaveLength(3); // exactly one background request in 60 s
  });

  it("AC-7: the checked-at time is shown and Refresh requests immediately", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-19T12:34:56Z"));
    const m = setup(OK);
    await screen.findByTestId("status-overall");
    await waitFor(() =>
      expect(screen.getByTestId("status-checked-at").textContent).toMatch(/Last checked/),
    );
    expect(screen.getByTestId("status-checked-at").textContent).not.toMatch(/Not checked/i);
    expect(healthCalls(m)).toHaveLength(1);
    fireEvent.click(screen.getByTestId("status-refresh"));
    await waitFor(() => expect(healthCalls(m)).toHaveLength(2));
  });

  it("AC-7: Refresh is disabled or aria-busy while a request is in flight", async () => {
    let release: (r: Response) => void = () => {};
    let n = 0;
    setup(() => {
      n += 1;
      if (n === 1) return OK;
      return new Promise<Response>((res) => (release = res));
    });
    await screen.findByTestId("status-overall");
    const btn = screen.getByTestId("status-refresh");
    fireEvent.click(btn);
    await waitFor(() =>
      expect(btn.hasAttribute("disabled") || btn.getAttribute("aria-busy") === "true").toBe(true),
    );
    release(json(OK));
    await waitFor(() =>
      expect(btn.hasAttribute("disabled") || btn.getAttribute("aria-busy") === "true").toBe(false),
    );
  });

  it("AC-5: a failed background refresh keeps the last data with an inline line, no alert", async () => {
    let n = 0;
    setup(() => {
      n += 1;
      if (n === 1) return OK;
      throw new TypeError("Failed to fetch");
    });
    await screen.findByTestId("status-overall");
    fireEvent.click(screen.getByTestId("status-refresh"));
    expect(await screen.findByText(/Last refresh failed/)).toBeTruthy();
    expect(screen.getByTestId("status-overall").textContent).toContain("All systems normal");
    expect(screen.getByTestId("status-rover-curiosity")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("AC-6: an unparseable last_success_at does not render NaN", async () => {
    setup({
      status: "ok",
      rovers: [{ ...curiosityOk, last_success_at: "not-a-date" }],
    });
    const card = await screen.findByTestId("status-rover-curiosity");
    expect(card.textContent).not.toMatch(/NaN|Invalid|undefined|null/);
  });

  it("AC-8: the header of /status links to /status (current page) and the page mentions archived rovers", async () => {
    setup(OK);
    await screen.findByTestId("status-overall");
    // moved from the footer to the main navigation (shell AC-1)
    const link = within(screen.getByRole("navigation", { name: "Main" })).getByRole("link", {
      name: "Status",
    });
    expect(link.getAttribute("href")).toBe("/status");
    expect(link.getAttribute("aria-current")).toBe("page");
    expect(link.textContent).toContain("Status");
    const text = document.body.textContent ?? "";
    for (const name of ["Spirit", "Opportunity", "Ingenuity"]) expect(text).toContain(name);
  });

  it("AC-8: the header link is present on another page and navigates to /status", async () => {
    setup(OK, "/no-such-page");
    const link = await within(await screen.findByRole("navigation", { name: "Main" })).findByRole(
      "link",
      { name: "Status" },
    );
    expect(link.getAttribute("href")).toBe("/status");
    fireEvent.click(link);
    expect((await screen.findByTestId("status-overall")).textContent).toContain(
      "All systems normal",
    );
    expect(window.location.pathname).toBe("/status");
  });
});
