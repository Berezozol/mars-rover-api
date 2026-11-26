import { describe, expect, it } from "vitest";
import * as errors from "../src/api/errors";
import { ApiError } from "../src/api/client";

// docs/proposals/frontend-data-texts.md, AC-6: the order of the range bounds has its own text.
const { humanizeIssue, describeError } = errors;

const SOL_ORDER = "“From sol” must not be greater than “To sol”";

describe("data-texts AC-6 the range order message", () => {
  it("data-texts AC-6 sol_min > sol_max says the lower bound must not exceed the upper one", () => {
    const text = humanizeIssue("query.sol_min", "sol_min must be less than or equal to sol_max");
    expect(text).toContain(SOL_ORDER);
    expect(text).not.toMatch(/whole number/);
  });

  it("data-texts AC-6 from_sol > to_sol gets the same text", () => {
    const text = humanizeIssue("query.from_sol", "from_sol must be less than or equal to to_sol");
    expect(text).toContain(SOL_ORDER);
    expect(text).not.toMatch(/whole number/);
  });

  it("data-texts AC-6 the sol_max side of the pair gets it too", () => {
    const text = humanizeIssue("query.sol_max", "sol_min must be less than or equal to sol_max");
    expect(text).toContain(SOL_ORDER);
  });

  it("data-texts AC-6 a pair of dates names the dates", () => {
    const text = humanizeIssue("query.date_min", "date_min must be less than or equal to date_max");
    expect(text).toMatch(/date/i);
    expect(text).toMatch(/must not be (greater than|after)/);
    expect(text).not.toMatch(/whole number/);
  });

  it("data-texts AC-6 a pydantic bound error keeps the 'whole number' text", () => {
    expect(humanizeIssue("query.sol", "Input should be less than or equal to 2147483647")).toBe(
      "must be a whole number from 0 to 2,147,483,647.",
    );
    expect(
      humanizeIssue("query.sol_min", "Input should be less than or equal to 2147483647"),
    ).toMatch(/whole number from 0 to 2,147,483,647/);
  });

  it("data-texts AC-6 describeError shows the order text once for the single issue of a 422", () => {
    const err = new ApiError({
      status: 422,
      code: "validation_error",
      message: "Invalid query parameters",
      details: [
        { field: "query.sol_min", message: "sol_min must be less than or equal to sol_max" },
      ],
    });
    // the backend sends one issue, for the low field; the text appears once, without a repeated label
    const view = describeError(err);
    expect(view.fields).toHaveLength(1);
    const field = view.fields[0] as unknown as {
      label: string;
      message: string;
      standalone?: boolean;
    };
    expect(field.standalone).toBe(true);
    expect(field.message).toBe(`${SOL_ORDER}.`);
  });

  it("data-texts AC-6 rangeOrderText(low, high) is exported and names both bounds", () => {
    const fn = (errors as unknown as { rangeOrderText?: (a: string, b: string) => string })
      .rangeOrderText;
    expect(typeof fn).toBe("function");
    expect(fn!("sol_min", "sol_max")).toContain(SOL_ORDER);
  });
});
