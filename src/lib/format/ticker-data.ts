// ── Pure data transforms for ticker charts ──────────────────────────────
//
// All React/rendering concerns live in components/finance/ticker-*.tsx.
// Functions here are side-effect-free and fully unit-testable.

import type { TickerPricePoint, TickerTransaction } from "@/lib/schemas";
import { parseLocalDate } from "@/lib/format/format";

export type TickerChartPoint = {
  date: string;
  ts: number;
  close: number;
  buyPrice?: number;      // VWAP across all buy txns on this date
  buyQty?: number;
  buyAmount?: number;
  buyTxnCount?: number;   // number of underlying buy transactions
  sellPrice?: number;
  sellQty?: number;
  sellAmount?: number;
  sellTxnCount?: number;
  reinvestAmount?: number;     // summed abs($) of reinvestment txns on this date
  reinvestTxnCount?: number;
};

export type Cluster = {
  ts: number;              // weighted-by-amount centroid timestamp
  price: number;           // VWAP across members
  qty: number;             // sum of abs qty
  amount: number;          // sum of abs amount (USD)
  count: number;           // total number of underlying transactions merged
  r: number;               // render radius (px) — filled in after normalization
  memberDates: string[];   // ISO dates of merged days (used to highlight table rows)
  // Optional per-constituent contribution — populated by group-aggregation
  // for the group chart's tooltip. Kept here so BuyClusterMarker/SellClusterMarker
  // accept both cluster kinds with one payload type.
  breakdown?: { symbol: string; signed: number }[];
};

export type ClusteredPoint = TickerChartPoint & {
  buyClusterPrice?: number;
  buyCluster?: Cluster;
  sellClusterPrice?: number;
  sellCluster?: Cluster;
  reinvestDot?: number;
};

// ── Merge prices + transactions into per-date chart points ──────────────

export function mergeTickerData(
  prices: TickerPricePoint[],
  transactions: TickerTransaction[],
): TickerChartPoint[] {
  // Index transactions by ISO date (qty/amount summed; price becomes VWAP below)
  const buyMap = new Map<string, { qty: number; amount: number; count: number }>();
  const sellMap = new Map<string, { qty: number; amount: number; count: number }>();
  const reinvestMap = new Map<string, { amount: number; count: number }>();

  for (const t of transactions) {
    const iso = t.runDate;
    const qty = Math.abs(t.quantity);
    const amount = Math.abs(t.amount);
    if (t.actionType === "buy") {
      const e = buyMap.get(iso);
      if (e) { e.qty += qty; e.amount += amount; e.count += 1; }
      else buyMap.set(iso, { qty, amount, count: 1 });
    } else if (t.actionType === "sell") {
      const e = sellMap.get(iso);
      if (e) { e.qty += qty; e.amount += amount; e.count += 1; }
      else sellMap.set(iso, { qty, amount, count: 1 });
    } else if (t.actionType === "reinvestment") {
      const e = reinvestMap.get(iso);
      if (e) { e.amount += amount; e.count += 1; }
      else reinvestMap.set(iso, { amount, count: 1 });
    }
  }

  return prices.map((p) => {
    const ts = parseLocalDate(p.date).getTime();
    const point: TickerChartPoint = { date: p.date, ts, close: p.close };
    const buy = buyMap.get(p.date);
    if (buy) { point.buyPrice = buy.qty > 0 ? buy.amount / buy.qty : 0; point.buyQty = buy.qty; point.buyAmount = buy.amount; point.buyTxnCount = buy.count; }
    const sell = sellMap.get(p.date);
    if (sell) { point.sellPrice = sell.qty > 0 ? sell.amount / sell.qty : 0; point.sellQty = sell.qty; point.sellAmount = sell.amount; point.sellTxnCount = sell.count; }
    const reinvest = reinvestMap.get(p.date);
    if (reinvest) { point.reinvestAmount = reinvest.amount; point.reinvestTxnCount = reinvest.count; }
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

/** Sqrt-normalize an amount into a pixel radius. Shared by ticker + group charts. */
export function scaleR(amount: number, maxAmount: number, minR: number, maxR: number): number {
  return minR + (maxR - minR) * Math.sqrt(amount / Math.max(maxAmount, 1));
}

/**
 * Average cost basis ($/share) via avg-cost replay. Matches the pipeline's
 * `etl/replay.py` semantics so the ticker chart's reference line agrees
 * with what the group chart / D1 reports as cost basis.
 *
 * Why not just sum(buys.amount) / sum(buys.quantity): that formula ignores
 * sells, so a buy→sell→buy cycle reports the wrong average. AVG-cost
 * accounting reduces remaining cost proportionally when shares leave.
 *
 * Stock splits are encoded by Fidelity as `distribution` with `price=0`
 * and a signed share delta (`quantity`) — we adjust qty but not cost.
 */
export function computeAvgCost(txns: TickerTransaction[]): number | null {
  let totalCost = 0;
  let totalQty = 0;
  const sorted = [...txns].sort((a, b) => a.runDate.localeCompare(b.runDate));
  for (const t of sorted) {
    if (t.actionType === "buy" || t.actionType === "reinvestment") {
      totalCost += Math.abs(t.amount);
      totalQty += Math.abs(t.quantity);
    } else if (t.actionType === "sell") {
      if (totalQty <= 0) continue;
      const avg = totalCost / totalQty;
      const sellQty = Math.min(Math.abs(t.quantity), totalQty);
      totalCost = Math.max(0, totalCost - sellQty * avg);
      totalQty -= sellQty;
    } else if (t.actionType === "distribution" && t.price === 0) {
      // Stock split — use signed quantity (positive for splits, negative for reverse)
      totalQty += t.quantity;
    }
  }
  return totalQty > 0 ? totalCost / totalQty : null;
}

/** Build a Map<date, close> from the prices array returned by /prices/:symbol. */
export function priceMapFromSeries(prices: TickerPricePoint[]): Map<string, number> {
  const m = new Map<string, number>();
  for (const p of prices) m.set(p.date, p.close);
  return m;
}

export function sizeClusters(buys: Cluster[], sells: Cluster[]): { buys: Cluster[]; sells: Cluster[] } {
  const maxAmount = Math.max(1, ...buys.map((c) => c.amount), ...sells.map((c) => c.amount));
  const withR = (c: Cluster): Cluster => ({ ...c, r: scaleR(c.amount, maxAmount, 9, 22) });
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
  for (let i = 0; i < out.length; i++) {
    const d = out[i];
    if (d.reinvestAmount != null) {
      out[i] = { ...d, reinvestDot: d.close };
    }
  }
  return out;
}
