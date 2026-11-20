import { api } from "../api/client";
import type { PhotoDetail, Sample } from "../api/types";
import { useAsync } from "../lib/hooks";
import { samplesHref, solPhotosHref } from "../lib/samples";
import { Link } from "../router";
import { SampleBadges, TubeLink } from "./SampleParts";

/**
 * The "Sample" section of the photo card (Perseverance). `PhotoDetail.samples` holds the numbers of the samples
 * sealed on the photo's sol; names and badges come from the cached `/samples` list. Without samples: nothing,
 * not even a heading.
 */
export function SampleBlock({ photo }: { photo: PhotoDetail }) {
  const numbers = photo.samples ?? [];
  if (numbers.length === 0) return null;
  return <SampleDetails numbers={numbers} sol={photo.sol} />;
}

function SampleDetails({ numbers, sol }: { numbers: number[]; sol: number }) {
  // The names are a bonus: if the list fails, the numbers and the links still work.
  const [state] = useAsync(() => api.samples("perseverance").catch(() => null), []);
  const list = state.status === "ok" ? (state.data?.items ?? []) : [];
  const byNumber = new Map<number, Sample>(list.map((x) => [x.number, x]));
  return (
    <section aria-labelledby="sm-h" aria-busy={state.status === "loading"}>
      <h2 id="sm-h">{numbers.length === 1 ? "Sample" : "Samples"}</h2>
      {numbers.map((n) => {
        const x = byNumber.get(n);
        return (
          <div key={n} style={{ marginBottom: "var(--s2)" }}>
            <p style={{ margin: "0 0 var(--s2)" }}>
              <Link href={samplesHref(n)} style={{ fontWeight: 700 }}>
                <span aria-hidden="true">◆ </span>
                {x ? `Sample ${n} — ${x.name}` : `Sample ${n}`}
              </Link>
            </p>
            {x && <SampleBadges x={x} />}
            {x && (
              <p class="small" style={{ margin: "var(--s2) 0 0" }}>
                {[
                  `Sealed on this sol${x.sealed_on ? ` (${x.sealed_on})` : ""}`,
                  x.feature_name && `feature ${x.feature_name}`,
                  x.sampling_location,
                ]
                  .filter(Boolean)
                  .join(", ")}
                .
              </p>
            )}
          </div>
        );
      })}
      <p class="loc-note" style={{ marginTop: "var(--s2)" }}>
        Linked by sol only: this frame does not necessarily show the sample.
      </p>
      <p class="small" style={{ margin: "var(--s2) 0 0" }}>
        <TubeLink sol={sol} label="Photos of the tube (CACHECAM)" after=" · " />
        <Link href={solPhotosHref(sol)}>Photos of sol {sol}</Link> ·{" "}
        <Link href={samplesHref()}>All samples</Link>
      </p>
    </section>
  );
}
