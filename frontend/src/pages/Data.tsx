import type { ComponentChildren } from "preact";
import { useEffect, useState } from "preact/hooks";
import { RadiationSection } from "../components/DataRadiation";
import { SamplesSection } from "../components/DataSamples";
import { useTitle } from "../lib/hooks";
import { useHash } from "../router";

type Key = "samples" | "radiation";
const KEYS: Key[] = ["samples", "radiation"];
const isKey = (s: string): s is Key => (KEYS as string[]).includes(s);

const META: Record<Key, string> = {
  samples: "Perseverance · sealed sample tubes",
  radiation: "Curiosity · daily dose rate",
};

function hashKey(hash: string): Key | null {
  const k = hash.replace(/^#/, "");
  return isKey(k) ? k : null;
}

export function Data() {
  useTitle("Data — Sol Index");
  const { hash, visit } = useHash();
  // Without a section in the address the first section is open.
  const [open, setOpen] = useState<Record<Key, boolean>>(() => {
    const k = hashKey(window.location.hash);
    return { samples: k === null || k === "samples", radiation: k === "radiation" };
  });
  // A section's content is built when it is first opened (the radiation answer is large).
  const [seen, setSeen] = useState<Record<Key, boolean>>(open);
  const [meta, setMeta] = useState<Record<Key, string>>(META);

  const show = (k: Key, on: boolean) => {
    setOpen((o) => ({ ...o, [k]: on }));
    if (on) setSeen((s) => ({ ...s, [k]: true }));
  };

  // A link to `#radiation` opens that section and scrolls to it (also when the page is already open).
  useEffect(() => {
    const k = hashKey(hash);
    if (!k) return;
    show(k, true);
    const t = setTimeout(() => document.getElementById(k)?.scrollIntoView?.({ block: "start" }), 0);
    return () => clearTimeout(t);
  }, [hash, visit]);

  const toggle = (k: Key) => {
    const on = !open[k];
    show(k, on);
    window.history.replaceState(null, "", on ? `#${k}` : window.location.pathname);
  };
  const all = (on: boolean) => {
    KEYS.forEach((k) => show(k, on));
    window.history.replaceState(null, "", window.location.pathname);
  };

  const section = (k: Key, name: string, badge: string, body: ComponentChildren) => (
    <section class="card acc-item" id={k}>
      <h2 class="acc-h">
        <button
          class="acc-btn"
          type="button"
          id={`btn-${k}`}
          aria-expanded={open[k]}
          aria-controls={`panel-${k}`}
          onClick={() => toggle(k)}
        >
          <span class="acc-title">
            <span class="acc-name">
              {name} <span class="badge no-dot">{badge}</span>
            </span>
            <span class="acc-meta">{meta[k]}</span>
          </span>
          <svg class="chev" width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
            <path
              d="M4 7l6 6 6-6"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
        </button>
      </h2>
      <div
        class="acc-panel"
        id={`panel-${k}`}
        role="region"
        aria-labelledby={`btn-${k}`}
        hidden={!open[k]}
      >
        {seen[k] && body}
      </div>
    </section>
  );

  return (
    <>
      <h1>Data</h1>
      <p class="muted lead">
        Datasets that the rovers measure besides taking photos. Each one opens as a section; share a
        link such as <code>/data#radiation</code> to open it directly.
      </p>

      <div class="toolbar">
        <button class="btn btn-sm" type="button" onClick={() => all(true)}>
          Expand all
        </button>
        <button class="btn btn-sm" type="button" onClick={() => all(false)}>
          Collapse all
        </button>
        <span class="spacer" />
        <span class="xsmall muted">2 datasets · more coming</span>
      </div>

      <div class="acc">
        {section(
          "samples",
          "Rock samples",
          "Perseverance",
          <SamplesSection
            onMeta={(t) => setMeta((m) => (m.samples === t ? m : { ...m, samples: t }))}
          />,
        )}
        {section(
          "radiation",
          "Radiation (RAD)",
          "Curiosity",
          <RadiationSection
            onMeta={(t) => setMeta((m) => (m.radiation === t ? m : { ...m, radiation: t }))}
          />,
        )}
      </div>
    </>
  );
}
