import { memo } from "react";
import type { ActivityResponse } from "@/lib/schema";
import { TickerTable } from "@/components/finance/shared";

// ── Convert API objects to tuple format expected by TickerTable ──────────

function toTuples(items: { symbol: string; count: number; total: number }[]): [string, number, number][] {
  return items.map((i) => [i.symbol, i.count, i.total]);
}

export const PortfolioActivity = memo(function PortfolioActivity({
  activity,
  periodLabel,
}: {
  activity: ActivityResponse;
  periodLabel?: string;
}) {
  return (
    <>
      {periodLabel && (
        <p className="text-sm text-muted-foreground mb-4">{periodLabel}</p>
      )}
      <div className="grid md:grid-cols-2 gap-6">
        <TickerTable title="Buys by Symbol" data={toTuples(activity.buysBySymbol)} />
        <TickerTable title="Dividends by Symbol" data={toTuples(activity.dividendsBySymbol)} />
      </div>
    </>
  );
});
