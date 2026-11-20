import type { ComponentChildren, RefObject } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { Sample } from "../api/types";
import { hasTubePhotos, STATUS_CLASS, STATUS_LABEL, tubePhotosHref } from "../lib/samples";
import { Link } from "../router";

/** True once the element is near the viewport (or at once where IntersectionObserver is missing). */
function useNearViewport<T extends Element>(): [RefObject<T>, boolean] {
  const ref = useRef<T>(null);
  const [near, setNear] = useState(typeof IntersectionObserver === "undefined");
  useEffect(() => {
    const el = ref.current;
    if (near || !el) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setNear(true);
          io.disconnect();
        }
      },
      { rootMargin: "300px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [near]);
  return [ref, near];
}

/**
 * "Photos of the tube" (CACHECAM of the sealing sol). Shown only when `/cameras?sol=` says CACHECAM has photos
 * that sol; the question is asked once the link scrolls near the screen, a few requests at a time.
 * Nothing is shown while unknown or when it cannot be told.
 */
export function TubeLink({
  sol,
  number,
  label = "Photos of the tube",
  after,
}: {
  sol: number;
  /** Omitted where several samples share the sol: the name then mentions the sol only. */
  number?: number;
  label?: string;
  /** Text that belongs to the link (a separator) and appears only together with it. */
  after?: ComponentChildren;
}) {
  const [ref, near] = useNearViewport<HTMLSpanElement>();
  const [has, setHas] = useState<boolean | null>(null);
  useEffect(() => {
    if (!near) return;
    let live = true;
    void hasTubePhotos(sol).then((v) => {
      if (live) setHas(v);
    });
    return () => {
      live = false;
    };
  }, [near, sol]);
  return (
    <span ref={ref} class="tube-slot">
      {has === true && (
        <>
          <Link
            href={tubePhotosHref(sol)}
            aria-label={
              number === undefined
                ? `${label}: sol ${sol}`
                : `${label}: sample ${number}, sol ${sol}`
            }
          >
            {label}
          </Link>
          {after}
        </>
      )}
    </span>
  );
}

/** Status badge; an unknown status (null) shows the page's own words, uncoloured. */
export function StatusBadge({ x }: { x: Sample }) {
  return x.status ? (
    <span class={`badge ${STATUS_CLASS[x.status]}`}>{STATUS_LABEL[x.status]}</span>
  ) : (
    <span class="badge no-dot">{x.current_location.trim() || "—"}</span>
  );
}

/** Status, type and rock badges, with the type label as published. */
export function SampleBadges({ x }: { x: Sample }) {
  return (
    <div class="row" style={{ gap: 6 }}>
      <StatusBadge x={x} />
      <span class="badge no-dot">{x.sample_type_label.trim() || "—"}</span>
      {x.rock_type && <span class="badge no-dot">{x.rock_type}</span>}
    </div>
  );
}
