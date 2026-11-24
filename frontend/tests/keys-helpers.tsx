// Stage 20B helpers: a method-aware fetch mock for the key screens. Written against docs/API.md
// ("Ключи API (этап 20B)"), not against the implementation.
import { vi } from "vitest";
import { apiError, json, perseverance, curiosity, spirit, ingenuity, unmatched } from "./helpers";

export const TOKEN = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-_AbCde"; // 43 base64url characters
export const KEY = "si_EXAMPLEEXAMPLEEXAMPLEEXAMPLE0000"; // si_ + 32 characters
export const NEW_KEY = "si_NEWKEYNEWKEYNEWKEYNEWKEYNEWKEY00";
export const EMAIL = "user@example.org";

export interface KeyInfo {
  id: number;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

export const key41: KeyInfo = {
  id: 41,
  prefix: "si_EXAMPLEE",
  created_at: "2026-12-01T10:00:00Z",
  last_used_at: "2026-12-03T08:12:00Z",
  revoked_at: null,
};
export const key44: KeyInfo = {
  id: 44,
  prefix: "si_EXAMPLEF",
  created_at: "2026-12-04T12:00:00Z",
  last_used_at: null,
  revoked_at: null,
};
export const key39Revoked: KeyInfo = {
  id: 39,
  prefix: "si_EXAMPLED",
  created_at: "2026-11-20T09:30:00Z",
  last_used_at: "2026-11-28T17:05:00Z",
  revoked_at: "2026-12-01T10:00:00Z",
};

export function accountBody(keys: KeyInfo[], over: Record<string, unknown> = {}) {
  return {
    email: EMAIL,
    status: "active",
    created_at: "2026-12-01T10:00:00Z",
    keys,
    usage: { today: 312, last_30_days: 10450 },
    limits: { per_minute: 120, per_hour: 10000, concurrent: 8 },
    ...over,
  };
}

export const confirmBody = {
  key: KEY,
  key_info: key41,
  account: { email: EMAIL, created_at: "2026-12-01T10:00:00Z" },
};

export interface Call {
  method: string;
  path: string;
  url: URL;
  headers: Headers;
  body: unknown;
}

export type KeyHandler = (call: Call) => Response | Promise<Response>;

export const noContent = () => new Response(null, { status: 204 });
export const accepted = (delayed = false) =>
  json({ status: "accepted", mail_delayed: delayed }, { status: 202 });

/** Routes are keyed `"METHOD /path"`. Anything else fails the test through `unmatched`. */
export function mockKeysApi(routes: Record<string, KeyHandler | Response>) {
  const calls: Call[] = [];
  const base: Record<string, KeyHandler | Response> = {
    "GET /api/v2/rovers": json({ items: [perseverance, curiosity, spirit, ingenuity] }),
    ...routes,
  };
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const req = input instanceof Request ? input : null;
    const url = new URL(req ? req.url : String(input), "http://localhost");
    const method = (init?.method ?? req?.method ?? "GET").toUpperCase();
    const headers = new Headers(init?.headers ?? req?.headers);
    const raw = init?.body ?? (req ? await req.clone().text() : undefined);
    let body: unknown = null;
    if (typeof raw === "string" && raw) {
      try {
        body = JSON.parse(raw);
      } catch {
        body = raw;
      }
    }
    const call: Call = { method, path: url.pathname, url, headers, body };
    calls.push(call);
    const handler = base[`${method} ${url.pathname}`];
    if (!handler) {
      unmatched.push(`${method} ${url.pathname}${url.search}`);
      return apiError(404, "not_found", "no mock");
    }
    return typeof handler === "function" ? await handler(call) : handler.clone();
  });
  vi.stubGlobal("fetch", fn);
  return {
    fn,
    calls,
    to: (method: string, path: string) =>
      calls.filter((c) => c.method === method && c.path === path),
    /** The n-th call of this kind; fails the test if there is none. */
    one: (method: string, path: string, n = 0): Call => {
      const hit = calls.filter((c) => c.method === method && c.path === path)[n];
      if (!hit) throw new Error(`no call #${n} to ${method} ${path}`);
      return hit;
    },
  };
}

/** A promise settled by the test: holds a request open to check the "sending" state. */
export function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}
