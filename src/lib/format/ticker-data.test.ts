import { describe, it, expect } from "vitest";
import {
  clusterByTime,
  sizeClusters,
  buildClusteredData,
  mergeTickerData,
  tsToIsoLocal,
  type TickerChartPoint,
} from "@/lib/format/ticker-data";

// Helper to build a chart point
const pt = (date: string, over: Partial<TickerChartPoint> = {}): TickerChartPoint => {
  const [y, m, d] = date.split("-").map((s) => parseInt(s, 10));
  return { date, ts: new Date(y, m - 1, d).getTime(), close: 100, ...over };
};

describe("clusterByTime", () => {
  it("returns empty when no points have the target fields", () => {
    const points = [pt("2025-01-01"), pt("2025-02-01")];
    expect(clusterByTime(points, "buyPrice", "buyQty", "buyAmount", "buyTxnCount")).toEqual([]);
  });

  it("returns empty when only one point exists (span is 0, cluster meaningless)", () => {
    const points = [pt("2025-01-01", { buyPrice: 100, buyQty: 1, buyAmount: 100, buyTxnCount: 1 })];
    expect(clusterByTime(points, "buyPrice", "buyQty", "buyAmount", "buyTxnCount")).toEqual([]);
  });

  it("sums txn counts across days in the same cluster (count != days)", () => {
    // Two adjacent days each with 2 txns → cluster count should be 4 (not 2)
    const points = [
      pt("2025-01-01", { buyPrice: 100, buyQty: 2, buyAmount: 200, buyTxnCount: 2 }),
      pt("2025-01-02", { buyPrice: 100, buyQty: 2, buyAmount: 200, buyTxnCount: 2 }),
      pt("2026-01-01"), // span anchor far in the future
    ];
    const clusters = clusterByTime(points, "buyPrice", "buyQty", "buyAmount", "buyTxnCount");
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
      pt("2025-01-01", { buyPrice: 100, buyQty: 1, buyAmount: 100, buyTxnCount: 1 }),
      pt("2025-01-08", { buyPrice: 100, buyQty: 1, buyAmount: 100, buyTxnCount: 1 }),
      pt("2026-01-01"),
    ];
    const clusters = clusterByTime(points, "buyPrice", "buyQty", "buyAmount", "buyTxnCount");
    expect(clusters).toHaveLength(2);
  });

  it("computes VWAP as amount/qty per cluster", () => {
    // Two buys: qty 10 @ $100 ($1000) and qty 20 @ $130 ($2600)
    // VWAP = 3600 / 30 = $120
    const points = [
      pt("2025-01-01", { buyPrice: 100, buyQty: 10, buyAmount: 1000, buyTxnCount: 1 }),
      pt("2025-01-02", { buyPrice: 130, buyQty: 20, buyAmount: 2600, buyTxnCount: 1 }),
      pt("2026-01-01"),
    ];
    const clusters = clusterByTime(points, "buyPrice", "buyQty", "buyAmount", "buyTxnCount");
    expect(clusters).toHaveLength(1);
    expect(clusters[0].price).toBeCloseTo(120);
  });

  it("weights centroid timestamp by amount (big trades pull toward their date)", () => {
    // Trade A: $100 at day 1; Trade B: $900 at day 2. Centroid ≈ day ~1.9
    const day1 = pt("2025-01-01", { buyPrice: 100, buyQty: 1, buyAmount: 100, buyTxnCount: 1 });
    const day2 = pt("2025-01-02", { buyPrice: 100, buyQty: 9, buyAmount: 900, buyTxnCount: 1 });
    const points = [day1, day2, pt("2026-01-01")];
    const clusters = clusterByTime(points, "buyPrice", "buyQty", "buyAmount", "buyTxnCount");
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
      pt("2025-01-01", { buyPrice: 100, buyQty: 1, buyAmount: 100, buyTxnCount: 1 }),
      pt("2025-01-02", { buyPrice: 100, buyQty: 1, buyAmount: 100, buyTxnCount: 1 }),
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
      pt("2025-01-01", { buyPrice: 100, buyQty: 1, buyAmount: 100, buyTxnCount: 1 }),
      pt("2026-01-01"),
    ];
    const out = buildClusteredData(points);
    expect(out[0].buyPrice).toBeUndefined();
    expect(out[0].sellPrice).toBeUndefined();
  });
});

describe("tsToIsoLocal", () => {
  it("formats a local-timezone timestamp as YYYY-MM-DD", () => {
    const ts = new Date(2026, 3, 15).getTime(); // April 15, 2026 (months are 0-indexed)
    expect(tsToIsoLocal(ts)).toBe("2026-04-15");
  });

  it("pads single-digit month and day", () => {
    const ts = new Date(2026, 0, 5).getTime();
    expect(tsToIsoLocal(ts)).toBe("2026-01-05");
  });
});

describe("mergeTickerData reinvestment split", () => {
  it("separates reinvestment from buys", () => {
    const out = mergeTickerData(
      [{ date: "2026-01-02", close: 100 }],
      [
        { runDate: "2026-01-02", actionType: "buy", amount: 100, quantity: 1, price: 100 },
        { runDate: "2026-01-02", actionType: "reinvestment", amount: 5, quantity: 0.05, price: 100 },
      ],
    );
    expect(out[0].buyAmount).toBe(100);
    expect(out[0].buyTxnCount).toBe(1);
    expect(out[0].reinvestAmount).toBe(5);
    expect(out[0].reinvestTxnCount).toBe(1);
  });

  it("reinvestment-only day has no buyAmount but has reinvestAmount", () => {
    const out = mergeTickerData(
      [{ date: "2026-01-03", close: 110 }],
      [
        { runDate: "2026-01-03", actionType: "reinvestment", amount: 10, quantity: 0.09, price: 110 },
      ],
    );
    expect(out[0].buyAmount).toBeUndefined();
    expect(out[0].buyTxnCount).toBeUndefined();
    expect(out[0].reinvestAmount).toBe(10);
    expect(out[0].reinvestTxnCount).toBe(1);
  });

  it("multiple reinvestments on the same day are summed", () => {
    const out = mergeTickerData(
      [{ date: "2026-01-04", close: 120 }],
      [
        { runDate: "2026-01-04", actionType: "reinvestment", amount: 3, quantity: 0.025, price: 120 },
        { runDate: "2026-01-04", actionType: "reinvestment", amount: 7, quantity: 0.058, price: 120 },
      ],
    );
    expect(out[0].reinvestAmount).toBeCloseTo(10);
    expect(out[0].reinvestTxnCount).toBe(2);
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
