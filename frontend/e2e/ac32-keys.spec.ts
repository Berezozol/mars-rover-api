import { expect, test, type Page, type Route } from "@playwright/test";
import { horizontalOverflow, settle } from "./helpers";

// AC-32 (stage 20B): request a key -> link with #token -> "Show my key" -> key -> /keys/manage -> revoke.
// The API is mocked in the browser (page.route): no mail is sent and no database is touched.
// The built frontend must be served at E2E_BASE_URL (default http://127.0.0.1:8000), as for the other specs.

const TOKEN = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-_AbCde";
const KEY = "si_EXAMPLEEXAMPLEEXAMPLEEXAMPLE0000";
const EMAIL = "user@example.org";

interface KeyInfo {
  id: number;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

interface Mock {
  requests: { method: string; path: string; url: string; body: unknown; key: string | null }[];
}

const json = (route: Route, status: number, body: unknown, headers: Record<string, string> = {}) =>
  route.fulfill({
    status,
    contentType: "application/json",
    headers: { "Cache-Control": "no-store", ...headers },
    body: JSON.stringify(body),
  });

async function mockKeysApi(page: Page): Promise<Mock> {
  const mock: Mock = { requests: [] };
  let keys: KeyInfo[] = [];
  await page.route("**/api/v2/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const method = req.method();
    const raw = req.postData();
    mock.requests.push({
      method,
      path: url.pathname,
      url: req.url(),
      body: raw ? JSON.parse(raw) : null,
      key: req.headers()["x-api-key"] ?? null,
    });
    const at = `${method} ${url.pathname}`;
    if (at === "POST /api/v2/keys/requests") {
      return json(route, 202, { status: "accepted", mail_delayed: false });
    }
    if (at === "POST /api/v2/keys/confirm") {
      const info: KeyInfo = {
        id: 41,
        prefix: KEY.slice(0, 11),
        created_at: "2026-12-01T10:00:00Z",
        last_used_at: null,
        revoked_at: null,
      };
      keys = [info];
      return json(route, 201, {
        key: KEY,
        key_info: info,
        account: { email: EMAIL, created_at: "2026-12-01T10:00:00Z" },
      });
    }
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
        keys,
        usage: { today: 3, last_30_days: 40 },
        limits: { per_minute: 120, per_hour: 10000, concurrent: 8 },
      });
    }
    if (at === "DELETE /api/v2/account/keys/41") {
      keys = keys.map((k) => ({ ...k, revoked_at: "2026-12-01T11:00:00Z" }));
      return route.fulfill({ status: 204 });
    }
    if (url.pathname === "/api/v2/rovers") return json(route, 200, { items: [] });
    return json(route, 404, { error: { code: "not_found", message: "no mock", details: null } });
  });
  return mock;
}

test("AC-32 request a key, open the mail link, show the key, manage and revoke it", async ({
  page,
}) => {
  const mock = await mockKeysApi(page);
  const consoleErrors: string[] = [];
  page.on("pageerror", (e) => consoleErrors.push(e.message));

  // 1. /keys: ask for a link.
  await page.goto("/keys");
  await expect(page.getByRole("heading", { level: 1, name: /get an api key/i })).toBeVisible();
  await page.getByLabel(/^email address$/i).fill(EMAIL);
  await page.getByRole("button", { name: /email me a link/i }).click();
  await expect(page.getByText(/check your mail/i)).toBeVisible();
  expect(mock.requests.find((r) => r.path === "/api/v2/keys/requests")?.body).toMatchObject({
    email: EMAIL,
  });

  // 2. The link from the mail: the token leaves the address bar and nothing is spent yet.
  await page.goto(`/keys/confirm#token=${TOKEN}`);
  const show = page.getByRole("button", { name: /show my key/i });
  await expect(show).toBeVisible();
  await expect(page).not.toHaveURL(new RegExp(TOKEN));
  expect(page.url()).not.toContain("#");
  expect(mock.requests.some((r) => r.path === "/api/v2/keys/confirm")).toBe(false);

  // 3. The button spends the token; the key is shown once.
  await show.click();
  await expect(page.getByText(KEY, { exact: true })).toBeVisible();
  await expect(page.getByText(/not be shown again/i)).toBeVisible();
  expect(mock.requests.find((r) => r.path === "/api/v2/keys/confirm")?.body).toEqual({
    token: TOKEN,
    revoke_existing: false,
  });
  expect(page.url()).not.toContain(KEY);

  // 4. On to My keys by the link (a client-side navigation: the key in memory is not needed there).
  await page.getByRole("link", { name: /go to my keys/i }).click();
  await expect(page).toHaveURL(/\/keys\/manage$/);
  await page.getByLabel(/^api key$/i).fill(KEY);
  await page.getByRole("button", { name: /show my account/i }).click();
  await expect(page.getByText(EMAIL).first()).toBeVisible();
  await expect(page.getByText(/si_EXAMPLEE/).first()).toBeVisible();
  const account = mock.requests.find((r) => r.path === "/api/v2/account");
  expect(account?.key).toBe(KEY);
  expect(account?.url).not.toContain(KEY);

  // 5. Revoke with the in-place confirmation.
  await page.getByRole("button", { name: /revoke key si_EXAMPLEE/i }).click();
  await page.getByRole("button", { name: /yes, revoke/i }).click();
  await expect(page.getByText(/revoked/i).first()).toBeVisible();
  expect(
    mock.requests.some((r) => r.method === "DELETE" && r.path === "/api/v2/account/keys/41"),
  ).toBe(true);

  // Neither the key nor the token ended up in web storage, in any URL or in the address bar.
  const stored = await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }));
  expect(stored).not.toContain(KEY);
  expect(stored).not.toContain(TOKEN);
  for (const r of mock.requests) {
    expect(r.url).not.toContain(KEY);
    expect(r.url).not.toContain(TOKEN);
  }
  expect(page.url()).not.toContain(KEY);
  expect(consoleErrors).toEqual([]);
});

const SCREENS = ["/keys", `/keys/confirm#token=${TOKEN}`, "/keys/manage", "/keys/delete"];

for (const scheme of ["light", "dark"] as const) {
  test.describe(`AC-32 360 px, ${scheme} theme`, () => {
    test.use({ viewport: { width: 360, height: 800 }, colorScheme: scheme });

    for (const path of SCREENS) {
      test(`AC-32 no horizontal page scroll: ${path.split("#")[0]} (${scheme})`, async ({
        page,
      }) => {
        await mockKeysApi(page);
        await page.goto(path);
        await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
        await settle(page);
        const { scrollWidth, innerWidth } = await horizontalOverflow(page);
        expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
      });
    }

    test(`AC-32 the key and the account list fit at 360 px (${scheme})`, async ({ page }) => {
      await mockKeysApi(page);
      await page.goto(`/keys/confirm#token=${TOKEN}`);
      await page.getByRole("button", { name: /show my key/i }).click();
      await expect(page.getByText(KEY, { exact: true })).toBeVisible();
      let o = await horizontalOverflow(page);
      expect(o.scrollWidth).toBeLessThanOrEqual(o.innerWidth);

      await page.goto("/keys/manage");
      await page.getByLabel(/^api key$/i).fill(KEY);
      await page.getByRole("button", { name: /show my account/i }).click();
      await expect(page.getByText(EMAIL).first()).toBeVisible();
      o = await horizontalOverflow(page);
      expect(o.scrollWidth).toBeLessThanOrEqual(o.innerWidth);
    });
  });
}
