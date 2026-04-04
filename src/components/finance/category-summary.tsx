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
import { DeviationCell, SectionHeader, SectionBody } from "@/components/finance/shared";
import { AllocationDonut } from "@/components/finance/charts";

export function CategorySummary({ report: r }: { report: ReportData }) {
  const allCategories = [...r.equityCategories, ...r.nonEquityCategories];
  const totalValue = allCategories.reduce((s, c) => s + c.value, 0);
  const totalPct = allCategories.reduce((s, c) => s + c.pct, 0);
  const totalTarget = allCategories.reduce((s, c) => s + c.target, 0);
  const totalDeviation = totalPct - totalTarget;

  return (
    <section>
      <SectionHeader>Category Summary</SectionHeader>
      <SectionBody>
        <div className="flex flex-col lg:flex-row gap-6">
        <div className="flex-1 min-w-0 overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Category</TableHead>
              <TableHead className="text-right hidden sm:table-cell">Value</TableHead>
              <TableHead className="text-right">Actual</TableHead>
              <TableHead className="text-right">Target</TableHead>
              <TableHead className="text-right">Deviation</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {/* Equity categories */}
            {r.equityCategories.map((cat) => (
              <>
                <TableRow key={cat.name} className="even:bg-muted/50">
                  <TableCell className="font-medium">{cat.name}</TableCell>
                  <TableCell className="text-right hidden sm:table-cell">
                    {fmtCurrency(cat.value)}
                  </TableCell>
                  <TableCell className="text-right">
                    {fmtPct(cat.pct, false)}
                  </TableCell>
                  <TableCell className="text-right">
                    {fmtPct(cat.target, false)}
                  </TableCell>
                  <DeviationCell value={cat.deviation} />
                </TableRow>
                {cat.subtypes.map((sub) => (
                  <TableRow
                    key={`${cat.name}-${sub.name}`}
                    className="even:bg-muted/50"
                  >
                    <TableCell className="text-muted-foreground">
                      &nbsp;&nbsp;
                      <em>{sub.name}</em>
                    </TableCell>
                    <TableCell className="text-right text-muted-foreground hidden sm:table-cell">
                      {fmtCurrency(sub.value)}
                    </TableCell>
                    <TableCell className="text-right text-muted-foreground">
                      {fmtPct(sub.pct, false)}
                    </TableCell>
                    <TableCell />
                    <TableCell />
                  </TableRow>
                ))}
              </>
            ))}

            {/* Non-Equity group header */}
            <TableRow className="bg-muted">
              <TableCell
                colSpan={5}
                className="font-semibold text-muted-foreground"
              >
                Non-Equity
              </TableCell>
            </TableRow>
            {r.nonEquityCategories.map((cat) => (
              <TableRow key={cat.name} className="even:bg-muted/50">
                <TableCell className="font-medium">{cat.name}</TableCell>
                <TableCell className="text-right hidden sm:table-cell">
                  {fmtCurrency(cat.value)}
                </TableCell>
                <TableCell className="text-right">
                  {fmtPct(cat.pct, false)}
                </TableCell>
                <TableCell className="text-right">
                  {fmtPct(cat.target, false)}
                </TableCell>
                <DeviationCell value={cat.deviation} />
              </TableRow>
            ))}

            {/* Total row */}
            <TableRow className="font-bold border-t-2 border-b-2 border-foreground/20">
              <TableCell>Total</TableCell>
              <TableCell className="text-right hidden sm:table-cell">
                {fmtCurrency(totalValue)}
              </TableCell>
              <TableCell className="text-right">
                {fmtPct(totalPct, false)}
              </TableCell>
              <TableCell className="text-right">
                {fmtPct(totalTarget, false)}
              </TableCell>
              <DeviationCell value={totalDeviation} />
            </TableRow>
          </TableBody>
        </Table>
        <p className="mt-3 text-sm text-muted-foreground">
          {r.goalPct.toFixed(2)}% of {fmtCurrency(r.goal)} goal
        </p>
        </div>
        <div className="lg:w-80 flex-shrink-0">
          <AllocationDonut categories={allCategories} total={totalValue} />
        </div>
        </div>
      </SectionBody>
    </section>
  );
}
