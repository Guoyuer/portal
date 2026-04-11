import type { ActivityResponse } from "@/lib/schema";
import { TickerTable } from "@/components/finance/shared";

export function PortfolioActivity({
  activity,
}: {
  activity: ActivityResponse;
}) {
  return (
    <div className="grid md:grid-cols-2 gap-6">
      <TickerTable title="Buys by Symbol" data={activity.buysBySymbol} />
      <TickerTable title="Dividends by Symbol" data={activity.dividendsBySymbol} />
    </div>
  );
}
