"use client";

// ── Fixed-position hover tooltip for cluster markers ────────────────────
// Used by both TickerChartDialog (shows Close price + qty@price) and
// GroupChartDialog (shows group Value + net buy/sell breakdown).

import { tooltipStyle } from "@/lib/format/chart-styles";
import { fmtCurrency, fmtDateMedium, fmtQty } from "@/lib/format/format";
import { BUY_COLOR, SELL_COLOR } from "@/lib/format/chart-colors";
import type { Cluster } from "@/lib/format/ticker-data";
import { tsToIsoLocal } from "@/lib/format/ticker-data";
import type { HoverState } from "@/lib/hooks/use-hover-state";

function TickerAmountLine({ cluster }: { cluster: Cluster }) {
  return (
    <>
      : {fmtQty(cluster.qty)} @ {fmtCurrency(cluster.price)} = {fmtCurrency(cluster.amount)}
      {cluster.count > 1 && (
        <> <span style={{ opacity: 0.7 }}>(around {fmtDateMedium(tsToIsoLocal(cluster.ts))})</span></>
      )}
    </>
  );
}

function GroupBreakdown({ breakdown }: { breakdown: NonNullable<Cluster["breakdown"]> }) {
  return (
    <>
      {breakdown.map((b) => (
        <p key={b.symbol} style={{ margin: 0, fontSize: 12, fontFamily: "monospace" }}>
          {b.symbol}{"  "}{b.signed >= 0 ? "−" : "+"}{fmtCurrency(Math.abs(b.signed))}
        </p>
      ))}
    </>
  );
}

export function MarkerHoverPanel({ hover, isDark, valueLabel }: { hover: HoverState; isDark: boolean; valueLabel?: string }) {
  const color = hover.side === "buy" ? BUY_COLOR : SELL_COLOR;
  // Group clusters have qty=0 and price=0; show breakdown instead.
  const isGroupCluster = hover.cluster.qty === 0 && hover.cluster.price === 0;
  const showBreakdown = isGroupCluster && hover.cluster.breakdown && hover.cluster.breakdown.length > 1;

  return (
    <div
      style={{
        ...tooltipStyle(isDark),
        position: "fixed",
        top: hover.y + 14,
        left: hover.x + 14,
        pointerEvents: "none",
        zIndex: 100,
      }}
    >
      <p style={{ fontWeight: 600, marginBottom: 2 }}>{fmtDateMedium(hover.dayIso)}</p>
      {hover.close > 0 && (
        <p style={{ margin: 0 }}>{valueLabel ?? "Close"}: {fmtCurrency(hover.close)}</p>
      )}
      <p style={{ color, margin: 0 }}>
        {hover.side === "buy" ? "Buy" : "Sell"}
        {hover.cluster.count > 1 ? ` ×${hover.cluster.count}` : ""}
        {isGroupCluster
          ? `: ${fmtCurrency(hover.cluster.amount)}`
          : <TickerAmountLine cluster={hover.cluster} />}
      </p>
      {showBreakdown && <GroupBreakdown breakdown={hover.cluster.breakdown!} />}
    </div>
  );
}
