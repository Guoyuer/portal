// @vitest-environment jsdom

import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

afterEach(cleanup);
import { TimemachineSummary } from "./timemachine";
import type { CategoryMeta, DailyPoint } from "@/lib/schemas";
import type { CashflowResponse, ActivityResponse } from "@/lib/computed-types";
import type { CrossCheck } from "@/lib/compute";

const CATEGORIES: CategoryMeta[] = [
  { key: "usEquity", name: "US Equity", displayOrder: 0, targetPct: 55 },
  { key: "nonUsEquity", name: "Non-US Equity", displayOrder: 1, targetPct: 15 },
  { key: "crypto", name: "Crypto", displayOrder: 2, targetPct: 3 },
  { key: "safeNet", name: "Safe Net", displayOrder: 3, targetPct: 27 },
];

// ── Helpers ─────────────────────────────────────────────────────────────

const SNAPSHOT: DailyPoint = {
  date: "2026-01-15",
  total: 100000,
  usEquity: 55000,
  nonUsEquity: 15000,
  crypto: 3000,
  safeNet: 27000,
  liabilities: -5000,
};

const CASHFLOW: CashflowResponse = {
  incomeItems: [{ category: "Salary", amount: 5000, count: 1 }],
  expenseItems: [{ category: "Rent", amount: 2000, count: 1 }],
  totalIncome: 5000,
  totalExpenses: 2000,
  netCashflow: 3000,
  ccPayments: 500,
  savingsRate: 60,
  takehomeSavingsRate: 55,
};

const ACTIVITY: ActivityResponse = {
  buysBySymbol: [{ symbol: "VTI", count: 2, total: 1000 }],
  sellsBySymbol: [],
  dividendsBySymbol: [{ symbol: "SCHD", count: 1, total: 50 }],
};

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
      fidelityTotal: 5000,
      matchedTotal: 5000,
      unmatchedTotal: 0,
      matchedCount: 3,
      totalCount: 3,
      ok: true,
    };
    render(<TimemachineSummary snapshot={SNAPSHOT} categories={CATEGORIES} crossCheck={cc} />);
    expect(screen.getByText("Deposit Cross-check")).toBeTruthy();
    expect(screen.getByText(/3\/3/)).toBeTruthy();
  });
});
