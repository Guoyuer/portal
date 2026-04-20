// @vitest-environment jsdom

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { GroupChartDialog } from "./group-dialog";
import { TransactionTable } from "./transaction-table";
import { MarkerHoverPanel } from "./marker-hover-panel";
import type { FidelityTxn } from "@/lib/schemas";
import type { Selection } from "./ticker-markers";

// jsdom doesn't implement HTMLDialogElement.showModal; polyfill lightly:
if (typeof HTMLDialogElement !== "undefined" && !HTMLDialogElement.prototype.showModal) {
  // eslint-disable-next-line @typescript-eslint/no-empty-function
  HTMLDialogElement.prototype.showModal = function () { (this as HTMLDialogElement).open = true; };
  // eslint-disable-next-line @typescript-eslint/no-empty-function
  HTMLDialogElement.prototype.close = function () { (this as HTMLDialogElement).open = false; };
}

// Recharts uses ResizeObserver (as a constructor); provide a no-op class for jsdom
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
global.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;

const matchMediaStub = (q: string) => ({
  matches: false,
  media: q,
  onchange: null,
  addEventListener: () => {},
  removeEventListener: () => {},
  addListener: () => {},
  removeListener: () => {},
  dispatchEvent: () => true,
});

// Mock useTickerData so GroupChartDialog doesn't make real network calls.
// Return a minimal resolved state with a few price points.
vi.mock("./ticker-chart", () => ({
  useTickerData: () => ({
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
  cleanup();
  vi.unstubAllGlobals();
});

function makeTxn(symbol: string, runDate: string, actionType = "buy"): FidelityTxn {
  return { runDate, actionType, symbol, amount: -1000, quantity: 5, price: 200 };
}

// ── GroupChartDialog integration tests ──────────────────────────────────

describe("GroupChartDialog", () => {
  it("renders group display name + constituent tickers", () => {
    render(
      <GroupChartDialog
        groupKey="sp500"
        dailyTickers={[]}
        fidelityTxns={[]}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("S&P 500")).toBeTruthy();
    expect(screen.getByText(/VOO.*IVV.*SPY/)).toBeTruthy();
  });

  it("renders Holdings label (not 'value') when daily data present", () => {
    render(
      <GroupChartDialog
        groupKey="sp500"
        dailyTickers={[
          { date: "2025-01-02", ticker: "VOO", value: 10000, category: "", subtype: "", costBasis: 0, gainLoss: 0, gainLossPct: 0 },
        ]}
        fidelityTxns={[]}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText(/Holdings/)).toBeTruthy();
  });

  it("transaction table shows only rows for group-member tickers", () => {
    const txns: FidelityTxn[] = [
      makeTxn("VOO", "2025-01-10"),
      makeTxn("QQQ", "2025-01-11"),   // not in sp500 group
      makeTxn("IVV", "2025-01-12"),
      makeTxn("AMZN", "2025-01-13"), // not in sp500 group
    ];
    render(
      <GroupChartDialog
        groupKey="sp500"
        dailyTickers={[]}
        fidelityTxns={txns}
        onClose={() => {}}
      />,
    );
    // VOO and IVV should appear in the table
    expect(screen.getAllByText("Jan 10, 2025").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Jan 12, 2025").length).toBeGreaterThanOrEqual(1);
    // QQQ and AMZN dates should NOT appear
    expect(screen.queryByText("Jan 11, 2025")).toBeNull();
    expect(screen.queryByText("Jan 13, 2025")).toBeNull();
  });
});

// ── TransactionTable unit tests ──────────────────────────────────────────

describe("TransactionTable", () => {
  const mkRef = (): React.RefObject<HTMLDivElement | null> => ({ current: null });

  const txns = [
    { runDate: "2025-03-01", actionType: "buy", quantity: 10, price: 100, amount: -1000 },
    { runDate: "2025-03-02", actionType: "sell", quantity: -5, price: 110, amount: 550 },
    { runDate: "2025-03-03", actionType: "reinvestment", quantity: 2, price: 98, amount: -196 },
  ];

  it("renders rows sorted as provided (caller's responsibility)", () => {
    render(<TransactionTable transactions={txns} selected={null} tableScrollRef={mkRef()} isDark={false} />);
    const dates = screen.getAllByText(/Mar \d+, 2025/);
    expect(dates.length).toBeGreaterThanOrEqual(3);
  });

  it("highlights buy rows when selection side=buy matches the date", () => {
    const selected: Selection = { key: "buy-123-1", dates: ["2025-03-01"], side: "buy" };
    const { container } = render(
      <TransactionTable transactions={txns} selected={selected} tableScrollRef={mkRef()} isDark={false} />,
    );
    const highlightedCell = container.querySelector('td[data-date="2025-03-01"][data-side="buy"]');
    expect(highlightedCell).toBeTruthy();
    // Should have a highlight class (emerald for buy)
    expect(highlightedCell?.className).toMatch(/bg-emerald/);
  });

  it("highlights sell rows when selection side=sell matches the date", () => {
    const selected: Selection = { key: "sell-456-1", dates: ["2025-03-02"], side: "sell" };
    const { container } = render(
      <TransactionTable transactions={txns} selected={selected} tableScrollRef={mkRef()} isDark={false} />,
    );
    const highlightedCell = container.querySelector('td[data-date="2025-03-02"][data-side="sell"]');
    expect(highlightedCell).toBeTruthy();
    expect(highlightedCell?.className).toMatch(/bg-amber/);
  });

  it("returns null when transactions is empty", () => {
    const { container } = render(
      <TransactionTable transactions={[]} selected={null} tableScrollRef={mkRef()} isDark={false} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("stamps data-side='buy' on reinvestment rows", () => {
    const { container } = render(
      <TransactionTable transactions={txns} selected={null} tableScrollRef={mkRef()} isDark={false} />,
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

  it("renders date and buy/sell label", () => {
    render(
      <MarkerHoverPanel
        hover={{ cluster: baseCluster, side: "buy", dayIso: "2025-06-15", close: 400, x: 0, y: 0 }}
        isDark={false}
      />,
    );
    expect(screen.getByText("Jun 15, 2025")).toBeTruthy();
    expect(screen.getByText(/Buy/)).toBeTruthy();
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

  it("sell side renders with correct label", () => {
    const { getByText } = render(
      <MarkerHoverPanel
        hover={{ cluster: baseCluster, side: "sell", dayIso: "2025-06-15", close: 400, x: 0, y: 0 }}
        isDark={false}
      />,
    );
    expect(getByText(/Sell/)).toBeTruthy();
  });
});
