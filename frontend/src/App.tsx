import { Component, type ComponentChildren } from "preact";
import { Layout } from "./components/Layout";
import { Flights } from "./pages/Flights";
import { Home } from "./pages/Home";
import { NotFound } from "./pages/NotFound";
import { PhotoPage } from "./pages/Photo";
import { RouteMapPage } from "./pages/RouteMap";
import { Photos } from "./pages/Photos";
import { Samples } from "./pages/Samples";
import { Search } from "./pages/Search";
import { Stats } from "./pages/Stats";
import { Status } from "./pages/Status";
import { Timeline } from "./pages/Timeline";
import { matchRoute, useLocation } from "./router";

/** Last line of defence: a rendering bug shows a message instead of a blank page. */
export class ErrorBoundary extends Component<
  { children: ComponentChildren; resetKey: string },
  { failed: boolean; key: string }
> {
  state = { failed: false, key: this.props.resetKey };

  // A new address clears a previous error.
  static getDerivedStateFromProps(
    props: { resetKey: string },
    state: { failed: boolean; key: string },
  ) {
    return props.resetKey !== state.key ? { failed: false, key: props.resetKey } : null;
  }

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    console.error("Render error", error);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div class="alert alert-error" role="alert">
        <div>
          <div class="alert-title">Something went wrong</div>
          <p>This page could not be displayed. Please try again or go back to the start.</p>
          <a class="btn btn-sm" href="/">
            Go to the rovers
          </a>
        </div>
      </div>
    );
  }
}

export function App() {
  const { pathname } = useLocation();
  const route = matchRoute(pathname);
  let page;
  switch (route.name) {
    case "home":
      page = <Home />;
      break;
    case "photos":
      page = <Photos key={route.rover} roverId={route.rover} />;
      break;
    case "timeline":
      page = <Timeline key={route.rover} roverId={route.rover} />;
      break;
    case "map":
      page = <RouteMapPage key={route.rover} roverId={route.rover} />;
      break;
    case "flights":
      page = <Flights key={route.rover} roverId={route.rover} />;
      break;
    case "samples":
      page = <Samples key={route.rover} roverId={route.rover} />;
      break;
    case "search":
      page = <Search />;
      break;
    case "stats":
      page = <Stats />;
      break;
    case "status":
      page = <Status />;
      break;
    case "photo":
      page = <PhotoPage key={route.id} id={route.id} />;
      break;
    default:
      page = <NotFound />;
  }
  return (
    <Layout route={route} pathname={pathname}>
      <ErrorBoundary resetKey={pathname}>{page}</ErrorBoundary>
    </Layout>
  );
}
