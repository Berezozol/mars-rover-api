import { describe, expect, it, vi } from "vitest";
import { ApiError, api, buildPath, errorFromResponse, NETWORK_ERROR } from "../src/api/client";
import { describeError, humanizeIssue } from "../src/api/errors";
import { apiError, json, mockApi, perseverance } from "./helpers";

describe("buildPath", () => {
  it("skips empty params and keeps the v2 prefix", () => {
    expect(
      buildPath("/rovers/x/photos", { sol: 5, camera: "", page: undefined, per_page: 24 }),
    ).toBe("/api/v2/rovers/x/photos?sol=5&per_page=24");
    expect(buildPath("/rovers")).toBe("/api/v2/rovers");
  });
});

describe("errorFromResponse", () => {
  it("parses the v2 error format with field details", async () => {
    const err = await errorFromResponse(
      apiError(422, "validation_error", "Invalid query parameters", [
        { field: "sol", message: "Input should be less than or equal to 2147483647" },
      ]),
    );
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(422);
    expect(err.code).toBe("validation_error");
    expect(err.details).toEqual([
      { field: "sol", message: "Input should be less than or equal to 2147483647" },
    ]);
  });

  it("reads Retry-After on 503 converter_busy", async () => {
    const err = await errorFromResponse(
      apiError(503, "converter_busy", "busy", null, { "Retry-After": "10" }),
    );
    expect(err.code).toBe("converter_busy");
    expect(err.retryAfter).toBe(10);
  });

  it("copes with a non-JSON error page", async () => {
    const err = await errorFromResponse(new Response("<html>Bad gateway</html>", { status: 502 }));
    expect(err.status).toBe(502);
    expect(err.code).toBe("http_error");
    expect(err.details).toBeNull();
  });
});

describe("api client", () => {
  it("returns parsed JSON and unwraps list items", async () => {
    const m = mockApi({ "/api/v2/rovers": { items: [perseverance] } });
    const rovers = await api.rovers();
    expect(rovers[0]?.id).toBe("perseverance");
    expect(m.calls).toHaveLength(1);
    await api.rovers(); // served from the cache
    expect(m.calls).toHaveLength(1);
  });

  it("throws ApiError for error responses", async () => {
    mockApi({ "/api/v2/photos/1": () => apiError(404, "photo_not_found", "Photo 1 not found") });
    await expect(api.photo(1)).rejects.toMatchObject({ status: 404, code: "photo_not_found" });
  });

  it("turns a network failure into a network_error ApiError", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(api.photo(1)).rejects.toMatchObject({ code: NETWORK_ERROR });
  });

  it("does not cache failures", async () => {
    let n = 0;
    mockApi({
      "/api/v2/rovers": () =>
        n++ === 0 ? apiError(503, "db_unavailable", "down") : json({ items: [] }),
    });
    await expect(api.rovers()).rejects.toBeInstanceOf(ApiError);
    await expect(api.rovers()).resolves.toEqual([]);
  });
});

describe("describeError", () => {
  it("lists fields for 422 and shows the camera hint verbatim", async () => {
    const msg = "Unknown camera 'XX'. Valid cameras: FHAZ_LEFT_A, MAST_LEFT";
    const err = await errorFromResponse(
      apiError(422, "validation_error", "Invalid query parameters", [
        { field: "camera", message: msg },
      ]),
    );
    const v = describeError(err);
    expect(v.fields).toEqual([{ label: "Camera", message: msg }]);
    expect(v.code).toBe("validation_error");
    expect(v.raw).toContain("validation_error");
  });

  it("explains db_unavailable as a temporary outage", async () => {
    const v = describeError(await errorFromResponse(apiError(503, "db_unavailable", "x")));
    expect(v.unavailable).toBe(true);
    expect(v.title).toMatch(/temporarily unavailable/i);
  });

  it("explains converter_busy, upstream_error and upstream_timeout", async () => {
    const busy = describeError(
      await errorFromResponse(apiError(503, "converter_busy", "x", null, { "Retry-After": "10" })),
    );
    expect(busy.message).toContain("10 s");
    expect(busy.retryable).toBe(true);
    const up = describeError(await errorFromResponse(apiError(502, "upstream_error", "x")));
    expect(up.retryable).toBe(true);
    const to = describeError(await errorFromResponse(apiError(504, "upstream_timeout", "x")));
    expect(to.title).toMatch(/too long/i);
  });

  it("treats a network error as retryable", () => {
    const v = describeError(
      new ApiError({ status: 0, code: NETWORK_ERROR, message: "Network error" }),
    );
    expect(v.retryable).toBe(true);
    expect(v.title).toMatch(/reach the server/i);
  });

  it("humanizes sol range messages", () => {
    expect(
      humanizeIssue("query.sol", "Input should be less than or equal to 2147483647"),
    ).toContain("0 to 2,147,483,647");
  });
});
