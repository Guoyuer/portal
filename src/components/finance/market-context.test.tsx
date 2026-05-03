// @vitest-environment jsdom

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MarketContext } from "./market-context";
import { MARKET } from "@/test/factories";

// Mock recharts (avoids SVG rendering issues in jsdom)
vi.mock("recharts", () => ({
  Area: () => null,
  AreaChart: () => null,
  YAxis: () => null,
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

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

  it("accepts exporter-guaranteed sparkline arrays", () => {
    render(<MarketContext data={MARKET} title="Market" />);
    expect(screen.getByText("S&P 500")).toBeTruthy();
    expect(MARKET.indices[0].sparkline.length).toBeGreaterThan(0);
  });
});
