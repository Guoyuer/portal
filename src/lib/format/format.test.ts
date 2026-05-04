import { describe, it, expect } from "vitest";
import {
  fmtCurrency,
  fmtCurrencyShort,
  fmtPct,
  fmtMonth,
  fmtMonthYear,
  fmtDateLong,
  fmtDateMedium,
  fmtDateMonthYear,
  parseLocalDate,
} from "@/lib/format/format";

describe("currency formatters", () => {
  it.each([
    [0, "$0.00"],
    [9.99, "$9.99"],
    [0.01, "$0.01"],
    [5, "$5.00"],
    [10.5, "$10.50"],
    [999.01, "$999.01"],
    [1234, "$1,234"],
    [1_234_567, "$1,234,567"],
    [50_000, "$50,000"],
    [-50.5, "-$50.50"],
    [-5.5, "-$5.50"],
    [-0.01, "-$0.01"],
    [-0, "$0.00"],
  ])("fmtCurrency(%s)", (input, expected) => {
    expect(fmtCurrency(input)).toBe(expected);
  });

  it.each([
    [0, "$0"],
    [1_000_000, "$1.0M"],
    [2_500_000, "$2.5M"],
    [1_000, "$1k"],
    [50_000, "$50k"],
    [999_999, "$1000k"],
    [999, "$999.00"],
    [5.5, "$5.50"],
    [-5_000, "-$5k"],
    [-50_000, "-$50k"],
    [-1_500_000, "-$1.5M"],
    [-500, "-$500.00"],
  ])("fmtCurrencyShort(%s)", (input, expected) => {
    expect(fmtCurrencyShort(input)).toBe(expected);
  });
});

describe("percent and month formatters", () => {
  it.each([
    [12.34, true, "+12.3%"],
    [-5.67, true, "-5.7%"],
    [0, true, "+0.0%"],
    [55, false, "55.0%"],
    [-3.2, false, "-3.2%"],
  ])("fmtPct(%s, %s)", (input, signed, expected) => {
    expect(fmtPct(input, signed)).toBe(expected);
  });

  it("maps all 12 months", () => {
    const expected = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    for (let i = 0; i < 12; i++) {
      expect(fmtMonth(`2026-${String(i + 1).padStart(2, "0")}`)).toBe(expected[i]);
    }
  });

  it.each([
    ["2026-13", "2026-13"],
    ["2026-00", "2026-00"],
  ])("fmtMonth fallback %s", (input, expected) => {
    expect(fmtMonth(input)).toBe(expected);
  });

  it.each([
    ["2026-03", "Mar 26"],
    ["2025-11", "Nov 25"],
    ["2026-13", "2026-13 26"],
  ])("fmtMonthYear(%s)", (input, expected) => {
    expect(fmtMonthYear(input)).toBe(expected);
  });
});

describe("date formatters", () => {
  it.each([
    ["2026-01-15", "January 15, 2026"],
    ["2025-12-01", "December 1, 2025"],
  ])("fmtDateLong(%s)", (input, expected) => {
    expect(fmtDateLong(input)).toBe(expected);
  });

  it.each([
    ["2026-01-15", "Jan 15, 2026"],
    ["2025-12-31", "Dec 31, 2025"],
  ])("fmtDateMedium(%s)", (input, expected) => {
    expect(fmtDateMedium(input)).toBe(expected);
  });

  it.each([
    ["2026-03-15", "Mar 2026"],
    ["2025-11-01", "Nov 2025"],
  ])("fmtDateMonthYear(%s)", (input, expected) => {
    expect(fmtDateMonthYear(input)).toBe(expected);
  });

  it("parses YYYY-MM-DD as local midnight on the same calendar day", () => {
    const d = parseLocalDate("2026-04-14");
    expect([d.getFullYear(), d.getMonth(), d.getDate(), d.getHours(), d.getMinutes()]).toEqual([2026, 3, 14, 0, 0]);
    expect(d.toLocaleDateString("en-US", { month: "short", day: "numeric" })).toBe("Apr 14");
  });
});
