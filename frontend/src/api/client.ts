// The single module that talks to the API. v2 errors are parsed here, once.
import type {
  Camera,
  ImageSize,
  LatestPhotos,
  Manifest,
  PhotoDetail,
  PhotoPage,
  PhotoQuery,
  Rover,
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

async function request<T>(path: string, params?: Params, signal?: AbortSignal): Promise<T> {
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
  if (!res.ok) throw await errorFromResponse(res);
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

const slug = (rover: string) => encodeURIComponent(rover);

export const api = {
  rovers: () => cached("rovers", () => request<{ items: Rover[] }>("/rovers").then((r) => r.items)),
  rover: (rover: string, signal?: AbortSignal) =>
    request<Rover>(`/rovers/${slug(rover)}`, undefined, signal),
  cameras: (rover: string) =>
    cached(`cameras:${rover}`, () =>
      request<{ items: Camera[] }>(`/rovers/${slug(rover)}/cameras`).then((r) => r.items),
    ),
  photos: (rover: string, q: PhotoQuery, signal?: AbortSignal) =>
    request<PhotoPage>(`/rovers/${slug(rover)}/photos`, { ...q }, signal),
  latest: (
    rover: string,
    q: { camera?: string; page?: number; per_page?: number } = {},
    signal?: AbortSignal,
  ) => request<LatestPhotos>(`/rovers/${slug(rover)}/photos/latest`, q, signal),
  photo: (id: number | string, signal?: AbortSignal) =>
    request<PhotoDetail>(`/photos/${encodeURIComponent(String(id))}`, undefined, signal),
  manifest: (rover: string, range?: { from_sol?: number; to_sol?: number }) =>
    cached(`manifest:${rover}:${range?.from_sol ?? ""}:${range?.to_sol ?? ""}`, () =>
      request<Manifest>(`/rovers/${slug(rover)}/manifest`, range),
    ),
};

/** Path (with API base) of the photo image endpoint, e.g. for "Open image". */
export function imageEndpoint(id: number, size: ImageSize): string {
  return apiBase() + buildPath(`/photos/${id}/image`, { size });
}

/** The query string for display in the "API: GET ..." line. */
export function photosRequestLine(rover: string, q: PhotoQuery): string {
  return `GET ${buildPath(`/rovers/${rover}/photos`, { ...q })}`;
}
