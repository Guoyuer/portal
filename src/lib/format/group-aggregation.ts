// ── Transaction classifier + group aggregation (pure data layer) ────────
// Classifies Fidelity transactions into a higher-level taxonomy so UI
// code doesn't have to ad-hoc match action strings. Group aggregation
// (added in a later task) uses this taxonomy to decide which txns count
// toward the group net.

import type { FidelityTxn } from "@/lib/schemas";
import { groupOfTicker } from "@/lib/config/equivalent-groups";

export type TxnType = "REAL" | "REINVEST" | "SPLIT" | "ROLLOVER" | "OTHER";

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
const WINDOW_DAYS = 2;
const THRESHOLD_USD = 50;

export type GroupNetEntry = {
  date: string;
  side: "buy" | "sell";
  net: number;
  breakdown: { symbol: string; signed: number }[];
};

type Real = { date: string; ts: number; symbol: string; side: "buy" | "sell"; amount: number };

function parseIso(iso: string): number {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).getTime();
}

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
      ts: parseIso(t.runDate),
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
