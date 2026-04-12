// @vitest-environment jsdom

import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup, within } from "@testing-library/react";

afterEach(cleanup);
import { CashFlow, CashFlowStatBar } from "./cash-flow";
import type { CashflowResponse } from "@/lib/computed-types";

// ── Helpers ─────────────────────────────────────────────────────────────

const BASE_DATA: CashflowResponse = {
  incomeItems: [
    { category: "Salary", amount: 5000, count: 1 },
    { category: "Interest", amount: 3, count: 2 },
  ],
  expenseItems: [
    { category: "Rent", amount: 2000, count: 1 },
    { category: "Food", amount: 500, count: 15 },
    { category: "Coffee", amount: 50, count: 5 },
  ],
  totalIncome: 5003,
  totalExpenses: 2550,
  netCashflow: 2453,
  ccPayments: 800,
  savingsRate: 49,
  takehomeSavingsRate: 45,
};

// ── Tests ───────────────────────────────────────────────────────────────

describe("CashFlow", () => {
  it("renders income and expense tables with headers", () => {
    render(<CashFlow data={BASE_DATA} />);
    expect(screen.getByText("Income")).toBeTruthy();
    expect(screen.getByText("Expenses")).toBeTruthy();
    expect(screen.getByText("Salary")).toBeTruthy();
    expect(screen.getByText("Rent")).toBeTruthy();
  });

  it("consolidates small income items into Other", () => {
    render(<CashFlow data={BASE_DATA} />);
    // Interest ($3) is below $10 threshold, merged into Other
    expect(screen.queryByText("Interest")).toBeNull();
    // "Other" appears in both income and expense tables
    expect(screen.getAllByText("Other").length).toBeGreaterThanOrEqual(1);
  });

  it("shows collapsible minor expenses", () => {
    render(<CashFlow data={BASE_DATA} />);
    // Coffee ($50) is below MAJOR_EXPENSE_THRESHOLD ($200)
    expect(screen.getByText(/and \d+ more/)).toBeTruthy();
  });

  it("shows totals in both tables", () => {
    render(<CashFlow data={BASE_DATA} />);
    const totals = screen.getAllByText("Total");
    expect(totals.length).toBe(2);
  });
});

describe("CashFlowStatBar", () => {
  it("renders net savings, invested, and CC payments", () => {
    render(<CashFlowStatBar data={BASE_DATA} invested={1500} />);
    expect(screen.getByText("Net Savings")).toBeTruthy();
    expect(screen.getByText("Invested")).toBeTruthy();
    expect(screen.getByText("CC Payments")).toBeTruthy();
  });

  it("shows savings rate percentage", () => {
    render(<CashFlowStatBar data={BASE_DATA} invested={1500} />);
    expect(screen.getAllByText("49%").length).toBeGreaterThanOrEqual(1);
  });

  it("shows period label when provided", () => {
    render(<CashFlowStatBar data={BASE_DATA} invested={1500} period="YTD" />);
    expect(screen.getByText("YTD Summary")).toBeTruthy();
  });
});
