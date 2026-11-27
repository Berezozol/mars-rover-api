import type { ComponentChildren } from "preact";
import { describeError } from "../api/errors";

const ICON_ERR = (
  <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true">
    <circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" stroke-width="2" />
    <path d="M12 7v6M12 16.5v.5" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" />
  </svg>
);

interface ErrorProps {
  error: unknown;
  onRetry?: () => void;
  /** Extra buttons next to "Try again" (e.g. "Go to latest sol"). */
  actions?: ComponentChildren;
  /** Text for what failed; replaces the default title for 422. */
  title?: string;
  /** Use `title` for every status, not only 422. */
  alwaysTitle?: boolean;
}

/** Error / 503 state. For 422 lists the offending fields; raw response is under a disclosure. */
export function ErrorAlert({ error, onRetry, actions, title, alwaysTitle }: ErrorProps) {
  const v = describeError(error);
  const heading = (alwaysTitle || v.status === 422) && title ? title : v.title;
  return (
    <div class={`alert ${v.unavailable ? "alert-info" : "alert-error"}`} role="alert">
      {ICON_ERR}
      <div>
        <div class="alert-title">{heading}</div>
        <p>{v.message}</p>
        {v.fields.length > 0 && (
          <ul>
            {v.fields
              // A standalone message (it names its own fields) is shown once, without a label.
              .filter(
                (f, i, all) => !f.standalone || all.findIndex((x) => x.message === f.message) === i,
              )
              .map((f, i) => (
                <li key={i}>
                  {f.standalone ? (
                    f.message
                  ) : (
                    <>
                      <strong>{f.label}</strong>: {f.message}
                    </>
                  )}
                </li>
              ))}
          </ul>
        )}
        <div class="row" style={{ alignItems: "center" }}>
          {v.retryable && onRetry && (
            <button class="btn btn-sm" type="button" onClick={onRetry}>
              Try again
            </button>
          )}
          {actions}
          {v.code !== "unknown" && (
            <span class="xsmall muted">
              Error code: <code>{v.code}</code>
              {v.status ? ` (HTTP ${v.status})` : ""}
            </span>
          )}
        </div>
        {v.raw && (
          <details style={{ marginTop: 8 }}>
            <summary class="small">Response details</summary>
            <pre class="raw">{v.raw}</pre>
          </details>
        )}
      </div>
    </div>
  );
}

export function GridSkeleton({ count = 12 }: { count?: number }) {
  return (
    <div class="photo-grid" aria-hidden="true">
      {Array.from({ length: count }, (_, i) => (
        <div key={i}>
          <div class="skeleton" style={{ aspectRatio: "4/3" }} />
          <div class="skeleton" style={{ height: 10, marginTop: 8, width: "70%" }} />
          <div class="skeleton" style={{ height: 10, marginTop: 6, width: "45%" }} />
        </div>
      ))}
    </div>
  );
}

export function EmptyState({
  title,
  children,
  actions,
  level = 3,
}: {
  title: string;
  children?: ComponentChildren;
  actions?: ComponentChildren;
  /** Heading level: 1 for a placeholder that stands in for the whole page. */
  level?: 1 | 2 | 3;
}) {
  const Heading = `h${level}` as "h1" | "h2" | "h3";
  return (
    <div class="card empty">
      <svg width="48" height="48" viewBox="0 0 48 48" aria-hidden="true">
        <circle cx="24" cy="24" r="14" fill="none" stroke="currentColor" stroke-width="2" />
        <path d="M6 30c10-6 26-6 36 0" fill="none" stroke="currentColor" stroke-width="2" />
      </svg>
      <Heading>{title}</Heading>
      {children && <p class="muted small">{children}</p>}
      {actions && <div class="row">{actions}</div>}
    </div>
  );
}

export function PageSkeleton({ label = "Loading…" }: { label?: string }) {
  return (
    <div role="status" aria-live="polite">
      <span class="visually-hidden">{label}</span>
      <div
        class="skeleton"
        style={{ height: 32, width: "40%", marginBottom: 16 }}
        aria-hidden="true"
      />
      <div class="skeleton" style={{ height: 140, marginBottom: 16 }} aria-hidden="true" />
      <GridSkeleton count={6} />
    </div>
  );
}
