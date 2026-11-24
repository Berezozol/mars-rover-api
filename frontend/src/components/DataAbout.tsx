import type { ComponentChildren } from "preact";
import { safeHref } from "../lib/format";

/** A responsive grid of label -> value pairs; columns never get narrower than 160 px, so values do not break mid-word. */
export function Facts({ items }: { items: [label: string, value: ComponentChildren][] }) {
  return (
    <dl class="facts">
      {items.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export const dateOnly = (iso: string) => iso.slice(0, 10);

interface AboutProps {
  /** What is measured, units. */
  measures: ComponentChildren;
  /** Name and (optional) link of the source. */
  sourceName: string;
  sourceUrl?: string | null;
  /** How often the source is published and how often Sol Index checks it. */
  schedule: ComponentChildren;
  credit?: ComponentChildren;
  citation?: ComponentChildren;
  /** Coverage and lag notes. */
  notes?: ComponentChildren;
  /** API endpoints (paths after /api/v2). */
  endpoints: string[];
}

/** The "About the data" block shared by the dataset pages. */
export function About(p: AboutProps) {
  const href = safeHref(p.sourceUrl ?? undefined);
  return (
    <section class="card about" aria-labelledby="about-h">
      <h2 id="about-h">About the data</h2>
      <dl class="about-list">
        <dt>What is measured</dt>
        <dd>{p.measures}</dd>
        <dt>Source</dt>
        <dd>
          {href ? (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {p.sourceName} ↗
            </a>
          ) : (
            p.sourceName
          )}
        </dd>
        <dt>Updates</dt>
        <dd>{p.schedule}</dd>
        {p.notes && (
          <>
            <dt>Coverage and lag</dt>
            <dd>{p.notes}</dd>
          </>
        )}
        {p.credit && (
          <>
            <dt>Credit</dt>
            <dd>{p.credit}</dd>
          </>
        )}
        {p.citation && (
          <>
            <dt>Citation</dt>
            <dd>{p.citation}</dd>
          </>
        )}
        <dt>API</dt>
        <dd>
          {p.endpoints.map((e) => (
            <div key={e}>
              <code>GET /api/v2{e}</code>
            </div>
          ))}
          <a href="/developers">Developer guide</a> · <a href="/docs">API reference</a>
        </dd>
      </dl>
    </section>
  );
}
