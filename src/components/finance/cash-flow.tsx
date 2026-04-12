import type { CashflowResponse } from "@/lib/computed-types";
import { fmtCurrency } from "@/lib/format";
import { MAJOR_EXPENSE_THRESHOLD } from "@/lib/thresholds";
import { TOTAL_ROW_CLASS } from "@/components/finance/ticker-table";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";


/** Merge income items below $10 into "Other" */
function consolidateSmallItems(items: CashflowResponse["incomeItems"], threshold = 10) {
  const big = items.filter((i) => i.amount >= threshold);
  const small = items.filter((i) => i.amount < threshold);
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
                <TableHead className="text-right">Count</TableHead>
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

// ── Micro visuals ───────────────────────────────────────────────────────

function MiniProgress({ pct, color, target }: { pct: number; color: string; target?: number }) {
  return (
    <div className="relative h-[3px] w-14 rounded-full bg-foreground/8 overflow-visible">
      <div className="h-full rounded-full" style={{ width: `${Math.min(pct, 100)}%`, background: color }} />
      {target != null && (
        <div className="absolute top-[-1px] bottom-[-1px] w-[1px] bg-foreground/30" style={{ left: `${target}%` }} />
      )}
    </div>
  );
}

function MiniDonut({ pct, color }: { pct: number; color: string }) {
  const RADIUS = 6;
  const CIRCUMFERENCE = 2 * Math.PI * RADIUS;
  const filledArc = (Math.min(pct, 100) / 100) * CIRCUMFERENCE;
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" className="shrink-0">
      <circle cx="8" cy="8" r={RADIUS} fill="none" stroke="currentColor" strokeWidth="2" className="text-foreground/8" />
      <circle
        cx="8" cy="8" r={RADIUS} fill="none" stroke={color} strokeWidth="2"
        strokeDasharray={`${filledArc} ${CIRCUMFERENCE}`}
        strokeLinecap="round"
        transform="rotate(-90 8 8)"
      />
    </svg>
  );
}

// ── Glow line helper ────────────────────────────────────────────────────

function GlowEdge({ color }: { color: string }) {
  const grad = `linear-gradient(90deg, transparent, ${color} 30%, ${color} 70%, transparent)`;
  return (
    <>
      <div className="absolute bottom-0 left-2 right-2 h-px" style={{ background: grad, opacity: 0.5 }} />
      <div className="absolute -bottom-px left-2 right-2 h-1 blur-[4px]" style={{ background: grad, opacity: 0.15 }} />
    </>
  );
}

// ── Integrated stat bar (rendered as chart header in page.tsx) ───────────

export function CashFlowStatBar({
  data,
  invested,
  period,
}: {
  data: CashflowResponse;
  invested: number;
  period?: string;
}) {
  const savingsRate = Math.round(data.savingsRate);
  const investRatio = data.netCashflow > 0 ? invested / data.netCashflow : 0;
  const ccPct = data.totalExpenses > 0 ? Math.round(data.ccPayments / data.totalExpenses * 100) : 0;

  return (
    <div className="relative">
      {/* Period label */}
      {period && (
        <p className="px-4 pt-2.5 pb-0 text-[9px] text-foreground/30 uppercase tracking-widest">
          {period} Summary
        </p>
      )}
      <div className="flex flex-col sm:flex-row">
        {/* Net Savings — color-synced with chart Savings bars (cyan) */}
        <div className="relative flex-1 px-4 py-2.5">
          <p className="text-[9px] text-foreground/40 uppercase tracking-widest">Net Savings</p>
          <div className="flex items-center gap-1.5 mt-0.5">
            <span className="text-sm font-bold tabular-nums text-cyan-600 dark:text-cyan-400">
              {fmtCurrency(data.netCashflow)}
            </span>
            <span className="text-foreground/15">&mdash;</span>
            <MiniProgress pct={savingsRate} color="#22d3ee" target={80} />
            <span className="text-[10px] tabular-nums" style={{ color: "rgba(34, 211, 238, 0.55)" }}>{savingsRate}%</span>
          </div>
          <GlowEdge color="#22d3ee" />
        </div>

        <div className="h-px sm:h-auto sm:w-px bg-gradient-to-r sm:bg-gradient-to-b from-transparent via-foreground/8 to-transparent" />

        {/* Invested */}
        <div className="relative flex-1 px-4 py-2.5">
          <p className="text-[9px] text-foreground/40 uppercase tracking-widest">Invested</p>
          <div className="flex items-center gap-1.5 mt-0.5">
            <span className="text-sm font-bold tabular-nums text-blue-600 dark:text-blue-400">
              {fmtCurrency(invested)}
            </span>
            <span className="text-foreground/15">&mdash;</span>
            <MiniProgress pct={Math.min(investRatio, 3) / 3 * 100} color="#3b82f6" />
            <span className="text-[10px] tabular-nums" style={{ color: "rgba(59, 130, 246, 0.55)" }}>
              {investRatio > 0 ? `${investRatio.toFixed(1)}x` : "—"}
            </span>
          </div>
          <GlowEdge color="#3b82f6" />
        </div>

        <div className="h-px sm:h-auto sm:w-px bg-gradient-to-r sm:bg-gradient-to-b from-transparent via-foreground/8 to-transparent" />

        {/* CC Payments — color-synced with chart Expenses bars (pink/red) */}
        <div className="relative flex-1 px-4 py-2.5">
          <p className="text-[9px] text-foreground/40 uppercase tracking-widest">CC Payments</p>
          <div className="flex items-center gap-1.5 mt-0.5">
            <span className="text-sm font-bold tabular-nums text-rose-500 dark:text-rose-400">
              {fmtCurrency(data.ccPayments)}
            </span>
            <span className="text-foreground/15">&mdash;</span>
            <MiniDonut pct={ccPct} color="#fb7185" />
            <span className="text-[10px] tabular-nums" style={{ color: "rgba(251, 113, 133, 0.55)" }}>{ccPct}%</span>
          </div>
          <GlowEdge color="#fb7185" />
        </div>
      </div>
    </div>
  );
}
