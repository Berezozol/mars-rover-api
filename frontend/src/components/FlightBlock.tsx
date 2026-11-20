import { api } from "../api/client";
import type { PhotoDetail } from "../api/types";
import { fmt } from "../lib/format";
import { mmss } from "../lib/flights";
import { useAsync } from "../lib/hooks";
import { Link } from "../router";

/** `PhotoDetail.flight` is meaningful for Ingenuity only; null elsewhere. */
export const isHelicopter = (photo: { rover: string }): boolean => photo.rover === "ingenuity";

/** The "Flight N" badge next to the sample type; renders nothing without a flight number. */
export function FlightBadge({ photo }: { photo: PhotoDetail }) {
  if (!isHelicopter(photo) || typeof photo.flight !== "number") return null;
  return (
    <Link
      class="badge no-dot badge-link"
      href={`/rovers/${photo.rover}/flights#flight-${photo.flight}`}
    >
      Flight {photo.flight}
    </Link>
  );
}

/**
 * The "Flight" section of the photo card (Ingenuity only). `flight` is a number when a flight
 * happened on the photo's sol (details come from `/flights/{n}`), null when it did not.
 */
export function FlightBlock({ photo }: { photo: PhotoDetail }) {
  if (!isHelicopter(photo)) return null;
  const n = photo.flight;
  if (typeof n !== "number") {
    return (
      <section aria-labelledby="fl-h">
        <h2 id="fl-h">Flight</h2>
        <p class="small muted" style={{ margin: 0 }}>
          No flight on this sol.{" "}
          <Link href={`/rovers/${photo.rover}/flights`}>See all flights</Link>.
        </p>
      </section>
    );
  }
  return <FlightDetails rover={photo.rover} n={n} sol={photo.sol} />;
}

function FlightDetails({ rover, n, sol }: { rover: string; n: number; sol: number }) {
  // The details are a bonus: if they fail the number and the links are still shown.
  const [state] = useAsync(() => api.flight(rover, n).catch(() => null), [rover, n]);
  const f = state.status === "ok" ? state.data : null;
  const flightsHref = `/rovers/${rover}/flights`;
  return (
    <section aria-labelledby="fl-h" aria-busy={state.status === "loading"}>
      <h2 id="fl-h">Flight</h2>
      <dl>
        <dt>Flight</dt>
        <dd>
          <Link href={`${flightsHref}#flight-${n}`}>Flight {n}</Link>
        </dd>
        {state.status === "loading" && (
          <>
            <dt>Details</dt>
            <dd>
              <span class="skeleton" style={{ display: "block", height: 14, width: "70%" }} />
            </dd>
          </>
        )}
        {f && (
          <>
            <dt>Date (PT)</dt>
            <dd>
              {f.date} <span class="muted">(sol {f.sol})</span>
            </dd>
            <dt>Route</dt>
            <dd>
              {f.from}
              <span aria-hidden="true"> → </span>
              <span class="visually-hidden"> to </span>
              {f.to ?? "—"}
            </dd>
            <dt>Distance</dt>
            <dd>
              {fmt(f.distance_m)} m · max altitude {f.max_altitude_m} m
            </dd>
            <dt>Duration</dt>
            <dd>
              {mmss(f.duration_s)} ({f.duration_s} s)
            </dd>
          </>
        )}
      </dl>
      <p class="loc-note">
        The flight took place on this sol. The frame may have been taken before or after it: the
        link is by sol only.
      </p>
      <p class="small" style={{ margin: "var(--s2) 0 0" }}>
        <Link href={`/rovers/${rover}?sol=${sol}`}>All photos of sol {sol}</Link> ·{" "}
        <Link href={flightsHref}>All flights</Link>
      </p>
    </section>
  );
}
