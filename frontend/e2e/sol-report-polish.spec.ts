import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page } from "@playwright/test";
import { CUR_URL, PER_URL, mockReportApi } from "./sol-report-mock";
import { horizontalOverflow, settle } from "./helpers";

// docs/proposals/sol-report-polish.md (2026-11-26), the browser part: AC-2, AC-3, AC-4, AC-5, AC-7, AC-9, AC-11
// and AC-12. The API is intercepted. Layout numbers are measured, not guessed from the CSS.

const heading = (page: Page, name: string, sol: number) =>
  page.getByRole("heading", { level: 1, name: new RegExp(`${name}.*sol ${sol}\\b`, "i") });
const charts = (page: Page) => page.locator("main svg[role='img'][aria-label*='hourly' i]");
const slider = (page: Page) => page.getByLabel(/inspect hour/i);
const output = (page: Page) => page.locator("main output").first();
const clearButton = (page: Page) => page.getByRole("button", { name: /^clear hour$/i });

async function open(page: Page, url: string, who: string, sol: number) {
  await mockReportApi(page);
  await page.goto(url);
  await expect(heading(page, who, sol)).toBeVisible();
  await expect(charts(page).first()).toBeVisible();
  await settle(page);
}

interface Bar {
  sticky: boolean;
  height: number;
  top: number;
  bottom: number;
  left: number;
  right: number;
  /** Vertical centres of the slider, the output and the clear button inside the bar. */
  centres: number[];
  hasShadeNight: boolean;
}

/** The nearest sticky or fixed ancestor of the hour slider: the pinned bar. */
async function barOf(page: Page): Promise<Bar> {
  return slider(page).evaluate((el): Bar => {
    const none = {
      sticky: false,
      height: 0,
      top: 0,
      bottom: 0,
      left: 0,
      right: 0,
      centres: [],
      hasShadeNight: false,
    };
    let n: Element | null = el;
    while (n && n !== document.body) {
      const p = getComputedStyle(n).position;
      if (p === "sticky" || p === "fixed") {
        const r = n.getBoundingClientRect();
        const parts: Element[] = [el];
        const out = n.querySelector("output");
        if (out) parts.push(out);
        for (const b of n.querySelectorAll("button")) {
          if (/clear hour/i.test(b.textContent ?? "")) parts.push(b);
        }
        const centres = parts.map((x) => {
          const b = x.getBoundingClientRect();
          return b.top + b.height / 2;
        });
        return {
          sticky: true,
          height: r.height,
          top: r.top,
          bottom: r.bottom,
          left: r.left,
          right: r.right,
          centres,
          hasShadeNight: /shade night/i.test(n.textContent ?? ""),
        };
      }
      n = n.parentElement;
    }
    return none;
  });
}

test.describe("polish AC-2 / AC-3 the hour and 'Clear hour'", () => {
  test("polish AC-2 the keyboard picks an hour, 'Clear hour' appears, and it brings back the idle state with the focus on the slider", async ({
    page,
  }) => {
    await open(page, CUR_URL, "Curiosity", 344);
    await expect(output(page)).toHaveText(/no hour selected/i);
    await expect(clearButton(page)).toHaveCount(0);
    await slider(page).focus();
    await page.keyboard.press("Home");
    await expect(output(page)).toHaveText(/00:00/);
    await expect(clearButton(page)).toBeVisible();
    await page.keyboard.press("ArrowRight");
    await expect(output(page)).toHaveText(/01:00/);
    // the button is reachable with the keyboard and works with Enter
    await clearButton(page).focus();
    await page.keyboard.press("Enter");
    await expect(output(page)).toHaveText(/no hour selected/i);
    await expect(slider(page)).toBeFocused();
    await expect(clearButton(page)).toHaveCount(0);
  });

  test("polish AC-2 a touch or a hover on a chart shows the button too, and the button clears it", async ({
    page,
  }, info) => {
    await open(page, CUR_URL, "Curiosity", 344);
    const first = charts(page).first();
    await first.evaluate((el) => el.scrollIntoView({ block: "center" }));
    const box = (await first.boundingBox())!;
    const at = { x: box.width * 0.5, y: box.height / 2 };
    if (info.project.name === "mobile") await first.tap({ position: at });
    else await first.hover({ position: at });
    await expect(clearButton(page)).toBeVisible();
    // a mouse leaving the chart clears the hover by itself (unchanged), so only a touch can click the button
    if (info.project.name !== "mobile") return;
    await clearButton(page).click();
    await expect(output(page)).toHaveText(/no hour selected/i);
  });

  test("polish AC-3 the slider keeps its width between 'No hour selected' and an hour", async ({
    page,
  }) => {
    await open(page, CUR_URL, "Curiosity", 344);
    const width = async () => (await slider(page).boundingBox())!.width;
    const idle = await width();
    await slider(page).focus();
    for (const key of ["Home", "End", "ArrowLeft"]) {
      await page.keyboard.press(key);
      await expect(clearButton(page)).toBeVisible();
      expect(Math.abs((await width()) - idle), `slider width after ${key}`).toBeLessThanOrEqual(1);
    }
    await clearButton(page).click();
    await expect(output(page)).toHaveText(/no hour selected/i);
    expect(Math.abs((await width()) - idle)).toBeLessThanOrEqual(1);
  });
});

test.describe("polish AC-4 / AC-9 the pinned hour bar at 375 x 812", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("polish AC-4 the bar is at most 64 px high, one row, without 'Shade night'; with an hour chosen too", async ({
    page,
  }) => {
    await open(page, CUR_URL, "Curiosity", 344);
    for (const state of ["idle", "selected"] as const) {
      if (state === "selected") {
        await slider(page).focus();
        await page.keyboard.press("Home");
        await expect(clearButton(page)).toBeVisible();
      }
      const bar = await barOf(page);
      expect(bar.sticky, `the bar is pinned (${state})`).toBe(true);
      expect(bar.height, `bar height (${state})`).toBeLessThanOrEqual(64);
      expect(bar.hasShadeNight, "'Shade night' is not in the pinned bar").toBe(false);
      const spread = Math.max(...bar.centres) - Math.min(...bar.centres);
      expect(spread, `slider, output and button share one row (${state})`).toBeLessThanOrEqual(16);
      expect(bar.left).toBeGreaterThanOrEqual(0);
      expect(bar.right).toBeLessThanOrEqual(375);
    }
    // 'Shade night' lives above the charts, in the page
    await expect(page.getByLabel(/shade night/i)).toBeVisible();
  });

  test("polish AC-4 the bar stays on the screen while the page scrolls", async ({ page }) => {
    await open(page, CUR_URL, "Curiosity", 344);
    await page.evaluate(() => window.scrollTo(0, 1500));
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(1000);
    const box = (await slider(page).boundingBox())!;
    expect(box.y).toBeGreaterThanOrEqual(0);
    expect(box.y + box.height).toBeLessThan(140);
  });

  test("polish AC-4 a focused element below the bar is not hidden under it, moving forward and back", async ({
    page,
  }) => {
    await open(page, CUR_URL, "Curiosity", 344);
    const hiddenUnderBar = () =>
      page.evaluate(() => {
        const a = document.activeElement as HTMLElement | null;
        if (!a || a === document.body) return null;
        let bar: HTMLElement | null = document.getElementById("hourIn") ?? a;
        while (
          bar &&
          bar !== document.body &&
          !["sticky", "fixed"].includes(getComputedStyle(bar).position)
        ) {
          bar = bar.parentElement;
        }
        if (!bar || bar === document.body || bar.contains(a)) return null;
        const r = a.getBoundingClientRect();
        const b = bar.getBoundingClientRect();
        const hidden =
          r.height > 0 &&
          r.bottom > b.top + 1 &&
          r.top < b.bottom - 1 &&
          r.right > b.left &&
          r.left < b.right;
        return hidden
          ? `${a.tagName} "${(a.textContent ?? "").trim().slice(0, 40)}" at ${r.top}`
          : null;
      });
    expect(
      await page.evaluate(() => {
        const v = getComputedStyle(document.documentElement).scrollPaddingTop;
        return Number.parseFloat(v) || 0;
      }),
      "scroll-padding-top of the page",
    ).toBeGreaterThan(0);
    await slider(page).focus();
    for (let i = 0; i < 14; i++) {
      await page.keyboard.press("Tab");
      expect(await hiddenUnderBar(), `Tab ${i + 1}`).toBeNull();
    }
    for (let i = 0; i < 14; i++) {
      await page.keyboard.press("Shift+Tab");
      expect(await hiddenUnderBar(), `Shift+Tab ${i + 1}`).toBeNull();
    }
    // a programmatic focus of a control far down the page lands below the bar as well
    const last = page.getByRole("link", { name: /photos of this sol/i }).last();
    await last.focus();
    expect(await hiddenUnderBar()).toBeNull();
  });

  test("polish AC-9 the slider and the 'Shade night' label are at least 44 px high", async ({
    page,
  }) => {
    await open(page, CUR_URL, "Curiosity", 344);
    const sliderBox = (await slider(page).boundingBox())!;
    expect(sliderBox.height, "slider").toBeGreaterThanOrEqual(44);
    const label = page.locator("label", { hasText: /shade night/i });
    const labelBox = (await label.boundingBox())!;
    expect(labelBox.height, "Shade night label").toBeGreaterThanOrEqual(44);
    // the control of that label is inside the label, so a tap on the text toggles it
    const checkbox = page.getByLabel(/shade night/i);
    const before = await checkbox.isChecked();
    await label.click({ position: { x: labelBox.width - 4, y: labelBox.height / 2 } });
    expect(await checkbox.isChecked()).toBe(!before);
  });

  test("polish AC-9 'Clear hour' is a target of 44 px as well", async ({ page }) => {
    await open(page, CUR_URL, "Curiosity", 344);
    await slider(page).focus();
    await page.keyboard.press("Home");
    const box = (await clearButton(page).boundingBox())!;
    expect(box.height).toBeGreaterThanOrEqual(44);
  });
});

test.describe("polish AC-4 a low screen: 812 x 400", () => {
  test.use({ viewport: { width: 812, height: 400 } });

  test("polish AC-4 the bar is not pinned when the screen is 500 px high or less", async ({
    page,
  }) => {
    await open(page, CUR_URL, "Curiosity", 344);
    expect((await barOf(page)).sticky, "the hour bar is pinned").toBe(false);
    await page.evaluate(() => window.scrollTo(0, 1500));
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(1000);
    await expect(slider(page)).not.toBeInViewport();
    const { scrollWidth, innerWidth } = await horizontalOverflow(page);
    expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
  });
});

test.describe("polish AC-5 the summary", () => {
  test("polish AC-5 Curiosity: the four figures under the heading", async ({ page }) => {
    await open(page, CUR_URL, "Curiosity", 344);
    const summary = page.getByTestId("sol-summary");
    await expect(summary).toBeVisible();
    await expect(summary).toContainText(/218\.7\s*µGy\/day/);
    const text = ((await summary.textContent()) ?? "").replace(/−/g, "-").replace(/\s+/g, " ");
    expect(text).toMatch(/-75(\.0)?\s*(…|\.\.\.|–|—|to)\s*-5(\.0)?\s*°C/);
    expect(text).toMatch(/745\.5\s*Pa/);
    expect(text).toMatch(/19\.20?\s*W\/m(²|2)/);
    const h1 = await heading(page, "Curiosity", 344).boundingBox();
    const s = (await summary.boundingBox())!;
    expect(s.y, "the summary is under the heading").toBeGreaterThan(h1!.y);
  });

  test("polish AC-5 Perseverance: air, pressure and ground temperature", async ({ page }) => {
    await open(page, PER_URL, "Perseverance", 100);
    const text = ((await page.getByTestId("sol-summary").textContent()) ?? "")
      .replace(/−/g, "-")
      .replace(/\s+/g, " ");
    expect(text).toMatch(/-82\.4\s*(…|\.\.\.|–|—|to)\s*-18\.9\s*°C/);
    expect(text).toMatch(/726(\.0)?\s*Pa/);
    expect(text).toMatch(/-70\.5\s*(…|\.\.\.|–|—|to)\s*-12\.3\s*°C/);
    expect(text).not.toMatch(/µGy|UV/);
  });
});

test.describe("polish AC-7 the explanations at 375 px", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("polish AC-7 the 'preliminary' line is on the screen without hovering", async ({ page }) => {
    await open(page, CUR_URL, "Curiosity", 344);
    const card = page.getByTestId("hour-chart-humidity");
    await expect(card).toContainText(/calibrat|revis|not final|may (still )?change/i);
  });

  test("polish AC-7 the sunrise 'optional' and 'Shade night' 'approx.' notes can be read by touch or keyboard", async ({
    page,
  }) => {
    await open(page, CUR_URL, "Curiosity", 344);
    const main = page.getByRole("main");
    const note = /not in the api|computed|calculated|latitude|few minutes/i;
    const text = async () => (await main.innerText()).replace(/\s+/g, " ");
    if (!note.test(await text())) {
      // not written out: a tip button must show it
      const tips = page.getByRole("button", {
        name: /approx|optional|explain|about|info|why|what|help|tip/i,
      });
      expect(await tips.count(), "a tip button").toBeGreaterThan(0);
      const first = tips.first();
      await first.focus();
      await page.keyboard.press("Enter");
      await expect.poll(text).toMatch(note);
      const box = (await first.boundingBox())!;
      expect(
        Math.min(box.width, box.height),
        "tip button is a touch target",
      ).toBeGreaterThanOrEqual(24);
    }
    const { scrollWidth, innerWidth } = await horizontalOverflow(page);
    expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
  });
});

test.describe("polish AC-11 the finger on a chart", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  async function touch(
    page: Page,
    chart: Locator,
    steps: { dx: number; dy: number }[],
    start = { fx: 0.2, fy: 0.5 },
  ) {
    const box = (await chart.boundingBox())!;
    let x = box.x + box.width * start.fx;
    let y = box.y + box.height * start.fy;
    const cdp = await page.context().newCDPSession(page);
    await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y }] });
    for (const s of steps) {
      x += s.dx;
      y += s.dy;
      await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x, y }] });
    }
    await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  }

  test("polish AC-11 dragging a finger sideways along a chart moves the hour", async ({
    page,
  }, info) => {
    test.skip(info.project.name !== "mobile", "touch emulation: the mobile project");
    await open(page, CUR_URL, "Curiosity", 344);
    const first = charts(page).first();
    await first.evaluate((el) => el.scrollIntoView({ block: "center" }));
    const hourOf = async () => Number(await slider(page).inputValue());
    // a drag to the right: eight steps of 25 px
    await touch(
      page,
      first,
      Array.from({ length: 8 }, () => ({ dx: 25, dy: 0 })),
    );
    await expect(clearButton(page)).toBeVisible();
    const right = await hourOf();
    expect(
      right,
      "20 % of the chart is early in the sol, 20 % + 200 px is much later",
    ).toBeGreaterThan(8);
    // and to the left
    await touch(
      page,
      first,
      Array.from({ length: 8 }, () => ({ dx: -25, dy: 0 })),
      { fx: 0.9, fy: 0.5 },
    );
    expect(await hourOf()).toBeLessThan(right);
    // the cursor is in the other charts too
    await expect(page.locator("main [data-testid='hour-cursor']").nth(1)).toBeAttached();
    expect(await first.evaluate((el) => getComputedStyle(el).touchAction)).toBe("pan-y");
  });

  test("polish AC-11 a vertical swipe over a chart scrolls the page", async ({ page }, info) => {
    test.skip(info.project.name !== "mobile", "touch emulation: the mobile project");
    await open(page, CUR_URL, "Curiosity", 344);
    const first = charts(page).first();
    await first.evaluate((el) => el.scrollIntoView({ block: "center" }));
    const before = await page.evaluate(() => window.scrollY);
    await touch(
      page,
      first,
      Array.from({ length: 10 }, () => ({ dx: 0, dy: -20 })),
      { fx: 0.5, fy: 0.5 },
    );
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(before + 40);
  });

  test("polish AC-11 after a gesture the page is not stuck in drag: the mouse-free slider still works", async ({
    page,
  }, info) => {
    test.skip(info.project.name !== "mobile", "touch emulation: the mobile project");
    await open(page, CUR_URL, "Curiosity", 344);
    const first = charts(page).first();
    await first.evaluate((el) => el.scrollIntoView({ block: "center" }));
    await touch(page, first, [{ dx: 60, dy: 0 }]);
    await clearButton(page).click();
    await expect(output(page)).toHaveText(/no hour selected/i);
    // a move over the chart with no finger pressed (touch and pen, buttons 0) changes nothing
    await first.evaluate(async (el) => {
      const r = el.getBoundingClientRect();
      for (const pointerType of ["touch", "pen"]) {
        for (const f of [0.3, 0.6, 0.8]) {
          el.dispatchEvent(
            new PointerEvent("pointermove", {
              bubbles: true,
              pointerId: 11,
              pointerType,
              buttons: 0,
              clientX: r.left + r.width * f,
              clientY: r.top + r.height / 2,
            }),
          );
        }
      }
      await new Promise((res) => requestAnimationFrame(() => requestAnimationFrame(res)));
    });
    await expect(output(page)).toHaveText(/no hour selected/i);
  });
});

// AC-12: no serious or critical violation, and a clean heading-order, on both rovers, both themes, 375 and 1280 px.
for (const scheme of ["light", "dark"] as const) {
  for (const width of [375, 1280] as const) {
    test.describe(`polish AC-12 axe, ${scheme} theme, ${width} px`, () => {
      test.use({
        colorScheme: scheme,
        viewport: { width, height: width === 375 ? 812 : 800 },
      });

      for (const [name, url, who, sol] of [
        ["Curiosity", CUR_URL, "Curiosity", 344],
        ["Perseverance", PER_URL, "Perseverance", 100],
      ] as const) {
        test(`polish AC-12 ${name}: no serious violation and no heading-order, idle and with an hour`, async ({
          page,
        }) => {
          await open(page, url, who, sol);
          if (name === "Curiosity")
            await expect(page.getByRole("heading", { name: /radiation/i }).first()).toBeVisible();
          const audit = async (state: string) => {
            const result = await new AxeBuilder({ page }).analyze();
            const bad = result.violations.filter(
              (v) => v.impact === "serious" || v.impact === "critical" || v.id === "heading-order",
            );
            expect(
              bad.map(
                (v) =>
                  `${state}: ${v.impact} ${v.id}: ${v.help} -> ${v.nodes.map((n) => n.html.slice(0, 140)).join(" | ")}`,
              ),
            ).toEqual([]);
          };
          await audit("idle");
          await slider(page).focus();
          await page.keyboard.press("Home");
          await expect(clearButton(page)).toBeVisible();
          await audit("hour chosen");
          const { scrollWidth, innerWidth } = await horizontalOverflow(page);
          expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
        });
      }
    });
  }
}
