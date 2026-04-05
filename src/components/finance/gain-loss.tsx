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
export function GainLoss({ report: r }: { report: ReportData }) {
  const allHoldings = [
    ...r.equityCategories.flatMap((c) =>
      c.subtypes.flatMap((s) => s.holdings)
    ),
    ...r.nonEquityCategories.flatMap((c) => c.holdings),
  ].filter((h) => h.costBasis > 0);

  if (allHoldings.length === 0) {
    return <p className="text-sm text-muted-foreground mt-4">No cost basis data available.</p>;
  }

  const sorted = [...allHoldings].sort((a, b) => b.gainLoss - a.gainLoss);
  const totalCost = sorted.reduce((s, h) => s + h.costBasis, 0);
  const totalGain = sorted.reduce((s, h) => s + h.gainLoss, 0);

  const TOP_N = 10;
  const showCollapse = sorted.length > TOP_N;
  const top = showCollapse ? sorted.slice(0, TOP_N) : sorted;
  const rest = showCollapse ? sorted.slice(TOP_N) : [];

  return (
    <div className="mt-6 pt-6 border-t border-border">
      <h3 className="font-semibold mb-3">Unrealized Gain/Loss</h3>
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
              {top.map((h) => (
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
              {rest.length > 0 && (
                <TableRow>
                  <TableCell colSpan={5} className="p-0">
                    <details className="group">
                      <summary className="px-2 py-1.5 text-sm text-muted-foreground cursor-pointer hover:text-foreground">
                        ... and {rest.length} more
                      </summary>
                      <table className="w-full text-sm">
                        <tbody>
                          {rest.map((h) => (
                            <tr key={h.ticker} className="border-b border-border even:bg-muted/50">
                              <td className="px-2 py-1.5 font-mono">{h.ticker}</td>
                              <td className="px-2 py-1.5 text-right">{fmtCurrency(h.value)}</td>
                              <td className="px-2 py-1.5 text-right hidden sm:table-cell">{fmtCurrency(h.costBasis)}</td>
                              <td className={`px-2 py-1.5 text-right ${h.gainLoss >= 0 ? "text-green-600" : "text-red-500"}`}>
                                {fmtCurrency(h.gainLoss)}
                              </td>
                              <td className={`px-2 py-1.5 text-right ${h.gainLossPct >= 0 ? "text-green-600" : "text-red-500"}`}>
                                {fmtPct(h.gainLossPct)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </details>
                  </TableCell>
                </TableRow>
              )}
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
    </div>
  );
}
