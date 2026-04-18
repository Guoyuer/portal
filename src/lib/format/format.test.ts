import { describe, it, expect } from "vitest";
import { fmtCurrency, fmtCurrencyShort, fmtPct, fmtMonth, fmtMonthYear, fmtDateLong, fmtDateMedium, fmtDateMonthYear, parseLocalDate } from "@/lib/format/format";

// ── fmtCurrency ─────────────────────────────────────────────────────────

describe("fmtCurrency", () => {
  it("formats zero", () => {
    expect(fmtCurrency(0)).toBe("$0.00");
  });

  it("small values show 2 decimals", () => {
    expect(fmtCurrency(9.99)).toBe("$9.99");
    expect(fmtCurrency(0.01)).toBe("$0.01");
    expect(fmtCurrency(5)).toBe("$5.00");
  });

  it("medium values show 2 decimals", () => {
    expect(fmtCurrency(10.5)).toBe("$10.50");
  });

  it("values under 1000 show 2 decimals", () => {
    expect(fmtCurrency(999.01)).toBe("$999.01");
  });

  it("values at 1000+ show 0 decimals", () => {
    expect(fmtCurrency(1234)).toBe("$1,234");
    expect(fmtCurrency(1234567)).toBe("$1,234,567");
  });

  it("large values show 0 decimals", () => {
    expect(fmtCurrency(50000)).toBe("$50,000");
  });

  it("formats negative values", () => {
    expect(fmtCurrency(-50.5)).toBe("-$50.50");
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
    expect(fmtCurrencyShort(999)).toBe("$999.00");
    expect(fmtCurrencyShort(5.5)).toBe("$5.50");
  });

  it("formats negative thousands", () => {
    expect(fmtCurrencyShort(-5000)).toBe("-$5k");
    expect(fmtCurrencyShort(-50_000)).toBe("-$50k");
  });

  it("formats negative millions", () => {
    expect(fmtCurrencyShort(-1_500_000)).toBe("-$1.5M");
  });

  it("formats small negatives via fmtCurrency", () => {
    expect(fmtCurrencyShort(-500)).toBe("-$500.00");
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

// ── fmtDateLong ────────────────────────────────────────────────────────

describe("fmtDateLong", () => {
  it("formats ISO date to long form", () => {
    expect(fmtDateLong("2026-01-15")).toBe("January 15, 2026");
    expect(fmtDateLong("2025-12-01")).toBe("December 1, 2025");
  });
});

// ── fmtDateMedium ──────────────────────────────────────────────────────

describe("fmtDateMedium", () => {
  it("formats ISO date to short month form", () => {
    expect(fmtDateMedium("2026-01-15")).toBe("Jan 15, 2026");
    expect(fmtDateMedium("2025-12-31")).toBe("Dec 31, 2025");
  });
});

// ── parseLocalDate ─────────────────────────────────────────────────────

describe("parseLocalDate", () => {
  // The whole point of this helper: `new Date("YYYY-MM-DD")` parses as UTC,
  // which shifts to the previous calendar day in any westward timezone.
  // parseLocalDate must produce a Date whose *local* components match the
  // ISO string verbatim, regardless of the host timezone.
  it("returns local midnight for the same calendar day", () => {
    const d = parseLocalDate("2026-04-14");
    expect(d.getFullYear()).toBe(2026);
    expect(d.getMonth()).toBe(3); // April (0-indexed)
    expect(d.getDate()).toBe(14);
    expect(d.getHours()).toBe(0);
    expect(d.getMinutes()).toBe(0);
  });

  it("round-trips through toLocaleDateString in en-US", () => {
    // The user-visible symptom we are protecting against: a NY viewer
    // seeing Apr 13 when the data row is Apr 14.
    expect(parseLocalDate("2026-04-14").toLocaleDateString("en-US", { month: "short", day: "numeric" })).toBe("Apr 14");
  });
});

// ── fmtDateMonthYear ───────────────────────────────────────────────────

describe("fmtDateMonthYear", () => {
  it("formats ISO date to month + year", () => {
    expect(fmtDateMonthYear("2026-03-15")).toBe("Mar 2026");
    expect(fmtDateMonthYear("2025-11-01")).toBe("Nov 2025");
  });
});
