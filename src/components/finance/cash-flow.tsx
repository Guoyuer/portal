import type { CashflowResponse } from "@/lib/compute/computed-types";
import { fmtCurrency } from "@/lib/format/format";
import { MAJOR_EXPENSE_THRESHOLD, SMALL_INCOME_THRESHOLD } from "@/lib/format/thresholds";
import { TOTAL_ROW_CLASS } from "@/components/finance/ticker-table";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

type CashFlowItem = CashflowResponse["incomeItems"][number];

function CashFlowRow({ item }: { item: CashFlowItem }) {
  return (
    <TableRow className="even:bg-muted/50">
      <TableCell>{item.category}</TableCell>
      <TableCell className="text-right">{item.count}</TableCell>
      <TableCell className="text-right">{fmtCurrency(item.amount)}</TableCell>
    </TableRow>
  );
}

function CompactCashFlowRow({ item }: { item: CashFlowItem }) {
  return (
    <tr className="border-b border-border even:bg-muted/50">
      <td className="px-2 py-1.5 text-muted-foreground">{item.category}</td>
      <td className="px-2 py-1.5 text-right text-muted-foreground">{item.count}</td>
      <td className="px-2 py-1.5 text-right text-muted-foreground">{fmtCurrency(item.amount)}</td>
    </tr>
  );
}

function TotalRow({ total }: { total: number }) {
  return (
    <TableRow className={TOTAL_ROW_CLASS}>
      <TableCell>Total</TableCell>
      <TableCell />
      <TableCell className="text-right">{fmtCurrency(total)}</TableCell>
    </TableRow>
  );
}

function CashFlowTable({
  title,
  testId,
  items,
  total,
  collapsedItems = [],
  collapsedTotal = 0,
}: {
  title: string;
  testId: string;
  items: CashFlowItem[];
  total: number;
  collapsedItems?: CashFlowItem[];
  collapsedTotal?: number;
}) {
  return (
    <div data-testid={testId}>
      <h3 className="font-semibold mb-2 text-foreground">{title}</h3>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Category</TableHead>
            <TableHead className="text-right">Txns</TableHead>
            <TableHead className="text-right">Amount</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((item) => <CashFlowRow key={item.category} item={item} />)}
          {collapsedItems.length > 0 && (
            <TableRow>
              <TableCell colSpan={3} className="p-0">
                <details className="group">
                  <summary className="px-2 py-1.5 text-sm text-muted-foreground cursor-pointer hover:text-foreground">
                    ... and {collapsedItems.length} more ({fmtCurrency(collapsedTotal)})
                  </summary>
                  <table className="w-full text-sm">
                    <tbody>
                      {collapsedItems.map((item) => <CompactCashFlowRow key={item.category} item={item} />)}
                    </tbody>
                  </table>
                </details>
              </TableCell>
            </TableRow>
          )}
          <TotalRow total={total} />
        </TableBody>
      </Table>
    </div>
  );
}

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
    <div className="grid md:grid-cols-2 gap-6 items-start">
      <CashFlowTable title="Income" testId="income-table" items={incomeItems} total={data.totalIncome} />
      <CashFlowTable
        title="Expenses"
        testId="expense-table"
        items={major}
        total={data.totalExpenses}
        collapsedItems={minor}
        collapsedTotal={minorTotal}
      />
    </div>
  );
}
