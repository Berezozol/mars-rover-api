import type { Rover } from "../api/types";
import { visibleRovers } from "../lib/format";
import { Link } from "../router";

interface Props {
  rovers: Rover[];
  current: string;
  /** Where a tab leads: the photo browser (latest sol) or the timeline. */
  to: (roverId: string) => string;
}

/** Tabs of rovers that have data. Opening a rover leads to its latest sol. */
export function RoverTabs({ rovers, current, to }: Props) {
  return (
    <nav class="rover-tabs" aria-label="Rover">
      {visibleRovers(rovers).map((r) => (
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
