// ── Transaction classifier + group aggregation (pure data layer) ────────
// Classifies Fidelity transactions into a higher-level taxonomy so UI
// code doesn't have to ad-hoc match action strings. Group aggregation
// (added in a later task) uses this taxonomy to decide which txns count
// toward the group net.

import type { FidelityTxn } from "@/lib/schemas";

export type TxnType = "REAL" | "REINVEST" | "SPLIT" | "ROLLOVER" | "OTHER";

export function classifyTxn(t: FidelityTxn): TxnType {
  const a = t.actionType;
  if (a === "buy" || a === "sell") return "REAL";
  if (a === "reinvestment") return "REINVEST";
  // Fidelity encodes splits as DISTRIBUTION with price=0 and qty≠0
  if (a === "distribution" && t.price === 0 && t.quantity !== 0) return "SPLIT";
  return "OTHER";
}
