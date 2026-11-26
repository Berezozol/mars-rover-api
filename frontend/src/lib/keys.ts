// Helpers shared by the API key screens.
import type { Ref } from "preact";
import { useEffect, useRef } from "preact/hooks";

/** Letters, digits, `-` and `_`; 43 characters, as the API issues them. */
const TOKEN = /^[A-Za-z0-9_-]{43}$/;

/**
 * Read `#token=...` from the address and remove the fragment at once, so the token stays out of the
 * address bar, history entries and shared links. Returns `null` when there is no well-formed token.
 */
export function takeToken(): string | null {
  const raw = window.location.hash;
  if (!raw) return null;
  const token = new URLSearchParams(raw.replace(/^#/, "")).get("token");
  window.history.replaceState(null, "", window.location.pathname + window.location.search);
  const ok = token !== null && TOKEN.test(token);
  // Remember whose flag this is: a navigation during a later `await` must not clear another page's.
  takenKey = linkFlagKey();
  setLinkFlag(ok, takenKey);
  return ok ? token : null;
}

/** `sessionStorage` key of the "this page was opened from an emailed link" flag (the value is only "1"). */
function linkFlagKey(): string {
  return `sol-index-link:${window.location.pathname.replace(/\/+$/, "")}`;
}

let takenKey: string | null = null;

function setLinkFlag(on: boolean, key: string = linkFlagKey()): void {
  try {
    if (on) window.sessionStorage.setItem(key, "1");
    else window.sessionStorage.removeItem(key);
  } catch {
    /* no storage: the page behaves as before */
  }
}

/**
 * True when this page was opened from the emailed link earlier in this tab (and reloaded since, so
 * the token is gone from the address). Reading removes the flag. Never holds the token itself.
 */
export function takeLinkFlag(): boolean {
  try {
    const had = window.sessionStorage.getItem(linkFlagKey()) === "1";
    if (had) window.sessionStorage.removeItem(linkFlagKey());
    return had;
  } catch {
    return false;
  }
}

/** The link is used up (or known bad): a reload must not say it is still valid. */
export function clearLinkFlag(): void {
  setLinkFlag(false, takenKey ?? linkFlagKey());
}

/** "about 42 minutes" from `Retry-After` seconds (rounded up); empty when the server gave none. */
export function waitText(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return "";
  const s = Math.max(1, Math.ceil(seconds));
  if (s < 60) return `${s} second${s === 1 ? "" : "s"}`;
  if (s < 3600) {
    const m = Math.ceil(s / 60);
    return `about ${m} minute${m === 1 ? "" : "s"}`;
  }
  const h = Math.ceil(s / 3600);
  return `about ${h} hour${h === 1 ? "" : "s"}`;
}

/** `2026-12-01 10:00 UTC` from an ISO timestamp. */
export function fmtUtc(iso: string | null): string | null {
  if (!iso) return null;
  return iso.replace("T", " ").slice(0, 16) + " UTC";
}

/** Pages with a token in the address ask browsers not to send a `Referer` while they are open. */
export function useNoReferrer(): void {
  useEffect(() => {
    const meta = document.createElement("meta");
    meta.name = "referrer";
    meta.content = "no-referrer";
    document.head.appendChild(meta);
    return () => meta.remove();
  }, []);
}

/** Move focus to the element behind the returned ref whenever `trigger` changes (not on first render). */
export function useFocusOn<T extends HTMLElement>(trigger: unknown): Ref<T> {
  const ref = useRef<T>(null);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    ref.current?.focus();
  }, [trigger]);
  return ref;
}

/** Copy text; returns false when the browser refuses, so the caller can fall back to selecting it. */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/** Select the contents of an element, so that Ctrl+C works when the clipboard API is not available. */
export function selectContents(el: HTMLElement | null): void {
  if (!el) return;
  const range = document.createRange();
  range.selectNodeContents(el);
  const sel = window.getSelection();
  sel?.removeAllRanges();
  sel?.addRange(range);
}
