import { useEffect, useRef, useState } from "preact/hooks";
import type { Rover } from "../api/types";
import { copyText, feedLabel, feedUrl, hasFeed, useFeedLink, type FeedGroup } from "../lib/feed";

const RssIcon = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true" style="margin-right:6px">
    <circle cx="3" cy="13" r="1.8" fill="currentColor" />
    <path
      d="M1.5 7.2a7.3 7.3 0 0 1 7.3 7.3M1.5 2.2a12.3 12.3 0 0 1 12.3 12.3"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linecap="round"
    />
  </svg>
);

/** "Subscribe (RSS)" button and panel; rendered only for rovers that have a feed. */
export function Subscribe({
  rover,
  camera,
  sampleType,
}: {
  rover: Rover;
  camera?: string;
  sampleType?: string;
}) {
  const available = hasFeed(rover);
  const opts = { camera, sample_type: sampleType };
  // Discovery link for the page: the whole-rover feed with the page's filters, by sol.
  useFeedLink(
    available ? feedUrl(rover.id, opts) : null,
    `${rover.name} — new raw images (Sol Index)`,
    rover.id,
  );

  const [open, setOpen] = useState(false);
  const [group, setGroup] = useState<FeedGroup>("sol");
  const [status, setStatus] = useState("");
  const wrap = useRef<HTMLDivElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const input = useRef<HTMLInputElement>(null);

  const url = feedUrl(rover.id, { ...opts, group });
  useEffect(() => setStatus(""), [url]);

  const close = (focusBack: boolean) => {
    setOpen(false);
    if (focusBack) btn.current?.focus();
  };
  useEffect(() => {
    if (!open) return;
    input.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close(true);
    const onClick = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) close(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("click", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("click", onClick);
    };
  }, [open]);

  if (!available) return null;

  const copy = async () => {
    const r = await copyText(url, input.current);
    setStatus(
      r === "copied"
        ? "Copied. Now paste it into your RSS reader."
        : "Press Ctrl+C (or ⌘C) to copy the selected link.",
    );
  };

  return (
    <div class="sub-wrap" ref={wrap}>
      <button
        class="btn btn-sm"
        type="button"
        ref={btn}
        aria-expanded={open}
        aria-controls="sub-panel"
        onClick={() => setOpen((o) => !o)}
      >
        <RssIcon />
        Subscribe (RSS)
      </button>
      {open && (
        <div class="card sub-panel" id="sub-panel" role="group" aria-labelledby="sub-h">
          <h2 id="sub-h">Subscribe to new photos</h2>
          <p class="small muted">
            RSS lets a feed reader app tell you when new photos arrive, so you don’t have to check
            the site. Paste the link below into your reader.
          </p>
          <p class="small">
            Feed for <span class="sub-for">RSS: {feedLabel(rover.name, opts)}</span>
          </p>
          <div class="field">
            <span class="label" id="grp-label">
              Entries
            </span>
            <div class="segmented" role="radiogroup" aria-labelledby="grp-label">
              <input
                type="radio"
                name="grp"
                id="grp-sol"
                checked={group === "sol"}
                onChange={() => setGroup("sol")}
              />
              <label for="grp-sol">One per sol</label>
              <input
                type="radio"
                name="grp"
                id="grp-photo"
                checked={group === "photo"}
                onChange={() => setGroup("photo")}
              />
              <label for="grp-photo">One per photo</label>
            </div>
            <span class="hint">
              {group === "photo"
                ? "Every new photo is its own entry (up to 50). Busy: hundreds of photos on some sols."
                : "One entry per sol with a thumbnail strip (up to 20). Quiet and easy to skim."}
            </span>
          </div>
          <div class="field">
            <label for="feed-url">Feed link</label>
            <div class="sub-url">
              <input
                id="feed-url"
                type="text"
                readOnly
                ref={input}
                value={url}
                aria-describedby="sub-status"
                onFocus={(e) => e.currentTarget.select()}
              />
              <button class="btn btn-primary btn-sm" type="button" onClick={copy}>
                Copy
              </button>
            </div>
            <span class="sub-status" id="sub-status" role="status" aria-live="polite">
              {status}
            </span>
          </div>
          <p class="xsmall muted">
            Follows the camera and sample type chosen below; sol and date are not part of a feed.
            Photos are listed as they reach us. Credit is included in every entry.
          </p>
        </div>
      )}
    </div>
  );
}
