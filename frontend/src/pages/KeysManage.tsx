import { useEffect, useRef, useState } from "preact/hooks";
import { ApiError, keysApi } from "../api/client";
import type { AccountInfo, KeyInfo } from "../api/types";
import { KeyBox, KeyError, SaveNowWarning } from "../components/KeyParts";
import { useTitle } from "../lib/hooks";
import { fmtUtc } from "../lib/keys";
import { Link } from "../router";

/** `API_KEY_MAX_PER_ACCOUNT`; the server has the last word (`409 key_limit_reached`). */
const MAX_KEYS = 2;

type Phase =
  | { name: "out"; error?: unknown; notice?: string }
  | { name: "loading" }
  | { name: "account" }
  | { name: "deleted" };

const num = (n: number) => n.toLocaleString("en-US");

/** `/keys/manage`: the key is the login. It lives in this component's state and nowhere else. */
export function KeysManage() {
  useTitle("My keys — Sol Index");
  const [input, setInput] = useState("");
  const [reveal, setReveal] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [phase, setPhase] = useState<Phase>({ name: "out" });
  const [account, setAccount] = useState<AccountInfo | null>(null);
  const [newKey, setNewKey] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<unknown>(undefined);
  const [limitHit, setLimitHit] = useState(false);
  const [delOpen, setDelOpen] = useState(false);
  const [delCheck, setDelCheck] = useState(false);

  const inputRef = useRef<HTMLInputElement>(null);
  const accTitle = useRef<HTMLHeadingElement>(null);
  const newTitle = useRef<HTMLHeadingElement>(null);
  const goneTitle = useRef<HTMLDivElement>(null);
  const revokeGo = useRef<HTMLButtonElement>(null);
  const delCheckRef = useRef<HTMLInputElement>(null);
  const delOpenBtn = useRef<HTMLButtonElement>(null);
  const focusAfter = useRef<(() => void) | null>(null);

  // Focus moves after the render that shows the new state.
  useEffect(() => {
    const f = focusAfter.current;
    focusAfter.current = null;
    f?.();
  });

  const signOut = (state: Phase = { name: "out" }) => {
    setApiKey("");
    setInput("");
    setReveal(false);
    setAccount(null);
    setNewKey(null);
    setRevoking(null);
    setBusy(false);
    setActionError(undefined);
    setLimitHit(false);
    setDelOpen(false);
    setDelCheck(false);
    setPhase(state);
    focusAfter.current = () => inputRef.current?.focus();
  };

  const login = async (e: Event) => {
    e.preventDefault();
    if (phase.name === "loading") return;
    const value = input.trim();
    if (!value) {
      setPhase({
        name: "out",
        error: new ApiError({
          status: 401,
          code: "api_key_required",
          message: "API key required",
        }),
      });
      return;
    }
    setPhase({ name: "loading" });
    try {
      const acc = await keysApi.account(value);
      setApiKey(value);
      setInput("");
      setAccount(acc);
      setPhase({ name: "account" });
      focusAfter.current = () => accTitle.current?.focus();
    } catch (err) {
      setPhase({ name: "out", error: err });
      focusAfter.current = () => inputRef.current?.focus();
    }
  };

  // A key problem during an action ends the session; anything else stays beside the action.
  const failAction = (err: unknown) => {
    if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
      signOut({ name: "out", error: err });
    } else {
      setActionError(err);
    }
  };

  const createKey = async () => {
    if (!account || busy || active >= MAX_KEYS) return;
    setBusy(true);
    setActionError(undefined);
    setLimitHit(false);
    try {
      const res = await keysApi.createKey(apiKey);
      setNewKey(res.key);
      setAccount({ ...account, keys: [res.key_info, ...account.keys] });
      focusAfter.current = () => newTitle.current?.focus();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setLimitHit(true);
      else failAction(err);
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (k: KeyInfo) => {
    if (!account || busy) return;
    setBusy(true);
    setActionError(undefined);
    try {
      await keysApi.revokeKey(apiKey, k.id);
      setRevoking(null);
      if (apiKey.startsWith(k.prefix)) {
        signOut({
          name: "out",
          notice:
            "You revoked the key you signed in with. Sign in with another key of yours to continue.",
        });
        return;
      }
      const at = new Date().toISOString();
      setAccount({
        ...account,
        keys: account.keys.map((x) => (x.id === k.id ? { ...x, revoked_at: at } : x)),
      });
      focusAfter.current = () => accTitle.current?.focus();
    } catch (err) {
      failAction(err);
    } finally {
      setBusy(false);
    }
  };

  const deleteAccount = async () => {
    if (busy || !delCheck) return;
    setBusy(true);
    setActionError(undefined);
    try {
      await keysApi.deleteAccount(apiKey);
      setApiKey("");
      setInput("");
      setAccount(null);
      setNewKey(null);
      setDelOpen(false);
      setDelCheck(false);
      setPhase({ name: "deleted" });
      focusAfter.current = () => goneTitle.current?.focus();
    } catch (err) {
      failAction(err);
    } finally {
      setBusy(false);
    }
  };

  const active = account ? account.keys.filter((k) => !k.revoked_at).length : 0;

  const authError = phase.name === "out" ? phase.error : undefined;
  const authApi = authError instanceof ApiError ? authError : null;

  return (
    <div class="key-page narrow">
      <h1>My keys</h1>

      {(phase.name === "out" || phase.name === "loading") && (
        <section class="card panel">
          <h2 id="in-title">Enter your API key</h2>
          <p class="small muted">
            Your key is your login. It stays in this page’s memory only: it is not saved in the
            browser and never put in the address.
          </p>

          {phase.name === "out" && phase.notice && (
            <div class="alert alert-warn" role="status" style={{ marginBottom: "var(--s4)" }}>
              <div>
                <p class="small">{phase.notice}</p>
              </div>
            </div>
          )}
          {authApi?.code === "invalid_api_key" && (
            <div class="alert alert-error" role="alert" style={{ marginBottom: "var(--s4)" }}>
              <div>
                <div class="alert-title">We do not recognise this key</div>
                <p class="small">
                  Check for a missing character or a space. Lost your key?{" "}
                  <Link href="/keys">Request a new one</Link> (a fresh link by email), or{" "}
                  <Link href="/keys/delete">delete your data</Link>.
                </p>
              </div>
            </div>
          )}
          {authApi?.code === "api_key_revoked" && (
            <div class="alert alert-error" role="alert" style={{ marginBottom: "var(--s4)" }}>
              <div>
                <div class="alert-title">This key has been revoked</div>
                <p class="small">
                  It no longer works. Sign in with another key of yours, or{" "}
                  <Link href="/keys">request a new one</Link>.
                </p>
              </div>
            </div>
          )}
          {authApi?.code === "account_blocked" && (
            <div class="alert alert-error" role="alert" style={{ marginBottom: "var(--s4)" }}>
              <div>
                <div class="alert-title">This account is blocked</div>
                <p class="small">
                  Keys of a blocked account cannot be managed here. If you think this is a mistake,
                  reply to any email we sent you.
                </p>
              </div>
            </div>
          )}
          {authApi?.code === "api_key_required" && (
            <div class="alert alert-error" role="alert" style={{ marginBottom: "var(--s4)" }}>
              <div>
                <div class="alert-title">Enter your API key first</div>
                <p class="small">
                  The key starts with <code>si_</code>.
                </p>
              </div>
            </div>
          )}
          {authError !== undefined &&
            !["invalid_api_key", "api_key_revoked", "account_blocked", "api_key_required"].includes(
              authApi?.code ?? "",
            ) && (
              <div style={{ marginBottom: "var(--s4)" }}>
                <KeyError error={authError} />
              </div>
            )}

          <form class="stack" noValidate onSubmit={login}>
            <div class="field">
              <label for="apikey" class="plain">
                <strong>API key</strong>
              </label>
              <input
                id="apikey"
                ref={inputRef}
                class="mono-in"
                name="apikey"
                type={reveal ? "text" : "password"}
                autocomplete="off"
                autocapitalize="off"
                spellcheck={false}
                maxLength={64}
                placeholder="si_EXAMPLE…"
                disabled={phase.name === "loading"}
                value={input}
                aria-describedby="apikey-hint"
                onInput={(ev) => setInput((ev.currentTarget as HTMLInputElement).value)}
              />
              <span id="apikey-hint" class="hint">
                Starts with <code>si_</code>.
              </span>
            </div>
            <label class="check small">
              <input
                type="checkbox"
                checked={reveal}
                onChange={(ev) => setReveal((ev.currentTarget as HTMLInputElement).checked)}
              />{" "}
              Show the key while I type
            </label>
            <button
              class="btn btn-primary btn-lg btn-block"
              type="submit"
              disabled={phase.name === "loading"}
            >
              {phase.name === "loading" ? "Loading…" : "Show my account"}
            </button>
            <p class="xsmall muted" role="status">
              {phase.name === "loading" ? "Loading your account…" : ""}
            </p>
          </form>
          <p class="small muted" style={{ marginTop: "var(--s4)" }}>
            No key yet? <Link href="/keys">Get an API key</Link>. Lost it?{" "}
            <Link href="/keys/delete">Delete my data</Link> with just your email.
          </p>
        </section>
      )}

      {phase.name === "account" && account && (
        <div class="stack-lg">
          <section class="card panel" aria-labelledby="acc-title">
            <div class="row" style={{ alignItems: "center" }}>
              <div>
                <h2
                  id="acc-title"
                  tabIndex={-1}
                  ref={accTitle}
                  style={{ marginBottom: "var(--s1)" }}
                >
                  {account.email}
                </h2>
                <span
                  class={`badge ${account.status === "active" ? "badge-active" : "badge-complete"}`}
                >
                  {account.status === "active" ? "Active" : account.status}
                </span>{" "}
                <span class="small muted">since {account.created_at.slice(0, 10)}</span>
              </div>
              <span class="spacer" />
              <button class="btn btn-sm" type="button" onClick={() => signOut()}>
                Sign out
              </button>
            </div>
          </section>

          {newKey && (
            <section class="card panel stack" aria-labelledby="new-title">
              <h2 id="new-title" tabIndex={-1} ref={newTitle}>
                Your new key
              </h2>
              <SaveNowWarning>We keep only a hash, so we cannot show or resend it.</SaveNowWarning>
              <KeyBox secret={newKey} label="New API key" />
            </section>
          )}

          <section aria-labelledby="keys-title">
            <div class="result-head" style={{ marginTop: 0 }}>
              <h2 id="keys-title">Keys</h2>
              <span class="muted small">
                {active} of {MAX_KEYS} active
              </span>
              <span class="spacer" />
              <button
                class="btn btn-primary"
                type="button"
                disabled={active >= MAX_KEYS || busy}
                aria-describedby={active >= MAX_KEYS ? "limit-note" : undefined}
                onClick={createKey}
              >
                Create a new key
              </button>
            </div>
            {active >= MAX_KEYS && (
              <p class="small muted" id="limit-note">
                You have {MAX_KEYS} active keys, the most an account can hold. Revoke one to create
                another.
              </p>
            )}
            {limitHit && (
              <p class="small field-err" role="alert">
                You already have {MAX_KEYS} active keys.
              </p>
            )}
            {actionError !== undefined && !delOpen && <KeyError error={actionError} />}
            <ul class="keys" aria-label="Your keys">
              {account.keys.map((k) => {
                const revoked = !!k.revoked_at;
                const current = apiKey.startsWith(k.prefix);
                return (
                  <li key={k.id} class={`card key-item${revoked ? " revoked" : ""}`}>
                    <div class="head">
                      <code>{k.prefix}…</code>
                      <span class={`badge ${revoked ? "badge-complete" : "badge-active"}`}>
                        {revoked ? "Revoked" : "Active"}
                      </span>
                      {current && !revoked && <span class="xsmall muted">this key</span>}
                      <span class="xsmall muted">id {k.id}</span>
                      <span class="spacer" />
                      {!revoked && (
                        <button
                          class="btn btn-sm"
                          type="button"
                          aria-label={`Revoke key ${k.prefix}, id ${k.id}`}
                          onClick={() => {
                            setRevoking(k.id);
                            setActionError(undefined);
                            focusAfter.current = () => revokeGo.current?.focus();
                          }}
                        >
                          Revoke
                        </button>
                      )}
                    </div>
                    <dl>
                      <div>
                        <dt>Created</dt>
                        <dd>{fmtUtc(k.created_at)}</dd>
                      </div>
                      <div>
                        <dt>Last used</dt>
                        <dd>{fmtUtc(k.last_used_at) ?? "Never"}</dd>
                      </div>
                      {revoked && (
                        <div>
                          <dt>Stopped working</dt>
                          <dd>{fmtUtc(k.revoked_at)}</dd>
                        </div>
                      )}
                    </dl>
                    {revoking === k.id && (
                      <div
                        class="confirm-box"
                        role="group"
                        aria-label={`Confirm revoking key ${k.prefix}`}
                      >
                        <p>
                          <strong>
                            Revoke {k.prefix}… (id {k.id})?
                          </strong>{" "}
                          It stops working immediately
                          {current
                            ? ", and it is the key you signed in with, so you will be signed out"
                            : active === 1
                              ? " and you will have no active key"
                              : ""}
                          .
                        </p>
                        <div class="row">
                          <button
                            class="btn btn-danger btn-sm"
                            type="button"
                            ref={revokeGo}
                            disabled={busy}
                            onClick={() => revoke(k)}
                          >
                            Yes, revoke
                          </button>
                          <button
                            class="btn btn-sm"
                            type="button"
                            onClick={() => {
                              setRevoking(null);
                              focusAfter.current = () =>
                                document
                                  .querySelector<HTMLButtonElement>(
                                    `button[aria-label="Revoke key ${k.prefix}, id ${k.id}"]`,
                                  )
                                  ?.focus();
                            }}
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
            <p class="xsmall muted" style={{ marginTop: "var(--s2)" }}>
              Revoked keys are listed for 30 days. A revoked key stops working at once.
            </p>
          </section>

          <section class="card panel" aria-labelledby="use-title">
            <h2 id="use-title">Usage and limits</h2>
            <dl class="stats">
              <div>
                <dt>Requests today (UTC)</dt>
                <dd>{num(account.usage.today)}</dd>
              </div>
              <div>
                <dt>Last 30 days</dt>
                <dd>{num(account.usage.last_30_days)}</dd>
              </div>
              <div>
                <dt>Per minute</dt>
                <dd>{num(account.limits.per_minute)}</dd>
              </div>
              <div>
                <dt>Per hour</dt>
                <dd>{num(account.limits.per_hour)}</dd>
              </div>
              <div>
                <dt>At once</dt>
                <dd>{num(account.limits.concurrent)}</dd>
              </div>
            </dl>
            <p class="xsmall muted" style={{ margin: "var(--s3) 0 0" }}>
              Counted per account across all your keys, up to a minute behind.
            </p>
          </section>

          <section class="card panel danger-zone stack" aria-labelledby="del-title">
            <h2 id="del-title">Delete my account</h2>
            <p class="small">
              Removes your email address, all keys and all usage counters for good. Scripts that use
              your keys will get <code>401 invalid_api_key</code>. This cannot be undone.
            </p>
            <button
              class="btn btn-danger"
              type="button"
              ref={delOpenBtn}
              aria-expanded={delOpen}
              aria-controls="del-confirm"
              onClick={() => {
                setDelOpen(true);
                setActionError(undefined);
                focusAfter.current = () => delCheckRef.current?.focus();
              }}
            >
              Delete my account…
            </button>
            {delOpen && (
              <div class="confirm-box" id="del-confirm">
                <p>
                  <strong>Delete {account.email} and everything linked to it?</strong>
                </p>
                <label class="check small">
                  <input
                    type="checkbox"
                    ref={delCheckRef}
                    checked={delCheck}
                    onChange={(ev) => setDelCheck((ev.currentTarget as HTMLInputElement).checked)}
                  />{" "}
                  I understand this is permanent and my keys will stop working.
                </label>
                {actionError !== undefined && (
                  <div style={{ marginTop: "var(--s3)" }}>
                    <KeyError error={actionError} />
                  </div>
                )}
                <div class="row" style={{ marginTop: "var(--s3)" }}>
                  <button
                    class="btn btn-danger"
                    type="button"
                    disabled={!delCheck || busy}
                    onClick={deleteAccount}
                  >
                    Yes, delete everything
                  </button>
                  <button
                    class="btn"
                    type="button"
                    onClick={() => {
                      setDelOpen(false);
                      setDelCheck(false);
                      setActionError(undefined);
                      focusAfter.current = () => delOpenBtn.current?.focus();
                    }}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </section>
        </div>
      )}

      {phase.name === "deleted" && (
        <section class="card panel stack" aria-labelledby="gone-title">
          <div class="alert alert-ok">
            <div>
              <div class="alert-title" id="gone-title" tabIndex={-1} ref={goneTitle}>
                Your account has been deleted
              </div>
              <p class="small">
                Your email address, keys and usage counters are removed. The API keeps working
                without a key.
              </p>
            </div>
          </div>
          <Link class="btn" href="/">
            Back to Sol Index
          </Link>
        </section>
      )}

      <p class="xsmall mono muted api-line">
        API: GET /api/v2/account · POST /api/v2/account/keys · DELETE /api/v2/account/keys/{"{id}"}{" "}
        · DELETE /api/v2/account
      </p>
    </div>
  );
}
