import { cleanup } from "@testing-library/preact";
import { afterEach, beforeEach, expect, vi } from "vitest";
import { clearApiCache } from "../src/api/client";
import { unmatched } from "./helpers";

beforeEach(() => {
  unmatched.length = 0;
  clearApiCache();
  sessionStorage.clear();
  window.history.replaceState(null, "", "/");
  document.documentElement.removeAttribute("data-theme");
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  cleanup();
  expect(unmatched, "requests without a mock").toEqual([]);
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
