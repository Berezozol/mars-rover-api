import type { ComponentChildren } from "preact";
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import { useOverflowStrip } from "../lib/overflow";
import {
  getThemeChoice,
  onSystemThemeChange,
  setThemeChoice,
  type ThemeChoice,
} from "../lib/theme";
import { lastNavigationKind, Link, savedScroll, type RouteMatch } from "../router";

interface Props {
  route: RouteMatch;
  pathname: string;
  children: ComponentChildren;
}

const DATA_ITEMS: { href: string; label: string; rover: string; set: string }[] = [
  { href: "/data/samples", label: "Rock samples", rover: "Perseverance", set: "samples" },
  { href: "/data/radiation", label: "Radiation", rover: "Curiosity", set: "radiation" },
  { href: "/data/weather", label: "Weather", rover: "Perseverance, Curiosity", set: "weather" },
];

/** "Data" disclosure menu: a button that shows a list of links; Esc and outside click close it. */
function DataMenu({ route }: { route: RouteMatch }) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const active = route.name === "dataSet" ? route.set : null;
  const here =
    route.name === "data" ||
    route.name === "dataSet" ||
    route.name === "samples" ||
    route.name === "solReport";

  // Navigating closes the menu.
  useEffect(() => setOpen(false), [route]);

  // The list is `position: fixed` so that the scrolling strip around the button cannot clip it; it is
  // placed under the button (under the whole header on a narrow screen) and follows resizes and scrolls.
  const list = useRef<HTMLUListElement>(null);
  // A flag for the CSS (the strip's fade mask must not cover the open list; no reliance on `:has`),
  // set before paint.
  useLayoutEffect(() => {
    const nav = wrap.current?.closest("nav");
    if (open) nav?.setAttribute("data-menu-open", "");
    else nav?.removeAttribute("data-menu-open");
    return () => nav?.removeAttribute("data-menu-open");
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const place = () => {
      const l = list.current;
      const b = btn.current;
      if (!l || !b) return;
      const r = b.getBoundingClientRect();
      const narrow =
        typeof window.matchMedia === "function" && window.matchMedia("(max-width: 640px)").matches;
      const header = b.closest("header");
      // The list has a 4px top margin on a wide screen; on a narrow one it spans the width (CSS).
      l.style.top = `${narrow && header ? header.getBoundingClientRect().bottom : r.bottom}px`;
      l.style.left = narrow
        ? ""
        : `${Math.max(8, Math.min(r.left, window.innerWidth - l.offsetWidth - 8))}px`;
    };
    place();
    window.addEventListener("resize", place);
    document.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      document.removeEventListener("scroll", place, true);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: Event) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        btn.current?.focus();
      }
    };
    const onFocus = (e: FocusEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    document.addEventListener("focusin", onFocus);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("focusin", onFocus);
    };
  }, [open]);

  return (
    <div class="nav-menu" ref={wrap}>
      <button
        type="button"
        class="nav-menu-btn"
        ref={btn}
        aria-expanded={open}
        aria-controls="data-menu"
        aria-current={here ? "page" : undefined}
        onClick={() => setOpen((o) => !o)}
      >
        Data
        <svg width="12" height="12" viewBox="0 0 20 20" aria-hidden="true">
          <path
            d="M4 7l6 6 6-6"
            fill="none"
            stroke="currentColor"
            stroke-width="2.5"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
      </button>
      <ul class="nav-menu-list" id="data-menu" ref={list} hidden={!open}>
        {DATA_ITEMS.map((i) => (
          <li key={i.href}>
            <Link
              href={i.href}
              aria-current={active === i.set ? "page" : undefined}
              aria-describedby={`dm-${i.set}`}
            >
              {i.label}
              <small id={`dm-${i.set}`} aria-hidden="true">
                {i.rover}
              </small>
            </Link>
          </li>
        ))}
        <li class="nav-menu-all">
          <Link href="/data" aria-current={route.name === "data" ? "page" : undefined}>
            All datasets
          </Link>
        </li>
      </ul>
    </div>
  );
}

export function Layout({ route, pathname, children }: Props) {
  const mainRef = useRef<HTMLElement>(null);
  const headerRef = useRef<HTMLElement>(null);
  const navRef = useRef<HTMLElement>(null);
  const [theme, setTheme] = useState<ThemeChoice>(getThemeChoice);

  // Auto follows the system (also while the page is open); a choice made in another tab applies here too.
  useEffect(() => onSystemThemeChange(setTheme), []);

  // The current item stays visible in the strip on a narrow screen; `data-overflow` marks hidden items.
  useOverflowStrip(navRef, pathname);

  // On a route change by a link: back to the top and move focus to the page's <h1> (or to <main> until
  // one appears) for keyboard/screen-reader users. Back/Forward keep the position and the focus: the
  // page restores the position itself (`useRestoreScroll`).
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    if (lastNavigationKind() === "pop") {
      // Back/Forward: the saved position at once (the top without one); pages with data restore it again.
      window.scrollTo?.(0, savedScroll());
      return;
    }
    window.scrollTo?.(0, 0);
    const main = mainRef.current;
    if (!main) return;
    const focusHeading = (): boolean => {
      const h1 = main.querySelector<HTMLElement>("h1");
      if (!h1) return false;
      h1.setAttribute("tabindex", "-1");
      h1.focus({ preventScroll: true });
      return true;
    };
    if (focusHeading()) return;
    // Still loading: <main> holds the focus, and the heading takes it when it appears (within 2 s),
    // unless the focus has moved elsewhere by then.
    main.focus({ preventScroll: true });
    const observer = new MutationObserver(() => {
      const a = document.activeElement;
      if (a !== main && a !== document.body) {
        observer.disconnect();
        return;
      }
      if (focusHeading()) observer.disconnect();
    });
    observer.observe(main, { childList: true, subtree: true });
    const timer = window.setTimeout(() => observer.disconnect(), 2000);
    return () => {
      observer.disconnect();
      window.clearTimeout(timer);
    };
  }, [pathname]);

  // The sticky table header on the timeline sits under the sticky site header.
  useEffect(() => {
    const set = () => {
      const h = headerRef.current?.offsetHeight;
      if (h) document.documentElement.style.setProperty("--header-h", `${h}px`);
    };
    set();
    window.addEventListener("resize", set);
    return () => window.removeEventListener("resize", set);
  }, []);

  const cur = (name: RouteMatch["name"]) => (route.name === name ? "page" : undefined);

  return (
    <>
      <a class="skip-link" href="#main">
        Skip to content
      </a>
      <header class="site-header" ref={headerRef}>
        <div class="container">
          <Link class="brand" href="/">
            <svg width="34" height="34" viewBox="0 0 64 64" aria-hidden="true">
              <defs>
                <clipPath id="brand-clip">
                  <circle cx="32" cy="32" r="24" />
                </clipPath>
                <mask id="brand-mask">
                  <rect width="64" height="64" fill="#fff" />
                  <g clip-path="url(#brand-clip)">
                    <circle cx="32" cy="62" r="29.5" fill="#000" />
                  </g>
                  <circle cx="45" cy="19" r="3.8" fill="#000" />
                  <circle cx="19" cy="21" r="1.6" fill="#000" />
                  <circle cx="28" cy="12.5" r="1.4" fill="#000" />
                  <circle cx="23" cy="31" r="1.3" fill="#000" />
                  <circle cx="32" cy="32" r="27" fill="none" stroke="#000" stroke-width="1.6" />
                </mask>
              </defs>
              <circle cx="32" cy="32" r="30" fill="currentColor" mask="url(#brand-mask)" />
              <circle cx="32" cy="62" r="26" fill="var(--accent)" clip-path="url(#brand-clip)" />
            </svg>
            Sol Index <small>unofficial</small>
          </Link>
          <nav class="nav" aria-label="Main" ref={navRef}>
            <Link href="/" aria-current={cur("home")}>
              Rovers
            </Link>
            <Link href="/search" aria-current={cur("search")} title="Search photos across rovers">
              Search
            </Link>
            <DataMenu route={route} />
            <Link
              href="/stats"
              aria-current={cur("stats")}
              title="Photo counts by month, year and camera"
            >
              Stats
            </Link>
            <Link href="/status" aria-current={cur("status")}>
              Status
            </Link>
            <a href="/docs">API docs</a>
          </nav>
          <div class="theme-field">
            <label for="theme-select">Theme</label>
            <select
              id="theme-select"
              class="theme-select"
              value={theme}
              onChange={(e) => {
                const choice = e.currentTarget.value as ThemeChoice;
                setThemeChoice(choice);
                setTheme(choice);
              }}
            >
              <option value="auto">Auto</option>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </select>
          </div>
        </div>
      </header>
      <main id="main" tabIndex={-1} ref={mainRef}>
        <div class="container">{children}</div>
      </main>
      <footer class="site-footer">
        <div class="container">
          Sol Index is an independent, unofficial project and is not affiliated with or endorsed by
          NASA or JPL. Image credit is shown next to every photo.
          <div class="footer-groups">
            <nav
              class="footer-group"
              aria-labelledby="footer-project-title"
              data-testid="footer-group-project"
            >
              <span class="footer-group-title" id="footer-project-title">
                Project
              </span>
              <div class="footer-links">
                <a href="/developers">For developers</a>
                <a href="/docs">API docs</a>
              </div>
            </nav>
            <nav
              class="footer-group"
              aria-labelledby="footer-keys-title"
              data-testid="footer-group-keys"
            >
              <span class="footer-group-title" id="footer-keys-title">
                API keys
              </span>
              <div class="footer-links">
                <Link href="/keys">Get an API key</Link>
                <Link href="/keys/manage">My keys</Link>
                <Link href="/keys/delete">Delete my data</Link>
              </div>
            </nav>
          </div>
        </div>
      </footer>
    </>
  );
}
