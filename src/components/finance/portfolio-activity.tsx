import type { ActivityData, ReconciliationData } from "@/lib/types";
import { fmtCurrency } from "@/lib/format";
import { valueColor } from "@/lib/style-helpers";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { TickerTable } from "@/components/finance/shared";

export function PortfolioActivity({
  activity,
  reconciliation,
}: {
  activity: ActivityData;
  reconciliation?: ReconciliationData | null;
}) {
  const fid = reconciliation?.fidelity?.details as Record<string, number> | undefined;
  const marketMovement = fid?.market_movement;

  const rows: { label: string; amount: number; indent?: boolean }[] = [
    { label: "Net Cash In", amount: activity.netCashIn },
    { label: "Net Deployed", amount: activity.netDeployed },
    ...(marketMovement != null ? [{ label: "Market Movement", amount: marketMovement }] : []),
    { label: "Net Passive Income", amount: activity.netPassive },
    { label: "Reinvestments", amount: activity.reinvestmentsTotal, indent: true },
    { label: "Interest", amount: activity.interestTotal, indent: true },
    { label: "Foreign Tax", amount: activity.foreignTaxTotal },
  ].filter((r) => r.amount !== 0);

  return (
    <>
      <p className="text-sm text-muted-foreground mb-4">
        {activity.periodStart} &ndash; {activity.periodEnd}
      </p>
      {reconciliation && (
        <div className="flex flex-wrap items-center gap-2 mb-4 py-2 px-3 rounded-md bg-muted text-sm">
          <span>{fmtCurrency(reconciliation.totalStart)}</span>
          <span className="text-muted-foreground">&rarr;</span>
          <span>{fmtCurrency(reconciliation.totalEnd)}</span>
          <span className={`font-semibold ${valueColor(reconciliation.totalChange)}`}>
            ({fmtCurrency(reconciliation.totalChange)})
          </span>
        </div>
      )}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Metric</TableHead>
            <TableHead className="text-right">Amount</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.label} className="even:bg-muted/50">
              <TableCell className={row.indent ? "pl-6 text-muted-foreground" : "font-medium"}>
                {row.label}
              </TableCell>
              <TableCell
                className={`text-right ${row.indent ? "text-muted-foreground" : valueColor(row.amount)}`}
              >
                {fmtCurrency(row.amount)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <div className="grid md:grid-cols-2 gap-6 mt-6">
        <TickerTable title="Buys by Symbol" data={activity.buysBySymbol} />
        <TickerTable title="Dividends by Symbol" data={activity.dividendsBySymbol} />
      </div>
    </>
  );
}
