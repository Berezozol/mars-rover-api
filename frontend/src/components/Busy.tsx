import type { ComponentChildren } from "preact";
import { useEffect, useState } from "preact/hooks";
import { ApiError } from "../api/client";
import { describeError } from "../api/errors";

const ICON_WARN = (
  <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true" style={{ flex: "none" }}>
    <path
      d="M12 3 2 20h20z"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linejoin="round"
    />
    <path d="M12 10v4.5M12 17v.5" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" />
  </svg>
);

interface Props {
  error: unknown;
  /** Repeats the same request. Enabled when the countdown ends. */
  onRetry: () => void;
  /** Ways to make the request lighter. */
  tips: string[];
  /** Extra buttons (e.g. "Drop time-of-day filters"). */
  actions?: ComponentChildren;
  testId?: string;
  retryTestId?: string;
}

/**
 * `503 service_busy`: the server asks to wait (`Retry-After`). Counts the seconds down, keeps the
 * retry button off until then and lists ways to narrow the request. A screen reader hears the
 * state twice only: when it appears and when the button becomes available.
 */
export function BusyAlert({
  error,
  onRetry,
  tips,
  actions,
  testId = "search-busy",
  retryTestId = "search-retry",
}: Props) {
  const retryAfter = error instanceof ApiError ? error.retryAfter : null;
  const wait = Math.max(0, Math.ceil(retryAfter ?? 5));
  const [left, setLeft] = useState(wait);

  useEffect(() => {
    setLeft(wait);
    if (wait <= 0) return;
    const timer = setInterval(() => {
      setLeft((n) => {
        if (n <= 1) {
          clearInterval(timer);
          return 0;
        }
        return n - 1;
      });
    }, 1000);
    return () => clearInterval(timer);
  }, [error, wait]);

  const view = describeError(error);
  const ready = left <= 0;
  return (
    <div class="alert alert-warn" data-testid={testId}>
      {ICON_WARN}
      <div style={{ minWidth: 0 }}>
        <div class="alert-title">The server is busy</div>
        <p style={{ margin: "0 0 8px" }}>
          Heavy searches are limited so that everyone gets answers.{" "}
          {ready ? (
            <span>You can try again now.</span>
          ) : (
            <span aria-hidden="true">
              Try again in <span class="busy-count">{left}</span> s.
            </span>
          )}
        </p>
        <div class="row" style={{ alignItems: "center" }}>
          <button
            class="btn btn-primary btn-sm"
            type="button"
            disabled={!ready}
            onClick={onRetry}
            data-testid={retryTestId}
          >
            Try again
          </button>
          <span class="xsmall muted">
            Error code: <code>{view.code}</code> (HTTP {view.status}
            {retryAfter !== null ? `, Retry-After: ${retryAfter}` : ""})
          </span>
        </div>
        <p class="small" style={{ margin: "12px 0 4px" }}>
          <strong>Or narrow the search</strong>, which also makes it faster:
        </p>
        <ul class="reasons" style={{ margin: 0 }}>
          {tips.map((t) => (
            <li key={t}>{t}</li>
          ))}
        </ul>
        {actions && <div class="row">{actions}</div>}
        {view.raw && (
          <details style={{ marginTop: 8 }}>
            <summary class="small">Response details</summary>
            <pre class="raw">{view.raw}</pre>
          </details>
        )}
      </div>
      <p class="visually-hidden" role="status">
        {ready
          ? "You can try again now."
          : `The server is busy. You can try again in ${wait} seconds.`}
      </p>
    </div>
  );
}
