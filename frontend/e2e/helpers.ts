import type { Page } from "@playwright/test";

/** Console errors and failed API responses collected while a page is used. */
export interface Watch {
  consoleErrors: string[];
  badResponses: string[];
  /** The highest number of /api requests that were in flight at the same time. */
  maxInFlightApi: () => number;
}

const isFavicon = (url: string) => /favicon/i.test(url);

export function watchPage(page: Page): Watch {
  const consoleErrors: string[] = [];
  const badResponses: string[] = [];
  const inFlight = new Set<unknown>();
  let peak = 0;

  page.on("console", (msg) => {
    if (msg.type() !== "error") return;
    const url = msg.location().url;
    if (isFavicon(url) || isFavicon(msg.text())) return;
    // The browser's own line for the expected 503 of /health/sync (see the response handler below).
    if (
      msg.text().startsWith("Failed to load resource") &&
      msg.text().includes("status of 503") &&
      new URL(url).pathname === "/health/sync"
    ) {
      return;
    }
    consoleErrors.push(`${msg.text()} (${url})`);
  });
  page.on("pageerror", (err) => consoleErrors.push(`pageerror: ${err.message}`));
  page.on("response", (res) => {
    const url = res.url();
    if (isFavicon(url)) return;
    // The Status screen reads /health/sync, which answers 503 on purpose while the data is stale.
    if (res.status() === 503 && new URL(url).pathname === "/health/sync") return;
    if (res.status() >= 400) badResponses.push(`${res.status()} ${url}`);
  });

  const isApi = (url: string) => new URL(url).pathname.startsWith("/api/");
  page.on("request", (req) => {
    if (!isApi(req.url())) return;
    inFlight.add(req);
    peak = Math.max(peak, inFlight.size);
  });
  const done = (req: unknown) => inFlight.delete(req);
  page.on("requestfinished", done);
  page.on("requestfailed", done);

  return { consoleErrors, badResponses, maxInFlightApi: () => peak };
}

/** Page width against the viewport width: the page scrolls sideways when the first exceeds the second. */
export async function horizontalOverflow(
  page: Page,
): Promise<{ scrollWidth: number; innerWidth: number }> {
  return page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
}

/** Waits until the first load is over: the network is quiet. */
export async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle");
}
