import { useEffect, useRef, useState } from "preact/hooks";
import { ApiError, keysApi } from "../api/client";
import type { IssuedKey } from "../api/types";
import { KeyBox, KeyError, SaveNowWarning } from "../components/KeyParts";
import { useTitle } from "../lib/hooks";
import { clearLinkFlag, fmtUtc, takeLinkFlag, takeToken, useNoReferrer } from "../lib/keys";
import { Link } from "../router";

type Phase =
  | { name: "ready" }
  | { name: "loading"; replacing: boolean }
  | { name: "shown"; issued: IssuedKey }
  | { name: "limit" }
  | { name: "invalid" }
  | { name: "nolink" }
  | { name: "blocked" }
  | { name: "failed"; error: unknown; replacing: boolean };

/**
 * `/keys/confirm#token=...`: the token is lifted out of the address at once; the request goes out only
 * when the visitor presses the button, so mail scanners that open the link spend nothing.
 */
export function KeysConfirm() {
  useTitle("Show my key — Sol Index");
  useNoReferrer();
  // Read and removed once, on the first render; kept only in this component's state.
  const [link] = useState(() => {
    const hadHash = window.location.hash.length > 1;
    // No hash, but the link was opened in this tab before: the page was reloaded.
    const reloaded = !hadHash && takeLinkFlag();
    return { hadHash, reloaded, token: takeToken() };
  });
  const token = link.token;
  const [phase, setPhase] = useState<Phase>(
    token ? { name: "ready" } : link.hadHash ? { name: "invalid" } : { name: "nolink" },
  );
  const headRef = useRef<HTMLHeadingElement & HTMLDivElement>(null);
  const name = phase.name;

  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    if (name !== "loading" && name !== "ready") headRef.current?.focus();
  }, [name]);

  const run = async (replacing: boolean) => {
    if (!token || phase.name === "loading") return;
    setPhase({ name: "loading", replacing });
    try {
      const issued = await keysApi.confirmKey(token, replacing);
      clearLinkFlag();
      setPhase({ name: "shown", issued });
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setPhase({ name: "limit" });
      else if (err instanceof ApiError && err.status === 400) {
        clearLinkFlag();
        setPhase({ name: "invalid" });
      } else if (err instanceof ApiError && err.status === 403) setPhase({ name: "blocked" });
      else setPhase({ name: "failed", error: err, replacing });
    }
  };

  const busy = phase.name === "loading";

  return (
    <div class="key-page narrow">
      <h1>Your API key</h1>

      {(phase.name === "ready" ||
        (phase.name === "loading" && !phase.replacing) ||
        phase.name === "failed") && (
        <section class="card panel stack" aria-labelledby="ready-title">
          <h2 id="ready-title">
            {phase.name === "failed" ? "Could not create your key" : "One step left"}
          </h2>
          <p>
            Press the button to create your API key.
            {phase.name !== "failed" &&
              " If the link has expired or was already used, we will tell you here."}
          </p>
          <p class="small muted">The key appears on this page only once. Be ready to copy it.</p>
          {phase.name === "failed" && (
            <KeyError error={phase.error} action="key" innerRef={headRef} />
          )}
          <button
            class="btn btn-primary btn-lg btn-block"
            type="button"
            disabled={busy}
            onClick={() => run(phase.name === "failed" ? phase.replacing : false)}
          >
            {busy ? "Creating…" : "Show my key"}
          </button>
          <p class="xsmall muted" role="status">
            {busy ? "Creating your key…" : ""}
          </p>
        </section>
      )}

      {phase.name === "shown" && (
        <section class="card panel stack" aria-labelledby="shown-title">
          <h2 id="shown-title" tabIndex={-1} ref={headRef}>
            Your key is ready
          </h2>
          <SaveNowWarning>
            We keep only a one-way hash of the key, so we cannot show or resend it. If you leave
            this page without saving it, request a new key.
          </SaveNowWarning>
          <KeyBox secret={phase.issued.key} label="API key" />
          <div>
            <h3>Use it</h3>
            <pre class="raw">
              {`curl -H "X-Api-Key: ${phase.issued.key_info.prefix}…" \\\n  "https://api.solindex.net/api/v2/rovers"`}
            </pre>
            <p class="small muted">
              Send it in the <code>X-Api-Key</code> header rather than in the URL, so it stays out
              of logs and browser history.
            </p>
          </div>
          <dl class="stats">
            <div>
              <dt>Account</dt>
              <dd>{phase.issued.account.email}</dd>
            </div>
            <div>
              <dt>Key</dt>
              <dd class="mono">
                {phase.issued.key_info.prefix}… (id {phase.issued.key_info.id})
              </dd>
            </div>
            <div>
              <dt>Created</dt>
              <dd>{fmtUtc(phase.issued.key_info.created_at)}</dd>
            </div>
          </dl>
          <div class="row">
            <Link class="btn" href="/keys/manage">
              Go to My keys
            </Link>
            <Link class="btn" href="/">
              Back to Sol Index
            </Link>
          </div>
        </section>
      )}

      {(phase.name === "limit" || (phase.name === "loading" && phase.replacing)) && (
        <section class="card panel stack" aria-labelledby="limit-title">
          <div class="alert alert-warn" role="alert">
            <div>
              <div class="alert-title" id="limit-title" tabIndex={-1} ref={headRef}>
                You already have 2 active keys
              </div>
              <p class="small">
                An account can hold 2 active keys. Your link is still valid: nothing was used up.
              </p>
            </div>
          </div>
          <p>
            Replace them with a new key? <strong>Both current keys stop working immediately</strong>
            , so any script that uses them will start getting <code>401 api_key_revoked</code>.
          </p>
          <div class="row">
            <button
              class="btn btn-danger btn-lg"
              type="button"
              disabled={busy}
              onClick={() => run(true)}
            >
              {busy ? "Replacing…" : "Replace my keys and show the new one"}
            </button>
            <Link class="btn btn-lg" href="/keys/manage">
              Keep my keys
            </Link>
          </div>
          <p class="small muted">
            Prefer to revoke just one? Sign in to <Link href="/keys/manage">My keys</Link> with one
            of your keys, revoke it, then come back to this link before it expires.
          </p>
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
                Links work once and for 60 minutes. Email programs that preview links can also use
                them up. The same message appears for a link that was never valid.
              </p>
            </div>
          </div>
          <Link class="btn btn-primary btn-lg" href="/keys">
            Request a new link
          </Link>
        </section>
      )}

      {phase.name === "nolink" && (
        <section class="card panel stack" aria-labelledby="nolink-title">
          <h2 id="nolink-title">
            {link.reloaded
              ? "Your link is still valid — open it again from the email"
              : "Open the link from your email"}
          </h2>
          <p>
            {link.reloaded
              ? "The page was reloaded, and for your safety the link is not kept in the address. Open it again from the email, or request a new one."
              : "This page works only with the one-time link we email you. Open that link, or request a new one."}
          </p>
          <Link class="btn btn-primary btn-lg" href="/keys">
            Get an API key
          </Link>
        </section>
      )}

      {phase.name === "blocked" && (
        <section class="card panel stack" aria-labelledby="blocked-title">
          <div class="alert alert-error" role="alert">
            <div>
              <div class="alert-title" id="blocked-title" tabIndex={-1} ref={headRef}>
                This account cannot get new keys
              </div>
              <p class="small">
                The account for this address is blocked. If you think this is a mistake, reply to
                the email you received.
              </p>
            </div>
          </div>
        </section>
      )}

      <p class="xsmall mono muted api-line">
        API: POST /api/v2/keys/confirm {`{"token": "…", "revoke_existing": false}`} → 201{" "}
        {`{"key", "key_info", "account"}`}
      </p>
    </div>
  );
}
