// @vitest-environment jsdom

import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

afterEach(cleanup);
import { mergeTickerData, type TickerChartPoint } from "@/lib/ticker-data";

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

  it("treats reinvestment as buy", () => {
    const txns = [
      { runDate: "2025-10-01", actionType: "reinvestment", quantity: 1, price: 99, amount: -99 },
    ];
    const points = mergeTickerData(prices, txns);
    const oct1 = points.find(p => p.date === "2025-10-01")!;
    expect(oct1.buyPrice).toBe(99);
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
