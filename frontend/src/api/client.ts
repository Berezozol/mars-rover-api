// The single module that talks to the API. v2 errors are parsed here, once.
import type {
  AccountInfo,
  Camera,
  Flight,
  ImageSize,
  IssuedKey,
  LatestPhotos,
  LocationResponse,
  MailAccepted,
  Manifest,
  NewKey,
  PhotoDetail,
  PhotoPage,
  PhotoQuery,
  RadiationResponse,
  Rover,
  SamplesResponse,
  SearchQuery,
  StatsQuery,
  StatsResponse,
  Traverse,
  WeatherResponse,
  WeatherSolDetail,
} from "./types";

export interface FieldIssue {
  field: string;
  message: string;
}

/** Any failure of an API call: an `{"error": ...}` response, a non-JSON reply or a network error. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: FieldIssue[] | null;
  /** Seconds from `Retry-After`, if the server sent one. */
  readonly retryAfter: number | null;
  /** The parsed response body (or null), for the "Response details" block. */
  readonly body: unknown;

  constructor(init: {
    status: number;
    code: string;
    message: string;
    details?: FieldIssue[] | null;
    retryAfter?: number | null;
    body?: unknown;
  }) {
    super(init.message);
    this.name = "ApiError";
    this.status = init.status;
    this.code = init.code;
    this.details = init.details ?? null;
    this.retryAfter = init.retryAfter ?? null;
    this.body = init.body ?? null;
  }
}

export const NETWORK_ERROR = "network_error";
export const HTTP_ERROR = "http_error";

/** API base from build configuration; empty means same origin. */
export function apiBase(): string {
  const base = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";
  return base.replace(/\/+$/, "");
}

function parseDetails(value: unknown): FieldIssue[] | null {
  if (!Array.isArray(value)) return null;
  const out: FieldIssue[] = [];
  for (const item of value) {
    if (item && typeof item === "object") {
      const rec = item as Record<string, unknown>;
      out.push({ field: String(rec.field ?? ""), message: String(rec.message ?? "") });
    }
  }
  return out.length ? out : null;
}

function parseRetryAfter(res: Response): number | null {
  const raw = res.headers.get("Retry-After");
  if (!raw) return null;
  const n = Number(raw);
  return Number.isFinite(n) && n >= 0 ? n : null;
}

/** Turn a failed `Response` into an `ApiError` (never throws). */
export async function errorFromResponse(res: Response): Promise<ApiError> {
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  const err =
    body && typeof body === "object" ? (body as { error?: Record<string, unknown> }).error : null;
  const retryAfter = parseRetryAfter(res);
  if (err && typeof err === "object" && typeof err.code === "string") {
    return new ApiError({
      status: res.status,
      code: err.code,
      message: typeof err.message === "string" ? err.message : res.statusText,
      details: parseDetails(err.details),
      retryAfter,
      body,
    });
  }
  return new ApiError({
    status: res.status,
    code: HTTP_ERROR,
    message: res.statusText || `HTTP ${res.status}`,
    retryAfter,
    body,
  });
}

type Params = Record<string, string | number | undefined | null>;

export function buildPath(path: string, params?: Params): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  }
  const s = qs.toString();
  return `/api/v2${path}${s ? `?${s}` : ""}`;
}

async function request<T>(
  path: string,
  params?: Params,
  signal?: AbortSignal,
  retry429 = true,
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(apiBase() + buildPath(path, params), {
      headers: { Accept: "application/json" },
      signal,
    });
  } catch (e) {
    if (signal?.aborted) throw e;
    throw new ApiError({ status: 0, code: NETWORK_ERROR, message: "Network error" });
  }
  if (!res.ok) {
    const err = await errorFromResponse(res);
    // The limit on concurrent requests answers 429 with `Retry-After: 1`: wait that long once and
    // repeat. A long wait (the per-minute window) is left to the visitor.
    if (err.status === 429 && retry429 && (err.retryAfter ?? 1) <= 2 && !signal?.aborted) {
      const wait = Math.min(Math.max(err.retryAfter ?? 1, 1), 5) * 1000;
      await new Promise((resolve) => setTimeout(resolve, wait));
      if (!signal?.aborted) return request<T>(path, params, signal, false);
    }
    throw err;
  }
  try {
    return (await res.json()) as T;
  } catch {
    throw new ApiError({ status: res.status, code: HTTP_ERROR, message: "Unreadable response" });
  }
}

// Small in-memory cache for data that is the same on every screen (rovers, cameras, manifest).
const cache = new Map<string, Promise<unknown>>();
function cached<T>(key: string, load: () => Promise<T>): Promise<T> {
  const hit = cache.get(key);
  if (hit) return hit as Promise<T>;
  const p = load().catch((e) => {
    cache.delete(key);
    throw e;
  });
  cache.set(key, p);
  return p;
}
export function clearApiCache(): void {
  cache.clear();
}

// At most a few queued requests at once, for lists that ask one small question per row.
const MAX_QUEUED = 3;
let queuedActive = 0;
const queuedWaiting: (() => void)[] = [];
async function queued<T>(run: () => Promise<T>): Promise<T> {
  // A finished request hands its slot straight to the next waiter, so the count never dips.
  if (queuedActive >= MAX_QUEUED) await new Promise<void>((resolve) => queuedWaiting.push(resolve));
  else queuedActive++;
  try {
    return await run();
  } finally {
    const next = queuedWaiting.shift();
    if (next) next();
    else queuedActive--;
  }
}

/** Cross-rover photo search: `GET /api/v2/photos`. Can answer `503 service_busy` with `Retry-After`. */
export function searchPhotos(query: SearchQuery, signal?: AbortSignal): Promise<PhotoPage> {
  return request<PhotoPage>("/photos", { ...query }, signal);
}

/** Counts by rover, camera, year or month: `GET /api/v2/photos/stats`. */
export function photoStats(query: StatsQuery, signal?: AbortSignal): Promise<StatsResponse> {
  return request<StatsResponse>("/photos/stats", { ...query }, signal);
}

/** Body of `GET /health/sync` (also sent with `503`). */
export interface HealthRover {
  rover: string;
  last_success_at: string | null;
  newest_photo_date: string | null;
  sync_stale: boolean;
  photos_stale: boolean;
}
export interface HealthSync {
  status: "ok" | "stale" | "degraded";
  rovers?: HealthRover[];
  db?: string;
}

/** `GET /health/sync` (outside `/api`): the JSON body is read at any HTTP status, `503` included. */
export async function healthSync(signal?: AbortSignal): Promise<HealthSync> {
  let res: Response;
  try {
    res = await fetch(apiBase() + "/health/sync", {
      headers: { Accept: "application/json" },
      cache: "no-store",
      signal,
    });
  } catch (e) {
    if (signal?.aborted) throw e;
    throw new ApiError({ status: 0, code: NETWORK_ERROR, message: "Network error" });
  }
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    throw new ApiError({ status: res.status, code: HTTP_ERROR, message: "Unreadable response" });
  }
  const st = body && typeof body === "object" ? (body as { status?: unknown }).status : null;
  if (st !== "ok" && st !== "stale" && st !== "degraded") {
    throw new ApiError({
      status: res.status,
      code: HTTP_ERROR,
      message: "Unreadable response",
      body,
    });
  }
  return body as HealthSync;
}

/**
 * `POST` / `DELETE` for the key screens. Never retried, never cached, no cookies and no `Referer`
 * (the body or `X-Api-Key` header holds a secret). A `204` gives `undefined`.
 */
async function send<T>(
  method: "POST" | "DELETE",
  path: string,
  opts: { body?: unknown; apiKey?: string } = {},
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.apiKey) headers["X-Api-Key"] = opts.apiKey;
  let res: Response;
  try {
    res = await fetch(apiBase() + buildPath(path), {
      method,
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      cache: "no-store",
      credentials: "omit",
      referrerPolicy: "no-referrer",
    });
  } catch {
    throw new ApiError({ status: 0, code: NETWORK_ERROR, message: "Network error" });
  }
  if (!res.ok) throw await errorFromResponse(res);
  if (res.status === 204) return undefined as T;
  try {
    return (await res.json()) as T;
  } catch {
    throw new ApiError({ status: res.status, code: HTTP_ERROR, message: "Unreadable response" });
  }
}

/** `GET /account` with the key in a header (never in the address). */
async function getAccount(apiKey: string, signal?: AbortSignal): Promise<AccountInfo> {
  let res: Response;
  try {
    res = await fetch(apiBase() + buildPath("/account"), {
      headers: { Accept: "application/json", "X-Api-Key": apiKey },
      cache: "no-store",
      credentials: "omit",
      referrerPolicy: "no-referrer",
      signal,
    });
  } catch (e) {
    if (signal?.aborted) throw e;
    throw new ApiError({ status: 0, code: NETWORK_ERROR, message: "Network error" });
  }
  if (!res.ok) throw await errorFromResponse(res);
  try {
    return (await res.json()) as AccountInfo;
  } catch {
    throw new ApiError({ status: res.status, code: HTTP_ERROR, message: "Unreadable response" });
  }
}

/** API keys (stage 20B). `website` is the honeypot: sent only when something was typed into it. */
export const keysApi = {
  requestKey: (email: string, website = "") =>
    send<MailAccepted>("POST", "/keys/requests", {
      body: website ? { email, website } : { email },
    }),
  confirmKey: (token: string, revokeExisting = false) =>
    send<IssuedKey>("POST", "/keys/confirm", {
      body: { token, revoke_existing: revokeExisting },
    }),
  account: getAccount,
  createKey: (apiKey: string) => send<NewKey>("POST", "/account/keys", { apiKey }),
  revokeKey: (apiKey: string, id: number) =>
    send<void>("DELETE", `/account/keys/${id}`, { apiKey }),
  deleteAccount: (apiKey: string) => send<void>("DELETE", "/account", { apiKey }),
  requestDeletion: (email: string, website = "") =>
    send<MailAccepted>("POST", "/account/deletion-requests", {
      body: website ? { email, website } : { email },
    }),
  confirmDeletion: (token: string) =>
    send<void>("POST", "/account/deletion-confirm", { body: { token } }),
};

const slug = (rover: string) => encodeURIComponent(rover);

export const api = {
  healthSync,
  rovers: () => cached("rovers", () => request<{ items: Rover[] }>("/rovers").then((r) => r.items)),
  rover: (rover: string, signal?: AbortSignal) =>
    request<Rover>(`/rovers/${slug(rover)}`, undefined, signal),
  /** Cameras of a rover; with `sol`, `total_photos` counts that sol only. */
  cameras: (rover: string, sol?: number) =>
    cached(`cameras:${rover}:${sol ?? ""}`, () =>
      queued(() =>
        request<{ items: Camera[] }>(`/rovers/${slug(rover)}/cameras`, { sol }).then(
          (r) => r.items,
        ),
      ),
    ),
  /** Same data and cache as `cameras(rover, sol)`, but at most a few requests in flight (one per sample row). */
  camerasQueued: (rover: string, sol: number) =>
    cached(`cameras:${rover}:${sol}`, () =>
      queued(() =>
        request<{ items: Camera[] }>(`/rovers/${slug(rover)}/cameras`, { sol }).then(
          (r) => r.items,
        ),
      ),
    ),
  photos: (rover: string, q: PhotoQuery, signal?: AbortSignal) =>
    request<PhotoPage>(`/rovers/${slug(rover)}/photos`, { ...q }, signal),
  latest: (
    rover: string,
    q: { camera?: string; sample_type?: string; page?: number; per_page?: number } = {},
    signal?: AbortSignal,
  ) => request<LatestPhotos>(`/rovers/${slug(rover)}/photos/latest`, q, signal),
  /** `camera` / `sample_type` only shape `in_sol` (the position among the filtered photos of the sol). */
  photo: (
    id: number | string,
    signal?: AbortSignal,
    ctx: { camera?: string; sample_type?: string } = {},
  ) => request<PhotoDetail>(`/photos/${encodeURIComponent(String(id))}`, { ...ctx }, signal),
  manifest: (rover: string, range?: { from_sol?: number; to_sol?: number }) =>
    cached(`manifest:${rover}:${range?.from_sol ?? ""}:${range?.to_sol ?? ""}`, () =>
      request<Manifest>(`/rovers/${slug(rover)}/manifest`, range),
    ),
  searchPhotos,
  photoStats,
  /** Stops of a rover (whole list is fetched once per rover and cached). */
  location: (rover: string) =>
    cached(`location:${rover}`, () => request<LocationResponse>(`/rovers/${slug(rover)}/location`)),
  /** Only the last stop (`items` has one element, or none). Not cached: it is one small request. */
  locationLatest: (rover: string, signal?: AbortSignal) =>
    request<LocationResponse>(`/rovers/${slug(rover)}/location`, { latest: "true" }, signal),
  /** The flight log (Ingenuity only): all flights in one response, flight ASC. */
  flights: (rover: string) =>
    cached(`flights:${rover}`, () =>
      request<{ items: Flight[] }>(`/rovers/${slug(rover)}/flights`).then((r) => r.items),
    ),
  /** One flight; taken from the flight list when that is already loaded, else `/flights/{n}`. */
  flight: (rover: string, n: number): Promise<Flight> => {
    const list = cache.get(`flights:${rover}`) as Promise<Flight[]> | undefined;
    const single = () =>
      cached(`flight:${rover}:${n}`, () => request<Flight>(`/rovers/${slug(rover)}/flights/${n}`));
    if (!list) return single();
    return list.then(
      (items) => items.find((f) => f.flight === n) ?? single(),
      () => single(),
    );
  },
  /** All samples of a rover (Perseverance only; 30 rows, no pagination): fetched once and cached. */
  samples: (rover: string) =>
    cached(`samples:${rover}`, () => request<SamplesResponse>(`/rovers/${slug(rover)}/samples`)),
  /** Daily RAD dose summaries (Curiosity only): the whole history in one response, fetched once and cached. */
  radiation: (rover: string) =>
    cached(`radiation:${rover}`, () =>
      request<RadiationResponse>(`/rovers/${slug(rover)}/radiation`),
    ),
  /** Daily weather summaries of a rover (Curiosity, Perseverance): the whole history in one response, cached. */
  weather: (rover: string) =>
    cached(`weather:${rover}`, () => request<WeatherResponse>(`/rovers/${slug(rover)}/weather`)),
  /** One sol of weather with the 24 hourly values (`404 sol_not_found` when the archive has none). */
  weatherSol: (rover: string, sol: number, signal?: AbortSignal) =>
    request<WeatherSolDetail>(`/rovers/${slug(rover)}/weather/${sol}`, undefined, signal),
  /** The whole route as GeoJSON (large for Curiosity: fetched once per rover and cached). */
  traverse: (rover: string) =>
    cached(`traverse:${rover}`, () => request<Traverse>(`/rovers/${slug(rover)}/traverse`)),
};

/** The query string for the "API: GET ..." line of the search and stats pages. */
export function requestLine(path: string, q: Record<string, string | number | undefined | null>) {
  return `GET ${buildPath(path, q).replace(/%2C/g, ",").replace(/%3A/g, ":")}`;
}

/** Path (with API base) of the photo image endpoint, e.g. for "Open image". */
export function imageEndpoint(id: number, size: ImageSize): string {
  return apiBase() + buildPath(`/photos/${id}/image`, { size });
}

/** The query string for display in the "API: GET ..." line. */
export function photosRequestLine(rover: string, q: PhotoQuery): string {
  return `GET ${buildPath(`/rovers/${rover}/photos`, { ...q })}`;
}
