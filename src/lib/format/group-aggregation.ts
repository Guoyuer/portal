// ── Transaction classifier + group aggregation (pure data layer) ────────
// Classifies Fidelity transactions into a higher-level taxonomy so UI
// code doesn't have to ad-hoc match action strings. `groupNetByDate`
// consumes the taxonomy and clusters REAL txns within an equivalence
// group to surface net exposure change (vs. noise from ticker swaps).

import type { FidelityTxn, DailyTicker } from "@/lib/schemas";
import { groupOfTicker } from "@/lib/config/equivalent-groups";
import { parseLocalDate } from "@/lib/format/format";

export type TxnType = "REAL" | "REINVEST" | "SPLIT" | "OTHER";

export function classifyTxn(t: FidelityTxn): TxnType {
  const a = t.actionType;
  if (a === "buy" || a === "sell") return "REAL";
  if (a === "reinvestment") return "REINVEST";
  // Fidelity encodes splits as DISTRIBUTION with price=0 and qty≠0
  if (a === "distribution" && t.price === 0 && t.quantity !== 0) return "SPLIT";
  return "OTHER";
}

// ── Group net aggregation ────────────────────────────────────────────────

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

type Real = { date: string; ts: number; symbol: string; side: "buy" | "sell"; amount: number };

export function groupNetByDate(
  txns: FidelityTxn[],
): Map<string, Map<string, GroupNetEntry>> {
  const byGroup = new Map<string, Real[]>();
  for (const t of txns) {
    if (classifyTxn(t) !== "REAL") continue;
    const groupKey = groupOfTicker(t.symbol);
    if (!groupKey) continue;
    const side: "buy" | "sell" = t.actionType === "sell" ? "sell" : "buy";
    const entry: Real = {
      date: t.runDate,
      ts: parseLocalDate(t.runDate).getTime(),
      symbol: t.symbol,
      side,
      amount: Math.abs(t.amount),
    };
    const arr = byGroup.get(groupKey);
    if (arr) arr.push(entry);
    else byGroup.set(groupKey, [entry]);
  }

  const result = new Map<string, Map<string, GroupNetEntry>>();

  for (const [groupKey, groupTxns] of byGroup) {
    groupTxns.sort((a, b) => a.ts - b.ts);

    const clusters: Real[][] = [];
    for (const t of groupTxns) {
      const last = clusters[clusters.length - 1];
      if (last && (t.ts - last[last.length - 1].ts) <= WINDOW_DAYS * MS_PER_DAY) {
        last.push(t);
      } else {
        clusters.push([t]);
      }
    }

    const byDate = new Map<string, GroupNetEntry>();
    for (const cluster of clusters) {
      let net = 0;
      const breakdown: { symbol: string; signed: number }[] = [];
      for (const t of cluster) {
        const signed = t.side === "sell" ? t.amount : -t.amount;
        net += signed;
        breakdown.push({ symbol: t.symbol, signed });
      }
      if (Math.abs(net) < THRESHOLD_USD) continue;
      const entry: GroupNetEntry = {
        date: cluster[0].date,
        side: net > 0 ? "sell" : "buy",
        net: Math.abs(net),
        breakdown,
      };
      byDate.set(entry.date, entry);
    }

    if (byDate.size > 0) result.set(groupKey, byDate);
  }

  return result;
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
