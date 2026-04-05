import type { CashFlowData } from "@/lib/types";
import { fmtCurrency } from "@/lib/format";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { SectionHeader, SectionBody } from "@/components/finance/shared";

const MAJOR_EXPENSE_THRESHOLD = 200;

export function CashFlow({ data }: { data: CashFlowData }) {
  const major = data.expenseItems.filter(
    (i) => i.amount >= MAJOR_EXPENSE_THRESHOLD
  );
  const minor = data.expenseItems.filter(
    (i) => i.amount < MAJOR_EXPENSE_THRESHOLD
  );
  const minorTotal = minor.reduce((s, i) => s + i.amount, 0);

  return (
    <section>
      <SectionHeader>Cash Flow &mdash; {data.period}</SectionHeader>
      <SectionBody>
        <div className="grid md:grid-cols-2 gap-6">
          {/* Income */}
          <div>
            <h3 className="font-semibold mb-2">Income</h3>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Category</TableHead>
                  <TableHead className="text-right">Count</TableHead>
                  <TableHead className="text-right">Amount</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.incomeItems.map((item) => (
                  <TableRow
                    key={item.category}
                    className="even:bg-muted/50"
                  >
                    <TableCell>{item.category}</TableCell>
                    <TableCell className="text-right">
                      {item.count}
                    </TableCell>
                    <TableCell className="text-right">
                      {fmtCurrency(item.amount)}
                    </TableCell>
                  </TableRow>
                ))}
                <TableRow className="font-bold border-t-2 border-b-2 border-foreground/20">
                  <TableCell>Total</TableCell>
                  <TableCell />
                  <TableCell className="text-right">
                    {fmtCurrency(data.totalIncome)}
                  </TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </div>

          {/* Expenses */}
          <div>
            <h3 className="font-semibold mb-2">Expenses</h3>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Category</TableHead>
                  <TableHead className="text-right">Count</TableHead>
                  <TableHead className="text-right">Amount</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {major.map((item) => (
                  <TableRow
                    key={item.category}
                    className="even:bg-muted/50"
                  >
                    <TableCell>{item.category}</TableCell>
                    <TableCell className="text-right">
                      {item.count}
                    </TableCell>
                    <TableCell className="text-right">
                      {fmtCurrency(item.amount)}
                    </TableCell>
                  </TableRow>
                ))}
                {minor.length > 0 && (
                  <TableRow>
                    <TableCell colSpan={3} className="p-0">
                      <details className="group">
                        <summary className="px-2 py-1.5 text-sm text-muted-foreground cursor-pointer hover:text-foreground">
                          ... and {minor.length} more ({fmtCurrency(minorTotal)})
                        </summary>
                        <table className="w-full text-sm">
                          <tbody>
                            {minor.map((item) => (
                              <tr
                                key={item.category}
                                className="border-b border-border even:bg-muted/50"
                              >
                                <td className="px-2 py-1.5 text-muted-foreground">
                                  {item.category}
                                </td>
                                <td className="px-2 py-1.5 text-right text-muted-foreground">
                                  {item.count}
                                </td>
                                <td className="px-2 py-1.5 text-right text-muted-foreground">
                                  {fmtCurrency(item.amount)}
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
                  <TableCell />
                  <TableCell className="text-right">
                    {fmtCurrency(data.totalExpenses)}
                  </TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </div>
        </div>

        {/* Cash Flow Summary */}
        <div className="mt-6">
          <h3 className="font-semibold mb-2">Summary</h3>
          <Table>
            <TableBody>
              <TableRow className="even:bg-muted/50">
                <TableCell className="font-medium">Net Cash Flow</TableCell>
                <TableCell className={`text-right font-semibold ${data.netCashflow >= 0 ? "text-green-600" : "text-red-500"}`}>
                  {fmtCurrency(data.netCashflow)}
                </TableCell>
              </TableRow>
              <TableRow className="even:bg-muted/50">
                <TableCell className="font-medium">Invested</TableCell>
                <TableCell className="text-right">
                  {fmtCurrency(data.invested)}
                </TableCell>
              </TableRow>
              <TableRow className="even:bg-muted/50">
                <TableCell className="font-medium">CC Payments</TableCell>
                <TableCell className="text-right">
                  {fmtCurrency(data.creditCardPayments)}
                </TableCell>
              </TableRow>
              <TableRow className="even:bg-muted/50">
                <TableCell className="font-medium">
                  Gross Savings Rate
                </TableCell>
                <TableCell className="text-right">
                  <Badge variant="secondary">
                    {data.savingsRate.toFixed(1)}%
                  </Badge>
                </TableCell>
              </TableRow>
              <TableRow className="even:bg-muted/50">
                <TableCell className="font-medium">
                  Take-home Savings Rate
                </TableCell>
                <TableCell className="text-right">
                  <Badge variant="secondary">
                    {data.takehomeSavingsRate.toFixed(1)}%
                  </Badge>
                </TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </div>
      </SectionBody>
    </section>
  );
}
