// ── Pure data transforms for ticker charts ──────────────────────────────
//
// All React/rendering concerns live in components/finance/ticker-*.tsx.
// Functions here are side-effect-free and fully unit-testable.

import type { TickerPricePoint, TickerTransaction } from "@/lib/schemas";

export type TickerChartPoint = {
  date: string;
  ts: number;
  close: number;
  buyPrice?: number;      // VWAP across all buy txns on this date
  buyQty?: number;
  buyAmount?: number;
  buyTxnCount?: number;   // number of underlying buy/reinvestment transactions
  sellPrice?: number;
  sellQty?: number;
  sellAmount?: number;
  sellTxnCount?: number;
};

export type Cluster = {
  ts: number;              // weighted-by-amount centroid timestamp
  price: number;           // VWAP across members
  qty: number;             // sum of abs qty
  amount: number;          // sum of abs amount (USD)
  count: number;           // total number of underlying transactions merged
  r: number;               // render radius (px) — filled in after normalization
  memberDates: string[];   // ISO dates of merged days (used to highlight table rows)
};

export type ClusteredPoint = TickerChartPoint & {
  buyClusterPrice?: number;
  buyCluster?: Cluster;
  sellClusterPrice?: number;
  sellCluster?: Cluster;
};

// ── Merge prices + transactions into per-date chart points ──────────────

export function mergeTickerData(
  prices: TickerPricePoint[],
  transactions: TickerTransaction[],
): TickerChartPoint[] {
  // Index transactions by ISO date (qty/amount summed; price becomes VWAP below)
  const buyMap = new Map<string, { qty: number; amount: number; count: number }>();
  const sellMap = new Map<string, { qty: number; amount: number; count: number }>();

  for (const t of transactions) {
    const iso = t.runDate;
    const qty = Math.abs(t.quantity);
    const amount = Math.abs(t.amount);
    if (t.actionType === "buy" || t.actionType === "reinvestment") {
      const existing = buyMap.get(iso);
      if (existing) {
        existing.qty += qty;
        existing.amount += amount;
        existing.count += 1;
      } else {
        buyMap.set(iso, { qty, amount, count: 1 });
      }
    } else if (t.actionType === "sell") {
      const existing = sellMap.get(iso);
      if (existing) {
        existing.qty += qty;
        existing.amount += amount;
        existing.count += 1;
      } else {
        sellMap.set(iso, { qty, amount, count: 1 });
      }
    }
  }

  return prices.map((p) => {
    const [y, m, d] = p.date.split("-");
    const ts = new Date(+y, +m - 1, +d).getTime();
    const point: TickerChartPoint = { date: p.date, ts, close: p.close };
    const buy = buyMap.get(p.date);
    if (buy) {
      point.buyPrice = buy.qty > 0 ? buy.amount / buy.qty : 0;
      point.buyQty = buy.qty;
      point.buyAmount = buy.amount;
      point.buyTxnCount = buy.count;
    }
    const sell = sellMap.get(p.date);
    if (sell) {
      point.sellPrice = sell.qty > 0 ? sell.amount / sell.qty : 0;
      point.sellQty = sell.qty;
      point.sellAmount = sell.amount;
      point.sellTxnCount = sell.count;
    }
    return point;
  });
}

// ── Time-based single-linkage clustering ────────────────────────────────

export function clusterByTime(
  points: TickerChartPoint[],
  priceField: "buyPrice" | "sellPrice",
  qtyField: "buyQty" | "sellQty",
  amountField: "buyAmount" | "sellAmount",
  countField: "buyTxnCount" | "sellTxnCount",
): Cluster[] {
  type M = { ts: number; price: number; qty: number; amount: number; count: number; date: string };
  const markers: M[] = [];
  for (const p of points) {
    const price = p[priceField];
    const qty = p[qtyField];
    const amount = p[amountField];
    const count = p[countField];
    if (price == null || qty == null || amount == null || count == null) continue;
    markers.push({ ts: p.ts, price, qty, amount, count, date: p.date });
  }
  if (markers.length === 0 || points.length < 2) return [];
  markers.sort((a, b) => a.ts - b.ts);

  const span = points[points.length - 1].ts - points[0].ts;
  const threshold = span * 0.015; // merge if within 1.5% of visible range

  const finalize = (bucket: M[]): Cluster => {
    const qty = bucket.reduce((s, m) => s + m.qty, 0);
    const amount = bucket.reduce((s, m) => s + m.amount, 0);
    const count = bucket.reduce((s, m) => s + m.count, 0);
    // centroid weighted by amount (big trades pull the marker to their date)
    const ts = bucket.reduce((s, m) => s + m.ts * m.amount, 0) / amount;
    const price = amount / qty; // VWAP
    const memberDates = bucket.map((m) => m.date);
    return { ts, price, qty, amount, count, r: 0, memberDates };
  };

  const clusters: Cluster[] = [];
  let bucket: M[] = [markers[0]];
  for (let i = 1; i < markers.length; i++) {
    if (markers[i].ts - bucket[bucket.length - 1].ts < threshold) {
      bucket.push(markers[i]);
    } else {
      clusters.push(finalize(bucket));
      bucket = [markers[i]];
    }
  }
  clusters.push(finalize(bucket));
  return clusters;
}

export function sizeClusters(buys: Cluster[], sells: Cluster[]): { buys: Cluster[]; sells: Cluster[] } {
  const maxAmount = Math.max(1, ...buys.map((c) => c.amount), ...sells.map((c) => c.amount));
  const MIN_R = 9;
  const MAX_R = 22;
  const withR = (c: Cluster): Cluster => ({
    ...c,
    r: MIN_R + (MAX_R - MIN_R) * Math.sqrt(c.amount / maxAmount),
  });
  return { buys: buys.map(withR), sells: sells.map(withR) };
}

// ── Snap clusters onto nearest chart-data anchor day ────────────────────

export function tsToIsoLocal(ts: number): string {
  const d = new Date(ts);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function buildClusteredData(data: TickerChartPoint[]): ClusteredPoint[] {
  const sized = sizeClusters(
    clusterByTime(data, "buyPrice", "buyQty", "buyAmount", "buyTxnCount"),
    clusterByTime(data, "sellPrice", "sellQty", "sellAmount", "sellTxnCount"),
  );

  // Drop per-day scatter markers (clusters replace them in the dialog)
  const out: ClusteredPoint[] = data.map((d) => ({
    ...d,
    buyPrice: undefined,
    sellPrice: undefined,
  }));

  if (out.length === 0) return out;

  const tsList = out.map((d) => d.ts);
  const nearestIdx = (ts: number): number => {
    let lo = 0, hi = tsList.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (tsList[mid] < ts) lo = mid + 1; else hi = mid;
    }
    if (lo === 0) return 0;
    return Math.abs(ts - tsList[lo - 1]) < Math.abs(ts - tsList[lo]) ? lo - 1 : lo;
  };

  for (const c of sized.buys) {
    const i = nearestIdx(c.ts);
    out[i] = { ...out[i], buyClusterPrice: c.price, buyCluster: c };
  }
  for (const c of sized.sells) {
    const i = nearestIdx(c.ts);
    out[i] = { ...out[i], sellClusterPrice: c.price, sellCluster: c };
  }
  return out;
}
