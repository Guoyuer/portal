// @vitest-environment jsdom

import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("./ticker-chart-base", () => ({
  TickerChartBase: ({ data }: { data: unknown[] }) => <div data-testid="ticker-chart-base">{data.length}</div>,
}));

vi.mock("./ticker-dialog", () => ({
  TickerChartDialog: () => <div data-testid="ticker-chart-dialog" />,
}));

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
import { mergeTickerData } from "@/lib/data/ticker-data";

async function loadTickerChart() {
  vi.resetModules();
  return import("./ticker-chart");
}

function mockPricesResponse(body: unknown, init?: ResponseInit) {
  vi.stubGlobal("fetch", vi.fn(async () => (
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
      ...init,
    })
  )));
}

// ── mergeTickerData ────────────────────────────────────────────────────

describe("mergeTickerData", () => {
  const prices = [
    { date: "2025-10-01", close: 100 },
    { date: "2025-10-02", close: 105 },
    { date: "2025-10-03", close: 102 },
  ];

  it("maps prices to chart points with timestamps", () => {
    const points = mergeTickerData(prices, []);
    expect(points).toHaveLength(3);
    expect(points[0].close).toBe(100);
    expect(points[0].date).toBe("2025-10-01");
    expect(typeof points[0].ts).toBe("number");
  });

  it("merges buy transactions onto matching dates", () => {
    const txns = [
      { runDate: "2025-10-02", actionType: "buy", quantity: 5, price: 104, amount: -520 },
    ];
    const points = mergeTickerData(prices, txns);
    const oct2 = points.find(p => p.date === "2025-10-02")!;
    expect(oct2.buyPrice).toBe(104);
    expect(oct2.buyQty).toBe(5);
  });

  it("merges sell transactions onto matching dates", () => {
    const txns = [
      { runDate: "2025-10-03", actionType: "sell", quantity: -3, price: 103, amount: 309 },
    ];
    const points = mergeTickerData(prices, txns);
    const oct3 = points.find(p => p.date === "2025-10-03")!;
    expect(oct3.sellPrice).toBe(103);
    expect(oct3.sellQty).toBe(3);
  });

  it("places reinvestment in its own reinvestAmount bucket (not buy)", () => {
    const txns = [
      { runDate: "2025-10-01", actionType: "reinvestment", quantity: 1, price: 99, amount: -99 },
    ];
    const points = mergeTickerData(prices, txns);
    const oct1 = points.find(p => p.date === "2025-10-01")!;
    // Reinvestment is now a separate muted-dot marker, not folded into the buy cluster
    expect(oct1.buyPrice).toBeUndefined();
    expect(oct1.reinvestAmount).toBe(99);
    expect(oct1.reinvestTxnCount).toBe(1);
  });

  it("ignores non-buy/sell/reinvestment actions", () => {
    const txns = [
      { runDate: "2025-10-01", actionType: "dividend", quantity: 0, price: 0, amount: 50 },
    ];
    const points = mergeTickerData(prices, txns);
    const oct1 = points.find(p => p.date === "2025-10-01")!;
    expect(oct1.buyPrice).toBeUndefined();
    expect(oct1.sellPrice).toBeUndefined();
  });

  it("handles transactions on non-trading days (no matching price)", () => {
    // 10/04 is a weekend, no price row
    const txns = [
      { runDate: "2025-10-04", actionType: "buy", quantity: 2, price: 101, amount: -202 },
    ];
    const points = mergeTickerData(prices, txns);
    // Should still have only 3 price points, no crash
    expect(points).toHaveLength(3);
  });
});

// ── TickerChart loading states ──────────────────────────────────────────

describe("TickerChart", () => {
  it("shows the pseudo-ticker fallback without fetching prices", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const { TickerChart } = await loadTickerChart();

    render(<TickerChart symbol="401k sp500" />);

    expect(screen.getByText("No price chart for 401k pseudo-tickers")).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("shows loading before real ticker data resolves", async () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => undefined)));
    const { TickerChart } = await loadTickerChart();

    render(<TickerChart symbol="AAPL" />);

    expect(screen.getByText("Loading AAPL chart...")).toBeTruthy();
  });

  it("renders the chart for real ticker data", async () => {
    mockPricesResponse({
      AAPL: {
        symbol: "AAPL",
        prices: [{ date: "2025-10-01", close: 100 }],
        transactions: [],
      },
    });
    const { TickerChart } = await loadTickerChart();

    render(<TickerChart symbol="AAPL" />);

    await waitFor(() => expect(screen.getByTestId("ticker-chart-base").textContent).toBe("1"));
  });

  it("shows the existing no-data message for missing ticker data", async () => {
    mockPricesResponse({});
    const { TickerChart } = await loadTickerChart();

    render(<TickerChart symbol="ZZZZ" />);

    await waitFor(() => expect(screen.getByText("No price data for ZZZZ")).toBeTruthy());
  });

  it("shows the existing error message for fetch failures", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => (
      new Response("Service Unavailable", { status: 503, statusText: "Service Unavailable" })
    )));
    const { TickerChart } = await loadTickerChart();

    render(<TickerChart symbol="AAPL" />);

    await waitFor(() => expect(screen.getByText("Failed to load chart: HTTP 503 Service Unavailable")).toBeTruthy());
  });
});

describe("TickerDialogOnly", () => {
  it("closes for empty ticker data", async () => {
    mockPricesResponse({
      AAPL: {
        symbol: "AAPL",
        prices: [],
        transactions: [],
      },
    });
    const onClose = vi.fn();
    const { TickerDialogOnly } = await loadTickerChart();

    render(<TickerDialogOnly symbol="AAPL" onClose={onClose} />);

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });
});
