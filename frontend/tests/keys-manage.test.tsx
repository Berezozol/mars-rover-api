// Stage 20B, screen `/keys/manage` (AC-30). Written against docs/API.md ("Ключи API (этап 20B)")
// and design/mockups/keys-manage.html.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { describe, expect, it } from "vitest";
import { App } from "../src/App";
import { apiError, json } from "./helpers";
import {
  accountBody,
  deferred,
  key39Revoked,
  key41,
  key44,
  KEY,
  mockKeysApi,
  NEW_KEY,
  noContent,
  type KeyInfo,
} from "./keys-helpers";

const ACCOUNT = "/api/v2/account";
const MY_KEY = "si_EXAMPLEE" + "X".repeat(24);

function type(el: HTMLElement, value: string) {
  fireEvent.input(el, { target: { value } });
  fireEvent.change(el, { target: { value } });
}

/** A tiny stateful server: the key list changes after a revoke or a create. */
function server(initial: KeyInfo[]) {
  let keys = initial.map((k) => ({ ...k }));
  const m = mockKeysApi({
    [`GET ${ACCOUNT}`]: () => json(accountBody(keys)),
    [`POST ${ACCOUNT}/keys`]: () => {
      const created: KeyInfo = {
        id: 45,
        prefix: NEW_KEY.slice(0, 11),
        created_at: "2026-12-05T09:00:00Z",
        last_used_at: null,
        revoked_at: null,
      };
      keys = [created, ...keys];
      return json({ key: NEW_KEY, key_info: created }, { status: 201 });
    },
    [`DELETE ${ACCOUNT}`]: noContent(),
    ...Object.fromEntries(
      [41, 44].map((id) => [
        `DELETE ${ACCOUNT}/keys/${id}`,
        () => {
          keys = keys.map((k) => (k.id === id ? { ...k, revoked_at: "2026-12-05T10:00:00Z" } : k));
          return noContent();
        },
      ]),
    ),
  });
  return m;
}

async function signIn(secret = MY_KEY) {
  window.history.replaceState(null, "", "/keys/manage");
  render(<App />);
  const field = await screen.findByLabelText(/^api key$/i);
  type(field, secret);
  fireEvent.click(screen.getByRole("button", { name: /show my account/i }));
}

// Scoped to the list: the status region also announces "Key si_XXXX… revoked" (keys-flow AC-9).
const keyRow = (prefix: string) =>
  within(screen.getByRole("list", { name: /your keys/i }))
    .getByText(new RegExp(prefix))
    .closest("li") as HTMLElement;

describe("AC-30 /keys/manage sign-in by key", () => {
  it("AC-30 shows a password-type key field and sends nothing before the button", async () => {
    const m = server([key41]);
    window.history.replaceState(null, "", "/keys/manage");
    render(<App />);
    const field = (await screen.findByLabelText(/^api key$/i)) as HTMLInputElement;
    expect(field.type).toBe("password");
    expect(field.autocomplete).toBe("off");
    type(field, MY_KEY);
    expect(m.to("GET", ACCOUNT)).toHaveLength(0);
  });

  it("AC-30 sends the key in the X-Api-Key header, never in the URL", async () => {
    const m = server([key41]);
    await signIn();
    await screen.findByText(/user@example\.org/);
    const call = m.one("GET", ACCOUNT, 0);
    expect(call.headers.get("X-Api-Key")).toBe(MY_KEY);
    expect(call.url.search).not.toContain("api_key");
    expect(call.url.href).not.toContain(MY_KEY);
  });

  it("AC-30 keeps the key out of localStorage, sessionStorage, cookies and the address", async () => {
    server([key41]);
    await signIn();
    await screen.findByText(/user@example\.org/);
    for (const store of [localStorage, sessionStorage]) {
      expect(JSON.stringify({ ...store })).not.toContain(MY_KEY);
      expect(store.length).toBe(0);
    }
    expect(document.cookie).not.toContain(MY_KEY);
    expect(window.location.href).not.toContain(MY_KEY);
    expect(window.location.pathname).toBe("/keys/manage");
    expect(window.location.search).toBe("");
    expect(window.location.hash).toBe("");
  });

  it("AC-30 also keeps a key that was just issued out of storage and the address", async () => {
    server([key41]);
    await signIn();
    fireEvent.click(await screen.findByRole("button", { name: /create a new key/i }));
    await screen.findByText(NEW_KEY);
    expect(JSON.stringify({ ...localStorage })).not.toContain(NEW_KEY);
    expect(JSON.stringify({ ...sessionStorage })).not.toContain(NEW_KEY);
    expect(document.cookie).not.toContain(NEW_KEY);
    expect(window.location.href).not.toContain(NEW_KEY);
  });

  it("AC-30 shows the loading state and blocks a second request", async () => {
    const gate = deferred<Response>();
    const m = mockKeysApi({ [`GET ${ACCOUNT}`]: () => gate.promise });
    window.history.replaceState(null, "", "/keys/manage");
    render(<App />);
    type(await screen.findByLabelText(/^api key$/i), MY_KEY);
    fireEvent.click(screen.getByRole("button", { name: /show my account/i }));
    await waitFor(() => expect(m.to("GET", ACCOUNT)).toHaveLength(1));
    expect((await screen.findByRole("status")).textContent).toMatch(/loading/i);
    for (const b of screen.getAllByRole("button")) fireEvent.click(b);
    gate.resolve(json(accountBody([key41])));
    await screen.findByText(/user@example\.org/);
    expect(m.to("GET", ACCOUNT)).toHaveLength(1);
  });

  it("AC-30 401 invalid_api_key says the key is not recognised and keeps the field", async () => {
    mockKeysApi({
      [`GET ${ACCOUNT}`]: apiError(401, "invalid_api_key", "Invalid API key"),
    });
    await signIn();
    expect((await screen.findByRole("alert")).textContent).toMatch(/do not recognise this key/i);
    expect(screen.getByLabelText(/^api key$/i)).toBeTruthy();
    expect(screen.queryByRole("heading", { name: /^keys$/i })).toBeNull();
  });

  it("AC-30 401 api_key_revoked says the key has been revoked", async () => {
    mockKeysApi({
      [`GET ${ACCOUNT}`]: apiError(401, "api_key_revoked", "API key has been revoked"),
    });
    await signIn();
    expect((await screen.findByRole("alert")).textContent).toMatch(/has been revoked/i);
  });

  it("AC-30 401 api_key_required (empty field) does not look like a crash", async () => {
    mockKeysApi({
      [`GET ${ACCOUNT}`]: apiError(401, "api_key_required", "API key required"),
    });
    await signIn("");
    // keys-flow AC-7: the empty key is reported at the field, not by a banner or a request.
    const field = screen.getByLabelText(/^api key$/i);
    await waitFor(() => expect(field.getAttribute("aria-invalid")).toBe("true"));
    expect(document.getElementById("apikey-err")?.textContent).toBe("Enter your API key.");
    expect(document.activeElement).toBe(field);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("AC-30 403 account_blocked says the account is blocked", async () => {
    mockKeysApi({
      [`GET ${ACCOUNT}`]: apiError(403, "account_blocked", "Account is blocked"),
    });
    await signIn();
    expect((await screen.findByRole("alert")).textContent).toMatch(/account is blocked/i);
  });
});

describe("AC-30 /keys/manage account", () => {
  it("AC-30 lists the keys with status, usage and limits", async () => {
    server([key44, key41, key39Revoked]);
    await signIn();
    expect(await screen.findByText(/user@example\.org/)).toBeTruthy();
    const list = screen.getByRole("list", { name: /your keys/i });
    expect(within(list).getAllByRole("listitem")).toHaveLength(3);
    expect(within(keyRow("si_EXAMPLEF")).getByText(/active/i)).toBeTruthy();
    expect(within(keyRow("si_EXAMPLED")).getAllByText(/revoked/i).length).toBeTruthy();
    // A revoked key cannot be revoked again.
    expect(within(keyRow("si_EXAMPLED")).queryByRole("button", { name: /revoke/i })).toBeNull();
    expect(screen.getByText("312")).toBeTruthy();
    expect(screen.getByText(/10,450|10 450|10450/)).toBeTruthy();
    expect(screen.getByText(/10,000|10 000|10000/)).toBeTruthy();
    expect(screen.getByText("120")).toBeTruthy();
    // The list never shows a whole key, only the prefix.
    expect(document.body.textContent).not.toContain(MY_KEY);
  });

  it("AC-30 marks the key that was used to sign in, matched by prefix", async () => {
    server([key44, key41]);
    await signIn(MY_KEY); // prefix si_EXAMPLEE = key41
    await screen.findByText(/user@example\.org/);
    expect(within(keyRow("si_EXAMPLEE")).getByText(/this key|current key|signed in/i)).toBeTruthy();
    expect(within(keyRow("si_EXAMPLEF")).queryByText(/this key|current key|signed in/i)).toBeNull();
  });

  it("AC-30 revokes a key only after an in-place confirmation, then shows it as revoked", async () => {
    const m = server([key44, key41]);
    await signIn(MY_KEY);
    await screen.findByText(/user@example\.org/);
    fireEvent.click(screen.getByRole("button", { name: /revoke key si_EXAMPLEF/i }));
    // Nothing is sent before the confirmation.
    expect(m.to("DELETE", `${ACCOUNT}/keys/44`)).toHaveLength(0);
    const group = await screen.findByRole("group", { name: /confirm revoking key si_EXAMPLEF/i });
    fireEvent.click(within(group).getByRole("button", { name: /yes, revoke/i }));
    await waitFor(() => expect(m.to("DELETE", `${ACCOUNT}/keys/44`)).toHaveLength(1));
    await waitFor(() =>
      expect(within(keyRow("si_EXAMPLEF")).getAllByText(/revoked/i).length).toBeTruthy(),
    );
    expect(m.one("DELETE", `${ACCOUNT}/keys/44`, 0).headers.get("X-Api-Key")).toBe(MY_KEY);
  });

  it("AC-30 Cancel on the revoke confirmation sends nothing and returns focus to the button", async () => {
    const m = server([key44, key41]);
    await signIn(MY_KEY);
    await screen.findByText(/user@example\.org/);
    const open = screen.getByRole("button", { name: /revoke key si_EXAMPLEF/i });
    fireEvent.click(open);
    const group = await screen.findByRole("group", { name: /confirm revoking key si_EXAMPLEF/i });
    fireEvent.click(within(group).getByRole("button", { name: /cancel/i }));
    expect(screen.queryByRole("group", { name: /confirm revoking/i })).toBeNull();
    expect(m.calls.filter((c) => c.method === "DELETE")).toHaveLength(0);
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole("button", { name: /revoke key si_EXAMPLEF/i }),
      ),
    );
  });

  it("AC-30 creates a new key and shows it once with a save-now warning", async () => {
    const m = server([key41]);
    await signIn(MY_KEY);
    fireEvent.click(await screen.findByRole("button", { name: /create a new key/i }));
    expect(await screen.findByText(NEW_KEY)).toBeTruthy();
    expect(
      screen.getAllByRole("alert").some((a) => /not be shown again/i.test(a.textContent ?? "")),
    ).toBe(true);
    expect(screen.getByRole("button", { name: /copy key/i })).toBeTruthy();
    expect(m.to("POST", `${ACCOUNT}/keys`)).toHaveLength(1);
    expect(m.one("POST", `${ACCOUNT}/keys`, 0).headers.get("X-Api-Key")).toBe(MY_KEY);
    // The list is refreshed: the new key is in it by prefix only.
    await waitFor(() =>
      expect(screen.getAllByText(new RegExp(NEW_KEY.slice(0, 11))).length).toBeGreaterThan(0),
    );
  });

  it("AC-30 disables 'Create a new key' at two active keys and explains why", async () => {
    const m = server([key44, key41]);
    await signIn(MY_KEY);
    const btn = (await screen.findByRole("button", {
      name: /create a new key/i,
    })) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(screen.getByText(/2 active keys/i)).toBeTruthy();
    expect(m.to("POST", `${ACCOUNT}/keys`)).toHaveLength(0);
  });

  it("AC-30 shows a message when the server answers 409 to a new key", async () => {
    mockKeysApi({
      [`GET ${ACCOUNT}`]: () => json(accountBody([key41])),
      [`POST ${ACCOUNT}/keys`]: apiError(409, "key_limit_reached", "Key limit reached"),
    });
    await signIn(MY_KEY);
    fireEvent.click(await screen.findByRole("button", { name: /create a new key/i }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/2 active keys/i);
  });

  it("AC-30 deletes the account only after a confirmation checkbox", async () => {
    const m = server([key41]);
    await signIn(MY_KEY);
    await screen.findByText(/user@example\.org/);
    fireEvent.click(screen.getByRole("button", { name: /delete my account/i }));
    const go = (await screen.findByRole("button", {
      name: /yes, delete everything/i,
    })) as HTMLButtonElement;
    expect(go.disabled).toBe(true);
    // A disabled button cannot be activated in a browser, so nothing is sent before the checkbox.
    expect(m.to("DELETE", ACCOUNT)).toHaveLength(0);

    fireEvent.click(screen.getByRole("checkbox", { name: /i understand/i }));
    expect(go.disabled).toBe(false);
    fireEvent.click(go);
    expect(await screen.findByText(/your account has been deleted/i)).toBeTruthy();
    expect(m.to("DELETE", ACCOUNT)).toHaveLength(1);
    expect(m.one("DELETE", ACCOUNT, 0).headers.get("X-Api-Key")).toBe(MY_KEY);
    // The key is forgotten with the account.
    expect(screen.queryByLabelText(/^api key$/i)).toBeNull();
    expect(document.body.textContent).not.toContain(MY_KEY);
  });

  it("AC-30 Cancel on the account deletion sends nothing", async () => {
    const m = server([key41]);
    await signIn(MY_KEY);
    await screen.findByText(/user@example\.org/);
    fireEvent.click(screen.getByRole("button", { name: /delete my account/i }));
    fireEvent.click(await screen.findByRole("button", { name: /^cancel$/i }));
    expect(screen.queryByRole("button", { name: /yes, delete everything/i })).toBeNull();
    expect(m.calls.filter((c) => c.method === "DELETE")).toHaveLength(0);
  });

  it("AC-30 Sign out clears the key field and the account from the page", async () => {
    server([key41]);
    await signIn(MY_KEY);
    await screen.findByText(/user@example\.org/);
    fireEvent.click(screen.getByRole("button", { name: /sign out/i }));
    const field = (await screen.findByLabelText(/^api key$/i)) as HTMLInputElement;
    expect(field.value).toBe("");
    expect(screen.queryByText(/user@example\.org/)).toBeNull();
  });

  it("AC-30 a key rejected in the middle of a session (401 on revoke) is explained", async () => {
    mockKeysApi({
      [`GET ${ACCOUNT}`]: () => json(accountBody([key44, key41])),
      [`DELETE ${ACCOUNT}/keys/44`]: apiError(401, "api_key_revoked", "API key has been revoked"),
    });
    await signIn(MY_KEY);
    await screen.findByText(/user@example\.org/);
    fireEvent.click(screen.getByRole("button", { name: /revoke key si_EXAMPLEF/i }));
    const group = await screen.findByRole("group", { name: /confirm revoking/i });
    fireEvent.click(within(group).getByRole("button", { name: /yes, revoke/i }));
    expect(
      (await screen.findAllByRole("alert")).some((a) => /revoked/i.test(a.textContent ?? "")),
    ).toBe(true);
  });

  it("AC-30 uses the key typed by the user only as typed (no trimming of the secret into the URL)", async () => {
    const m = server([key41]);
    await signIn(KEY);
    await screen.findByText(/user@example\.org/);
    expect(m.one("GET", ACCOUNT, 0).headers.get("X-Api-Key")).toBe(KEY);
  });
});
