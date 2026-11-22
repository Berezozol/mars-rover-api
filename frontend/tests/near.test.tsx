// Stage 19, mode "Photos near a point" of the route map (`?near=LAT,LON&radius_km=R`).
// Written against docs/proposals/stage-19-frontend.md, docs/API.md and design/mockups/near.html.
import { fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { describe, expect, it } from "vitest";
import { App } from "../src/App";
import curiosityLocation from "./fixtures/curiosity-location.json";
import curiosityTraverse from "./fixtures/curiosity-traverse.json";
import perseveranceLocation from "./fixtures/perseverance-location.json";
import perseveranceTraverse from "./fixtures/perseverance-traverse.json";
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
const NEAR = "/rovers/curiosity/map?near=-4.7331,137.3985&radius_km=2";

function setup(
  start: string,
  photosHandler: Parameters<typeof mockApi>[0][string] = page([photo(7)], 1),
) {
  const m = mockApi({
    "/api/v2/rovers": { items: [perseverance, curiosity, spirit, ingenuity, opportunity] },
    "/api/v2/rovers/opportunity/traverse": emptyRoute("opportunity"),
    "/api/v2/rovers/opportunity/location": unavailable("opportunity"),
    "/api/v2/rovers/ingenuity/traverse": emptyRoute("ingenuity"),
    "/api/v2/rovers/ingenuity/location": unavailable("ingenuity"),
    "/api/v2/rovers/curiosity/traverse": curiosityTraverse,
    "/api/v2/rovers/curiosity/location": curiosityLocation,
    "/api/v2/rovers/curiosity/cameras": {
      items: [{ code: "MAST_LEFT", name: "Mast Left", total_photos: 5 }],
    },
    "/api/v2/rovers/perseverance/traverse": perseveranceTraverse,
    "/api/v2/rovers/perseverance/location": perseveranceLocation,
    "/api/v2/rovers/perseverance/cameras": {
      items: [{ code: "SKYCAM", name: "Skycam", total_photos: 5 }],
    },
    "/api/v2/rovers/spirit/traverse": {
      type: "FeatureCollection",
      rover: "spirit",
      available: false,
      updated_at: null,
      source_max_sol: null,
      features: [],
    },
    "/api/v2/rovers/spirit/location": {
      rover: "spirit",
      available: false,
      updated_at: null,
      source_max_sol: null,
      items: [],
    },
    [PHOTOS]: photosHandler,
  });
  window.history.replaceState(null, "", start);
  render(<App />);
  return m;
}

const opportunity = { ...spirit, id: "opportunity", name: "Opportunity" };
const unavailable = (rover: string) => ({
  rover,
  available: false,
  updated_at: null,
  source_max_sol: null,
  items: [],
});
const emptyRoute = (rover: string) => ({
  type: "FeatureCollection",
  features: [],
  ...unavailable(rover),
});
const searches = (m: ReturnType<typeof mockApi>) => m.calls.filter((c) => c.pathname === PHOTOS);
const input = (id: string) => screen.getByTestId(id) as HTMLInputElement;
const setValue = (id: string, value: string) => fireEvent.input(input(id), { target: { value } });

describe("Photos near a point", () => {
  it("AC-13: the address opens the mode, asks /photos with near and radius, and shows the grid", async () => {
    const m = setup(NEAR);
    await screen.findByTestId("near-mode");
    await waitFor(() => expect(searches(m).length).toBeGreaterThan(0));
    const q = searches(m).at(-1)!.searchParams;
    expect(q.get("rovers")).toBe("curiosity");
    expect(q.get("near")).toBe("-4.7331,137.3985");
    expect(q.get("radius_km")).toBe("2");
    const results = await screen.findByTestId("near-results");
    await waitFor(() => expect(results.textContent).toContain(CREDIT));
    expect(input("near-lat").value).toBe("-4.7331");
    expect(input("near-lon").value).toBe("137.3985");
    expect(input("near-radius").value).toBe("2");
  });

  it("AC-13: the radius circle is drawn on the map with a positive radius", async () => {
    setup(NEAR);
    await screen.findByTestId("near-results");
    const ring = document.querySelector("svg circle.ring");
    expect(ring).toBeTruthy();
    expect(Number(ring!.getAttribute("r"))).toBeGreaterThan(0);
  });

  it("AC-13: the search point (pin) is drawn on the map", async () => {
    setup(NEAR);
    await screen.findByTestId("near-results");
    expect(document.querySelector("svg .pin-dot")).toBeTruthy();
    expect(document.querySelector("svg .pin-cross")).toBeTruthy();
  });

  it("AC-14: the radius defaults to 1 km", async () => {
    const m = setup("/rovers/curiosity/map?near=-4.7331,137.3985");
    await screen.findByTestId("near-mode");
    expect(input("near-radius").value).toBe("1");
    await waitFor(() => expect(searches(m).length).toBeGreaterThan(0));
    expect(Number(searches(m).at(-1)!.searchParams.get("radius_km") ?? "1")).toBe(1);
  });

  it.each([["100"], ["0.001"], ["0"], ["-3"]])(
    "AC-14: radius %s is an error at the field and sends no request",
    async (value) => {
      const m = setup(NEAR);
      await screen.findByTestId("near-results");
      await waitFor(() => expect(searches(m)).toHaveLength(1));
      setValue("near-radius", value);
      fireEvent.change(input("near-radius"));
      const err = await screen.findByTestId("near-radius-error");
      expect(err.textContent).toMatch(/0\.01|50/);
      await Promise.resolve();
      expect(searches(m)).toHaveLength(1);
    },
  );

  it.each([["abc"], [".5"], ["5."], ["+5"], ["1e1"], ["05"]])(
    "AC-14: radius %j is either refused or sent in the server's number format",
    async (value) => {
      const m = setup(NEAR);
      await screen.findByTestId("near-results");
      await waitFor(() => expect(searches(m)).toHaveLength(1));
      setValue("near-radius", value);
      fireEvent.change(input("near-radius"));
      fireEvent.keyDown(input("near-radius"), { key: "Enter" });
      await waitFor(() => {
        const refused = screen.queryByTestId("near-radius-error") !== null;
        const sent = searches(m).length > 1;
        expect(refused || sent).toBe(true);
      });
      for (const c of searches(m).slice(1)) {
        expect(c.searchParams.get("radius_km")).toMatch(/^[0-9]{1,10}(\.[0-9]{1,10})?$/);
      }
    },
  );

  it("AC-14: the radius field is marked aria-invalid while the error shows", async () => {
    setup(NEAR);
    await screen.findByTestId("near-results");
    setValue("near-radius", "100");
    await screen.findByTestId("near-radius-error");
    expect(input("near-radius").getAttribute("aria-invalid")).toBe("true");
  });

  it("AC-14: a valid radius clears the error and asks again with it", async () => {
    const m = setup(NEAR);
    await screen.findByTestId("near-results");
    setValue("near-radius", "100");
    await screen.findByTestId("near-radius-error");
    setValue("near-radius", "5");
    await waitFor(() => expect(screen.queryByTestId("near-radius-error")).toBeNull());
    await waitFor(() => expect(searches(m).at(-1)!.searchParams.get("radius_km")).toBe("5"));
  });

  it("AC-15: 'Photos within 1 km' on a selected stop opens the mode with its coordinates", async () => {
    setup("/rovers/curiosity/map?sol=45&site=4&drive=2100"); // select from the address: the list shows only the newest stops
    fireEvent.click(await screen.findByTestId("stop-near-button"));
    await waitFor(() => expect(window.location.search).toContain("near="));
    const p = new URLSearchParams(window.location.search);
    const [lat, lon] = (p.get("near") ?? "").split(",").map(Number);
    expect(lat).toBeCloseTo(-4.59043507, 3);
    expect(lon).toBeCloseTo(137.4453477, 3);
    expect(p.get("radius_km")).toBe("1");
    expect(await screen.findByTestId("near-mode")).toBeTruthy();
  });

  it("AC-16: latitude and longitude typed into the fields set the point (no mouse)", async () => {
    const m = setup(NEAR);
    await screen.findByTestId("near-results");
    setValue("near-lat", "-4.6");
    setValue("near-lon", "137.44");
    fireEvent.click(screen.getByRole("button", { name: /set point/i }));
    await waitFor(() => expect(searches(m).at(-1)!.searchParams.get("near")).toBe("-4.6,137.44"));
    await waitFor(() => expect(window.location.search).toContain("near=-4.6"));
  });

  it.each([
    ["near-lat", "95", /latitude|90/i],
    ["near-lon", "190", /longitude|180/i],
  ])("AC-16: %s = %s is an error at the field and sends no request", async (id, value, text) => {
    const m = setup(NEAR);
    await screen.findByTestId("near-results");
    await waitFor(() => expect(searches(m)).toHaveLength(1));
    setValue(id, value);
    fireEvent.click(screen.getByRole("button", { name: /set point/i }));
    const alerts = await screen.findAllByRole("alert");
    expect(alerts.some((a) => text.test(a.textContent ?? ""))).toBe(true);
    expect(searches(m)).toHaveLength(1);
    expect(input(id).getAttribute("aria-invalid")).toBe("true");
  });

  it.each([["spirit"], ["opportunity"], ["ingenuity"]])(
    "AC-17: %s has no 'Photos near a point' switch",
    async (rover) => {
      setup(`/rovers/${rover}/map`);
      await screen.findByRole("heading", { name: new RegExp(rover, "i") });
      expect(screen.queryByTestId("near-mode")).toBeNull();
      expect(screen.queryByTestId("near-mode-switch")).toBeNull();
      expect(screen.queryByText("Photos near a point")).toBeNull();
    },
  );

  it.each([["spirit"], ["opportunity"], ["ingenuity"]])(
    "AC-17: a ?near= link for %s sends no request and explains why",
    async (rover) => {
      const m = setup(`/rovers/${rover}/map?near=-4.7,137.4&radius_km=1`);
      await screen.findByRole("heading", { name: new RegExp(rover, "i") });
      expect(
        await screen.findByText(/only .*(Curiosity|Perseverance)|not available/i),
      ).toBeTruthy();
      expect(searches(m)).toHaveLength(0);
    },
  );

  it("AC-18: the coverage block says what Curiosity lacks (about half of the photos)", async () => {
    setup(NEAR);
    const block = await screen.findByTestId("near-coverage");
    expect(block.textContent).toMatch(/half/i);
  });

  it("AC-18: for Perseverance it names the landing photos", async () => {
    setup("/rovers/perseverance/map?near=18.4,77.4&radius_km=2");
    const block = await screen.findByTestId("near-coverage");
    expect(block.textContent).toMatch(/landing/i);
  });

  it("shows the 'near' error state when the photo request fails with 503", async () => {
    setup(NEAR, () => apiError(503, "db_unavailable", "down"));
    expect(await screen.findByText("Service temporarily unavailable")).toBeTruthy();
  });
});

describe("Photos near a point: failures and races", () => {
  it("503 service_busy shows the busy block with a retry that repeats the request", async () => {
    let first = true;
    const m = setup(NEAR, () =>
      first
        ? ((first = false), apiError(503, "service_busy", "busy", null, { "Retry-After": "1" }))
        : page([photo(7)], 1),
    );
    const box = await screen.findByTestId("near-busy");
    expect(box.textContent).toMatch(/radius|sol/i);
    const retry = screen.getByTestId("near-retry") as HTMLButtonElement;
    expect(retry.disabled).toBe(true);
    await waitFor(() => expect(retry.disabled).toBe(false), { timeout: 4000 });
    const before = searches(m).length;
    fireEvent.click(retry);
    await waitFor(() => expect(searches(m)).toHaveLength(before + 1));
    expect(searches(m).at(-1)!.search).toBe(searches(m)[0]!.search);
    await waitFor(() => expect(screen.queryByTestId("near-busy")).toBeNull());
  });

  it("a slow older response never replaces a newer one (race)", async () => {
    const waiting: Record<string, (r: object) => void> = {};
    const m = setup(
      NEAR,
      (url: URL) => new Promise((res) => (waiting[url.searchParams.get("radius_km") ?? ""] = res)),
    );
    await screen.findByTestId("near-results");
    await waitFor(() => expect(searches(m)).toHaveLength(1));
    setValue("near-radius", "5");
    await waitFor(() => expect(searches(m)).toHaveLength(2));
    waiting["5"]!(page([photo(1, { credit: "NEW-CREDIT" })], 1));
    expect(await screen.findByText("NEW-CREDIT")).toBeTruthy();
    waiting["2"]!(page([photo(2, { credit: "OLD-CREDIT" })], 1));
    await new Promise((r) => setTimeout(r, 20)); // let the stale answer land: absence cannot be awaited
    expect(screen.queryByText("OLD-CREDIT")).toBeNull();
  });
});
