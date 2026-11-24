import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { ROVER_MODELS, ROVER_PHOTOS } from "../lib/roverMedia";

type Phase = "idle" | "loading" | "ready" | "error";

const reducedMotion = (): boolean =>
  typeof window !== "undefined" &&
  typeof window.matchMedia === "function" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Interactive 3D model; nothing heavy is loaded before the button is pressed. */
export function Rover3D({ roverId }: { roverId: string }) {
  const model = ROVER_MODELS[roverId];
  const photo = ROVER_PHOTOS[roverId];
  const [phase, setPhase] = useState<Phase>("idle");
  const [progress, setProgress] = useState(0);
  const [open, setOpen] = useState(false);
  const viewer = useRef<HTMLElement | null>(null);
  const mounted = phase === "loading" || phase === "ready";

  useEffect(() => {
    const el = viewer.current;
    if (!mounted || !el) return;
    const onProgress = (e: Event) => {
      const total = (e as CustomEvent<{ totalProgress?: number }>).detail?.totalProgress;
      if (typeof total === "number") setProgress(total);
    };
    const onLoad = () => setPhase("ready");
    const onError = () => setPhase("error");
    el.addEventListener("progress", onProgress);
    el.addEventListener("load", onLoad);
    el.addEventListener("error", onError);
    return () => {
      el.removeEventListener("progress", onProgress);
      el.removeEventListener("load", onLoad);
      el.removeEventListener("error", onError);
    };
  }, [mounted]);

  if (!model) return null;

  const start = () => {
    setProgress(0);
    setPhase("loading");
    import("@google/model-viewer").catch(() => setPhase("error"));
  };
  // One click expands the block and starts loading; later clicks only collapse/expand it.
  const toggle = () => {
    if (!open && phase === "idle") start();
    setOpen(!open);
  };
  const pct = Math.round(progress * 100);

  return (
    <section class="card rover3d" aria-label="3D model">
      <div class="rover3d-bar">
        <button
          class="btn btn-sm rover3d-toggle"
          type="button"
          aria-expanded={open}
          aria-controls="r3d-body"
          onClick={toggle}
        >
          <span class="chev" aria-hidden="true">
            {open ? "▾" : "▸"}
          </span>
          {open ? "Hide 3D model" : `Show 3D model · ${model.sizeMb} MB`}
        </button>
        <span class="small muted">
          {model.label}. <a href={model.resourceUrl}>3D model: NASA/JPL-Caltech</a>
        </span>
      </div>
      <div id="r3d-body" hidden={!open}>
        {phase === "error" && (
          <div class="alert alert-error" role="alert">
            <p>The 3D model could not be loaded.</p>
            <p>
              <button class="btn btn-sm" type="button" onClick={start}>
                Try again
              </button>{" "}
              <a href={model.resourceUrl}>Open the model on NASA’s site</a>
            </p>
          </div>
        )}
        {mounted && (
          <div class="rover3d-stage">
            {h("model-viewer", {
              ref: viewer,
              class: "rover3d-viewer",
              src: model.src,
              poster: photo?.src,
              alt: `3D model: ${model.label}. Drag to rotate, scroll or pinch to zoom.`,
              "camera-controls": "",
              "shadow-intensity": "0.6",
              ...(reducedMotion() ? {} : { "auto-rotate": "" }),
              "camera-orbit": "45deg 75deg auto",
              "min-field-of-view": "15deg",
              "max-field-of-view": "45deg",
              tabindex: 0,
            })}
          </div>
        )}
        {phase === "loading" && (
          <>
            <div role="status" aria-live="polite" class="small muted">
              Loading 3D model… {pct}%{" "}
              <progress max={100} value={pct} aria-label="3D model loading progress" />
            </div>
            {photo && (
              <p class="small muted">
                Poster: <span>{photo.credit}</span> · <a href={photo.sourceUrl}>{photo.source}</a>
              </p>
            )}
          </>
        )}
        {phase === "ready" && (
          <p class="small muted">
            Drag or use the arrow keys to rotate; scroll, pinch or +/− to zoom; right-drag or
            two-finger drag to pan.
          </p>
        )}
      </div>
    </section>
  );
}
