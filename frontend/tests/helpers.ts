import { vi } from "vitest";
import type { Manifest, Photo, PhotoDetail, Rover, Sample } from "../src/api/types";

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

export function samplesResponse(items: Sample[]) {
  return {
    rover: "perseverance",
    source_url: "https://science.nasa.gov/mission/mars-2020-perseverance/mars-rock-samples/",
    source_updated_at: items.length ? "2026-06-20T16:45:46Z" : null,
    updated_at: items.length ? "2026-11-20T03:00:12Z" : null,
    items,
  };
}

/** Requests that no mock handled; `setup.ts` fails the test if any remain. */
export const unmatched: string[] = [];

type Result = Response | object;
type Handler = Result | ((url: URL) => Result | Promise<Result>);

/** Replace `fetch` with a router by pathname. Unmatched requests fail the test loudly. */
export function mockApi(overrides: Record<string, Handler>) {
  // The route map asks for the sample list of Perseverance; most tests do not care, so it is empty unless a test says otherwise.
  const routes: Record<string, Handler> = {
    "/api/v2/rovers/perseverance/samples": samplesResponse([]),
    ...overrides,
  };
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
  end_date: null,
  status: "active",
  max_sol: 2031,
  max_date: "2026-11-06",
  total_photos: 579518,
  cameras_count: 23,
  last_synced_at: "2026-11-17T06:00:12Z",
  data_source: "sync",
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
  end_date: "2011-05-25",
  data_source: "archive",
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

export const ingenuity: Rover = {
  ...perseverance,
  id: "ingenuity",
  name: "Ingenuity",
  mission: "mars2020",
  status: "complete",
  end_date: "2024-01-25",
  data_source: "archive",
  max_sol: 1069,
  max_date: "2024-02-22",
  total_photos: 14553,
  cameras_count: 2,
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
    width: 1648,
    height: 1200,
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
    extra: {},
    location: null,
    local_time: null,
    in_sol: null,
    ...over,
  };
}

export function page(
  items: Photo[],
  total = items.length,
  p = 1,
  per = 24,
  nearest_sols: { before: number | null; after: number | null } | null = null,
) {
  return { items, page: p, per_page: per, total, nearest_sols };
}

export function manifest(rover: Rover, sols: Manifest["sols"]): Manifest {
  return { rover, sols };
}
