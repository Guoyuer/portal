import type { AnnualSummary as AnnualSummaryData } from "@/lib/types";
import { fmtCurrency } from "@/lib/format";
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
  const grossRate = data.totalIncome > 0 ? (netSaved / data.totalIncome * 100).toFixed(1) + "%" : "\u2014";

  return (
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
            <TableRow className="font-bold border-t-2 border-b-2 border-foreground/20">
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
          <span className={`font-medium ${netSaved >= 0 ? "text-green-600" : "text-red-500"}`}>
            {fmtCurrency(netSaved)}
          </span>
        </div>
        <div className="flex justify-between py-2 border-b border-border">
          <span className="text-muted-foreground">Gross Savings Rate</span>
          <span className={`font-medium ${netSaved >= 0 ? "text-green-600" : "text-red-500"}`}>
            {grossRate}
          </span>
        </div>
        <div className="flex justify-between py-2">
          <span className="text-muted-foreground">Take-home Savings Rate</span>
          <span className={`font-medium ${data.takehomeSavingsRate == null ? "" : data.takehomeSavingsRate >= 0 ? "text-green-600" : "text-red-500"}`}>
            {data.takehomeSavingsRate != null
              ? `${data.takehomeSavingsRate.toFixed(1)}%`
              : "\u2014"}
          </span>
        </div>
      </div>
    </div>
  );
}
