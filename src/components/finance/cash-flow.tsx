import type { CashflowResponse } from "@/lib/computed-types";
import { fmtCurrency } from "@/lib/format";
import { MAJOR_EXPENSE_THRESHOLD, SMALL_INCOME_THRESHOLD } from "@/lib/thresholds";
import { TOTAL_ROW_CLASS } from "@/components/finance/ticker-table";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";


/** Merge income items below SMALL_INCOME_THRESHOLD into "Other" */
function consolidateSmallItems(items: CashflowResponse["incomeItems"]) {
  const big = items.filter((i) => i.amount >= SMALL_INCOME_THRESHOLD);
  const small = items.filter((i) => i.amount < SMALL_INCOME_THRESHOLD);
  if (small.length === 0) return big;
  const extraAmt = small.reduce((s, i) => s + i.amount, 0);
  const extraCnt = small.reduce((s, i) => s + i.count, 0);
  const existing = big.find((i) => i.category === "Other");
  if (existing) {
    return big.map((i) => i === existing ? { ...i, amount: i.amount + extraAmt, count: i.count + extraCnt } : i);
  }
  return [...big, { category: "Other", amount: extraAmt, count: extraCnt }];
}

export function CashFlow({ data }: { data: CashflowResponse }) {
  const major = data.expenseItems.filter(
    (i) => i.amount >= MAJOR_EXPENSE_THRESHOLD
  );
  const minor = data.expenseItems.filter(
    (i) => i.amount < MAJOR_EXPENSE_THRESHOLD
  );
  const minorTotal = minor.reduce((s, i) => s + i.amount, 0);
  const incomeItems = consolidateSmallItems(data.incomeItems);

  return (
    <>
      <div className="grid md:grid-cols-2 gap-6 items-start">
        {/* Income */}
        <div data-testid="income-table">
          <h3 className="font-semibold mb-2 text-foreground">Income</h3>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Category</TableHead>
                <TableHead className="text-right">Txns</TableHead>
                <TableHead className="text-right">Amount</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {incomeItems.map((item) => (
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
              <TableRow className={TOTAL_ROW_CLASS}>
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
        <div data-testid="expense-table">
          <h3 className="font-semibold mb-2 text-foreground">Expenses</h3>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Category</TableHead>
                <TableHead className="text-right">Txns</TableHead>
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
              <TableRow className={TOTAL_ROW_CLASS}>
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

    </>
  );
}

