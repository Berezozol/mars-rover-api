import { useState } from "preact/hooks";

interface Props {
  /** Candidate URLs in fallback order (small -> medium -> full). */
  sources: string[];
  alt: string;
  /** Pixel size of the full frame (v2 `width`/`height`); sets the placeholder proportions. */
  width?: number | null;
  height?: number | null;
}

/** Proportions of a placeholder: the frame's own, within a sane range; 4:3 when unknown. */
export function thumbRatio(width?: number | null, height?: number | null): number {
  if (!width || !height || width <= 0 || height <= 0) return 4 / 3;
  return Math.min(Math.max(width / height, 0.5), 2.5);
}

/**
 * A box with reserved size (CSS aspect-ratio from the frame's width/height, 4:3 if unknown, so the
 * grid never jumps) with a lazy image. If an image fails to load, the next candidate is tried.
 */
export function Thumb({ sources, alt, width, height }: Props) {
  // The failure count belongs to one list of sources: a different list starts again at the first.
  const key = sources.join("|");
  const [failed, setFailed] = useState({ key, n: 0 });
  const i = failed.key === key ? failed.n : 0;
  const src = sources[i];
  const ratio = thumbRatio(width, height);
  const known = !!width && !!height;
  // Frame size unknown (Curiosity thumbnails: 192x144, 144x144, even portrait 80x112): the tile stays
  // 4:3 and a loaded image of other proportions is shown whole (letterboxed), never cropped or stretched.
  const [fitWhole, setFitWhole] = useState({ src, whole: false });
  const whole = fitWhole.src === src && fitWhole.whole;
  return (
    <div
      class={whole ? "thumb thumb-whole" : "thumb"}
      style={known ? { aspectRatio: String(ratio) } : undefined}
    >
      {src ? (
        <img
          src={src}
          alt={alt}
          width={320}
          height={Math.round(320 / ratio)}
          loading="lazy"
          decoding="async"
          onLoad={(e) => {
            const { naturalWidth: w, naturalHeight: h } = e.currentTarget;
            if (!known && w > 0 && h > 0 && Math.abs(w / h - ratio) / ratio > 0.05) {
              setFitWhole({ src, whole: true });
            }
          }}
          onError={() => setFailed({ key, n: i + 1 })}
        />
      ) : (
        <span class="thumb-fail" role="img" aria-label={`${alt} (preview unavailable)`}>
          Preview unavailable
        </span>
      )}
    </div>
  );
}
