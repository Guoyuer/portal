// @vitest-environment jsdom

import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

// The chart is a ResponsiveContainer/recharts hierarchy that doesn't matter
// for MoM/YoY badge assertions — stub it so the render stays purely about badges.
vi.mock("@/components/finance/charts", () => ({
  NetWorthTrendChart: () => <div data-testid="trend-chart" />,
}));

import { NetWorthGrowth } from "./net-worth-growth";

afterEach(cleanup);

describe("NetWorthGrowth", () => {
  it("shows 'not enough data' when trend has zero points", () => {
    render(<NetWorthGrowth data={[]} />);
    expect(screen.getByText(/not enough data/i)).toBeTruthy();
  });

  it("renders chart-only (no MoM/YoY) when trend has 1 point", () => {
    render(<NetWorthGrowth data={[{ date: "2026-04-01", total: 100_000 }]} />);
    expect(screen.getByTestId("trend-chart")).toBeTruthy();
    expect(screen.queryByText(/MoM/)).toBeNull();
    expect(screen.queryByText(/YoY/)).toBeNull();
  });

  it("computes MoM from the last two entries", () => {
    render(
      <NetWorthGrowth
        data={[
          { date: "2026-03-01", total: 100_000 },
          { date: "2026-04-01", total: 110_000 },
        ]}
      />,
    );
    expect(screen.getByText(/MoM/)).toBeTruthy();
    expect(screen.getByText(/YoY/)).toBeTruthy();
    // Only 2 entries → YoY falls back to the earliest (same as prev), so both
    // badges display the same +10% / $10,000
    expect(screen.getAllByText("+10.0%").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("$10,000").length).toBeGreaterThanOrEqual(1);
  });

  it("computes YoY against the closest-to-1-year-ago entry", () => {
    render(
      <NetWorthGrowth
        data={[
          { date: "2025-03-31", total: 100_000 }, // ~1 year before latest → YoY baseline
          { date: "2025-08-01", total: 130_000 },
          { date: "2026-03-01", total: 180_000 },
          { date: "2026-04-01", total: 200_000 }, // latest
        ]}
      />,
    );
    // YoY: (200k - 100k) / 100k = +100%
    expect(screen.getByText("+100.0%")).toBeTruthy();
    // MoM: (200k - 180k) / 180k ≈ +11.1%
    expect(screen.getByText("+11.1%")).toBeTruthy();
  });

  it("handles zero previous value gracefully (mom = 0)", () => {
    render(
      <NetWorthGrowth
        data={[
          { date: "2026-03-01", total: 0 },
          { date: "2026-04-01", total: 100_000 },
        ]}
      />,
    );
    // mom = 0 because prev total is 0 → returns 0 rather than Infinity
    const allPcts = screen.getAllByText(/[+\-]?0\.0%/);
    expect(allPcts.length).toBeGreaterThan(0);
  });

  it("picks the chronologically closest data point when no exact 1-year match exists", () => {
    // Latest = 2026-04-15, so 1y ago = 2025-04-15.
    // Closest candidate: 2025-04-10 (5 days before), NOT 2025-05-20 (35 days after).
    render(
      <NetWorthGrowth
        data={[
          { date: "2025-03-01", total: 50_000 },
          { date: "2025-04-10", total: 60_000 }, // closest to 1y ago
          { date: "2025-05-20", total: 70_000 },
          { date: "2026-04-15", total: 120_000 },
        ]}
      />,
    );
    // YoY: (120000 - 60000) / 60000 = 100%
    expect(screen.getByText("+100.0%")).toBeTruthy();
  });
});
