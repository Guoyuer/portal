// ── Group transaction aggregation (pure data layer) ─────────────────────
// `groupNetByDate` clusters real buy/sell flows within an equivalence group
// to surface net exposure change, filtering out ticker-swap noise.

import type { DailyTicker } from "@/lib/schemas/timeline";
import type { InvestmentTxn } from "@/lib/compute/compute";
import { groupOfTicker } from "@/lib/data/equivalent-groups";
import { parseLocalDate } from "@/lib/format/format";

const MS_PER_DAY = 86_400_000;
// T+2 settlement window: a rebalance that executes Mon may settle Wed, so
// trades within 2 calendar days of the prior trade chain into one cluster.
const WINDOW_DAYS = 2;
// Below this the cluster is treated as exact-swap noise (FP dust, tiny
// bounceback), not a real exposure change.
const THRESHOLD_USD = 50;

export type GroupNetEntry = {
  date: string;
  side: "buy" | "sell";
  net: number;
  breakdown: { symbol: string; signed: number }[];
};

type GroupTxnInput = Pick<InvestmentTxn, "source" | "date" | "actionType" | "ticker" | "amount">;

type Real = {
  date: string;
  ts: number;
  symbol: string;
  side: "buy" | "sell";
  amount: number;
};

function groupNetSide(actionType: string): "buy" | "sell" | null {
  if (actionType === "sell") return "sell";
  if (actionType === "buy" || actionType === "contribution") return "buy";
  return null;
}

function extractGroupTxns(txns: GroupTxnInput[]): Map<string, Map<string, Real[]>> {
  const byGroup = new Map<string, Map<string, Real[]>>();
  for (const t of txns) {
    const side = groupNetSide(t.actionType);
    if (!side) continue;
    const groupKey = groupOfTicker(t.ticker);
    if (!groupKey) continue;
    const entry: Real = {
      date: t.date,
      ts: parseLocalDate(t.date).getTime(),
      symbol: t.ticker,
      side,
      amount: Math.abs(t.amount),
    };
    let bySource = byGroup.get(groupKey);
    if (!bySource) {
      bySource = new Map<string, Real[]>();
      byGroup.set(groupKey, bySource);
    }
    const arr = bySource.get(t.source);
    if (arr) arr.push(entry);
    else bySource.set(t.source, [entry]);
  }
  return byGroup;
}

function clusterByWindow(groupTxns: Real[]): Real[][] {
  const sorted = [...groupTxns].sort((a, b) => a.ts - b.ts);
  const clusters: Real[][] = [];
  for (const t of sorted) {
    const last = clusters[clusters.length - 1];
    if (last && (t.ts - last[last.length - 1].ts) <= WINDOW_DAYS * MS_PER_DAY) {
      last.push(t);
    } else {
      clusters.push([t]);
    }
  }
  return clusters;
}

function aggregateCluster(cluster: Real[]): GroupNetEntry | null {
  let net = 0;
  const breakdown: { symbol: string; signed: number }[] = [];
  for (const t of cluster) {
    const signed = t.side === "sell" ? t.amount : -t.amount;
    net += signed;
    breakdown.push({ symbol: t.symbol, signed });
  }
  if (Math.abs(net) < THRESHOLD_USD) return null;
  return {
    date: cluster[0].date,
    side: net > 0 ? "sell" : "buy",
    net: Math.abs(net),
    breakdown,
  };
}

export function groupNetByDate(
  txns: GroupTxnInput[],
): Map<string, Map<string, GroupNetEntry>> {
  const byGroup = extractGroupTxns(txns);
  const result = new Map<string, Map<string, GroupNetEntry>>();

  for (const [groupKey, bySource] of byGroup) {
    const byDate = result.get(groupKey) ?? new Map<string, GroupNetEntry>();
    for (const groupTxns of bySource.values()) {
      for (const cluster of clusterByWindow(groupTxns)) {
        const entry = aggregateCluster(cluster);
        if (entry) addEntryByDate(byDate, entry);
      }
    }
    if (byDate.size > 0) result.set(groupKey, byDate);
  }

  return result;
}

function signedNet(entry: GroupNetEntry): number {
  return entry.side === "sell" ? entry.net : -entry.net;
}

function addEntryByDate(byDate: Map<string, GroupNetEntry>, entry: GroupNetEntry): void {
  const existing = byDate.get(entry.date);
  const net = (existing ? signedNet(existing) : 0) + signedNet(entry);
  if (Math.abs(net) < THRESHOLD_USD) {
    byDate.delete(entry.date);
    return;
  }
  byDate.set(entry.date, {
    date: entry.date,
    side: net > 0 ? "sell" : "buy",
    net: Math.abs(net),
    breakdown: existing ? [...existing.breakdown, ...entry.breakdown] : entry.breakdown,
  });
}

// ── Group value series (for the header total-holdings display) ───────────

type GroupValuePoint = {
  date: string;
  ts: number;
  value: number;
  constituents: { ticker: string; value: number }[];
};

/**
 * Sum constituent tickers' daily `value` into a per-date $ series.
 * Used for the header's "Holdings $X" display — not plotted on the chart.
 */
export function buildGroupValueSeries(
  dailyTickers: DailyTicker[],
  groupTickers: string[],
): GroupValuePoint[] {
  const set = new Set(groupTickers);
  const byDate = new Map<string, { value: number; parts: { ticker: string; value: number }[] }>();
  for (const dt of dailyTickers) {
    if (!set.has(dt.ticker)) continue;
    const e = byDate.get(dt.date);
    const part = { ticker: dt.ticker, value: dt.value };
    if (e) { e.value += dt.value; e.parts.push(part); }
    else byDate.set(dt.date, { value: dt.value, parts: [part] });
  }
  return [...byDate.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, { value, parts }]) => ({
      date,
      ts: parseLocalDate(date).getTime(),
      value,
      constituents: parts,
    }));
}
