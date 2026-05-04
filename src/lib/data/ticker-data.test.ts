import { describe, it, expect } from "vitest";
import {
  clusterByTime,
  sizeClusters,
  buildClusteredData,
  mergeTickerData,
  tsToIsoLocal,
  computeAvgCost,
  type TickerChartPoint,
} from "@/lib/data/ticker-data";
import type { TickerTxn } from "@/lib/schemas/ticker";

// Helper to build a chart point
const pt = (date: string, over: Partial<TickerChartPoint> = {}): TickerChartPoint => {
  const [y, m, d] = date.split("-").map((s) => parseInt(s, 10));
  return { date, ts: new Date(y, m - 1, d).getTime(), close: 100, ...over };
};
const buyPoint = (date: string, qty = 1, amount = 100, price = 100, count = 1) =>
  pt(date, { buyPrice: price, buyQty: qty, buyAmount: amount, buyTxnCount: count });

describe("clusterByTime", () => {
  it.each([
    ["no points have target fields", [pt("2025-01-01"), pt("2025-02-01")]],
    ["only one point exists", [buyPoint("2025-01-01")]],
  ])("returns empty when %s", (_name, points) => {
    expect(clusterByTime(points, "buy")).toEqual([]);
  });

  it("sums txn counts across days in the same cluster (count != days)", () => {
    // Two adjacent days each with 2 txns → cluster count should be 4 (not 2)
    const points = [
      buyPoint("2025-01-01", 2, 200, 100, 2),
      buyPoint("2025-01-02", 2, 200, 100, 2),
      pt("2026-01-01"), // span anchor far in the future
    ];
    const clusters = clusterByTime(points, "buy");
    expect(clusters).toHaveLength(1);
    expect(clusters[0].count).toBe(4);
    expect(clusters[0].qty).toBe(4);
    expect(clusters[0].amount).toBe(400);
    expect(clusters[0].memberDates).toEqual(["2025-01-01", "2025-01-02"]);
  });

  it("splits into separate clusters when gap exceeds 1.5% of visible span", () => {
    // Span = 365 days, threshold = 5.4 days.
    // Buys 7 days apart should NOT cluster.
    const points = [
      buyPoint("2025-01-01"),
      buyPoint("2025-01-08"),
      pt("2026-01-01"),
    ];
    const clusters = clusterByTime(points, "buy");
    expect(clusters).toHaveLength(2);
  });

  it("computes VWAP as amount/qty per cluster", () => {
    // Two buys: qty 10 @ $100 ($1000) and qty 20 @ $130 ($2600)
    // VWAP = 3600 / 30 = $120
    const points = [
      buyPoint("2025-01-01", 10, 1000, 100),
      buyPoint("2025-01-02", 20, 2600, 130),
      pt("2026-01-01"),
    ];
    const clusters = clusterByTime(points, "buy");
    expect(clusters).toHaveLength(1);
    expect(clusters[0].price).toBeCloseTo(120);
  });

  it("weights centroid timestamp by amount (big trades pull toward their date)", () => {
    // Trade A: $100 at day 1; Trade B: $900 at day 2. Centroid ≈ day ~1.9
    const day1 = buyPoint("2025-01-01", 1, 100);
    const day2 = buyPoint("2025-01-02", 9, 900);
    const points = [day1, day2, pt("2026-01-01")];
    const clusters = clusterByTime(points, "buy");
    expect(clusters).toHaveLength(1);
    // Closer to day2 (big trade) than day1
    expect(clusters[0].ts).toBeGreaterThan(day1.ts + (day2.ts - day1.ts) * 0.5);
    expect(clusters[0].ts).toBeLessThanOrEqual(day2.ts);
  });
});

describe("sizeClusters", () => {
  it("assigns r within [MIN_R, MAX_R] based on sqrt(amount/max)", () => {
    const buy = { ts: 0, price: 1, qty: 1, amount: 100, count: 1, r: 0, memberDates: ["2025-01-01"] };
    const sell = { ts: 0, price: 1, qty: 1, amount: 400, count: 1, r: 0, memberDates: ["2025-01-02"] };
    const { buys, sells } = sizeClusters([buy], [sell]);
    // max=400 → sell is MAX_R=22, buy = 9 + 13*sqrt(0.25) = 9+6.5 = 15.5
    expect(sells[0].r).toBeCloseTo(22);
    expect(buys[0].r).toBeCloseTo(15.5, 1);
  });

  it("treats empty input gracefully (no division by zero)", () => {
    expect(sizeClusters([], [])).toEqual({ buys: [], sells: [] });
  });
});

describe("buildClusteredData", () => {
  it("snaps cluster centroids to the nearest data anchor", () => {
    const points = [
      buyPoint("2025-01-01"),
      buyPoint("2025-01-02"),
      pt("2025-01-03"),
      pt("2026-01-01"),
    ];
    const out = buildClusteredData(points);
    // One cluster with both 01-01 and 01-02 as members, centroid midway → snaps to 01-01 or 01-02
    const clusterHosts = out.filter((p) => p.buyCluster);
    expect(clusterHosts).toHaveLength(1);
    expect(clusterHosts[0].date).toMatch(/2025-01-0[12]/);
  });

  it("strips per-day buyPrice/sellPrice so inline markers don't double-render", () => {
    const points = [
      buyPoint("2025-01-01"),
      pt("2026-01-01"),
    ];
    const out = buildClusteredData(points);
    expect(out[0].buyPrice).toBeUndefined();
    expect(out[0].sellPrice).toBeUndefined();
  });
});

describe("tsToIsoLocal", () => {
  it.each([
    [new Date(2026, 3, 15).getTime(), "2026-04-15"],
    [new Date(2026, 0, 5).getTime(), "2026-01-05"],
  ])("formats local timestamp %s as %s", (ts, expected) => {
    expect(tsToIsoLocal(ts)).toBe(expected);
  });
});

describe("mergeTickerData reinvestment split", () => {
  const txn = (date: string, actionType: TickerTxn["actionType"], amount: number, quantity: number, price: number): TickerTxn => ({
    runDate: date, actionType, amount, quantity, price,
  });

  it.each([
    {
      name: "separates reinvestment from buys",
      date: "2026-01-02",
      close: 100,
      txns: [
        txn("2026-01-02", "buy", 100, 1, 100),
        txn("2026-01-02", "reinvestment", 5, 0.05, 100),
      ],
      expected: { buyAmount: 100, buyTxnCount: 1, reinvestAmount: 5, reinvestTxnCount: 1 },
    },
    {
      name: "reinvestment-only day has no buyAmount but has reinvestAmount",
      date: "2026-01-03",
      close: 110,
      txns: [txn("2026-01-03", "reinvestment", 10, 0.09, 110)],
      expected: { reinvestAmount: 10, reinvestTxnCount: 1 },
      absent: ["buyAmount", "buyTxnCount"] as const,
    },
    {
      name: "multiple reinvestments on the same day are summed",
      date: "2026-01-04",
      close: 120,
      txns: [
        txn("2026-01-04", "reinvestment", 3, 0.025, 120),
        txn("2026-01-04", "reinvestment", 7, 0.058, 120),
      ],
      expected: { reinvestAmount: 10, reinvestTxnCount: 2 },
    },
  ])("$name", ({ date, close, txns, expected, absent = [] }) => {
    const row = mergeTickerData([{ date, close }], txns)[0];
    expect(row).toMatchObject(expected);
    for (const key of absent) expect(row[key]).toBeUndefined();
  });

  it("buildClusteredData sets reinvestDot to close price when reinvestAmount is present", () => {
    const out = buildClusteredData([
      pt("2025-06-01", { reinvestAmount: 5, reinvestTxnCount: 1 }),
      pt("2026-01-01"),
    ]);
    expect(out[0].reinvestDot).toBe(100); // close is 100 from pt helper
    expect(out[1].reinvestDot).toBeUndefined();
  });
});

describe("computeAvgCost", () => {
  const buy = (date: string, qty: number, price: number): TickerTxn => ({
    runDate: date, actionType: "buy", quantity: qty, price, amount: -qty * price,
  });
  const sell = (date: string, qty: number, price: number): TickerTxn => ({
    runDate: date, actionType: "sell", quantity: -qty, price, amount: qty * price,
  });
  const reinvest = (date: string, qty: number, price: number): TickerTxn => ({
    runDate: date, actionType: "reinvestment", quantity: qty, price, amount: qty * price,
  });
  const split = (date: string, qtyDelta: number): TickerTxn => ({
    runDate: date, actionType: "distribution", quantity: qtyDelta, price: 0, amount: 0,
  });

  it.each([
    { name: "returns null when no qty", txns: [], expected: null },
    { name: "single buy: avg = amount / qty", txns: [buy("2025-01-01", 10, 100)], expected: 100 },
    {
      name: "buy → sell → buy reports avg-cost basis of remaining shares",
      txns: [buy("2025-01-01", 100, 10), sell("2025-02-01", 50, 20), buy("2025-03-01", 50, 30)],
      expected: 20,
    },
    {
      name: "reinvestment counts toward cost + qty",
      txns: [buy("2025-01-01", 10, 100), reinvest("2025-02-01", 0.5, 100)],
      expected: 100,
    },
    {
      name: "stock split preserves cost basis, doubles qty, halves avg",
      txns: [buy("2025-01-01", 100, 10), split("2025-06-01", 100)],
      expected: 5,
    },
    {
      name: "chronological order matters — unsorted input still produces correct result",
      txns: [buy("2025-03-01", 50, 30), sell("2025-02-01", 50, 20), buy("2025-01-01", 100, 10)],
      expected: 20,
    },
    {
      name: "selling more than held is clamped",
      txns: [buy("2025-01-01", 10, 100), sell("2025-02-01", 15, 100)],
      expected: null,
    },
    {
      name: "dividends and other action types are ignored",
      txns: [
        buy("2025-01-01", 10, 100),
        { runDate: "2025-02-01", actionType: "dividend", quantity: 0, price: 0, amount: 50 },
        { runDate: "2025-03-01", actionType: "interest", quantity: 0, price: 0, amount: 5 },
      ],
      expected: 100,
    },
  ])("$name", ({ txns, expected }: { txns: TickerTxn[]; expected: number | null }) => {
    const result = computeAvgCost(txns);
    if (expected === null) expect(result).toBeNull();
    else expect(result).toBeCloseTo(expected);
  });
});
