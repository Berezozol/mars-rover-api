import type { Photo } from "../api/types";
import { isFilterable, thumbSources, timeOf } from "../lib/format";
import { Link, qs } from "../router";
import { Thumb } from "./Thumb";

interface Props {
  photo: Photo;
  /** The list URL this tile was shown in; lets the photo page offer newer/older neighbours. */
  from?: string;
  /** Lists that mix rovers (search): name the rover and the sol on the tile. */
  showRover?: boolean;
}

const roverName = (slug: string) => slug.charAt(0).toUpperCase() + slug.slice(1);

export function photoHref(id: number, from?: string): string {
  return `/photos/${id}${qs({ from })}`;
}

export function PhotoTile({ photo: p, from, showRover }: Props) {
  const time = timeOf(p.taken_at);
  return (
    <li>
      <Link class="tile" href={photoHref(p.id, from)}>
        <Thumb
          sources={thumbSources(p.images)}
          width={p.width}
          height={p.height}
          alt={`${showRover ? `${roverName(p.rover)}, ` : ""}${p.camera.name}, sol ${p.sol}, ${time} UTC`}
        />
        <div class="tile-meta">
          <span class="cam" title={p.camera.name}>
            {p.camera.code}
          </span>
          <span class={isFilterable(p.sample_type) ? "st" : "st other"}>{p.sample_type}</span>
        </div>
        {showRover && (
          <div class="tile-meta muted tile-line">
            <span>
              <strong>{roverName(p.rover)}</strong> · sol {p.sol}
            </span>
          </div>
        )}
        <div class="tile-meta muted">
          <time dateTime={p.taken_at}>{time} UTC</time>
          <span>#{p.id}</span>
        </div>
        <div class="tile-credit" title={p.credit ? `Credit: ${p.credit}` : undefined}>
          {p.credit ?? "Credit not provided"}
        </div>
      </Link>
    </li>
  );
}
