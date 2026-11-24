// Stage 20B, screens `/keys` (AC-28) and `/keys/delete` step 1 (AC-31).
// Written against docs/API.md ("Ключи API (этап 20B)"), design/mockups/keys*.html and the stage spec.
import { fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { describe, expect, it } from "vitest";
import { App } from "../src/App";
import { apiError, json } from "./helpers";
import { accepted, deferred, EMAIL, mockKeysApi, type Call } from "./keys-helpers";

const REQUESTS = "/api/v2/keys/requests";
const DELETION = "/api/v2/account/deletion-requests";

function type(el: HTMLElement, value: string) {
  fireEvent.input(el, { target: { value } });
  fireEvent.change(el, { target: { value } });
}

function open(path: string, routes: Parameters<typeof mockKeysApi>[0]) {
  const m = mockKeysApi(routes);
  window.history.replaceState(null, "", path);
  render(<App />);
  return m;
}

const emailField = () => screen.getByLabelText(/^email address$/i) as HTMLInputElement;

async function submit(label: RegExp, email = EMAIL) {
  type(emailField(), email);
  fireEvent.click(screen.getByRole("button", { name: label }));
}

const SCREENS = [
  {
    ac: "AC-28",
    path: "/keys",
    endpoint: REQUESTS,
    button: /email me a link/i,
    heading: /get an api key/i,
  },
  {
    ac: "AC-31",
    path: "/keys/delete",
    endpoint: DELETION,
    button: /email me a deletion link/i,
    heading: /delete my data/i,
  },
] as const;

for (const s of SCREENS) {
  describe(`${s.ac} ${s.path}: request a link by email`, () => {
    it(`${s.ac} shows a heading and a labelled email field`, async () => {
      open(s.path, {});
      expect(await screen.findByRole("heading", { level: 1, name: s.heading })).toBeTruthy();
      expect(emailField()).toBeTruthy();
      expect(screen.getByRole("button", { name: s.button })).toBeTruthy();
    });

    it(`${s.ac} posts the email as JSON and says "check your mail" without naming the address status`, async () => {
      const m = open(s.path, { [`POST ${s.endpoint}`]: accepted() });
      await screen.findByRole("heading", { level: 1 });
      await submit(s.button);
      expect(await screen.findByText(/check your mail/i)).toBeTruthy();
      const calls = m.to("POST", s.endpoint);
      expect(calls).toHaveLength(1);
      expect((calls.at(0)!.body as { email: string }).email).toBe(EMAIL);
      // The answer must not reveal whether the address is known.
      expect(
        screen.queryByText(/not registered|no account found|we found|already registered/i),
      ).toBeNull();
      expect(screen.queryByText(/running late/i)).toBeNull();
    });

    it(`${s.ac} warns about a delay when mail_delayed is true`, async () => {
      open(s.path, { [`POST ${s.endpoint}`]: accepted(true) });
      await screen.findByRole("heading", { level: 1 });
      await submit(s.button);
      expect(await screen.findByText(/mail is running late/i)).toBeTruthy();
      expect(screen.getByText(/check your mail/i)).toBeTruthy();
    });

    it(`${s.ac} shows a 422 next to the field and marks it invalid`, async () => {
      open(s.path, {
        [`POST ${s.endpoint}`]: apiError(422, "validation_error", "Request validation failed", [
          { field: "email", message: "value is not a valid email address" },
        ]),
      });
      await screen.findByRole("heading", { level: 1 });
      await submit(s.button, "someone@blocked.example");
      await waitFor(() => expect(emailField().getAttribute("aria-invalid")).toBe("true"));
      expect(screen.getByText(/enter a valid address/i)).toBeTruthy();
      // The form stays usable after the error.
      expect((screen.getByRole("button", { name: s.button }) as HTMLButtonElement).disabled).toBe(
        false,
      );
    });

    it(`${s.ac} shows 429 with the wait time taken from Retry-After`, async () => {
      open(s.path, {
        [`POST ${s.endpoint}`]: apiError(429, "rate_limited", "Rate limit exceeded", null, {
          "Retry-After": "2520",
        }),
      });
      await screen.findByRole("heading", { level: 1 });
      await submit(s.button);
      const alert = await screen.findByRole("alert");
      expect(alert.textContent).toMatch(/42 minutes/i);
    });

    it(`${s.ac} shows 503 with the wait time taken from Retry-After`, async () => {
      open(s.path, {
        [`POST ${s.endpoint}`]: apiError(
          503,
          "mail_unavailable",
          "Daily mail limit reached",
          null,
          {
            "Retry-After": "14400",
          },
        ),
      });
      await screen.findByRole("heading", { level: 1 });
      await submit(s.button);
      const alert = await screen.findByRole("alert");
      expect(alert.textContent).toMatch(/4 hours/i);
    });

    it(`${s.ac} 503 with details.resets_at names the local reset time (TZ fixed)`, async () => {
      const tz = process.env.TZ;
      process.env.TZ = "Europe/Moscow"; // UTC+3 all year: 00:00 UTC is 03:00 local
      try {
        open(s.path, {
          [`POST ${s.endpoint}`]: json(
            {
              error: {
                code: "mail_unavailable",
                message: "Daily mail limit reached",
                details: { resets_at: "2026-12-01T00:00:00Z" },
              },
            },
            { status: 503, headers: { "Retry-After": "14400" } },
          ),
        });
        await screen.findByRole("heading", { level: 1 });
        await submit(s.button);
        const text = (await screen.findByRole("alert")).textContent ?? "";
        expect(text).toMatch(/Today's email limit is reached\. Please try again after/);
        expect(text).toMatch(/1 Dec 2026, 03:00 \(00:00 UTC\)/);
        expect(text).not.toMatch(/4 hours/);
      } finally {
        if (tz === undefined) delete process.env.TZ;
        else process.env.TZ = tz;
      }
    });

    it(`${s.ac} rounds a Retry-After that is not a whole minute up`, async () => {
      open(s.path, {
        [`POST ${s.endpoint}`]: apiError(429, "rate_limited", "Rate limit exceeded", null, {
          "Retry-After": "61",
        }),
      });
      await screen.findByRole("heading", { level: 1 });
      await submit(s.button);
      expect((await screen.findByRole("alert")).textContent).toMatch(/2 minutes/i);
    });

    it(`${s.ac} disables the button while the request is in flight and announces it`, async () => {
      const gate = deferred<Response>();
      const m = open(s.path, { [`POST ${s.endpoint}`]: () => gate.promise });
      await screen.findByRole("heading", { level: 1 });
      await submit(s.button);
      await waitFor(() => expect(m.to("POST", s.endpoint)).toHaveLength(1));
      const status = await screen.findByRole("status");
      expect(status.textContent).toMatch(/sending/i);
      const busy = screen.getAllByRole("button").filter((b) => (b as HTMLButtonElement).disabled);
      expect(busy.length).toBeGreaterThan(0);
      // A second press does not send a second request.
      fireEvent.click(busy.at(0)!);
      fireEvent.submit(emailField().closest("form") as HTMLFormElement);
      gate.resolve(accepted());
      expect(await screen.findByText(/check your mail/i)).toBeTruthy();
      expect(m.to("POST", s.endpoint)).toHaveLength(1);
    });

    it(`${s.ac} hides the honeypot from people and assistive technology and sends it empty`, async () => {
      const m = open(s.path, { [`POST ${s.endpoint}`]: accepted() });
      await screen.findByRole("heading", { level: 1 });
      const trap = document.querySelector<HTMLInputElement>('input[name="website"]');
      expect(trap, "honeypot input named website").not.toBeNull();
      expect(trap!.closest('[aria-hidden="true"]')).not.toBeNull();
      expect(trap!.tabIndex).toBe(-1);
      expect(trap!.getAttribute("autocomplete")).toBe("off");
      // Not reachable through the accessibility tree.
      expect(screen.queryByRole("textbox", { name: /website/i })).toBeNull();
      expect(screen.queryByLabelText(/website/i)).toBeNull();
      expect(document.activeElement).not.toBe(trap);

      await submit(s.button);
      await screen.findByText(/check your mail/i);
      const body = m.one("POST", s.endpoint, 0).body as { email: string; website?: string };
      expect(body.website ?? "").toBe("");
    });

    it(`${s.ac} treats a network failure as an error the user can retry from`, async () => {
      open(s.path, {
        [`POST ${s.endpoint}`]: () => {
          throw new TypeError("Failed to fetch");
        },
      });
      await screen.findByRole("heading", { level: 1 });
      await submit(s.button);
      expect(await screen.findByRole("alert")).toBeTruthy();
      expect((screen.getByRole("button", { name: s.button }) as HTMLButtonElement).disabled).toBe(
        false,
      );
    });
  });
}

describe("AC-28 /keys specifics", () => {
  it("AC-28 shows a closed-signup state when POST /keys/requests answers 404", async () => {
    open("/keys", {
      [`POST ${REQUESTS}`]: apiError(404, "not_found", "Not found"),
    });
    await screen.findByRole("heading", { level: 1 });
    await submit(/email me a link/i);
    expect(await screen.findByText(/not open yet/i)).toBeTruthy();
    expect(screen.queryByText(/check your mail/i)).toBeNull();
  });

  it("AC-28 states what is stored and links the manage and delete screens", async () => {
    open("/keys", {});
    await screen.findByRole("heading", { level: 1 });
    expect(screen.getByText(/what we store/i)).toBeTruthy();
    const manage = screen.getAllByRole("link", { name: /my keys/i });
    expect(manage.some((a) => a.getAttribute("href") === "/keys/manage")).toBe(true);
    const del = screen.getAllByRole("link", { name: /delete my data/i });
    expect(del.some((a) => a.getAttribute("href") === "/keys/delete")).toBe(true);
  });

  it("AC-28 does not put the email address into the URL or web storage", async () => {
    open("/keys", { [`POST ${REQUESTS}`]: accepted() });
    await screen.findByRole("heading", { level: 1 });
    await submit(/email me a link/i);
    await screen.findByText(/check your mail/i);
    expect(window.location.href).not.toContain("example.org");
    expect(JSON.stringify({ ...localStorage })).not.toContain("example.org");
    expect(JSON.stringify({ ...sessionStorage })).not.toContain("example.org");
  });

  it("AC-28 sends the request once on Enter in the email field", async () => {
    const m = open("/keys", { [`POST ${REQUESTS}`]: accepted() });
    await screen.findByRole("heading", { level: 1 });
    type(emailField(), EMAIL);
    fireEvent.submit(emailField().closest("form") as HTMLFormElement);
    await screen.findByText(/check your mail/i);
    expect(m.to("POST", REQUESTS)).toHaveLength(1);
  });
});

describe("AC-31 /keys/delete step 1 specifics", () => {
  it("AC-31 words the answer as conditional: if the address has an account", async () => {
    open("/keys/delete", { [`POST ${DELETION}`]: accepted() });
    await screen.findByRole("heading", { level: 1 });
    await submit(/email me a deletion link/i);
    expect(await screen.findByText(/if .* has an account/i)).toBeTruthy();
  });

  it("AC-31 sends the same request body shape for any address", async () => {
    const m = open("/keys/delete", {
      [`POST ${DELETION}`]: () =>
        json({ status: "accepted", mail_delayed: false }, { status: 202 }),
    });
    await screen.findByRole("heading", { level: 1 });
    await submit(/email me a deletion link/i, "nobody@example.org");
    await screen.findByText(/check your mail/i);
    const call = m.one("POST", DELETION, 0) as Call;
    expect(Object.keys(call.body as object).sort()).toEqual(
      Object.keys(call.body as object).includes("website") ? ["email", "website"] : ["email"],
    );
  });
});
