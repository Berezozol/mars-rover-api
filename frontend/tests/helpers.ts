import { vi } from "vitest";
import type { Manifest, Photo, PhotoDetail, Rover } from "../src/api/types";

export function json(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

export function apiError(
  status: number,
  code: string,
  message: string,
  details: { field: string; message: string }[] | null = null,
  headers: Record<string, string> = {},
): Response {
  return json({ error: { code, message, details } }, { status, headers });
}

/** Requests that no mock handled; `setup.ts` fails the test if any remain. */
export const unmatched: string[] = [];

type Result = Response | object;
type Handler = Result | ((url: URL) => Result | Promise<Result>);

/** Replace `fetch` with a router by pathname. Unmatched requests fail the test loudly. */
export function mockApi(routes: Record<string, Handler>) {
  const calls: URL[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    calls.push(url);
    const handler = routes[url.pathname];
    if (!handler) {
      unmatched.push(url.pathname + url.search);
      return apiError(404, "not_found", `no mock for ${url.pathname}`);
    }
    const out = typeof handler === "function" ? await handler(url) : handler;
    return out instanceof Response ? out : json(out);
  });
  vi.stubGlobal("fetch", fn);
  return { fn, calls, last: (path: string) => calls.filter((c) => c.pathname === path).at(-1) };
}

export const perseverance: Rover = {
  id: "perseverance",
  name: "Perseverance",
  mission: "mars2020",
  launch_date: "2020-07-30",
  landing_date: "2021-02-18",
  status: "active",
  max_sol: 2031,
  max_date: "2026-11-06",
  total_photos: 579518,
  cameras_count: 23,
  last_synced_at: "2026-11-17T06:00:12Z",
};
export const curiosity: Rover = {
  ...perseverance,
  id: "curiosity",
  name: "Curiosity",
  mission: "msl",
  landing_date: "2012-08-06",
  max_sol: 5066,
  max_date: "2026-11-07",
  total_photos: 1184530,
  cameras_count: 17,
};
export const spirit: Rover = {
  ...perseverance,
  id: "spirit",
  name: "Spirit",
  mission: "mer",
  landing_date: "2004-01-04",
  status: "complete",
  max_sol: 2208,
  max_date: "2010-03-21",
  total_photos: 124550,
  cameras_count: 9,
};
export const pending: Rover = {
  ...spirit,
  id: "opportunity",
  name: "Opportunity",
  max_sol: null,
  max_date: null,
};

export function photo(id: number, over: Partial<Photo> = {}): Photo {
  return {
    id,
    rover: "perseverance",
    sol: 2031,
    earth_date: "2026-11-06",
    taken_at: "2026-11-06T12:16:25Z",
    camera: { code: "SUPERCAM_RMI", name: "SuperCam Remote Micro Imager" },
    sample_type: "full",
    title: "Mars Perseverance Sol 2031: SuperCam Camera",
    img_src: `https://img.example/${id}.png`,
    images: {
      small: `https://img.example/${id}_320.jpg`,
      medium: `https://img.example/${id}_800.jpg`,
      large: `https://img.example/${id}_1200.jpg`,
      full: `https://img.example/${id}.png`,
    },
    credit: "NASA/JPL-Caltech/LANL/CNES/IRAP",
    source: { id: `SRC_${id}`, link: `https://source.example/${id}` },
    ...over,
  };
}

export function detail(id: number, over: Partial<PhotoDetail> = {}): PhotoDetail {
  return {
    ...photo(id),
    caption: "A caption.",
    received_at: "2026-11-07T03:41:09Z",
    site: 112,
    drive: 1874,
    extra: { dimension: "(2048,2048)" },
    ...over,
  };
}

export function page(items: Photo[], total = items.length, p = 1, per = 24) {
  return { items, page: p, per_page: per, total };
}

export function manifest(rover: Rover, sols: Manifest["sols"]): Manifest {
  return { rover, sols };
}
