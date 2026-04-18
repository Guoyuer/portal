import { describe, it, expect } from "vitest";
import {
  valueColor,
  SAVINGS_RATE_GOOD,
  SAVINGS_RATE_WARNING,
  MAJOR_EXPENSE_THRESHOLD,
  SCROLL_SHOW_THRESHOLD,
} from "@/lib/format/thresholds";

describe("valueColor", () => {
  it("returns green-family classes for positive values", () => {
    const cls = valueColor(1);
    expect(cls).toContain("emerald");
    expect(cls).toContain("cyan");
  });

  it("returns red-family classes for negative values", () => {
    const cls = valueColor(-1);
    expect(cls).toContain("red");
    expect(cls).not.toContain("emerald");
  });

  it("treats zero as non-negative (green family)", () => {
    expect(valueColor(0)).toContain("emerald");
  });

  it("includes both light and dark mode variants", () => {
    const pos = valueColor(5);
    expect(pos).toMatch(/\bdark:/);
    const neg = valueColor(-5);
    expect(neg).toMatch(/\bdark:/);
  });
});

describe("threshold constants", () => {
  it("has a sane ordering of savings-rate bands", () => {
    expect(SAVINGS_RATE_GOOD).toBeGreaterThan(SAVINGS_RATE_WARNING);
    expect(SAVINGS_RATE_WARNING).toBeGreaterThan(0);
  });

  it("major-expense threshold is positive", () => {
    expect(MAJOR_EXPENSE_THRESHOLD).toBeGreaterThan(0);
  });

  it("scroll-show threshold is positive", () => {
    expect(SCROLL_SHOW_THRESHOLD).toBeGreaterThan(0);
  });
});
