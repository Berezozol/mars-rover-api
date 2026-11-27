import { useRef } from "preact/hooks";
import { useOverflowStrip } from "../lib/overflow";
import { roverViews, VIEW_LABEL, viewHref, type View } from "../lib/views";
import { Link } from "../router";

interface Props {
  /** Rover id. */
  rover: string;
  /** Display name for the label ("Views of Curiosity"); the id when omitted. */
  name?: string;
  current: View;
}

/** The one row of views of a rover, the same on all its pages; a view the rover lacks is not shown. */
export function ViewTabs({ rover, name, current }: Props) {
  const ref = useRef<HTMLElement>(null);
  useOverflowStrip(ref, `${rover}/${current}`);
  return (
    <nav
      class="view-tabs"
      aria-label={`Views of ${name ?? rover.charAt(0).toUpperCase() + rover.slice(1)}`}
      data-testid="view-tabs"
      ref={ref}
    >
      {roverViews(rover).map((v) => (
        <Link key={v} href={viewHref(rover, v)} aria-current={v === current ? "page" : undefined}>
          {VIEW_LABEL[v]}
        </Link>
      ))}
    </nav>
  );
}
