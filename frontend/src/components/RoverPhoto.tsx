import { ROVER_PHOTOS } from "../lib/roverMedia";

/** Rover photo with a fixed aspect ratio and its credit line; renders nothing for unknown rovers. */
export function RoverPhoto({ roverId, class: cls }: { roverId: string; class?: string }) {
  const p = ROVER_PHOTOS[roverId];
  if (!p) return null;
  return (
    <figure class={`rover-photo ${cls ?? ""}`}>
      <img
        src={p.src}
        alt={p.alt}
        width={p.width}
        height={p.height}
        loading="lazy"
        decoding="async"
      />
      <figcaption>
        <span>{p.credit}</span> · <a href={p.sourceUrl}>{p.source}</a>
      </figcaption>
    </figure>
  );
}
