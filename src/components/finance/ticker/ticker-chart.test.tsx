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
