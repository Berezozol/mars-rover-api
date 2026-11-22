// Turns an API failure into text a person can read. One place for all error wording.
import { ApiError, HTTP_ERROR, NETWORK_ERROR } from "./client";

export interface ErrorView {
  title: string;
  message: string;
  /** Per-field problems of a `422`, already humanised. */
  fields: { label: string; message: string }[];
  code: string;
  status: number;
  /** A retry may help (network, 5xx, rate limit). */
  retryable: boolean;
  /** `503`: the service as a whole is temporarily unavailable. */
  unavailable: boolean;
  /** Pretty-printed raw response for the "Response details" block. */
  raw: string | null;
}

const FIELD_LABELS: Record<string, string> = {
  sol: "Sol",
  from_sol: "From sol",
  to_sol: "To sol",
  earth_date: "Earth date",
  camera: "Camera",
  sample_type: "Sample type",
  page: "Page",
  per_page: "Per page",
  size: "Size",
  rovers: "Rovers",
  sol_min: "From sol",
  sol_max: "To sol",
  date_min: "From date",
  date_max: "To date",
  local_time_min: "Time of day (from)",
  local_time_max: "Time of day (until)",
  golden_hour: "Golden hour",
  min_width: "Minimum width",
  min_height: "Minimum height",
  near: "Search point",
  radius_km: "Radius",
  sort: "Sort",
  group_by: "Group by",
  site: "Stop",
};

export function fieldLabel(field: string): string {
  const last = field.split(".").pop() ?? field;
  return FIELD_LABELS[last] ?? (last || "Request");
}

const MAX_INT = "2,147,483,647";

/** Pydantic-style messages are terse; say the same thing in plain words where we can. */
export function humanizeIssue(field: string, message: string): string {
  const last = field.split(".").pop() ?? field;
  const isSol =
    last === "sol" ||
    last === "from_sol" ||
    last === "to_sol" ||
    last === "sol_min" ||
    last === "sol_max";
  if (isSol && /less than or equal|greater than or equal|valid integer|parsing/i.test(message)) {
    return `must be a whole number from 0 to ${MAX_INT}.`;
  }
  if (
    (last === "earth_date" || last === "date_min" || last === "date_max") &&
    /date/i.test(message)
  ) {
    return "must be a date in the format YYYY-MM-DD.";
  }
  if (last === "page" && /10000|10,000|exceed/i.test(message)) {
    return "only the first 10,000 photos of a search can be browsed (page × per page ≤ 10,000). Narrow the search to see the rest.";
  }
  if (last === "page" && /equal|integer/i.test(message)) {
    return "must be a whole number from 1 to 1,000,000.";
  }
  if (last === "per_page" && /equal|integer/i.test(message)) {
    return "must be a whole number from 1 to 100.";
  }
  return message;
}

function pretty(body: unknown): string | null {
  if (body === null || body === undefined) return null;
  try {
    return JSON.stringify(body, null, 2);
  } catch {
    return null;
  }
}

export function describeError(error: unknown): ErrorView {
  if (!(error instanceof ApiError)) {
    return {
      title: "Something went wrong",
      message: "An unexpected error occurred. Please try again.",
      fields: [],
      code: "unknown",
      status: 0,
      retryable: true,
      unavailable: false,
      raw: null,
    };
  }
  const base = {
    fields: [] as ErrorView["fields"],
    code: error.code,
    status: error.status,
    retryable: false,
    unavailable: false,
    raw: pretty(error.body),
  };
  if (error.code === NETWORK_ERROR) {
    return {
      ...base,
      title: "Can’t reach the server",
      message: "Check your connection and try again.",
      retryable: true,
    };
  }
  if (error.status === 422) {
    return {
      ...base,
      title: "We couldn’t run this request",
      message: error.details ? "Please fix the following:" : error.message,
      fields: (error.details ?? []).map((d) => ({
        label: fieldLabel(d.field),
        message: humanizeIssue(d.field, d.message),
      })),
    };
  }
  switch (error.code) {
    case "rover_not_found":
      return { ...base, title: "Rover not found", message: "We don’t know a rover by that name." };
    case "photo_not_found":
      return { ...base, title: "Photo not found", message: "There is no photo with that id." };
    case "not_found":
      return { ...base, title: "Not found", message: "That resource doesn’t exist." };
    case "service_busy": {
      const wait = error.retryAfter ? ` Try again in about ${Math.ceil(error.retryAfter)} s.` : "";
      return {
        ...base,
        title: "The server is busy",
        message: `Heavy searches are limited so that everyone gets answers.${wait}`,
        retryable: true,
        unavailable: true,
      };
    }
    case "converter_busy": {
      const wait = error.retryAfter ? ` Try again in about ${Math.ceil(error.retryAfter)} s.` : "";
      return {
        ...base,
        title: "The image converter is busy",
        message: `Full-size frames of this rover are converted on demand, and the queue is full.${wait}`,
        retryable: true,
        unavailable: true,
      };
    }
    case "upstream_error":
      return {
        ...base,
        title: "The image source didn’t answer",
        message:
          "The archive that holds this image is unavailable or sent unreadable data. Try again later.",
        retryable: true,
      };
    case "upstream_timeout":
      return {
        ...base,
        title: "Converting the image took too long",
        message:
          "The full-size frame couldn’t be prepared in time. Try again, or use the preview size.",
        retryable: true,
      };
    case "rate_limited": {
      const wait = error.retryAfter ? ` Try again in ${Math.ceil(error.retryAfter)} s.` : "";
      return {
        ...base,
        title: "Too many requests",
        message: `You’re going a bit fast.${wait}`,
        retryable: true,
      };
    }
    default:
  }
  if (error.status === 503) {
    return {
      ...base,
      title: "Service temporarily unavailable",
      message: "The service can’t reach its database right now. Please try again in a minute.",
      retryable: true,
      unavailable: true,
    };
  }
  if (error.status >= 500 || error.code === HTTP_ERROR) {
    return {
      ...base,
      title: "Something went wrong on our side",
      message: "The server returned an error. Please try again later.",
      retryable: true,
    };
  }
  return { ...base, title: "Request failed", message: error.message };
}
