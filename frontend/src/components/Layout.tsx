import type { ComponentChildren } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { visibleRovers } from "../lib/format";
import { useRovers } from "../lib/hooks";
import { currentTheme, toggleTheme } from "../lib/theme";
import { Link, type RouteMatch } from "../router";

interface Props {
  route: RouteMatch;
  pathname: string;
  children: ComponentChildren;
}

export function Layout({ route, pathname, children }: Props) {
  const mainRef = useRef<HTMLElement>(null);
  const headerRef = useRef<HTMLElement>(null);
  const [rovers] = useRovers();
  const [theme, setTheme] = useState(currentTheme);

  // On every route change: back to the top and move focus to <main> for keyboard/screen-reader users.
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
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

  const own = route.name === "photos" || route.name === "timeline" ? route.rover : null;
  const list = rovers.status === "ok" ? visibleRovers(rovers.data) : [];
  const rover = own ?? list[0]?.id ?? "perseverance";

  const cur = (name: RouteMatch["name"]) => (route.name === name ? "page" : undefined);

  return (
    <>
      <a class="skip-link" href="#main">
        Skip to content
      </a>
      <header class="site-header" ref={headerRef}>
        <div class="container">
          <Link class="brand" href="/" aria-label="Sol Index home">
            <svg width="26" height="26" viewBox="0 0 26 26" aria-hidden="true">
              <circle cx="13" cy="13" r="8" fill="var(--accent)" />
              <ellipse
                cx="13"
                cy="13"
                rx="12"
                ry="4.5"
                fill="none"
                stroke="var(--text-muted)"
                stroke-width="1.4"
                transform="rotate(-20 13 13)"
              />
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
            <Link href={`/rovers/${rover}/timeline`} aria-current={cur("timeline")}>
              Timeline
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
          </div>
        </div>
      </footer>
    </>
  );
}
