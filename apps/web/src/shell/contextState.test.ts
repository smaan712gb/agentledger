import { describe, expect, it } from "vitest";

import {
  basisOf,
  clientSearchSchema,
  frozenState,
  parseClientSearch,
  periodChoices,
  periodLabel,
} from "./contextState";

describe("context bar state in the URL", () => {
  it("parses year and engagement from search params, coercing strings", () => {
    expect(parseClientSearch({ year: "2025", engagement: "7" })).toEqual({ year: 2025, engagement: 7 });
    expect(parseClientSearch({ year: 2024 })).toEqual({ year: 2024 });
  });

  it("drops malformed values instead of failing the route", () => {
    expect(parseClientSearch({ year: "abc", engagement: "-1" })).toEqual({});
    expect(parseClientSearch({ year: 1800 })).toEqual({});
    expect(parseClientSearch(undefined)).toEqual({});
    expect(clientSearchSchema.parse({})).toEqual({});
  });

  it("offers recent periods and always the selected one", () => {
    const now = new Date(2026, 9, 9);
    const choices = periodChoices(2019, now);
    expect(choices[0]).toBe(2027);
    expect(choices).toContain(2019);
    expect(choices).toContain(2026);
  });

  it("labels the period with the closing date when there is one", () => {
    expect(periodLabel(2026, null)).toBe("FY2026");
    expect(periodLabel(2026, "2026-03-31")).toMatch(/^FY2026 · closed through /);
  });
});

describe("frozen periods", () => {
  it("is open when the books were never closed", () => {
    expect(frozenState(2026, null)).toEqual({ frozen: false, partial: false, reason: null });
  });

  it("freezes periods on or before the closing date", () => {
    expect(frozenState(2025, "2026-03-31").frozen).toBe(true);
    expect(frozenState(2026, "2026-12-31").frozen).toBe(true);
    expect(frozenState(2025, "2026-03-31").reason).toContain("FY2025 cannot be changed");
  });

  it("marks a period that contains the closing date as partly closed", () => {
    const state = frozenState(2026, "2026-03-31");
    expect(state).toMatchObject({ frozen: false, partial: true });
    expect(state.reason).toContain("on or before");
  });

  it("leaves later periods open", () => {
    expect(frozenState(2027, "2026-03-31")).toEqual({ frozen: false, partial: false, reason: null });
  });
});

describe("accounting basis is recorded, never defaulted", () => {
  it("reads the recorded fact", () => {
    expect(basisOf({ accounting_basis: "accrual" })).toEqual({ recorded: true, basis: "accrual" });
  });

  it("reports an unrecorded basis", () => {
    expect(basisOf({})).toEqual({ recorded: false });
    expect(basisOf({ accounting_basis: "" })).toEqual({ recorded: false });
    expect(basisOf(undefined)).toEqual({ recorded: false });
  });
});
