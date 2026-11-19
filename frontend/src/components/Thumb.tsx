import { useEffect, useState } from "preact/hooks";

interface Props {
  /** Candidate URLs in fallback order (small -> medium -> full). */
  sources: string[];
  alt: string;
}

/**
 * A 4:3 box (size reserved by CSS aspect-ratio, so the grid never jumps) with a lazy image.
 * If an image fails to load, the next candidate is tried.
 */
export function Thumb({ sources, alt }: Props) {
  const [i, setI] = useState(0);
  const key = sources.join("|");
  useEffect(() => setI(0), [key]);
  const src = sources[i];
  return (
    <div class="thumb">
      {src ? (
        <img
          src={src}
          alt={alt}
          width={320}
          height={240}
          loading="lazy"
          decoding="async"
          onError={() => setI((n) => n + 1)}
        />
      ) : (
        <span class="thumb-fail" role="img" aria-label={`${alt} (preview unavailable)`}>
          Preview unavailable
        </span>
      )}
    </div>
  );
}
