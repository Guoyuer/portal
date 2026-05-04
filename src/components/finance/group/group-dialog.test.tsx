// @vitest-environment jsdom

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { GroupChartDialog } from "./group-dialog";
import { TransactionTable } from "../transaction-table";
import { MarkerHoverPanel } from "../charts/marker-hover-panel";
import type { InvestmentTxn } from "@/lib/compute/compute";
import type { Selection } from "../ticker/ticker-markers";

const matchMediaStub = (q: string) => ({
  matches: false,
  media: q,
  addEventListener: () => {},
  removeEventListener: () => {},
});

// Mock useTickerData so GroupChartDialog doesn't make real network calls.
// Return a minimal resolved state with a few price points.
vi.mock("./ticker-chart", () => ({
  useTickerData: () => ({
    status: "data",
    symbol: "VOO",
    data: [
      { date: "2025-01-02", ts: Date.parse("2025-01-02"), close: 500 },
      { date: "2025-01-03", ts: Date.parse("2025-01-03"), close: 505 },
    ],
    avgCost: null,
    transactions: [],
    error: null,
  }),
  // Preserve the real exports used elsewhere in this file (none needed here)
  TickerChart: () => null,
  TickerDialogOnly: () => null,
}));

beforeEach(() => {
  vi.stubGlobal("matchMedia", matchMediaStub);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

function makeTxn(ticker: string, date: string, actionType: InvestmentTxn["actionType"] = "buy", source: InvestmentTxn["source"] = "fidelity"): InvestmentTxn {
  return { source, date, actionType, ticker, amount: -1000, quantity: 5, price: 200 };
}

function renderGroupDialog(overrides: Partial<Parameters<typeof GroupChartDialog>[0]> = {}) {
  return render(
    <GroupChartDialog
      groupKey="sp500"
      dailyTickers={[]}
      investmentTxns={[]}
      onClose={() => {}}
      {...overrides}
    />,
  );
}

// ── GroupChartDialog integration tests ──────────────────────────────────

describe("GroupChartDialog", () => {
  it("renders group display name + constituent tickers", () => {
    renderGroupDialog();
    expect(screen.getByText("S&P 500")).toBeTruthy();
    expect(screen.getByText(/VOO.*IVV.*SPY/)).toBeTruthy();
  });

  it("renders Holdings label (not 'value') when daily data present", () => {
    renderGroupDialog({
      dailyTickers: [
        { date: "2025-01-02", ticker: "VOO", value: 10000, category: "", subtype: "" },
      ],
    });
    expect(screen.getByText(/Holdings/)).toBeTruthy();
  });

  it("transaction table shows only rows for group-member tickers", () => {
    const txns: InvestmentTxn[] = [
      makeTxn("VOO", "2025-01-10"),
      makeTxn("QQQ", "2025-01-11"),   // not in sp500 group
      makeTxn("IVV", "2025-01-12"),
      makeTxn("AMZN", "2025-01-13"), // not in sp500 group
    ];
    renderGroupDialog({ investmentTxns: txns });
    // VOO and IVV should appear in the table
    expect(screen.getAllByText("Jan 10, 2025").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Jan 12, 2025").length).toBeGreaterThanOrEqual(1);
    // QQQ and AMZN dates should NOT appear
    expect(screen.queryByText("Jan 11, 2025")).toBeNull();
    expect(screen.queryByText("Jan 13, 2025")).toBeNull();
  });

  it("shows normalized 401k group-member transactions in the detail table", () => {
    renderGroupDialog({
      investmentTxns: [
        makeTxn("401k sp500", "2025-02-14", "contribution", "401k"),
        makeTxn("QQQ", "2025-02-15", "buy", "fidelity"),
      ],
    });
    expect(screen.getAllByText("Feb 14, 2025").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText("Feb 15, 2025")).toBeNull();
  });
});

// ── TransactionTable unit tests ──────────────────────────────────────────

describe("TransactionTable", () => {
  const txns = [
    { runDate: "2025-03-01", actionType: "buy", quantity: 10, price: 100, amount: -1000 },
    { runDate: "2025-03-02", actionType: "sell", quantity: -5, price: 110, amount: 550 },
    { runDate: "2025-03-03", actionType: "reinvestment", quantity: 2, price: 98, amount: -196 },
  ];

  it("renders rows sorted as provided (caller's responsibility)", () => {
    render(<TransactionTable transactions={txns} selected={null} />);
    const dates = screen.getAllByText(/Mar \d+, 2025/);
    expect(dates.length).toBeGreaterThanOrEqual(3);
  });

  it.each([
    ["buy", "2025-03-01", /bg-emerald/],
    ["sell", "2025-03-02", /bg-amber/],
  ] as const)("highlights %s rows when selected", (side, date, className) => {
    const selected: Selection = { key: `${side}-selected`, dates: [date], side };
    const { container } = render(
      <TransactionTable transactions={txns} selected={selected} />,
    );
    const highlightedCell = container.querySelector(`td[data-date="${date}"][data-side="${side}"]`);
    expect(highlightedCell).toBeTruthy();
    expect(highlightedCell?.className).toMatch(className);
  });

  it("returns null when transactions is empty", () => {
    const { container } = render(
      <TransactionTable transactions={[]} selected={null} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("stamps data-side='buy' on reinvestment rows", () => {
    const { container } = render(
      <TransactionTable transactions={txns} selected={null} />,
    );
    const reinvestCell = container.querySelector('td[data-date="2025-03-03"]');
    expect(reinvestCell?.getAttribute("data-side")).toBe("buy");
  });
});

// ── MarkerHoverPanel unit tests ──────────────────────────────────────────

describe("MarkerHoverPanel", () => {
  const baseCluster = {
    ts: Date.parse("2025-06-15"),
    count: 1,
    r: 8,
    amount: 2000,
    price: 400,
    qty: 5,
    memberDates: ["2025-06-15"],
  };

  it.each([
    ["buy", /Buy/],
    ["sell", /Sell/],
  ] as const)("renders date and %s label", (side, label) => {
    render(
      <MarkerHoverPanel
        hover={{ cluster: baseCluster, side, dayIso: "2025-06-15", close: 400, x: 0, y: 0 }}
        isDark={false}
      />,
    );
    expect(screen.getByText("Jun 15, 2025")).toBeTruthy();
    expect(screen.getByText(label)).toBeTruthy();
  });

  it("shows Close label for ticker clusters (qty > 0)", () => {
    const { container } = render(
      <MarkerHoverPanel
        hover={{ cluster: baseCluster, side: "buy", dayIso: "2025-06-15", close: 400, x: 0, y: 0 }}
        isDark={false}
      />,
    );
    expect(container.querySelector("p")?.nextElementSibling?.textContent).toMatch(/Close/);
  });

  it("shows custom valueLabel for group clusters (qty=0, price=0)", () => {
    const groupCluster = { ...baseCluster, qty: 0, price: 0 };
    const { getByText } = render(
      <MarkerHoverPanel
        hover={{ cluster: groupCluster, side: "buy", dayIso: "2025-06-15", close: 500, x: 0, y: 0 }}
        isDark={false}
        valueLabel="VOO"
      />,
    );
    expect(getByText(/VOO:/)).toBeTruthy();
  });

  it("renders breakdown for group clusters with multiple tickers", () => {
    const groupCluster = {
      ...baseCluster,
      qty: 0,
      price: 0,
      breakdown: [
        { symbol: "VOO", signed: -1200 },
        { symbol: "IVV", signed: -800 },
      ],
    };
    const { getAllByText, getByText } = render(
      <MarkerHoverPanel
        hover={{ cluster: groupCluster, side: "buy", dayIso: "2025-06-15", close: 50000, x: 0, y: 0 }}
        isDark={false}
        valueLabel="VOO"
      />,
    );
    // VOO appears as both the valueLabel line and the breakdown line
    expect(getAllByText(/VOO/).length).toBeGreaterThanOrEqual(1);
    expect(getByText(/IVV/)).toBeTruthy();
  });
});
