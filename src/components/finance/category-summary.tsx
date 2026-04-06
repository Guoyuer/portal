"use client";

import { Fragment, useState } from "react";
import type { ReportData, CategoryData, HoldingData } from "@/lib/types";
import { fmtCurrency, fmtCurrencyShort, fmtPct } from "@/lib/format";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { DeviationCell, SectionHeader, SectionBody, TOTAL_ROW_CLASS } from "@/components/finance/shared";
import { AllocationDonut } from "@/components/finance/charts";
import { tooltipStyle } from "@/lib/chart-styles";
import { useIsDark } from "@/lib/hooks";

function HoldingsList({ holdings }: { holdings: HoldingData[] }) {
  return (
    <div className="grid grid-cols-[auto_auto] gap-x-3 gap-y-0">
      {holdings
        .sort((a, b) => b.value - a.value)
        .map((h) => (
          <Fragment key={h.ticker}>
            <span className="font-mono">{h.ticker}</span>
            <span className="text-right text-muted-foreground">{fmtCurrencyShort(h.value)}</span>
          </Fragment>
        ))}
    </div>
  );
}

function SubtypeTooltip({ holdings, children }: { holdings: HoldingData[]; children: React.ReactNode }) {
  const [show, setShow] = useState(false);
  const isDark = useIsDark();
  if (holdings.length === 0) return <>{children}</>;

  return (
    <span className="relative" onMouseEnter={() => setShow(true)} onMouseLeave={() => setShow(false)}>
      {children}
      {show && (
        <div className="absolute left-0 top-full z-50 mt-1 w-max max-w-xs text-xs" style={tooltipStyle(isDark)}>
          <HoldingsList holdings={holdings} />
        </div>
      )}
    </span>
  );
}

function HoldingsTooltip({ cat, children }: { cat: CategoryData; children: React.ReactNode }) {
  const [show, setShow] = useState(false);
  const isDark = useIsDark();
  const hasHoldings = cat.holdings.length > 0 || cat.subtypes.some((s) => s.holdings.length > 0);
  if (!hasHoldings) return <>{children}</>;

  return (
    <div className="relative" onMouseEnter={() => setShow(true)} onMouseLeave={() => setShow(false)}>
      {children}
      {show && (
        <div className="absolute left-0 top-full z-50 mt-1 w-max max-w-sm text-xs" style={tooltipStyle(isDark)}>
          {cat.subtypes.length > 0
            ? cat.subtypes.map((st) => (
                <div key={st.name} className="mb-1.5 last:mb-0">
                  <p className="font-semibold text-foreground/70 mb-0.5">{st.name}</p>
                  <HoldingsList holdings={st.holdings} />
                </div>
              ))
            : <HoldingsList holdings={cat.holdings} />
          }
        </div>
      )}
    </div>
  );
}

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
        <div className="flex-1 min-w-0 overflow-x-auto scrollbar-none">
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
              <Fragment key={cat.name}>
                <TableRow className="hover:bg-white/10 dark:hover:bg-white/5 transition-colors">
                  <TableCell className="font-medium">
                    <HoldingsTooltip cat={cat}>{cat.name}</HoldingsTooltip>
                  </TableCell>
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
                    className="hover:bg-white/10 dark:hover:bg-white/5 transition-colors"
                  >
                    <TableCell className="text-muted-foreground pl-6">
                      <SubtypeTooltip holdings={sub.holdings}>
                        <em>{sub.name}</em>
                      </SubtypeTooltip>
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
              </Fragment>
            ))}

            {/* Non-Equity group header */}
            <TableRow className="bg-white/5 dark:bg-white/3">
              <TableCell
                colSpan={5}
                className="font-semibold text-muted-foreground"
              >
                Non-Equity
              </TableCell>
            </TableRow>
            {r.nonEquityCategories.map((cat) => (
              <TableRow key={cat.name} className="hover:bg-white/10 dark:hover:bg-white/5 transition-colors">
                <TableCell className="text-muted-foreground pl-6">
                  <HoldingsTooltip cat={cat}><em>{cat.name}</em></HoldingsTooltip>
                </TableCell>
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
            <TableRow className={TOTAL_ROW_CLASS}>
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
        </div>
        <div className="lg:w-80 flex-shrink-0">
          <AllocationDonut categories={allCategories} total={totalValue} />
        </div>
        </div>
      </SectionBody>
    </section>
  );
}
