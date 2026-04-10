import type { AnnualSummary as AnnualSummaryData } from "@/lib/schema";
import { fmtCurrency } from "@/lib/format";
import { valueColor } from "@/lib/style-helpers";
import { TOTAL_ROW_CLASS } from "@/components/finance/shared";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export function AnnualSummary({ data }: { data: AnnualSummaryData }) {
  const netSaved = data.totalIncome - data.totalExpenses;
  const grossSavingsRate = data.totalIncome > 0
    ? (netSaved / data.totalIncome * 100)
    : null;

  return (
    <>
      <div className="grid md:grid-cols-2 gap-6">
        <div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Category</TableHead>
                <TableHead className="text-right">Count</TableHead>
                <TableHead className="text-right">Amount</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.expenseByCategory.map((item) => (
                <TableRow key={item.category} className="even:bg-muted/50">
                  <TableCell>{item.category}</TableCell>
                  <TableCell className="text-right">{item.count}</TableCell>
                  <TableCell className="text-right">{fmtCurrency(item.amount)}</TableCell>
                </TableRow>
              ))}
              <TableRow className={TOTAL_ROW_CLASS}>
                <TableCell>Total Expenses</TableCell>
                <TableCell />
                <TableCell className="text-right">{fmtCurrency(data.totalExpenses)}</TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </div>
        <div className="space-y-3">
          <div className="flex justify-between py-2 border-b border-border">
            <span className="text-muted-foreground">Total Income</span>
            <span className="font-medium">{fmtCurrency(data.totalIncome)}</span>
          </div>
          <div className="flex justify-between py-2 border-b border-border">
            <span className="text-muted-foreground">Net Saved</span>
            <span className={`font-medium ${valueColor(netSaved)}`}>
              {fmtCurrency(netSaved)}
            </span>
          </div>
          <div className="flex justify-between py-2 border-b border-border">
            <span className="text-muted-foreground">Gross Savings Rate</span>
            <span className={`font-medium ${grossSavingsRate != null ? valueColor(grossSavingsRate) : ""}`}>
              {grossSavingsRate != null
                ? `${grossSavingsRate.toFixed(1)}%`
                : "\u2014"}
            </span>
          </div>
          <div className="flex justify-between py-2">
            <span className="text-muted-foreground">Take-home Savings Rate</span>
            <span className={`font-medium ${data.takehomeSavingsRate != null ? valueColor(data.takehomeSavingsRate) : ""}`}>
              {data.takehomeSavingsRate != null
                ? `${data.takehomeSavingsRate.toFixed(1)}%`
                : "\u2014"}
            </span>
          </div>
        </div>
      </div>
    </>
  );
}
