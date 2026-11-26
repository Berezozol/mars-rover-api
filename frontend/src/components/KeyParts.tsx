import type { ComponentChildren, Ref } from "preact";
import { useRef, useState } from "preact/hooks";
import { ApiError } from "../api/client";
import { describeError } from "../api/errors";
import { copyText, selectContents, waitText } from "../lib/keys";
import { Link } from "../router";

function Details({ raw }: { raw: string | null }) {
  if (!raw) return null;
  return (
    <details class="more">
      <summary>Response</summary>
      <pre class="raw">{raw}</pre>
    </details>
  );
}

/** `details.resets_at` of a `503 mail_unavailable` as "1 Dec 2026, 03:00 (00:00 UTC)", or null. */
export function resetText(body: unknown): string | null {
  const err = body && typeof body === "object" ? (body as { error?: unknown }).error : null;
  const det = err && typeof err === "object" ? (err as { details?: unknown }).details : null;
  const at = det && typeof det === "object" ? (det as { resets_at?: unknown }).resets_at : null;
  if (typeof at !== "string") return null;
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return null;
  const local = d.toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  const utc = d.toISOString().slice(11, 16);
  return `${local} (${utc} UTC)`;
}

/** What the visitor was doing, so that `503 mail_unavailable` can name it. */
export type KeyAction = "key" | "deletion" | "account" | "revoke" | "other";

/**
 * A failed request of the key screens in words: `429` and `503` with the wait from `Retry-After`,
 * everything else through the shared wording. A `422` on the email field is shown at the field instead.
 */
export function KeyError({
  error,
  action = "other",
  actions,
  innerRef,
}: {
  error: unknown;
  action?: KeyAction;
  actions?: ComponentChildren;
  /** Lets the page move focus onto the message after a failed request. */
  innerRef?: Ref<HTMLDivElement>;
}) {
  const v = describeError(error);
  const e = error instanceof ApiError ? error : null;
  const wait = waitText(e?.retryAfter ?? null);
  let kind: "alert-warn" | "alert-error" = "alert-error";
  let title = v.title;
  let message: string = v.message;
  if (e?.status === 429) {
    kind = "alert-warn";
    title = "Too many requests from your network";
    message = wait ? `Please try again in ${wait}.` : "Please try again later.";
  } else if (e?.code === "mail_unavailable") {
    title =
      action === "deletion"
        ? "Email sending is paused for today"
        : "Key requests are paused for today";
    const reset = resetText(e.body);
    message = reset
      ? `Today's email limit is reached. Please try again after ${reset}.`
      : `Our daily email quota is used up. Please try again ${wait ? `in ${wait}` : "later"}.`;
    if (action === "deletion") {
      message += " Or reply to any email we sent you and we will delete your data by hand.";
    }
  } else if (e?.status === 503) {
    title = "Service temporarily unavailable";
    message = `Please try again ${wait ? `in ${wait}` : "in a minute"}.`;
  } else if (e?.status === 404 && action === "revoke" && e.code === "not_found") {
    title = "Key not found";
    message =
      "That key no longer exists on this account. Reload your account to see the current list.";
  } else if (e?.status === 404) {
    // Key endpoints answer 404 as well while the feature is switched off.
    title = "Not available right now";
    message = "This part of Sol Index is not open right now. Please check back later.";
    if (action === "deletion") {
      message += " Reply to any email we sent you and we will delete your data by hand.";
    }
  }
  return (
    <div class={`alert ${kind}`} role="alert" tabIndex={-1} ref={innerRef} data-testid="key-error">
      <div>
        <div class="alert-title">{title}</div>
        <p class="small">{message}</p>
        {actions}
        {v.code !== "unknown" && (
          <p class="xsmall muted">
            Error code: <code>{v.code}</code>
            {v.status ? ` (HTTP ${v.status})` : ""}
          </p>
        )}
        <Details raw={v.raw} />
      </div>
    </div>
  );
}

/** The trap for form-filling bots: off-screen, out of the tab order, hidden from assistive technology. */
export function Honeypot({ value, onInput }: { value: string; onInput: (v: string) => void }) {
  return (
    <div class="hp" aria-hidden="true">
      <input
        id="website"
        name="website"
        type="text"
        tabIndex={-1}
        autocomplete="off"
        value={value}
        onInput={(e) => onInput((e.currentTarget as HTMLInputElement).value)}
      />
    </div>
  );
}

/** A key shown once, with a copy button; when the clipboard is refused the key is selected for Ctrl+C. */
export function KeyBox({ secret, label }: { secret: string; label: string }) {
  const codeRef = useRef<HTMLElement>(null);
  const [status, setStatus] = useState("");
  const copy = async () => {
    if (await copyText(secret)) {
      setStatus("Copied. Store it in your password manager or secrets file now.");
    } else {
      selectContents(codeRef.current);
      setStatus("Key selected. Press Ctrl+C (or ⌘C) to copy it.");
    }
  };
  return (
    <div class="field">
      <span class="label" id="key-label">
        {label}
      </span>
      <div class="keybox">
        <code ref={codeRef} aria-labelledby="key-label" data-testid="issued-key">
          {secret}
        </code>
        <button class="btn btn-primary" type="button" onClick={copy}>
          Copy key
        </button>
      </div>
      <span class="copy-status" role="status">
        {status}
      </span>
    </div>
  );
}

export function SaveNowWarning({ children }: { children: ComponentChildren }) {
  return (
    <div class="alert alert-warn" role="alert">
      <div>
        <div class="alert-title">Save it now. It will not be shown again.</div>
        <p class="small">{children}</p>
      </div>
    </div>
  );
}

export function DataNote() {
  return (
    <section class="data-note" aria-labelledby="data-title">
      <h2 id="data-title">What we store</h2>
      <p>
        For a key we store your email address, the dates it was created and confirmed, your keys
        (only as hashes) and the number of requests per day. The address is used to issue the key
        and to contact you about abuse; we send nothing but the messages you asked for and never
        share it with anyone. Email is sent through Google (Gmail). Unconfirmed requests are deleted
        within a day, request counters after 90 days, an account after 24 months without a request.
        You can delete your data at any time: in <Link href="/keys/manage">My keys</Link> with your
        key, via the link on <Link href="/keys/delete">Delete my data</Link>, or by replying to any
        of our emails.
      </p>
    </section>
  );
}

/** The same two-sentence answer on both email forms: nothing says whether the address is known. */
export function DelayedNote() {
  return (
    <div class="alert alert-warn" role="status">
      <div>
        <div class="alert-title">Our mail is running late</div>
        <p class="small">
          The mail server is retrying right now, so the message can take up to about 20 minutes. You
          do not need to ask again.
        </p>
      </div>
    </div>
  );
}
