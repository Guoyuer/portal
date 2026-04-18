// @vitest-environment jsdom

import { describe, it, expect, vi, beforeAll, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

afterEach(cleanup);
import { MarketContext } from "./market-context";
import type { MarketData } from "@/lib/schemas";

// ── Mock recharts (avoids SVG rendering issues in jsdom) ────────────────

vi.mock("recharts", () => ({
  Area: () => null,
  AreaChart: () => null,
  YAxis: () => null,
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

// ── Mock ResizeObserver ─────────────────────────────────────────────────

beforeAll(() => {
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

// ── Helpers ─────────────────────────────────────────────────────────────

const MARKET: MarketData = {
  indices: [
    { ticker: "^GSPC", name: "S&P 500", monthReturn: 2.5, ytdReturn: 12.3, current: 5500, sparkline: null, high52w: 5800, low52w: 4200 },
    { ticker: "^NDX", name: "NASDAQ 100", monthReturn: -1.2, ytdReturn: 8.7, current: 19000, sparkline: null, high52w: 20000, low52w: 15000 },
  ],
};

// ── Tests ───────────────────────────────────────────────────────────────

describe("MarketContext", () => {
  it("renders index cards with display names", () => {
    render(<MarketContext data={MARKET} title="Market" />);
    expect(screen.getByText("S&P 500")).toBeTruthy();
    expect(screen.getByText("NASDAQ 100")).toBeTruthy();
  });

  it("renders current prices formatted correctly", () => {
    render(<MarketContext data={MARKET} title="Market" />);
    expect(screen.getByText("5,500")).toBeTruthy();
    expect(screen.getByText("19,000")).toBeTruthy();
  });

  it("renders return badges with M and YTD labels", () => {
    render(<MarketContext data={MARKET} title="Market" />);
    const mLabels = screen.getAllByText("M");
    const ytdLabels = screen.getAllByText("YTD");
    expect(mLabels.length).toBe(2);
    expect(ytdLabels.length).toBe(2);
  });

  it("shows empty state when no indices", () => {
    const emptyMarket = { ...MARKET, indices: [] };
    render(<MarketContext data={emptyMarket} title="Market" />);
    expect(screen.getByText("Index data unavailable")).toBeTruthy();
  });

  it("renders 52-week range bars", () => {
    render(<MarketContext data={MARKET} title="Market" />);
    expect(screen.getAllByText("52-week range").length).toBe(2);
    expect(screen.getByText("4,200")).toBeTruthy();
    expect(screen.getByText("5,800")).toBeTruthy();
  });

  it("renders without chart when sparkline is null", () => {
    // MARKET already has sparkline: null for both indices
    const { container } = render(<MarketContext data={MARKET} title="Market" />);
    // No AreaChart should be rendered (mocked to null, but data guard should prevent call)
    expect(screen.getByText("S&P 500")).toBeTruthy();
    // Verify the component doesn't crash and still shows index data
    expect(container.querySelectorAll("[data-testid='sparkline-chart']").length).toBe(0);
  });
});
