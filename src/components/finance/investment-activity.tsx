import type { ActivityData } from "@/lib/types";
import { fmtCurrency } from "@/lib/format";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { SectionHeader, SectionBody, TickerTable } from "@/components/finance/shared";

export function InvestmentActivity({ data }: { data: ActivityData }) {
  return (
    <section>
      <SectionHeader>Investment Activity</SectionHeader>
      <SectionBody>
        <p className="text-sm text-muted-foreground mb-4">
          {data.periodStart} &ndash; {data.periodEnd}
        </p>

        {/* Activity Summary */}
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Metric</TableHead>
              <TableHead className="text-right">Amount</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {[
              { label: "Net Cash In", amount: data.netCashIn },
              { label: "Net Deployed", amount: data.netDeployed },
              { label: "Net Passive Income", amount: data.netPassive },
              { label: "Reinvestments", amount: data.reinvestmentsTotal },
              { label: "Interest", amount: data.interestTotal },
              { label: "Foreign Tax", amount: data.foreignTaxTotal },
            ].filter((row) => row.amount !== 0).map((row) => (
              <TableRow key={row.label} className="even:bg-muted/50">
                <TableCell className="font-medium">{row.label}</TableCell>
                <TableCell
                  className={`text-right ${row.amount >= 0 ? "text-green-600" : "text-red-500"}`}
                >
                  {fmtCurrency(row.amount)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>

        {/* Buys by Ticker and Dividends by Ticker */}
        <div className="grid md:grid-cols-2 gap-6 mt-6">
          <TickerTable
            title="Buys by Symbol"
            data={data.buysBySymbol}
          />
          <TickerTable
            title="Dividends by Symbol"
            data={data.dividendsBySymbol}
          />
        </div>
      </SectionBody>
    </section>
  );
}
