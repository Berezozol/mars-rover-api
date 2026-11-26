import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page, type Route } from "@playwright/test";
import { horizontalOverflow, settle } from "./helpers";

// keys-flow (docs/proposals/frontend-keys-flow.md): AC-3 (404 while keys are off), AC-6 (the danger button),
// AC-10 (reload hint) and the axe check of the four pages. The API is mocked in the browser (page.route);
// no mail is sent, nothing is stored. Viewports come from the projects of playwright.config.ts (375 and 1280 px).

const TOKEN = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-_AbCde";
const KEY = "si_EXAMPLEEXAMPLEEXAMPLEEXAMPLE0000";
const EMAIL = "user@example.org";

const json = (route: Route, status: number, body: unknown) =>
  route.fulfill({
    status,
    contentType: "application/json",
    headers: { "Cache-Control": "no-store" },
    body: JSON.stringify(body),
  });

const notFound = (route: Route) =>
  json(route, 404, { error: { code: "not_found", message: "Not found", details: null } });

/** The keys are switched off (`API_KEYS_ENABLED=false`): every key endpoint answers 404 `not_found`. */
async function mockKeysOff(page: Page) {
  await page.route("**/api/v2/**", (route) => {
    if (new URL(route.request().url()).pathname === "/api/v2/rovers") {
      return json(route, 200, { items: [] });
    }
    return notFound(route);
  });
}

/** The keys are on, one account with one active key (`KEY`). */
async function mockKeysOn(page: Page) {
  await page.route("**/api/v2/**", (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const at = `${req.method()} ${url.pathname}`;
    if (url.pathname === "/api/v2/rovers") return json(route, 200, { items: [] });
    if (at === "GET /api/v2/account") {
      if (req.headers()["x-api-key"] !== KEY) {
        return json(route, 401, {
          error: { code: "invalid_api_key", message: "Invalid API key", details: null },
        });
      }
      return json(route, 200, {
        email: EMAIL,
        status: "active",
        created_at: "2026-12-01T10:00:00Z",
        keys: [
          {
            id: 41,
            prefix: KEY.slice(0, 11),
            created_at: "2026-12-01T10:00:00Z",
            last_used_at: null,
            revoked_at: null,
          },
        ],
        usage: { today: 3, last_30_days: 40 },
        limits: { per_minute: 120, per_hour: 10000, concurrent: 8 },
      });
    }
    return notFound(route);
  });
}

async function signIn(page: Page) {
  await page.goto("/keys/manage");
  await page.getByLabel(/^api key$/i).fill(KEY);
  await page.getByRole("button", { name: /show my account/i }).click();
  await expect(page.getByText(EMAIL).first()).toBeVisible();
}

test.describe("keys-flow AC-3 the key endpoints are switched off", () => {
  test("keys-flow AC-3 confirm, deletion and My keys say 'Not available right now'", async ({
    page,
  }) => {
    await mockKeysOff(page);
    const notAvailable = page.getByText("Not available right now");

    await page.goto(`/keys/confirm#token=${TOKEN}`);
    await page.getByRole("button", { name: /show my key/i }).click();
    await expect(notAvailable).toBeVisible();

    await page.goto(`/keys/delete#token=${TOKEN}`);
    await page.getByRole("button", { name: /yes, delete my data/i }).click();
    await expect(notAvailable).toBeVisible();
    await expect(page.getByText(/reply to any email we sent you/i)).toBeVisible();

    await page.goto("/keys/delete");
    await page.getByLabel(/^email address$/i).fill(EMAIL);
    await page.getByRole("button", { name: /email me a deletion link/i }).click();
    await expect(notAvailable).toBeVisible();
    await expect(page.getByText(/reply to any email we sent you/i)).toBeVisible();

    await page.goto("/keys/manage");
    await page.getByLabel(/^api key$/i).fill(KEY);
    await page.getByRole("button", { name: /show my account/i }).click();
    await expect(notAvailable).toBeVisible();

    await expect(page.getByText(/key not found/i)).toHaveCount(0);
    await expect(page.getByText(/reload your account/i)).toHaveCount(0);
  });
});

interface Box {
  height: number;
  width: number;
  background: string;
  border: string;
  color: string;
}

const measure = (el: Locator): Promise<Box> =>
  el.evaluate((node) => {
    const r = node.getBoundingClientRect();
    const s = getComputedStyle(node);
    return {
      height: r.height,
      width: r.width,
      background: s.backgroundColor,
      border: s.borderTopColor,
      color: s.color,
    };
  });

/** Resolves CSS custom properties to computed colours (`rgb(...)`) the way the page itself sees them. */
async function tokens(page: Page): Promise<{ err: string; surface: string }> {
  return page.evaluate(() => {
    const probe = document.createElement("span");
    document.body.append(probe);
    const resolve = (name: string) => {
      probe.style.color = `var(${name})`;
      return getComputedStyle(probe).color;
    };
    const out = { err: resolve("--err"), surface: resolve("--surface") };
    probe.remove();
    return out;
  });
}

const TRANSPARENT = new Set(["rgba(0, 0, 0, 0)", "transparent"]);

async function expectOutlineDanger(page: Page, danger: Locator, safe: Locator) {
  // Resting state: no focus (a focused button is filled) and no pointer over it.
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  await page.mouse.move(0, 0);
  const t = await tokens(page);
  const d = await measure(danger);
  const s = await measure(safe);
  // Not wider than the safe button, and both are comfortable touch targets.
  expect(d.width).toBeLessThanOrEqual(s.width + 0.5);
  expect(d.height).toBeGreaterThanOrEqual(44);
  expect(s.height).toBeGreaterThanOrEqual(44);
  // Outline: no fill (transparent or the card surface), the border and the text in the error colour.
  expect(TRANSPARENT.has(d.background) || d.background === t.surface).toBe(true);
  expect(d.border).toBe(t.err);
  expect(d.color).toBe(t.err);
  // Hover fills it.
  await danger.hover();
  await expect.poll(async () => (await measure(danger)).background).toBe(t.err);
}

/** The safe action precedes the danger one in the DOM and in the tab order (texts compared lower-cased). */
async function expectSafeFirst(
  page: Page,
  safe: Locator,
  danger: Locator,
  safeText: string,
  dangerText: string,
) {
  const safeFirst = await safe.evaluate(
    (a, b) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING),
    await danger.elementHandle(),
  );
  expect(safeFirst).toBe(true);

  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  const seen: string[] = [];
  for (let i = 0; i < 60 && !(seen.includes("safe") && seen.includes("danger")); i++) {
    await page.keyboard.press("Tab");
    seen.push(
      await page.evaluate(
        ([sa, da]) => {
          const t = (document.activeElement?.textContent ?? "").trim().toLowerCase();
          return t === sa ? "safe" : t === da ? "danger" : "other";
        },
        [safeText, dangerText],
      ),
    );
  }
  expect(seen.indexOf("safe")).toBeGreaterThanOrEqual(0);
  expect(seen.indexOf("safe")).toBeLessThan(seen.indexOf("danger"));
}

for (const scheme of ["light", "dark"] as const) {
  test.describe(`keys-flow AC-6 the danger button, ${scheme} theme`, () => {
    test.use({ colorScheme: scheme });

    test(`keys-flow AC-6 /keys/delete#token: safe first, danger outlined (${scheme})`, async ({
      page,
    }) => {
      await mockKeysOn(page);
      await page.goto(`/keys/delete#token=${TOKEN}`);
      const danger = page.getByRole("button", { name: /yes, delete my data/i });
      const safe = page.getByRole("link", { name: /no, keep everything/i });
      await expect(danger).toBeVisible();

      await expectSafeFirst(page, safe, danger, "no, keep everything", "yes, delete my data");

      await expectOutlineDanger(page, danger, safe);
      const o = await horizontalOverflow(page);
      expect(o.scrollWidth).toBeLessThanOrEqual(o.innerWidth);
    });

    test(`keys-flow AC-6 My keys: Cancel first, "Yes, delete everything" outlined, equal width (${scheme})`, async ({
      page,
    }) => {
      await mockKeysOn(page);
      await signIn(page);
      await page.getByRole("button", { name: /delete my account/i }).click();
      // The danger button stays disabled (and out of the tab order) until the box is ticked.
      await page.getByRole("checkbox", { name: /i understand/i }).check();
      const danger = page.getByRole("button", { name: /yes, delete everything/i });
      const safe = page.getByRole("button", { name: /^cancel$/i });
      await expect(danger).toBeEnabled();
      await expectSafeFirst(page, safe, danger, "cancel", "yes, delete everything");
      await expectOutlineDanger(page, danger, safe);
      const o = await horizontalOverflow(page);
      expect(o.scrollWidth).toBeLessThanOrEqual(o.innerWidth);
    });
  });
}

test.describe("keys-flow AC-10 reload after opening the link", () => {
  for (const path of ["/keys/confirm", "/keys/delete"]) {
    test(`keys-flow AC-10 ${path}: reload says the link is still valid, the token is not stored`, async ({
      page,
    }) => {
      await mockKeysOn(page);
      await page.goto(`${path}#token=${TOKEN}`);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      expect(page.url()).not.toContain(TOKEN);

      await page.reload();
      await expect(page.getByText(/your link is still valid/i)).toBeVisible();
      await expect(page.getByText(/open it again from the email/i).first()).toBeVisible();

      const stored = await page.evaluate(() =>
        JSON.stringify({ ...localStorage, ...sessionStorage }),
      );
      expect(stored).not.toContain(TOKEN);
      expect(stored).not.toContain("sol-index-link"); // the flag is removed once shown

      // Shown once: the second reload is a plain page.
      await page.reload();
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expect(page.getByText(/your link is still valid/i)).toHaveCount(0);
    });
  }
});

const SCREENS = [
  "/keys",
  `/keys/confirm#token=${TOKEN}`,
  "/keys/manage",
  `/keys/delete#token=${TOKEN}`,
];

for (const scheme of ["light", "dark"] as const) {
  test.describe(`keys-flow axe, ${scheme} theme`, () => {
    test.use({ colorScheme: scheme });

    for (const path of SCREENS) {
      test(`keys-flow axe ${path.split("#")[0]} (${scheme}): no serious or critical violation`, async ({
        page,
      }) => {
        await mockKeysOn(page);
        await page.goto(path);
        await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
        await settle(page);
        const result = await new AxeBuilder({ page }).analyze();
        const bad = result.violations.filter(
          (v) => v.impact === "serious" || v.impact === "critical",
        );
        expect(
          bad.map(
            (v) =>
              `${v.impact} ${v.id}: ${v.help} -> ${v.nodes.map((n) => n.html.slice(0, 160)).join(" | ")}`,
          ),
        ).toEqual([]);
      });
    }

    test(`keys-flow axe My keys signed in (${scheme}): no serious or critical violation`, async ({
      page,
    }) => {
      await mockKeysOn(page);
      await signIn(page);
      await settle(page);
      const result = await new AxeBuilder({ page }).analyze();
      const bad = result.violations.filter(
        (v) => v.impact === "serious" || v.impact === "critical",
      );
      expect(bad.map((v) => `${v.impact} ${v.id}: ${v.help}`)).toEqual([]);
    });
  });
}
