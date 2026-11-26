import type { ComponentChildren } from "preact";
import { useId, useState } from "preact/hooks";
import { Link, navigate } from "../router";

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
  const nav = (label: ComponentChildren, target: number, disabled: boolean) =>
    disabled ? (
      <span class="btn btn-sm" aria-disabled="true">
        {label}
      </span>
    ) : (
      <Link class="btn btn-sm" href={hrefFor(target)}>
        {label}
      </Link>
    );
  return (
    <nav class="pager" aria-label="Pagination">
      {nav(
        <>
          <span aria-hidden="true">‹</span> Previous page
        </>,
        page - 1,
        page <= 1,
      )}
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
      {nav(
        <>
          Next page <span aria-hidden="true">›</span>
        </>,
        page + 1,
        page >= pages,
      )}
      <span class="xsmall muted">
        Page {page} of {pages}
      </span>{" "}
      <GoToPage page={page} pages={pages} hrefFor={hrefFor} />
    </nav>
  );
}

/** Jump to a page by number. Invalid or out-of-range input shows an inline error and does not navigate. */
function GoToPage({ page, pages, hrefFor }: Props) {
  const [value, setValue] = useState("");
  const [error, setError] = useState("");
  const id = useId();
  const errId = `${id}-err`;

  const submit = (e: Event) => {
    e.preventDefault();
    const text = value.trim();
    if (text === "") {
      setError(`Enter a page number from 1 to ${pages}.`);
      return;
    }
    const n = Number(text);
    if (!/^\d+$/.test(text) || !Number.isSafeInteger(n) || n < 1 || n > pages) {
      setError(`Enter a whole number from 1 to ${pages}.`);
      return;
    }
    setError("");
    setValue("");
    if (n !== page) navigate(hrefFor(n));
  };

  return (
    <form class="goto-page" onSubmit={submit} data-testid="goto-page" noValidate>
      <label for={id} class="xsmall muted">
        Go to page
      </label>
      <input
        id={id}
        type="number"
        inputMode="numeric"
        min={1}
        max={pages}
        step={1}
        placeholder={String(page)}
        value={value}
        onInput={(e) => {
          setValue((e.currentTarget as HTMLInputElement).value);
          setError("");
        }}
        aria-invalid={error ? "true" : undefined}
        aria-describedby={error ? errId : undefined}
        data-testid="goto-page-input"
      />
      <button type="submit" class="btn btn-sm" data-testid="goto-page-submit">
        Go
      </button>
      {error && (
        <span id={errId} class="goto-page-err" role="alert" data-testid="goto-page-error">
          {error}
        </span>
      )}
    </form>
  );
}
