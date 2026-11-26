import { useEffect, useState } from "preact/hooks";
import { api } from "../api/client";
import type { Rover, Sample, SamplesResponse } from "../api/types";
import { StatusBadge, TubeLink } from "../components/SampleParts";
import { EmptyState, ErrorAlert, PageSkeleton } from "../components/States";
import { fmt } from "../lib/format";
import { useAsync, useRovers, useTitle } from "../lib/hooks";
import { mapHref } from "../lib/maplinks";
import {
  countSamples,
  filterSamples,
  heightText,
  KIND_LABEL,
  lag,
  SAMPLES_ROVER,
  solPhotosHref,
  STATUS_LABEL,
  summarize,
  type StatusFilter,
  type TypeFilter,
} from "../lib/samples";
import { Link, useHash, useQueryParam, useRestoreScroll } from "../router";

export function Samples({ roverId }: { roverId: string }) {
  const [rovers, reload] = useRovers();
  useTitle(`Samples — ${roverId} — Sol Index`);
  if (rovers.status === "loading") return <PageSkeleton label="Loading rover…" />;
  if (rovers.status === "error") return <ErrorAlert error={rovers.error} onRetry={reload} />;
  const rover = rovers.data.find((r) => r.id === roverId);
  const back = (
    <Link class="btn btn-sm" href="/">
      All rovers
    </Link>
  );
  if (!rover) {
    return (
      <EmptyState title="Rover not found" actions={back}>
        We don’t know a rover called “{roverId}”.
      </EmptyState>
    );
  }
  if (rover.id !== SAMPLES_ROVER) {
    const per = rovers.data.find((r) => r.id === SAMPLES_ROVER);
    return (
      <EmptyState
        title={`${rover.name} has no sample list`}
        actions={
          <>
            {per && (
              <Link class="btn btn-sm" href={`/rovers/${per.id}/samples`}>
                Perseverance samples
              </Link>
            )}
            {back}
          </>
        }
      >
        Only Perseverance has a list of sealed rock samples.
      </EmptyState>
    );
  }
  return <SamplesView rover={rover} />;
}

/** 29 / 29 and 30 / 29, 30 and 31 */
function listNumbers(n: number[]): string {
  return n.length < 2 ? n.join("") : `${n.slice(0, -1).join(", ")} and ${n[n.length - 1]}`;
}

const dateOnly = (iso: string) => iso.slice(0, 10);

const STATUSES: StatusFilter[] = ["on_rover", "depot"];
const TYPES: TypeFilter[] = ["rock_core", "regolith", "atmospheric"];
const parseStatus = (raw: string) => STATUSES.find((x) => x === raw);
const parseType = (raw: string) => TYPES.find((x) => x === raw);

function SamplesView({ rover }: { rover: Rover }) {
  const [state, retry] = useAsync(() => api.samples(rover.id), [rover.id]);
  const [status, setStatus] = useQueryParam<StatusFilter>("status", parseStatus, "all");
  const [type, setType] = useQueryParam<TypeFilter>("type", parseType, "all");
  const [target, setTarget] = useState<number | null>(null);
  const { hash, visit } = useHash();
  const data = state.status === "ok" ? state.data : null;
  const items = data?.items ?? null;
  useRestoreScroll(state.status === "ok");

  // `#sample-N`: the rows appear after loading and may be filtered out, so the page scrolls to the row itself.
  useEffect(() => {
    if (!items) return;
    const m = /^#sample-(\d+)$/.exec(hash);
    if (!m) {
      setTarget(null);
      return;
    }
    const n = Number(m[1]);
    setTarget(n);
    if (!filterSamples(items, status, type).some((x) => x.number === n)) {
      setStatus("all");
      setType("all");
    }
    const t = setTimeout(
      () => document.getElementById(`sample-${n}`)?.scrollIntoView?.({ block: "center" }),
      0,
    );
    return () => clearTimeout(t);
    // The filters are read once per address change on purpose.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, hash, visit]);

  const summary = items ? summarize(items) : null;

  return (
    <>
      <nav class="crumbs" aria-label="Breadcrumb">
        <Link href="/">Rovers</Link> › <Link href={`/rovers/${rover.id}`}>{rover.name}</Link> ›{" "}
        <span aria-current="page">Samples</span>
      </nav>

      <nav class="view-tabs" aria-label={`Views of ${rover.name}`}>
        <Link href={`/rovers/${rover.id}`}>Photos</Link>
        <Link href={`/rovers/${rover.id}/timeline`}>Timeline</Link>
        <Link href={`/rovers/${rover.id}/map`}>Route map</Link>
        <Link href={`/rovers/${rover.id}/samples`} aria-current="page">
          Samples
        </Link>
      </nav>

      <h1>{rover.name} rock samples</h1>
      <p class="muted" style={{ maxWidth: "70ch" }}>
        Sample tubes sealed by the rover, one row per tube. Open a sample’s photos to see everything
        taken that sol, or the photos of the tube itself (camera CACHECAM).
      </p>

      {data && data.items.length > 0 && <SourceBar data={data} />}
      {items && items.length > 0 && <Banner items={items} maxSol={rover.max_sol} />}

      <Tiles summary={summary} loading={state.status === "loading"} />

      <section aria-live="polite" aria-busy={state.status === "loading"}>
        {state.status === "loading" && (
          <div class="card panel" role="status" aria-label="Loading samples">
            <span class="visually-hidden">Loading samples…</span>
            {Array.from({ length: 8 }, (_, i) => (
              <div
                key={i}
                class="skeleton"
                style={{ height: 34, marginBottom: 10 }}
                aria-hidden="true"
              />
            ))}
          </div>
        )}
        {state.status === "error" && (
          <ErrorAlert error={state.error} onRetry={retry} title="Samples could not be loaded" />
        )}
        {data && data.items.length === 0 && (
          <EmptyState
            title="No samples loaded yet"
            actions={
              <a class="btn" href={data.source_url}>
                Open NASA’s sample page ↗
              </a>
            }
          >
            Sol Index has not fetched NASA’s sample table yet. Check back soon, or read the page at
            the source.
          </EmptyState>
        )}
        {items && items.length > 0 && (
          <Listing
            items={items}
            status={status}
            type={type}
            onStatus={setStatus}
            onType={setType}
            target={target}
            roverId={rover.id}
          />
        )}
        <p class="api-line">
          API: <code>GET /api/v2/rovers/{rover.id}/samples</code>
        </p>
      </section>
    </>
  );
}

function SourceBar({ data }: { data: SamplesResponse }) {
  return (
    <section class="card source" aria-label="Data source">
      <div>
        <strong>
          Source: <a href={data.source_url}>NASA, Mars Rock Samples page ↗</a>
        </strong>
      </div>
      <div class="muted">
        {data.source_updated_at && (
          <>
            Page last edited{" "}
            <time dateTime={data.source_updated_at}>{dateOnly(data.source_updated_at)}</time>
            {data.updated_at && " · "}
          </>
        )}
        {data.updated_at && (
          <>
            updated here <time dateTime={data.updated_at}>{dateOnly(data.updated_at)}</time>
          </>
        )}
      </div>
      <span class="spacer" />
      <div class="muted xsmall">
        Data from NASA’s Mars Rock Samples page; it may lag the mission.
      </div>
    </section>
  );
}

/** Yellow strip: the page is far behind the rover, and/or some samples have no date yet (TBD). */
function Banner({ items, maxSol }: { items: Sample[]; maxSol: number | null }) {
  const behind = lag(items, maxSol);
  const { tbd } = summarize(items);
  if (!behind && tbd.length === 0) return null;
  return (
    <div class="banner" id="lag-banner">
      {behind && (
        <p style={{ margin: 0 }}>
          <strong>The NASA page may be behind.</strong> Its last sealed sample was sealed on sol{" "}
          {behind.last}; Perseverance is now at sol {behind.max}. Newer samples, if any, are not
          listed yet.
        </p>
      )}
      {tbd.length > 0 && (
        <p style={{ margin: 0 }}>
          Sample{tbd.length > 1 ? "s" : ""} {listNumbers(tbd)} {tbd.length > 1 ? "have" : "has"} no
          date or sol on the page yet (TBD).
        </p>
      )}
    </div>
  );
}

function Tiles({
  summary,
  loading,
}: {
  summary: ReturnType<typeof summarize> | null;
  loading: boolean;
}) {
  if (loading) {
    return (
      <section class="tiles" aria-hidden="true">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} class="card tile">
            <div class="skeleton" style={{ height: 14, width: "50%" }} />
            <div class="skeleton" style={{ height: 30, marginTop: 8, width: "70%" }} />
          </div>
        ))}
      </section>
    );
  }
  if (!summary || summary.count === 0) return null;
  const c = summary.counts;
  return (
    <section class="tiles" aria-label="Sample summary">
      <div class="card tile">
        <div class="k">Samples</div>
        <div class="v">{summary.count}</div>
        <div class="s">
          {summary.firstSol !== null && `sol ${summary.firstSol} – ${summary.lastSol}`}
          {summary.tbd.length > 0 && ` · ${summary.tbd.length} without date yet`}
        </div>
      </div>
      <div class="card tile">
        <div class="k">On the rover</div>
        <div class="v">{c.on_rover}</div>
        <div class="s">still carried by Perseverance</div>
      </div>
      <div class="card tile">
        <div class="k">In the depot</div>
        <div class="v">{c.depot}</div>
        <div class="s">left on the surface</div>
      </div>
      <div class="card tile">
        <div class="k">By type</div>
        <ul>
          <li>
            <span>Rock core</span>
            <strong>{c.rock_core}</strong>
          </li>
          <li>
            <span>Regolith</span>
            <strong>{c.regolith}</strong>
          </li>
          <li>
            <span>Atmospheric</span>
            <strong>{c.atmospheric}</strong>
          </li>
        </ul>
      </div>
    </section>
  );
}

function Chip<T extends string>({
  value,
  current,
  label,
  count,
  onPick,
}: {
  value: T;
  current: T;
  label: string;
  count: number;
  onPick: (v: T) => void;
}) {
  return (
    <button
      type="button"
      class={`chip${count === 0 ? " dim" : ""}`}
      aria-pressed={current === value}
      onClick={() => onPick(value)}
    >
      {label} <span class="count">{count}</span>
    </button>
  );
}

function Listing({
  items,
  status,
  type,
  onStatus,
  onType,
  target,
  roverId,
}: {
  items: Sample[];
  status: StatusFilter;
  type: TypeFilter;
  onStatus: (s: StatusFilter) => void;
  onType: (t: TypeFilter) => void;
  target: number | null;
  roverId: string;
}) {
  const c = countSamples(items);
  const rows = filterSamples(items, status, type);
  const clear = () => {
    onStatus("all");
    onType("all");
  };
  return (
    <>
      <div class="smp-filters" role="group" aria-label="Filter samples">
        <div class="fgroup" role="group" aria-labelledby="f-status-l">
          <span class="label" id="f-status-l">
            Status
          </span>
          <div class="chips">
            <Chip value="all" current={status} label="All" count={items.length} onPick={onStatus} />
            <Chip
              value="on_rover"
              current={status}
              label={STATUS_LABEL.on_rover}
              count={c.on_rover}
              onPick={onStatus}
            />
            <Chip
              value="depot"
              current={status}
              label={STATUS_LABEL.depot}
              count={c.depot}
              onPick={onStatus}
            />
          </div>
        </div>
        <div class="fgroup" role="group" aria-labelledby="f-type-l">
          <span class="label" id="f-type-l">
            Type
          </span>
          <div class="chips">
            <Chip value="all" current={type} label="All" count={items.length} onPick={onType} />
            {(["rock_core", "regolith", "atmospheric"] as const).map((k) => (
              <Chip
                key={k}
                value={k}
                current={type}
                label={KIND_LABEL[k]}
                count={c[k]}
                onPick={onType}
              />
            ))}
          </div>
        </div>
      </div>
      <p class="count-line" id="count-line">
        Showing {rows.length} of {items.length} samples
      </p>
      {rows.length === 0 ? (
        <div class="card state">
          <h2>No samples match these filters</h2>
          <p>Try another status or type.</p>
          <button class="btn btn-sm" type="button" onClick={clear}>
            Clear filters
          </button>
        </div>
      ) : (
        <div class="card wrap">
          <table class="samples" role="table">
            <caption class="visually-hidden">Perseverance rock samples</caption>
            <thead role="rowgroup">
              <tr role="row">
                <th scope="col" role="columnheader" class="num">
                  №
                </th>
                <th scope="col" role="columnheader">
                  Sample
                </th>
                <th scope="col" role="columnheader">
                  Sampling location
                </th>
                <th scope="col" role="columnheader">
                  Type / rock
                </th>
                <th scope="col" role="columnheader">
                  Sealed
                </th>
                <th scope="col" role="columnheader" class="num">
                  Height
                </th>
                <th scope="col" role="columnheader">
                  Status
                </th>
                <th scope="col" role="columnheader">
                  Links
                </th>
              </tr>
            </thead>
            <tbody role="rowgroup">
              {rows.map((x) => (
                <Row key={x.number} x={x} target={target === x.number} roverId={roverId} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p class="xsmall muted" style={{ marginTop: "var(--s3)" }}>
        № is the sample’s order on the NASA page, not the tube serial number. Names and places are
        shown as published (including aliases). The sealing date has no time zone on the source
        page; the sol is the reliable key. Height is the core length in centimetres; “TBD” means the
        page has no date or sol yet. “Photos of the tube” = photos of that sol from the CACHECAM
        camera, shown only when that camera has photos that sol; the link is by sol only, so the
        tube may be out of frame. Dossier = the science report (PDF) on the Planetary Data System.
      </p>
    </>
  );
}

function Row({ x, target, roverId }: { x: Sample; target: boolean; roverId: string }) {
  const height = heightText(x);
  const l = x.location;
  return (
    <tr id={`sample-${x.number}`} class={target ? "target" : undefined} role="row">
      <th scope="row" role="rowheader" class="num" data-label="№">
        {x.number}
        <span class="only-phone nm"> {x.name}</span>
      </th>
      <td class="name-cell" role="cell" data-label="Sample">
        <span class="nm">{x.name}</span>
        {x.feature_name && <span class="sub">Feature: {x.feature_name}</span>}
      </td>
      <td class="where" role="cell" data-label="Sampling location">
        {x.sampling_location ?? <span class="na">—</span>}
        {x.feature_name && <span class="sub only-phone">Feature: {x.feature_name}</span>}
        {l && (
          <span class="sub">
            <Link
              href={mapHref(roverId, {
                stop: { sol: l.sol, site: l.site, drive: l.drive },
                from: l.sol,
                to: l.sol,
              })}
              title={`Stop of sol ${l.sol}, site ${l.site}, drive ${l.drive}`}
            >
              Show on route map
            </Link>
          </span>
        )}
      </td>
      <td role="cell" data-label="Type / rock">
        {x.sample_type_label.trim() || "—"}
        <span class="sub">{x.rock_type ?? "—"}</span>
      </td>
      <td role="cell" data-label="Sealed">
        {x.sol === null ? (
          <>
            <span class="tbd">TBD</span>
            <span class="sub">date and sol not published yet</span>
          </>
        ) : (
          <>
            {x.sealed_on ? <time dateTime={x.sealed_on}>{x.sealed_on}</time> : null}
            <span class="sub">sol {x.sol}</span>
          </>
        )}
      </td>
      <td class="num" role="cell" data-label="Height">
        {height ?? (
          <span class="na" title="No height published">
            —
          </span>
        )}
      </td>
      <td role="cell" data-label="Status">
        <StatusBadge x={x} />
      </td>
      <td class="links" role="cell" data-label="Links">
        {x.dossier_url && /^https:\/\//i.test(x.dossier_url) ? (
          <a
            href={x.dossier_url}
            aria-label={`Dossier PDF for sample ${x.number}, ${x.name}`}
            target="_blank"
            rel="noopener noreferrer"
          >
            Dossier (PDF) ↗
          </a>
        ) : (
          <span class="na" title="No dossier published yet">
            No dossier yet
          </span>
        )}
        {x.sol === null ? (
          <span class="na">Photos: sol TBD</span>
        ) : (
          <>
            <Link
              href={solPhotosHref(x.sol)}
              aria-label={`${x.total_photos !== null ? fmt(x.total_photos) : "All"} photos of sol ${x.sol} (sample ${x.number})`}
            >
              Photos of sol {x.sol}
              {x.total_photos !== null && ` (${fmt(x.total_photos)})`}
            </Link>
            <TubeLink sol={x.sol} number={x.number} />
          </>
        )}
      </td>
    </tr>
  );
}
