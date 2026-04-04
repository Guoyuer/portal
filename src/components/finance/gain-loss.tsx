import type { ReportData } from "@/lib/types";
import { fmtCurrency, fmtPct } from "@/lib/format";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { SectionHeader, SectionBody } from "@/components/finance/shared";

export function GainLoss({ report: r }: { report: ReportData }) {
  const allHoldings = [
    ...r.equityCategories.flatMap((c) =>
      c.subtypes.flatMap((s) => s.holdings)
    ),
    ...r.nonEquityCategories.flatMap((c) => c.holdings),
  ].filter((h) => h.costBasis > 0);

  if (allHoldings.length === 0) return null;

  const sorted = [...allHoldings].sort((a, b) => b.gainLoss - a.gainLoss);
  const totalCost = sorted.reduce((s, h) => s + h.costBasis, 0);
  const totalGain = sorted.reduce((s, h) => s + h.gainLoss, 0);

  return (
    <section>
      <SectionHeader>Unrealized Gain/Loss</SectionHeader>
      <SectionBody>
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Ticker</TableHead>
                <TableHead className="text-right">Value</TableHead>
                <TableHead className="text-right hidden sm:table-cell">Cost Basis</TableHead>
                <TableHead className="text-right">Gain/Loss</TableHead>
                <TableHead className="text-right">%</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sorted.map((h) => (
                <TableRow key={h.ticker} className="even:bg-muted/50">
                  <TableCell className="font-mono">{h.ticker}</TableCell>
                  <TableCell className="text-right">{fmtCurrency(h.value)}</TableCell>
                  <TableCell className="text-right hidden sm:table-cell">{fmtCurrency(h.costBasis)}</TableCell>
                  <TableCell className={`text-right ${h.gainLoss >= 0 ? "text-green-600" : "text-red-500"}`}>
                    {fmtCurrency(h.gainLoss)}
                  </TableCell>
                  <TableCell className={`text-right ${h.gainLossPct >= 0 ? "text-green-600" : "text-red-500"}`}>
                    {fmtPct(h.gainLossPct)}
                  </TableCell>
                </TableRow>
              ))}
              <TableRow className="font-bold border-t-2 border-b-2 border-foreground/20">
                <TableCell>Total</TableCell>
                <TableCell className="text-right">{fmtCurrency(sorted.reduce((s, h) => s + h.value, 0))}</TableCell>
                <TableCell className="text-right hidden sm:table-cell">{fmtCurrency(totalCost)}</TableCell>
                <TableCell className={`text-right ${totalGain >= 0 ? "text-green-600" : "text-red-500"}`}>
                  {fmtCurrency(totalGain)}
                </TableCell>
                <TableCell className={`text-right ${totalGain >= 0 ? "text-green-600" : "text-red-500"}`}>
                  {totalCost > 0 ? fmtPct(totalGain / totalCost * 100) : "\u2014"}
                </TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </div>
      </SectionBody>
    </section>
  );
}
