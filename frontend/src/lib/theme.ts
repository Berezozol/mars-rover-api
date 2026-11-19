// Light/dark theme: follows prefers-color-scheme until the visitor picks one; the pick is remembered.
const KEY = "sol-index-theme";

export type Theme = "light" | "dark";

export function systemTheme(): Theme {
  return typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

export function currentTheme(): Theme {
  const set = document.documentElement.dataset.theme;
  return set === "light" || set === "dark" ? set : systemTheme();
}

export function toggleTheme(): Theme {
  const next: Theme = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try {
    window.localStorage.setItem(KEY, next);
  } catch {
    /* storage unavailable (private mode, blocked): the choice just isn't remembered */
  }
  return next;
}

/** Re-apply a saved choice (index.html already does this before first paint). */
export function restoreTheme(): void {
  try {
    const saved = window.localStorage.getItem(KEY);
    if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
  } catch {
    /* ignore */
  }
}
