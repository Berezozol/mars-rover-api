// Theme: Auto (follows prefers-color-scheme), Light or Dark. Light/Dark are remembered; Auto removes the key.
const KEY = "sol-index-theme";

export type Theme = "light" | "dark";
export type ThemeChoice = "auto" | "light" | "dark";

/** The visitor's choice: the saved Light/Dark, otherwise Auto. */
export function getThemeChoice(): ThemeChoice {
  try {
    const saved = window.localStorage.getItem(KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    /* storage unavailable: fall back to what the page shows */
  }
  const set = document.documentElement.dataset.theme;
  return set === "light" || set === "dark" ? set : "auto";
}

function apply(choice: ThemeChoice): void {
  if (choice === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = choice;
}

/** Apply and remember a choice; Auto drops the saved value and the `data-theme` attribute. */
export function setThemeChoice(choice: ThemeChoice): void {
  apply(choice);
  try {
    if (choice === "auto") window.localStorage.removeItem(KEY);
    else window.localStorage.setItem(KEY, choice);
  } catch {
    /* storage unavailable (private mode, blocked): the choice just isn't remembered */
  }
}

/**
 * Call `cb` when the system theme changes, and when the choice is changed in another tab (`storage`; that
 * choice is applied here too). Returns the unsubscribe function.
 */
export function onSystemThemeChange(cb: (choice: ThemeChoice) => void): () => void {
  const mq =
    typeof window.matchMedia === "function"
      ? window.matchMedia("(prefers-color-scheme: dark)")
      : null;
  // With Auto there is no `data-theme`, so the CSS media query already follows the system; just tell the UI.
  const onMedia = () => cb(getThemeChoice());
  const onStorage = (e: StorageEvent) => {
    if (e.key !== null && e.key !== KEY) return;
    const v = e.newValue;
    const choice: ThemeChoice = v === "light" || v === "dark" ? v : "auto";
    apply(choice);
    cb(choice);
  };
  mq?.addEventListener?.("change", onMedia);
  window.addEventListener("storage", onStorage);
  return () => {
    mq?.removeEventListener?.("change", onMedia);
    window.removeEventListener("storage", onStorage);
  };
}
