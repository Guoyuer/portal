// @vitest-environment jsdom

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { TimemachineSummary } from "./timemachine";
import type { CrossCheck } from "@/lib/compute/compute";
import { CATEGORIES, SNAPSHOT, CASHFLOW, ACTIVITY } from "@/test/factories";

// ── Tests ───────────────────────────────────────────────────────────────

describe("TimemachineSummary", () => {
  it("returns null when snapshot is null", () => {
    const { container } = render(<TimemachineSummary snapshot={null} categories={CATEGORIES} />);
    expect(container.innerHTML).toBe("");
  });

  it("renders date and total", () => {
    render(<TimemachineSummary snapshot={SNAPSHOT} categories={CATEGORIES} />);
    expect(screen.getByTestId("tm-date").textContent).toBe("January 15, 2026");
    // netWorth = total + liabilities = 100000 + (-5000) = 95000
    expect(screen.getByTestId("tm-total").textContent).toBe("$95,000");
  });

  it("renders 4 category percentages", () => {
    render(<TimemachineSummary snapshot={SNAPSHOT} categories={CATEGORIES} />);
    // Each category pct appears in the stat grid
    expect(screen.getAllByText("55%").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("15%").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("3%").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("27%").length).toBeGreaterThanOrEqual(1);
  });

  it("shows range stats when cashflow and activity are provided", () => {
    render(
      <TimemachineSummary
        snapshot={SNAPSHOT}
        categories={CATEGORIES}
        cashflow={CASHFLOW}
        activity={ACTIVITY}
        startDate="2025-07-01"
      />,
    );
    expect(screen.getByText("Net Savings")).toBeTruthy();
    expect(screen.getByText("Investments")).toBeTruthy();
    expect(screen.getByText("CC Payments")).toBeTruthy();
    expect(screen.getByText("Income")).toBeTruthy();
    expect(screen.getByText("Expenses")).toBeTruthy();
    expect(screen.getByText("Dividends")).toBeTruthy();
  });

  it("hides range stats when no cashflow or activity", () => {
    render(<TimemachineSummary snapshot={SNAPSHOT} categories={CATEGORIES} cashflow={null} activity={null} />);
    expect(screen.queryByText("Income")).toBeNull();
    expect(screen.queryByText("Buys")).toBeNull();
  });

  it("shows cross-check section when data is present", () => {
    const cc: CrossCheck = {
      matchedCount: 3,
      totalCount: 3,
      ok: true,
      perSource: {
        fidelity:  { matched: 2, total: 2, unmatched: [] },
        robinhood: { matched: 1, total: 1, unmatched: [] },
      },
      allUnmatched: [],
    };
    render(<TimemachineSummary snapshot={SNAPSHOT} categories={CATEGORIES} crossCheck={cc} />);
    expect(screen.getByText("Deposit Cross-check")).toBeTruthy();
    expect(screen.getByText(/3\/3/)).toBeTruthy();
  });
});
