import type { ComponentChildren } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { visibleRovers } from "../lib/format";
import { useRovers } from "../lib/hooks";
import { currentTheme, toggleTheme } from "../lib/theme";
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
      <ul class="nav-menu-list" id="data-menu" hidden={!open}>
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
  const [rovers] = useRovers();
  const [theme, setTheme] = useState(currentTheme);

  // On a route change by a link: back to the top and move focus to <main> for keyboard/screen-reader
  // users. Back/Forward keep the position: the page restores it itself (`useRestoreScroll`).
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
    mainRef.current?.focus({ preventScroll: true });
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

  const own =
    route.name === "photos" ||
    route.name === "timeline" ||
    route.name === "map" ||
    route.name === "flights" ||
    route.name === "samples"
      ? route.rover
      : null;
  const list = rovers.status === "ok" ? visibleRovers(rovers.data) : [];
  const rover = own ?? list[0]?.id ?? "perseverance";

  // Only Ingenuity flies; the item appears once the API lists it.
  const hasFlights = rovers.status === "ok" && rovers.data.some((r) => r.id === "ingenuity");

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
          <nav class="nav" aria-label="Main">
            <Link href="/" aria-current={cur("home")}>
              Rovers
            </Link>
            <Link href={`/rovers/${rover}`} aria-current={cur("photos")} title="Latest sol">
              Photos
            </Link>
            <Link href="/search" aria-current={cur("search")} title="Search photos across rovers">
              Search
            </Link>
            <Link href={`/rovers/${rover}/timeline`} aria-current={cur("timeline")}>
              Timeline
            </Link>
            <Link href={`/rovers/${rover}/map`} aria-current={cur("map")}>
              Route map
            </Link>
            {hasFlights && (
              <Link href="/rovers/ingenuity/flights" aria-current={cur("flights")}>
                Flights
              </Link>
            )}
            <DataMenu route={route} />
            <Link
              href="/stats"
              aria-current={cur("stats")}
              title="Photo counts by month, year and camera"
            >
              Stats
            </Link>
            <a href="/docs">API docs</a>
          </nav>
          <button
            class="theme-toggle"
            type="button"
            aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
            onClick={() => setTheme(toggleTheme())}
          >
            {theme === "dark" ? "Light" : "Dark"}
          </button>
        </div>
      </header>
      <main id="main" tabIndex={-1} ref={mainRef}>
        <div class="container">{children}</div>
      </main>
      <footer class="site-footer">
        <div class="container">
          Sol Index is an independent, unofficial project and is not affiliated with or endorsed by
          NASA or JPL. Image credit is shown next to every photo.
          <div class="footer-links">
            <a href="/developers">For developers</a>
            <a href="/docs">API docs</a>
            <Link href="/status" data-testid="footer-status-link">
              Status
            </Link>
          </div>
          <nav class="footer-links" aria-label="API keys">
            <Link href="/keys">Get an API key</Link>
            <Link href="/keys/manage">My keys</Link>
            <Link href="/keys/delete">Delete my data</Link>
          </nav>
        </div>
      </footer>
    </>
  );
}
