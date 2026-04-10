import { describe, it, expect } from "vitest";
import { fmtCurrency, fmtCurrencyShort, fmtPct, fmtMonth, fmtMonthYear } from "./format";

// ── fmtCurrency ─────────────────────────────────────────────────────────

describe("fmtCurrency", () => {
  it("formats zero", () => {
    expect(fmtCurrency(0)).toBe("$0.00");
  });

  it("formats small values with 2 decimals", () => {
    expect(fmtCurrency(9.99)).toBe("$9.99");
    expect(fmtCurrency(0.01)).toBe("$0.01");
    expect(fmtCurrency(5)).toBe("$5.00");
  });

  it("formats values >= 10 with 0 decimals", () => {
    expect(fmtCurrency(10)).toBe("$10");
    expect(fmtCurrency(1234)).toBe("$1,234");
    expect(fmtCurrency(1234567)).toBe("$1,234,567");
  });

  it("formats negative values", () => {
    expect(fmtCurrency(-100)).toBe("-$100");
    expect(fmtCurrency(-5.5)).toBe("-$5.50");
    expect(fmtCurrency(-0.01)).toBe("-$0.01");
  });

  it("handles -0 as positive zero", () => {
    expect(fmtCurrency(-0)).toBe("$0.00");
  });
});

// ── fmtCurrencyShort ────────────────────────────────────────────────────

describe("fmtCurrencyShort", () => {
  it("returns $0 for zero", () => {
    expect(fmtCurrencyShort(0)).toBe("$0");
  });

  it("formats millions", () => {
    expect(fmtCurrencyShort(1_000_000)).toBe("$1.0M");
    expect(fmtCurrencyShort(2_500_000)).toBe("$2.5M");
  });

  it("formats thousands", () => {
    expect(fmtCurrencyShort(1_000)).toBe("$1k");
    expect(fmtCurrencyShort(50_000)).toBe("$50k");
    expect(fmtCurrencyShort(999_999)).toBe("$1000k");
  });

  it("falls through to fmtCurrency for small values", () => {
    expect(fmtCurrencyShort(999)).toBe("$999");
    expect(fmtCurrencyShort(5.5)).toBe("$5.50");
  });
});

// ── fmtPct ──────────────────────────────────────────────────────────────

describe("fmtPct", () => {
  it("formats signed positive", () => {
    expect(fmtPct(12.34, true)).toBe("+12.3%");
  });

  it("formats signed negative", () => {
    expect(fmtPct(-5.67, true)).toBe("-5.7%");
  });

  it("formats signed zero", () => {
    expect(fmtPct(0, true)).toBe("+0.0%");
  });

  it("formats unsigned", () => {
    expect(fmtPct(55.0, false)).toBe("55.0%");
    expect(fmtPct(-3.2, false)).toBe("-3.2%");
  });
});

// ── fmtMonth ────────────────────────────────────────────────────────────

describe("fmtMonth", () => {
  it("maps all 12 months", () => {
    const expected = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    for (let i = 0; i < 12; i++) {
      const m = `2026-${String(i + 1).padStart(2, "0")}`;
      expect(fmtMonth(m)).toBe(expected[i]);
    }
  });

  it("falls back to raw string for invalid month", () => {
    expect(fmtMonth("2026-13")).toBe("2026-13");
    expect(fmtMonth("2026-00")).toBe("2026-00");
  });
});

// ── fmtMonthYear ────────────────────────────────────────────────────────

describe("fmtMonthYear", () => {
  it("formats month and 2-digit year", () => {
    expect(fmtMonthYear("2026-03")).toBe("Mar 26");
    expect(fmtMonthYear("2025-11")).toBe("Nov 25");
  });

  it("falls back for invalid month", () => {
    expect(fmtMonthYear("2026-13")).toBe("2026-13 26");
  });
});
