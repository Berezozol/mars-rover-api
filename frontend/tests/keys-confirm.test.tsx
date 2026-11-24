// Stage 20B, screens `/keys/confirm` (AC-29) and `/keys/delete#token` (AC-31, step 2).
// Written against docs/API.md ("Ключи API (этап 20B)"), design/mockups/keys-confirm.html, keys-delete.html.
import { fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { describe, expect, it, vi } from "vitest";
import { App } from "../src/App";
import { apiError, json } from "./helpers";
import { confirmBody, deferred, KEY, mockKeysApi, noContent, TOKEN } from "./keys-helpers";

const CONFIRM = "/api/v2/keys/confirm";
const DELETE_CONFIRM = "/api/v2/account/deletion-confirm";

function open(path: string, routes: Parameters<typeof mockKeysApi>[0]) {
  const m = mockKeysApi(routes);
  window.history.replaceState(null, "", path);
  render(<App />);
  return m;
}

describe("AC-29 /keys/confirm", () => {
  it("AC-29 removes the token from the address bar at once and sends nothing yet", async () => {
    const replace = vi.spyOn(window.history, "replaceState");
    const m = open(`/keys/confirm#token=${TOKEN}`, {
      [`POST ${CONFIRM}`]: json(confirmBody, { status: 201 }),
    });
    await screen.findByRole("button", { name: /show my key/i });
    expect(window.location.hash).toBe("");
    expect(window.location.href).not.toContain(TOKEN);
    expect(replace.mock.calls.some((c) => !String(c[2] ?? "").includes(TOKEN))).toBe(true);
    // The link is opened by people and by mail scanners: only the button spends the token.
    expect(m.to("POST", CONFIRM)).toHaveLength(0);
    expect(m.calls.filter((c) => c.method !== "GET")).toHaveLength(0);
  });

  it("AC-29 spends the token only on the button press, with revoke_existing false", async () => {
    const m = open(`/keys/confirm#token=${TOKEN}`, {
      [`POST ${CONFIRM}`]: json(confirmBody, { status: 201 }),
    });
    fireEvent.click(await screen.findByRole("button", { name: /show my key/i }));
    expect(await screen.findByText(KEY)).toBeTruthy();
    const calls = m.to("POST", CONFIRM);
    expect(calls).toHaveLength(1);
    expect(calls.at(0)!.body).toMatchObject({ token: TOKEN });
    // revoke_existing is optional in the contract: absent means false.
    expect((calls.at(0)!.body as { revoke_existing?: boolean }).revoke_existing ?? false).toBe(
      false,
    );
    expect(calls.at(0)!.headers.get("Content-Type")).toMatch(/application\/json/);
  });

  it("AC-29 shows the key once with a save-now warning and a copy button", async () => {
    open(`/keys/confirm#token=${TOKEN}`, {
      [`POST ${CONFIRM}`]: json(confirmBody, { status: 201 }),
    });
    fireEvent.click(await screen.findByRole("button", { name: /show my key/i }));
    expect(await screen.findByText(KEY)).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toMatch(/save it now/i);
    expect(screen.getByRole("alert").textContent).toMatch(/not be shown again/i);
    expect(screen.getByRole("button", { name: /copy key/i })).toBeTruthy();
    // The token is gone for good, and the key is neither in the address nor in storage.
    expect(window.location.href).not.toContain(KEY);
    expect(JSON.stringify({ ...localStorage })).not.toContain(KEY);
    expect(JSON.stringify({ ...sessionStorage })).not.toContain(KEY);
    expect(document.cookie).not.toContain(KEY);
    // The way on: manage the keys.
    expect(screen.getByRole("link", { name: /go to my keys/i }).getAttribute("href")).toBe(
      "/keys/manage",
    );
  });

  it("AC-29 copies the key to the clipboard and says so", async () => {
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    open(`/keys/confirm#token=${TOKEN}`, {
      [`POST ${CONFIRM}`]: json(confirmBody, { status: 201 }),
    });
    fireEvent.click(await screen.findByRole("button", { name: /show my key/i }));
    fireEvent.click(await screen.findByRole("button", { name: /copy key/i }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(KEY));
    await waitFor(() =>
      expect(screen.getAllByRole("status").some((s) => /copied/i.test(s.textContent ?? ""))).toBe(
        true,
      ),
    );
  });

  it("AC-29 keeps the copy button usable when the clipboard is unavailable", async () => {
    vi.stubGlobal("navigator", { ...navigator, clipboard: undefined });
    open(`/keys/confirm#token=${TOKEN}`, {
      [`POST ${CONFIRM}`]: json(confirmBody, { status: 201 }),
    });
    fireEvent.click(await screen.findByRole("button", { name: /show my key/i }));
    fireEvent.click(await screen.findByRole("button", { name: /copy key/i }));
    // The key is still on the page for a manual copy.
    expect(screen.getByText(KEY)).toBeTruthy();
  });

  it("AC-29 disables the button while the key is being created", async () => {
    const gate = deferred<Response>();
    const m = open(`/keys/confirm#token=${TOKEN}`, { [`POST ${CONFIRM}`]: () => gate.promise });
    fireEvent.click(await screen.findByRole("button", { name: /show my key/i }));
    await waitFor(() => expect(m.to("POST", CONFIRM)).toHaveLength(1));
    expect((await screen.findByRole("status")).textContent).toMatch(/creating/i);
    const btn = screen.getAllByRole("button").find((b) => (b as HTMLButtonElement).disabled);
    expect(btn).toBeTruthy();
    fireEvent.click(btn!);
    gate.resolve(json(confirmBody, { status: 201 }));
    await screen.findByText(KEY);
    expect(m.to("POST", CONFIRM)).toHaveLength(1);
  });

  it("AC-29 on 409 offers to replace the keys and repeats with revoke_existing true", async () => {
    let n = 0;
    const m = open(`/keys/confirm#token=${TOKEN}`, {
      [`POST ${CONFIRM}`]: () =>
        n++ === 0
          ? apiError(409, "key_limit_reached", "Key limit reached")
          : json(confirmBody, { status: 201 }),
    });
    fireEvent.click(await screen.findByRole("button", { name: /show my key/i }));
    expect(await screen.findByText(/already have 2 active keys/i)).toBeTruthy();
    // The consequence is spelled out before the destructive button.
    expect(screen.getByText(/both current keys stop working immediately/i)).toBeTruthy();
    expect(
      (m.one("POST", CONFIRM, 0).body as { revoke_existing?: boolean }).revoke_existing ?? false,
    ).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: /replace my keys/i }));
    expect(await screen.findByText(KEY)).toBeTruthy();
    const calls = m.to("POST", CONFIRM);
    expect(calls).toHaveLength(2);
    // Same token: the first answer did not spend it.
    expect(calls.at(1)!.body).toEqual({ token: TOKEN, revoke_existing: true });
    expect(window.location.hash).toBe("");
  });

  it("AC-29 on 409 lets the user keep the keys (link to My keys)", async () => {
    open(`/keys/confirm#token=${TOKEN}`, {
      [`POST ${CONFIRM}`]: apiError(409, "key_limit_reached", "Key limit reached"),
    });
    fireEvent.click(await screen.findByRole("button", { name: /show my key/i }));
    const keep = await screen.findByRole("link", { name: /keep my keys/i });
    expect(keep.getAttribute("href")).toBe("/keys/manage");
  });

  it("AC-29 on 400 says the link is stale and links to /keys", async () => {
    open(`/keys/confirm#token=${TOKEN}`, {
      [`POST ${CONFIRM}`]: apiError(400, "invalid_token", "Token is invalid or expired"),
    });
    fireEvent.click(await screen.findByRole("button", { name: /show my key/i }));
    expect(await screen.findByText(/expired or was already used/i)).toBeTruthy();
    const again = screen.getByRole("link", { name: /request a new link/i });
    expect(again.getAttribute("href")).toBe("/keys");
    expect(screen.queryByText(KEY)).toBeNull();
  });

  it("AC-29 on 403 says the account is blocked and shows no key", async () => {
    open(`/keys/confirm#token=${TOKEN}`, {
      [`POST ${CONFIRM}`]: apiError(403, "account_blocked", "Account is blocked"),
    });
    fireEvent.click(await screen.findByRole("button", { name: /show my key/i }));
    expect(await screen.findByText(/cannot get new keys/i)).toBeTruthy();
    expect(screen.queryByText(KEY)).toBeNull();
  });

  it("AC-29 on a network error keeps the button so the user can try again", async () => {
    let n = 0;
    const m = open(`/keys/confirm#token=${TOKEN}`, {
      [`POST ${CONFIRM}`]: () => {
        if (n++ === 0) throw new TypeError("Failed to fetch");
        return json(confirmBody, { status: 201 });
      },
    });
    fireEvent.click(await screen.findByRole("button", { name: /show my key/i }));
    await screen.findByRole("alert");
    const retry = await screen.findByRole("button", { name: /show my key|try again/i });
    fireEvent.click(retry);
    expect(await screen.findByText(KEY)).toBeTruthy();
    // The token survived in the component, not in the address.
    expect(m.one("POST", CONFIRM, 1).body).toMatchObject({ token: TOKEN });
    expect(window.location.hash).toBe("");
  });

  it("AC-29 asks the browser not to send a Referer from this screen", async () => {
    open(`/keys/confirm#token=${TOKEN}`, {});
    await screen.findByRole("button", { name: /show my key/i });
    expect(document.querySelector('meta[name="referrer"]')?.getAttribute("content")).toBe(
      "no-referrer",
    );
  });

  it("AC-29 without a #token asks to open the link from the email and sends nothing", async () => {
    const m = open("/keys/confirm", {});
    expect(await screen.findByText(/open the link from your email/i)).toBeTruthy();
    const back = screen
      .getAllByRole("link")
      .find((a) => a.getAttribute("href") === "/keys" && !a.closest("nav, footer"));
    expect(back).toBeTruthy();
    expect(screen.queryByRole("button", { name: /show my key/i })).toBeNull();
    expect(m.calls.filter((c) => c.method !== "GET")).toHaveLength(0);
  });
});

describe("AC-31 /keys/delete#token (step 2)", () => {
  it("AC-31 removes the token from the address at once and sends nothing yet", async () => {
    const m = open(`/keys/delete#token=${TOKEN}`, { [`POST ${DELETE_CONFIRM}`]: noContent() });
    await screen.findByRole("button", { name: /yes, delete my data/i });
    expect(window.location.hash).toBe("");
    expect(window.location.href).not.toContain(TOKEN);
    expect(m.calls.filter((c) => c.method !== "GET")).toHaveLength(0);
    expect(document.querySelector('meta[name="referrer"]')?.getAttribute("content")).toBe(
      "no-referrer",
    );
  });

  it("AC-31 asks to confirm, then deletes on the button press and says the data is deleted", async () => {
    const m = open(`/keys/delete#token=${TOKEN}`, { [`POST ${DELETE_CONFIRM}`]: noContent() });
    expect(await screen.findByRole("heading", { name: /delete your data\?/i })).toBeTruthy();
    expect(screen.getByRole("link", { name: /no, keep everything/i })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /yes, delete my data/i }));
    expect(await screen.findByText(/your data has been deleted/i)).toBeTruthy();
    const calls = m.to("POST", DELETE_CONFIRM);
    expect(calls).toHaveLength(1);
    expect(calls.at(0)!.body).toEqual({ token: TOKEN });
  });

  it("AC-31 shows progress and blocks a second press while deleting", async () => {
    const gate = deferred<Response>();
    const m = open(`/keys/delete#token=${TOKEN}`, {
      [`POST ${DELETE_CONFIRM}`]: () => gate.promise,
    });
    fireEvent.click(await screen.findByRole("button", { name: /yes, delete my data/i }));
    await waitFor(() => expect(m.to("POST", DELETE_CONFIRM)).toHaveLength(1));
    expect((await screen.findByRole("status")).textContent).toMatch(/deleting/i);
    for (const b of screen.getAllByRole("button")) fireEvent.click(b);
    gate.resolve(noContent());
    await screen.findByText(/your data has been deleted/i);
    expect(m.to("POST", DELETE_CONFIRM)).toHaveLength(1);
  });

  it("AC-31 on 400 says the link is stale", async () => {
    open(`/keys/delete#token=${TOKEN}`, {
      [`POST ${DELETE_CONFIRM}`]: apiError(400, "invalid_token", "Token is invalid or expired"),
    });
    fireEvent.click(await screen.findByRole("button", { name: /yes, delete my data/i }));
    expect(await screen.findByText(/expired or was already used/i)).toBeTruthy();
    expect(screen.queryByText(/your data has been deleted/i)).toBeNull();
  });
});
