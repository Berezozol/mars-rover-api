import { useEffect, useRef, useState } from "preact/hooks";
import { ApiError, keysApi } from "../api/client";
import { DelayedNote, Honeypot, KeyError } from "../components/KeyParts";
import { useTitle } from "../lib/hooks";
import { takeToken, useNoReferrer } from "../lib/keys";
import { Link } from "../router";

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const EMAIL_HELP = "Enter a valid address: one “@”, no spaces, a domain with a dot.";

type Phase =
  | { name: "form"; error?: unknown; fieldError?: boolean }
  | { name: "sending" }
  | { name: "sent"; delayed: boolean; to: string }
  | { name: "confirm"; error?: unknown }
  | { name: "deleting" }
  | { name: "done" }
  | { name: "invalid" };

/**
 * `/keys/delete`: ask for a deletion link by email, or (opened from that link, `#token=...`) confirm
 * the deletion with a button. The token leaves the address at once; nothing is sent until the press.
 */
export function KeysDelete() {
  useTitle("Delete my data — Sol Index");
  useNoReferrer();
  const [token] = useState<string | null>(() => {
    const hadHash = window.location.hash.length > 1;
    const t = takeToken();
    return hadHash ? (t ?? "") : null;
  });
  const [email, setEmail] = useState("");
  const [trap, setTrap] = useState("");
  const [phase, setPhase] = useState<Phase>(() =>
    token === null ? { name: "form" } : token ? { name: "confirm" } : { name: "invalid" },
  );
  const headRef = useRef<HTMLHeadingElement & HTMLDivElement>(null);
  const emailRef = useRef<HTMLInputElement>(null);
  const name = phase.name;
  const fieldError = phase.name === "form" && phase.fieldError;

  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    if (name === "sent" || name === "confirm" || name === "done" || name === "invalid") {
      headRef.current?.focus();
    } else if (name === "form" && fieldError) emailRef.current?.focus();
  }, [name, fieldError]);

  const submit = async (e: Event) => {
    e.preventDefault();
    if (phase.name === "sending") return;
    const value = email.trim();
    if (!EMAIL.test(value)) {
      setPhase({ name: "form", fieldError: true });
      return;
    }
    setPhase({ name: "sending" });
    try {
      const res = await keysApi.requestDeletion(value, trap);
      setPhase({ name: "sent", delayed: res.mail_delayed === true, to: value });
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        setPhase({ name: "form", fieldError: true });
      } else {
        setPhase({ name: "form", error: err });
      }
    }
  };

  const confirm = async () => {
    if (!token || phase.name === "deleting") return;
    setPhase({ name: "deleting" });
    try {
      await keysApi.confirmDeletion(token);
      setPhase({ name: "done" });
    } catch (err) {
      if (err instanceof ApiError && (err.status === 400 || err.status === 422)) {
        setPhase({ name: "invalid" });
      } else {
        setPhase({ name: "confirm", error: err });
      }
    }
  };

  const sending = phase.name === "sending";
  const deleting = phase.name === "deleting";

  return (
    <div class="key-page narrow">
      <h1>Delete my data</h1>

      {(phase.name === "form" || sending) && (
        <section class="card panel" aria-labelledby="form-title">
          <h2 id="form-title">Delete by email</h2>
          <p class="small muted">
            Use this if you no longer have your key. Have it? Delete in{" "}
            <Link href="/keys/manage">My keys</Link> with one click. We email a link to the address;
            opening it deletes your email, keys and usage counters.
          </p>
          {phase.name === "form" && phase.error !== undefined && (
            <div style={{ marginBottom: "var(--s4)" }}>
              <KeyError error={phase.error} action="deletion" />
            </div>
          )}
          <form class="stack" noValidate onSubmit={submit}>
            <div class="field">
              <label for="email" class="plain">
                <strong>Email address</strong>
              </label>
              <input
                id="email"
                ref={emailRef}
                name="email"
                type="email"
                inputMode="email"
                autocomplete="email"
                spellcheck={false}
                maxLength={254}
                placeholder="user@example.org"
                disabled={sending}
                value={email}
                aria-invalid={fieldError ? "true" : undefined}
                aria-describedby={fieldError ? "email-hint email-err" : "email-hint"}
                onInput={(ev) => setEmail((ev.currentTarget as HTMLInputElement).value)}
              />
              <span id="email-hint" class="hint">
                The address you used to get your key.
              </span>
              {fieldError && (
                <span id="email-err" class="field-err">
                  {EMAIL_HELP}
                </span>
              )}
            </div>
            <Honeypot value={trap} onInput={setTrap} />
            <button class="btn btn-primary btn-lg btn-block" type="submit" disabled={sending}>
              {sending ? "Sending…" : "Email me a deletion link"}
            </button>
            <p class="xsmall muted" role="status">
              {sending ? "Sending…" : ""}
            </p>
          </form>
        </section>
      )}

      {phase.name === "sent" && (
        <section class="card panel" aria-labelledby="sent-title">
          <div class="alert alert-ok" style={{ marginBottom: "var(--s4)" }}>
            <div>
              <div class="alert-title" id="sent-title" tabIndex={-1} ref={headRef}>
                Check your mail
              </div>
              <p>
                If {phase.to} has an account, a message with a deletion link is on its way. The link
                works once, for 60 minutes. If there is no account, nothing is stored and no message
                is sent; we show the same answer either way.
              </p>
            </div>
          </div>
          {phase.delayed && (
            <div style={{ marginBottom: "var(--s4)" }}>
              <DelayedNote />
            </div>
          )}
          <p class="small muted">
            <button class="linklike" type="button" onClick={() => setPhase({ name: "form" })}>
              Use another address
            </button>
          </p>
        </section>
      )}

      {(phase.name === "confirm" || deleting) && (
        <section class="card panel stack" aria-labelledby="c-title">
          <h2 id="c-title" tabIndex={-1} ref={headRef}>
            Delete your data?
          </h2>
          <p>
            This removes your email address, all your API keys and your usage counters for good.
            Scripts that use your keys will start getting <code>401 invalid_api_key</code>. The API
            itself keeps working without a key.
          </p>
          {phase.name === "confirm" && phase.error !== undefined && (
            <KeyError error={phase.error} action="deletion" />
          )}
          <button
            class="btn btn-danger btn-lg btn-block"
            type="button"
            disabled={deleting}
            onClick={confirm}
          >
            {deleting ? "Deleting…" : "Yes, delete my data"}
          </button>
          <p class="xsmall muted" role="status">
            {deleting ? "Deleting…" : ""}
          </p>
          <Link class="btn" href="/">
            No, keep everything
          </Link>
        </section>
      )}

      {phase.name === "done" && (
        <section class="card panel stack" aria-labelledby="done-title">
          <div class="alert alert-ok">
            <div>
              <div class="alert-title" id="done-title" tabIndex={-1} ref={headRef}>
                Your data has been deleted
              </div>
              <p class="small">
                Your email address, keys and usage counters are gone. You can get a new key at any
                time.
              </p>
            </div>
          </div>
          <div class="row">
            <Link class="btn" href="/">
              Back to Sol Index
            </Link>
            <Link class="btn" href="/keys">
              Get an API key
            </Link>
          </div>
        </section>
      )}

      {phase.name === "invalid" && (
        <section class="card panel stack" aria-labelledby="invalid-title">
          <div class="alert alert-error" role="alert">
            <div>
              <div class="alert-title" id="invalid-title" tabIndex={-1} ref={headRef}>
                This link has expired or was already used
              </div>
              <p class="small">
                Links work once and for 60 minutes. If your data was already deleted, there is
                nothing more to do.
              </p>
            </div>
          </div>
          <button
            class="btn btn-primary btn-lg"
            type="button"
            onClick={() => setPhase({ name: "form" })}
          >
            Request a new link
          </button>
        </section>
      )}

      <p class="small muted" style={{ marginTop: "var(--s5)" }}>
        Prefer email? Reply to any message we sent you and we will delete your data by hand.
      </p>

      <p class="xsmall mono muted api-line">
        API: POST /api/v2/account/deletion-requests · POST /api/v2/account/deletion-confirm
      </p>
    </div>
  );
}
