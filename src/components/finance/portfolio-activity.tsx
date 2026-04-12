import type { ActivityResponse } from "@/lib/schema";
import { TickerTable } from "@/components/finance/shared";

export function PortfolioActivity({
  activity,
  startDate,
  endDate,
}: {
  activity: ActivityResponse;
  startDate?: string;
  endDate?: string;
}) {
  return (
    <div className="grid md:grid-cols-2 gap-6">
      <TickerTable title="Buys by Symbol" data={activity.buysBySymbol} startDate={startDate} endDate={endDate} />
      <TickerTable title="Dividends by Symbol" data={activity.dividendsBySymbol} startDate={startDate} endDate={endDate} />
    </div>
  );
}
