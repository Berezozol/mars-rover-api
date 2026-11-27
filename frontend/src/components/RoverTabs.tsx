import type { Rover } from "../api/types";
import { visibleRovers } from "../lib/format";
import { roverViews, type View } from "../lib/views";
import { Link } from "../router";

interface Props {
  rovers: Rover[];
  current: string;
  /** Where a tab leads: the photo browser (latest sol) or the timeline. */
  to: (roverId: string) => string;
  /** Only rovers that have this view (e.g. "map") are listed. */
  view?: View;
}

/** Tabs of rovers that have data. Opening a rover leads to its latest sol. */
export function RoverTabs({ rovers, current, to, view }: Props) {
  return (
    <nav class="rover-tabs" aria-label="Rover">
      {visibleRovers(rovers)
        .filter((r) => !view || roverViews(r.id).includes(view))
        .map((r) => (
          <Link
            key={r.id}
            href={to(r.id)}
            aria-current={r.id === current ? "page" : undefined}
            title="Opens the latest sol"
          >
            {r.name}
          </Link>
        ))}
    </nav>
  );
}
