import { Link } from "../router";

interface Props {
  page: number;
  pages: number;
  hrefFor: (page: number) => string;
}

/** Pages shown: first, last and the current one with its neighbours; gaps become an ellipsis. */
export function pageWindow(page: number, pages: number): (number | "gap")[] {
  const set = new Set([1, pages, page - 1, page, page + 1].filter((n) => n >= 1 && n <= pages));
  const out: (number | "gap")[] = [];
  let last = 0;
  for (const n of [...set].sort((a, b) => a - b)) {
    if (n - last > 1) out.push("gap");
    out.push(n);
    last = n;
  }
  return out;
}

export function Pager({ page, pages, hrefFor }: Props) {
  if (pages <= 1) return null;
  const nav = (label: string, target: number, disabled: boolean, aria: string) =>
    disabled ? (
      <span class="btn btn-sm" aria-disabled="true" aria-label={aria}>
        {label}
      </span>
    ) : (
      <Link class="btn btn-sm" href={hrefFor(target)} aria-label={aria}>
        {label}
      </Link>
    );
  return (
    <nav class="pager" aria-label="Pagination">
      {nav("‹ Prev", page - 1, page <= 1, "Previous page")}
      {pageWindow(page, pages).map((n, i) =>
        n === "gap" ? (
          <span key={`g${i}`} class="muted" aria-hidden="true">
            …
          </span>
        ) : (
          <Link
            key={n}
            class="btn btn-sm"
            href={hrefFor(n)}
            aria-current={n === page ? "page" : undefined}
            aria-label={`Page ${n}`}
          >
            {n}
          </Link>
        ),
      )}
      {nav("Next ›", page + 1, page >= pages, "Next page")}
      <span class="xsmall muted">
        Page {page} of {pages}
      </span>
    </nav>
  );
}
