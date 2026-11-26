// Frontend keys flow, package 2 of the 2026-11-26 review (docs/proposals/frontend-keys-flow.md), AC-1..AC-10.
// Written against the spec and docs/API.md (API keys section), not against the implementation.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { describe, expect, it } from "vitest";
import { App } from "../src/App";
import { apiError, json } from "./helpers";
import {
  accountBody,
  confirmBody,
  key41,
  key44,
  mockKeysApi,
  noContent,
  TOKEN,
} from "./keys-helpers";

const CONFIRM = "/api/v2/keys/confirm";
const REQUESTS = "/api/v2/keys/requests";
const DELETION = "/api/v2/account/deletion-requests";
const DELETE_CONFIRM = "/api/v2/account/deletion-confirm";
const ACCOUNT = "/api/v2/account";
const MY_KEY = "si_EXAMPLEE" + "X".repeat(24);

type Routes = Parameters<typeof mockKeysApi>[0];

function open(path: string, routes: Routes) {
  const m = mockKeysApi(routes);
  window.history.replaceState(null, "", path);
  render(<App />);
  return m;
}

function type(el: HTMLElement, value: string) {
  fireEvent.input(el, { target: { value } });
  fireEvent.change(el, { target: { value } });
}

const notFound = () => apiError(404, "not_found", "Not found");
const serverError = () => apiError(500, "internal_error", "Internal error");
const networkError = (): Response => {
  throw new TypeError("Failed to fetch");
};

/** Every failure the spec lists for AC-2 / AC-8: a 500 body and a dropped connection. */
const FAILURES = [
  ["a 500 answer", serverError],
  ["a network error", networkError],
] as const;

const pageText = () => document.body.textContent ?? "";

describe("keys-flow AC-1 /keys/confirm before the button", () => {
  it("keys-flow AC-1 does not claim the email is confirmed or the link valid", async () => {
    const m = open(`/keys/confirm#token=${TOKEN}`, {});
    const heading = await screen.findByRole("heading", { name: "One step left" });
    const card = heading.closest("section")!;
    expect(card.textContent).not.toMatch(/confirmed/i);
    expect(card.textContent).not.toMatch(/\bvalid\b/i);
    expect(screen.queryByRole("heading", { name: /email confirmed/i })).toBeNull();
    // The button creates the key; an expired link is reported here, after the press.
    expect(card.textContent).toMatch(/button/i);
    expect(card.textContent).toMatch(/create your api key/i);
    expect(card.textContent).toMatch(/expired/i);
    expect(card.textContent).toMatch(/only once/i);
    expect(m.to("POST", CONFIRM)).toHaveLength(0);
  });
});

describe("keys-flow AC-2 /keys/confirm after a failed press", () => {
  for (const [label, failure] of FAILURES) {
    it(`keys-flow AC-2 ${label} gives the card the heading "Could not create your key"`, async () => {
      open(`/keys/confirm#token=${TOKEN}`, { [`POST ${CONFIRM}`]: failure });
      fireEvent.click(await screen.findByRole("button", { name: /show my key/i }));
      expect(
        await screen.findByRole("heading", { name: "Could not create your key" }),
      ).toBeTruthy();
      expect(screen.queryByRole("heading", { name: "One step left" })).toBeNull();
      expect(pageText()).not.toMatch(/email confirmed/i);
    });
  }

  it("keys-flow AC-2 a 404 gives the same failed heading", async () => {
    open(`/keys/confirm#token=${TOKEN}`, { [`POST ${CONFIRM}`]: notFound() });
    fireEvent.click(await screen.findByRole("button", { name: /show my key/i }));
    expect(await screen.findByRole("heading", { name: "Could not create your key" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "One step left" })).toBeNull();
  });
});

describe("keys-flow AC-3 404 while the key endpoints are switched off", () => {
  const NOT_OPEN = /this part of sol index is not open right now/i;
  const BY_HAND = /reply to any email we sent you and we will delete your data by hand/i;

  function expectNotAvailable(withHandPath: boolean) {
    expect(screen.getByText("Not available right now")).toBeTruthy();
    expect(pageText()).toMatch(NOT_OPEN);
    if (withHandPath) expect(pageText()).toMatch(BY_HAND);
    expect(pageText()).not.toMatch(/key not found/i);
    expect(pageText()).not.toMatch(/reload your account/i);
  }

  it("keys-flow AC-3 POST /keys/confirm", async () => {
    open(`/keys/confirm#token=${TOKEN}`, { [`POST ${CONFIRM}`]: notFound() });
    fireEvent.click(await screen.findByRole("button", { name: /show my key/i }));
    await screen.findByText("Not available right now");
    expectNotAvailable(false);
  });

  it("keys-flow AC-3 POST /account/deletion-confirm adds the by-hand path", async () => {
    open(`/keys/delete#token=${TOKEN}`, { [`POST ${DELETE_CONFIRM}`]: notFound() });
    fireEvent.click(await screen.findByRole("button", { name: /yes, delete my data/i }));
    await screen.findByText("Not available right now");
    expectNotAvailable(true);
  });

  it("keys-flow AC-3 POST /account/deletion-requests adds the by-hand path", async () => {
    open("/keys/delete", { [`POST ${DELETION}`]: notFound() });
    type(await screen.findByLabelText(/^email address$/i), "user@example.org");
    fireEvent.click(screen.getByRole("button", { name: /email me a deletion link/i }));
    await screen.findByText("Not available right now");
    expectNotAvailable(true);
  });

  it("keys-flow AC-3 GET /account on entering My keys", async () => {
    open("/keys/manage", { [`GET ${ACCOUNT}`]: notFound() });
    type(await screen.findByLabelText(/^api key$/i), MY_KEY);
    fireEvent.click(screen.getByRole("button", { name: /show my account/i }));
    await screen.findByText("Not available right now");
    expectNotAvailable(false);
  });
});

describe("keys-flow AC-4 404 on revoking a key", () => {
  it("keys-flow AC-4 keeps the 'Key not found' text, and only here", async () => {
    open("/keys/manage", {
      [`GET ${ACCOUNT}`]: () => json(accountBody([key44, key41])),
      [`DELETE ${ACCOUNT}/keys/44`]: notFound(),
    });
    type(await screen.findByLabelText(/^api key$/i), MY_KEY);
    fireEvent.click(screen.getByRole("button", { name: /show my account/i }));
    fireEvent.click(await screen.findByRole("button", { name: /revoke key si_EXAMPLEF/i }));
    const group = await screen.findByRole("group", { name: /confirm revoking key si_EXAMPLEF/i });
    fireEvent.click(within(group).getByRole("button", { name: /yes, revoke/i }));
    expect(await screen.findByText(/key not found/i)).toBeTruthy();
    expect(pageText()).toMatch(/reload your account/i);
    expect(screen.queryByText("Not available right now")).toBeNull();
  });
});

describe("keys-flow AC-5 deletion texts", () => {
  it("keys-flow AC-5 the form says the link leads to a confirmation page, not 'one click'", async () => {
    open("/keys/delete", {});
    const heading = await screen.findByRole("heading", { name: /delete by email/i });
    const card = heading.closest("section")!;
    expect(card.textContent).toMatch(/link/i);
    expect(card.textContent).toMatch(/confirm/i);
    expect(card.textContent).not.toMatch(/one click/i);
    expect(card.textContent).not.toMatch(/opening it deletes/i);
    expect(
      within(card)
        .getByRole("link", { name: /my keys/i })
        .getAttribute("href"),
    ).toBe("/keys/manage");
  });

  it("keys-flow AC-5 the opened link says the deletion cannot be undone", async () => {
    open(`/keys/delete#token=${TOKEN}`, {});
    const heading = await screen.findByRole("heading", { name: /delete your data\?/i });
    expect(heading.closest("section")!.textContent).toContain("This cannot be undone.");
  });
});

describe("keys-flow AC-7 empty and malformed input", () => {
  for (const path of ["/keys", "/keys/delete"]) {
    it(`keys-flow AC-7 ${path} empty email: "Enter your email address." at the field`, async () => {
      open(path, {});
      const field = (await screen.findByLabelText(/^email address$/i)) as HTMLInputElement;
      fireEvent.click(screen.getByRole("button", { name: /email me a (deletion )?link/i }));
      await waitFor(() => expect(field.getAttribute("aria-invalid")).toBe("true"));
      const err = document.getElementById("email-err")!;
      expect(err.textContent).toBe("Enter your email address.");
      expect(field.getAttribute("aria-describedby")).toContain("email-err");
      await waitFor(() => expect(document.activeElement).toBe(field));
    });

    it(`keys-flow AC-7 ${path} malformed email keeps the format text`, async () => {
      open(path, {});
      const field = (await screen.findByLabelText(/^email address$/i)) as HTMLInputElement;
      type(field, "not an address");
      fireEvent.click(screen.getByRole("button", { name: /email me a (deletion )?link/i }));
      await waitFor(() => expect(field.getAttribute("aria-invalid")).toBe("true"));
      const text = document.getElementById("email-err")!.textContent ?? "";
      expect(text.length).toBeGreaterThan(0);
      expect(text).not.toBe("Enter your email address.");
    });
  }

  it("keys-flow AC-7 /keys/manage empty key: error at the field, no banner, no request", async () => {
    const m = open("/keys/manage", {});
    const field = (await screen.findByLabelText(/^api key$/i)) as HTMLInputElement;
    fireEvent.click(screen.getByRole("button", { name: /show my account/i }));
    await waitFor(() => expect(field.getAttribute("aria-invalid")).toBe("true"));
    const err = document.getElementById("apikey-err")!;
    expect(err.textContent).toBe("Enter your API key.");
    expect(field.getAttribute("aria-describedby")).toContain("apikey-err");
    await waitFor(() => expect(document.activeElement).toBe(field));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText(/enter your api key first/i)).toBeNull();
    expect(m.to("GET", ACCOUNT)).toHaveLength(0);
  });
});

describe("keys-flow AC-8 focus after a server error", () => {
  const settled = async () => {
    await screen.findByTestId("key-error");
    await waitFor(() => {
      const active = document.activeElement as HTMLElement | null;
      expect(active).not.toBeNull();
      expect(active).not.toBe(document.body);
      expect((active as HTMLButtonElement).disabled).not.toBe(true);
    });
  };

  for (const [label, failure] of FAILURES) {
    it(`keys-flow AC-8 /keys after ${label}`, async () => {
      open("/keys", { [`POST ${REQUESTS}`]: failure });
      type(await screen.findByLabelText(/^email address$/i), "user@example.org");
      fireEvent.click(screen.getByRole("button", { name: /email me a link/i }));
      await settled();
    });

    it(`keys-flow AC-8 /keys/delete form after ${label}`, async () => {
      open("/keys/delete", { [`POST ${DELETION}`]: failure });
      type(await screen.findByLabelText(/^email address$/i), "user@example.org");
      fireEvent.click(screen.getByRole("button", { name: /email me a deletion link/i }));
      await settled();
    });

    it(`keys-flow AC-8 /keys/delete confirmation after ${label}`, async () => {
      open(`/keys/delete#token=${TOKEN}`, { [`POST ${DELETE_CONFIRM}`]: failure });
      fireEvent.click(await screen.findByRole("button", { name: /yes, delete my data/i }));
      await settled();
    });

    it(`keys-flow AC-8 /keys/confirm after ${label}`, async () => {
      open(`/keys/confirm#token=${TOKEN}`, { [`POST ${CONFIRM}`]: failure });
      fireEvent.click(await screen.findByRole("button", { name: /show my key/i }));
      await settled();
    });

    it(`keys-flow AC-8 /keys/manage sign-in after ${label}`, async () => {
      open("/keys/manage", { [`GET ${ACCOUNT}`]: failure });
      type(await screen.findByLabelText(/^api key$/i), MY_KEY);
      fireEvent.click(screen.getByRole("button", { name: /show my account/i }));
      await settled();
    });
  }

  it("keys-flow AC-8 the error plate is focusable but not in the tab order", async () => {
    open(`/keys/confirm#token=${TOKEN}`, { [`POST ${CONFIRM}`]: serverError() });
    fireEvent.click(await screen.findByRole("button", { name: /show my key/i }));
    const plate = await screen.findByTestId("key-error");
    expect(plate.getAttribute("tabindex")).toBe("-1");
  });
});

describe("keys-flow AC-9 revoking a key", () => {
  function server(others = [key44]) {
    let revoked = false;
    return mockKeysApi({
      [`GET ${ACCOUNT}`]: () =>
        json(
          accountBody([
            ...others,
            revoked ? { ...key41, revoked_at: "2026-12-05T10:00:00Z" } : key41,
          ]),
        ),
      [`DELETE ${ACCOUNT}/keys/44`]: () => {
        revoked = true;
        return noContent();
      },
      [`POST ${ACCOUNT}/keys`]: () =>
        json(
          {
            key: "si_NEWKEYNEWKEYNEWKEYNEWKEYNEWKEY00",
            key_info: { ...key41, id: 45, prefix: "si_NEWKEYNE" },
          },
          { status: 201 },
        ),
    });
  }

  async function signIn() {
    window.history.replaceState(null, "", "/keys/manage");
    render(<App />);
    type(await screen.findByLabelText(/^api key$/i), MY_KEY);
    fireEvent.click(screen.getByRole("button", { name: /show my account/i }));
    await screen.findByText(/user@example\.org/);
  }

  it("keys-flow AC-9 focus goes to Cancel after Revoke; Enter there cancels and sends nothing", async () => {
    const m = server();
    await signIn();
    fireEvent.click(screen.getByRole("button", { name: /revoke key si_EXAMPLEF/i }));
    const group = await screen.findByRole("group", { name: /confirm revoking key si_EXAMPLEF/i });
    const cancel = within(group).getByRole("button", { name: /cancel/i });
    await waitFor(() => expect(document.activeElement).toBe(cancel));
    expect(document.activeElement).not.toBe(
      within(group).getByRole("button", { name: /yes, revoke/i }),
    );
    // Enter on the focused element: jsdom has no default action, so emulate the browser (Enter on a button clicks it).
    const active = document.activeElement as HTMLElement;
    const notPrevented = fireEvent.keyDown(active, { key: "Enter", code: "Enter" });
    if (notPrevented && active.tagName === "BUTTON") fireEvent.click(active);
    fireEvent.keyUp(active, { key: "Enter", code: "Enter" });
    await waitFor(() =>
      expect(screen.queryByRole("group", { name: /confirm revoking/i })).toBeNull(),
    );
    expect(m.to("DELETE", `${ACCOUNT}/keys/44`)).toHaveLength(0);
    expect(m.calls.filter((c) => c.method === "DELETE")).toHaveLength(0);
  });

  it("keys-flow AC-9 a successful revoke is announced in the status region", async () => {
    const m = server();
    await signIn();
    const status = screen.getByTestId("keys-status");
    expect(status.getAttribute("role")).toBe("status");
    fireEvent.click(screen.getByRole("button", { name: /revoke key si_EXAMPLEF/i }));
    const group = await screen.findByRole("group", { name: /confirm revoking key si_EXAMPLEF/i });
    fireEvent.click(within(group).getByRole("button", { name: /yes, revoke/i }));
    await waitFor(() => expect(m.to("DELETE", `${ACCOUNT}/keys/44`)).toHaveLength(1));
    await waitFor(() => expect(status.textContent).toMatch(/key si_EXAMPLEF.*revoked/i));
  });

  it("keys-flow AC-9 creating a key still focuses 'Your new key'", async () => {
    server([]); // one active key only: the limit is two
    await signIn();
    fireEvent.click(screen.getByRole("button", { name: /create a new key/i }));
    const heading = await screen.findByRole("heading", { name: /your new key/i });
    await waitFor(() => expect(document.activeElement).toBe(heading));
  });
});

describe("keys-flow AC-10 links and reload hints", () => {
  it("keys-flow AC-10 'Lost it?' leads to a new key, deletion is a second link", async () => {
    open("/keys/manage", {});
    await screen.findByLabelText(/^api key$/i);
    const lost = within(screen.getByRole("main"))
      .getByText(/lost it\?/i)
      .closest("p")!;
    expect(
      within(lost)
        .getByRole("link", { name: /request a new key/i })
        .getAttribute("href"),
    ).toBe("/keys");
    expect(
      within(lost)
        .getByRole("link", { name: /delete my data/i })
        .getAttribute("href"),
    ).toBe("/keys/delete");
  });

  for (const path of ["/keys/confirm", "/keys/delete"]) {
    const flag = `sol-index-link:${path}`;

    it(`keys-flow AC-10 ${path}: reload without the hash says the link is still valid`, async () => {
      open(`${path}#token=${TOKEN}`, {});
      await screen.findByRole("heading", { level: 1 });
      expect(sessionStorage.getItem(flag)).toBe("1");
      // No token anywhere in the web storage: only the flag.
      expect(JSON.stringify({ ...sessionStorage, ...localStorage })).not.toContain(TOKEN);
      expect(window.location.hash).toBe("");

      cleanup(); // the reload: the same tab (sessionStorage stays), the address has no hash
      render(<App />);
      expect(await screen.findByText(/your link is still valid/i)).toBeTruthy();
      expect(pageText()).toMatch(/open it again from the email/i);
      expect(pageText()).not.toMatch(/open the link from your email/i);
      expect(sessionStorage.getItem(flag)).toBeNull();

      cleanup(); // the hint is shown once
      render(<App />);
      await screen.findByRole("heading", { level: 1 });
      expect(screen.queryByText(/your link is still valid/i)).toBeNull();
    });
  }

  it("keys-flow AC-10 a hash with a malformed token clears an old flag", async () => {
    sessionStorage.setItem("sol-index-link:/keys/confirm", "1");
    open("/keys/confirm#token=too-short", {});
    await screen.findByRole("heading", { level: 1 });
    expect(sessionStorage.getItem("sol-index-link:/keys/confirm")).toBeNull();
    // A later reload without the hash must not claim a valid link.
    cleanup();
    render(<App />);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByText(/your link is still valid/i)).toBeNull();
  });

  it("keys-flow AC-10 /keys/confirm without a hash and without a flag keeps the old prompt", async () => {
    open("/keys/confirm", {});
    expect(await screen.findByText(/open the link from your email/i)).toBeTruthy();
    expect(screen.queryByText(/your link is still valid/i)).toBeNull();
  });

  it("keys-flow AC-10 the flag of one page does not leak to the other", async () => {
    open(`/keys/confirm#token=${TOKEN}`, {});
    await screen.findByRole("heading", { level: 1 });
    cleanup();
    window.history.replaceState(null, "", "/keys/delete");
    render(<App />);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByText(/your link is still valid/i)).toBeNull();
  });

  it("keys-flow AC-10 without a usable sessionStorage the page still opens and behaves as before", async () => {
    const proto = Object.getPrototypeOf(sessionStorage) as Storage;
    const orig = { s: proto.setItem, g: proto.getItem, r: proto.removeItem };
    const denied = () => {
      throw new Error("denied");
    };
    proto.setItem = denied;
    proto.getItem = denied;
    proto.removeItem = denied;
    try {
      open(`/keys/confirm#token=${TOKEN}`, {
        [`POST ${CONFIRM}`]: json(confirmBody, { status: 201 }),
      });
      expect(await screen.findByRole("button", { name: /show my key/i })).toBeTruthy();
      cleanup();
      render(<App />);
      expect(await screen.findByText(/open the link from your email/i)).toBeTruthy();
    } finally {
      proto.setItem = orig.s;
      proto.getItem = orig.g;
      proto.removeItem = orig.r;
    }
  });
});
