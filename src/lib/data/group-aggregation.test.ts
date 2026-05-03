import { describe, it, expect } from "vitest";
import { groupNetByDate, buildGroupValueSeries } from "./group-aggregation";
import type { FidelityTxn } from "@/lib/schemas";

const real = (runDate: string, symbol: string, actionType: "buy" | "sell", amount: number): FidelityTxn => ({
  runDate, actionType, symbol,
  amount: actionType === "sell" ? -Math.abs(amount) : amount,
  quantity: 1,
  price: amount,
});

describe("groupNetByDate", () => {
  it("same-day exact swap → no marker", () => {
    const txns = [real("2026-01-02", "SPY", "sell", 1000), real("2026-01-02", "VOO", "buy", 1000)];
    const out = groupNetByDate(txns);
    expect(out.get("sp500")?.size ?? 0).toBe(0);
  });

  it("same-day partial swap → one S marker at net", () => {
    const txns = [real("2026-01-02", "SPY", "sell", 1000), real("2026-01-02", "VOO", "buy", 500)];
    const out = groupNetByDate(txns);
    const entries = Array.from(out.get("sp500")!.values());
    expect(entries).toHaveLength(1);
    expect(entries[0].side).toBe("sell");
    expect(entries[0].net).toBe(500);
    expect(entries[0].date).toBe("2026-01-02");
    expect(entries[0].breakdown).toEqual([
      { symbol: "SPY", signed: 1000 },
      { symbol: "VOO", signed: -500 },
    ]);
  });

  it("T+1 swap pairs (Mon sell → Tue buy)", () => {
    const txns = [real("2026-01-05", "SPY", "sell", 1000), real("2026-01-06", "VOO", "buy", 1000)];
    expect(groupNetByDate(txns).get("sp500")?.size ?? 0).toBe(0);
  });

  it("T+2 window: 3-day gap (Mon → Thu) does NOT pair → two markers", () => {
    const txns = [real("2026-01-05", "SPY", "sell", 1000), real("2026-01-08", "VOO", "buy", 1000)];
    const entries = Array.from(groupNetByDate(txns).get("sp500")!.values());
    expect(entries).toHaveLength(2);
    expect(entries[0].side).toBe("sell");
    expect(entries[1].side).toBe("buy");
  });

  it("chained T+2 (day1 → day3 → day5) all in one cluster", () => {
    const txns = [
      real("2026-01-05", "SPY", "sell", 1000),
      real("2026-01-07", "VOO", "buy", 500),
      real("2026-01-09", "IVV", "buy", 400),
    ];
    const entries = Array.from(groupNetByDate(txns).get("sp500")!.values());
    expect(entries).toHaveLength(1);
    expect(entries[0].net).toBe(100);
    expect(entries[0].side).toBe("sell");
    expect(entries[0].date).toBe("2026-01-05");
  });

  it("REINVEST excluded from net", () => {
    const txns: FidelityTxn[] = [
      real("2026-01-02", "SPY", "sell", 1000),
      { runDate: "2026-01-02", actionType: "reinvestment", symbol: "VOO", amount: 50, quantity: 0.5, price: 100 },
    ];
    const entries = Array.from(groupNetByDate(txns).get("sp500")!.values());
    expect(entries).toHaveLength(1);
    expect(entries[0].net).toBe(1000);
  });

  it("tickers outside any group produce no entries", () => {
    const txns = [real("2026-01-02", "NVDA", "buy", 1000)];
    expect(groupNetByDate(txns).size).toBe(0);
  });

  it("$49 net → no marker (below threshold); $51 → marker", () => {
    const below = [real("2026-01-02", "SPY", "sell", 1000), real("2026-01-02", "VOO", "buy", 951)];
    expect(groupNetByDate(below).get("sp500")?.size ?? 0).toBe(0);
    const above = [real("2026-01-02", "SPY", "sell", 1000), real("2026-01-02", "VOO", "buy", 949)];
    expect(groupNetByDate(above).get("sp500")!.size).toBe(1);
  });

  it("cross-group txns don't interfere", () => {
    const txns = [
      real("2026-01-02", "SPY", "sell", 1000),
      real("2026-01-02", "QQQ", "sell", 500),
    ];
    const out = groupNetByDate(txns);
    expect(out.get("sp500")?.size).toBe(1);
    expect(out.get("nasdaq_100")?.size).toBe(1);
  });
});

const dt = (date: string, ticker: string, value: number) => ({
  date, ticker, value, category: "", subtype: "", costBasis: 0, gainLoss: 0, gainLossPct: 0,
});

describe("buildGroupValueSeries", () => {
  it("sums constituent values per date", () => {
    const series = buildGroupValueSeries([
      dt("2026-01-02", "SPY", 1000),
      dt("2026-01-02", "VOO", 500),
      dt("2026-01-03", "SPY", 1100),
    ], ["SPY", "VOO"]);
    expect(series).toHaveLength(2);
    expect(series[0]).toMatchObject({ date: "2026-01-02", value: 1500 });
    expect(series[0].constituents).toHaveLength(2);
    expect(series[1]).toMatchObject({ date: "2026-01-03", value: 1100 });
  });

  it("ignores tickers not in the group", () => {
    const series = buildGroupValueSeries([
      dt("2026-01-02", "SPY", 1000),
      dt("2026-01-02", "NVDA", 800),
    ], ["SPY"]);
    expect(series).toHaveLength(1);
    expect(series[0].value).toBe(1000);
  });
});
