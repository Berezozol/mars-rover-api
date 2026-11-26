import { useEffect, useRef, useState } from "preact/hooks";
import { ApiError, keysApi } from "../api/client";
import { DataNote, DelayedNote, Honeypot, KeyError } from "../components/KeyParts";
import { useTitle } from "../lib/hooks";
import { Link } from "../router";

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const EMAIL_HELP = "Enter a valid address: one “@”, no spaces, a domain with a dot.";
const EMAIL_EMPTY = "Enter your email address.";

type Phase =
  | { name: "form"; error?: unknown; fieldError?: string }
  | { name: "sending" }
  | { name: "sent"; delayed: boolean; to: string }
  | { name: "closed" };

/** `/keys`: ask for a one-time link by email. The answer is the same for every address. */
export function Keys() {
  useTitle("Get an API key — Sol Index");
  const [email, setEmail] = useState("");
  const [trap, setTrap] = useState("");
  const [phase, setPhase] = useState<Phase>({ name: "form" });
  const sentRef = useRef<HTMLDivElement>(null);
  const closedRef = useRef<HTMLHeadingElement>(null);
  const emailRef = useRef<HTMLInputElement>(null);
  const name = phase.name;
  const fieldError = phase.name === "form" ? phase.fieldError : undefined;
  const formError = phase.name === "form" ? phase.error : undefined;
  const errRef = useRef<HTMLDivElement>(null);

  // After a state change the focus goes to the new state's heading (or the field with the error).
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    if (name === "sent") sentRef.current?.focus();
    else if (name === "closed") closedRef.current?.focus();
    else if (name === "form" && fieldError) emailRef.current?.focus();
  }, [name, fieldError]);

  // A failed request: the focus goes to the message (the button was disabled meanwhile).
  useEffect(() => {
    if (formError !== undefined) errRef.current?.focus();
  }, [formError]);

  const submit = async (e: Event) => {
    e.preventDefault();
    if (phase.name === "sending") return;
    const value = email.trim();
    if (!EMAIL.test(value)) {
      setPhase({ name: "form", fieldError: value ? EMAIL_HELP : EMAIL_EMPTY });
      return;
    }
    setPhase({ name: "sending" });
    try {
      const res = await keysApi.requestKey(value, trap);
      setPhase({ name: "sent", delayed: res.mail_delayed === true, to: value });
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setPhase({ name: "closed" });
      } else if (err instanceof ApiError && err.status === 422) {
        setPhase({ name: "form", fieldError: EMAIL_HELP });
      } else {
        setPhase({ name: "form", error: err });
      }
    }
  };

  const sending = phase.name === "sending";
  const showForm = phase.name === "form" || phase.name === "sending";

  return (
    <div class="key-page narrow">
      <h1>Get an API key</h1>
      <p class="muted">
        A free key gives your scripts a higher rate limit. Browsing Sol Index and calling the API
        without a key keeps working. We email you a one-time link; the key is shown once, in your
        browser.
      </p>

      {showForm && (
        <section class="card panel">
          <h2 id="form-title">Your email address</h2>
          {phase.name === "form" && phase.error !== undefined && (
            <div style={{ marginBottom: "var(--s4)" }}>
              <KeyError error={phase.error} action="key" innerRef={errRef} />
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
                required
                disabled={sending}
                value={email}
                aria-invalid={fieldError ? "true" : undefined}
                aria-describedby={fieldError ? "email-hint email-err" : "email-hint"}
                onInput={(ev) => setEmail((ev.currentTarget as HTMLInputElement).value)}
              />
              <span id="email-hint" class="hint">
                We send one link, valid for 60 minutes. ASCII addresses only.
              </span>
              {fieldError && (
                <span id="email-err" class="field-err">
                  {fieldError}
                </span>
              )}
            </div>
            <Honeypot value={trap} onInput={setTrap} />
            <button class="btn btn-primary btn-lg btn-block" type="submit" disabled={sending}>
              {sending ? "Sending…" : "Email me a link"}
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
              <div class="alert-title" id="sent-title" tabIndex={-1} ref={sentRef}>
                Check your mail
              </div>
              <p>
                If this address can receive mail, a message from Sol Index is on its way to{" "}
                <strong>{phase.to}</strong>. Open the link in it within 60 minutes to see your key.
              </p>
            </div>
          </div>
          {phase.delayed && (
            <div style={{ marginBottom: "var(--s4)" }}>
              <DelayedNote />
            </div>
          )}
          <ol class="steps">
            <li>Open the message and press the link.</li>
            <li>On the next page press “Show my key” and copy it. It is shown once.</li>
          </ol>
          <p class="small muted">
            Nothing after a few minutes? Check the spam folder, then you can{" "}
            <button class="linklike" type="button" onClick={() => setPhase({ name: "form" })}>
              try again
            </button>{" "}
            (up to 3 messages per address per day). The page cannot tell you whether an address is
            registered, on purpose.
          </p>
        </section>
      )}

      {phase.name === "closed" && (
        <section class="card panel stack" aria-labelledby="closed-title">
          <h2 id="closed-title" tabIndex={-1} ref={closedRef}>
            Key signup is not open yet
          </h2>
          <p>
            Getting a key by email is not switched on right now. Browsing Sol Index and calling the
            API without a key keeps working. Please check back later.
          </p>
          <Link class="btn" href="/">
            Back to Sol Index
          </Link>
        </section>
      )}

      <section class="stack" aria-label="Other key actions" style={{ marginTop: "var(--s5)" }}>
        <p class="small">
          Already have a key? <Link href="/keys/manage">Manage my keys</Link> · Lost it? Request a
          new one above, or <Link href="/keys/delete">delete my data</Link>.
        </p>
      </section>

      <div style={{ marginTop: "var(--s5)" }}>
        <DataNote />
      </div>

      <p class="xsmall mono muted api-line">
        API: POST /api/v2/keys/requests {`{"email": "user@example.org"}`} → 202{" "}
        {`{"status": "accepted", "mail_delayed": false}`}
      </p>
    </div>
  );
}
