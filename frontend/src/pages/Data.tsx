import { useEffect } from "preact/hooks";
import { RadiationSection } from "../components/DataRadiation";
import { SamplesSection } from "../components/DataSamples";
import { WeatherSection } from "../components/DataWeather";
import { useTitle } from "../lib/hooks";
import { type DataSet, Link, navigate, useHash } from "../router";

export const DATASETS: {
  key: DataSet;
  name: string;
  title: string;
  rovers: string[];
  blurb: string;
  lead: string;
}[] = [
  {
    key: "samples",
    name: "Rock samples",
    title: "Rock samples",
    rovers: ["Perseverance"],
    blurb: "Sealed sample tubes: where each came from and where it is now.",
    lead: "Sample tubes sealed by Perseverance for a possible return to Earth: where each came from, whether it is still on the rover or already left in the depot.",
  },
  {
    key: "radiation",
    name: "Radiation",
    title: "Radiation",
    rovers: ["Curiosity"],
    blurb: "Daily absorbed dose rate at the surface, from the RAD instrument.",
    lead: "How much radiation reaches the surface of Mars: the daily absorbed dose rate measured by the RAD instrument on Curiosity.",
  },
  {
    key: "weather",
    name: "Weather",
    title: "Weather",
    rovers: ["Perseverance", "Curiosity"],
    blurb: "Daily air temperature, pressure, humidity and more, by sol.",
    lead: "Daily air measurements from the rovers' weather stations: the lowest, highest and mean values for each sol.",
  },
];

const OLD_HASH = /^#(samples|radiation|weather)$/;

/** Overview: one card per dataset, linking to its page. Old `/data#radiation` links go to the new paths. */
export function Data() {
  useTitle("Data — Sol Index");
  const { hash, visit } = useHash();
  useEffect(() => {
    const m = OLD_HASH.exec(hash || window.location.hash);
    if (m) navigate(`/data/${m[1]}`, { replace: true });
  }, [hash, visit]);
  return (
    <>
      <h1>Data</h1>
      <p class="muted lead">
        Datasets that the rovers measure besides taking photos. Each one has its own page with a
        chart, the numbers and a description of the source.
      </p>
      <ul class="ds-cards" aria-label="Datasets">
        {DATASETS.map((d) => (
          <li key={d.key} class="card ds-card">
            <h2>
              <Link href={`/data/${d.key}`}>{d.name}</Link>
            </h2>
            <p class="ds-rovers">
              {d.rovers.map((r) => (
                <span key={r} class="badge no-dot">
                  {r}
                </span>
              ))}
            </p>
            <p>{d.blurb}</p>
            <Link class="btn btn-sm" href={`/data/${d.key}`}>
              Open {d.name.toLowerCase()}
            </Link>
          </li>
        ))}
      </ul>
    </>
  );
}

/** One dataset on its own page. */
export function DataSetPage({ set }: { set: DataSet }) {
  const d = DATASETS.find((x) => x.key === set)!;
  useTitle(`${d.title} — Sol Index`);
  return (
    <>
      <nav class="crumbs" aria-label="Breadcrumb">
        <Link href="/data">Data</Link> <span aria-hidden="true">/</span>{" "}
        <span aria-current="page">{d.name}</span>
      </nav>
      <h1>{d.name}</h1>
      <p class="ds-rovers">
        {d.rovers.map((r) => (
          <span key={r} class="badge no-dot">
            {r}
          </span>
        ))}
      </p>
      <p class="muted lead">{d.lead}</p>
      <div class="ds-body">
        {set === "samples" && <SamplesSection />}
        {set === "radiation" && <RadiationSection />}
        {set === "weather" && <WeatherSection />}
      </div>
    </>
  );
}
