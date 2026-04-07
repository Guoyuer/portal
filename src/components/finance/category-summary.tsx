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

function GlassTooltip({ content, children }: { content: React.ReactNode; children: React.ReactNode }) {
  const [show, setShow] = useState(false);
  const isDark = useIsDark();

  return (
    <span
      className="relative"
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
      onFocus={() => setShow(true)}
      onBlur={() => setShow(false)}
      tabIndex={0}
    >
      {children}
      {show && (
        <div
          className="absolute left-0 top-full z-50 mt-1 w-max max-w-sm text-xs pointer-events-none"
          role="tooltip"
          style={tooltipStyle(isDark)}
        >
          {content}
        </div>
      )}
    </span>
  );
}

function CategoryTooltip({ cat, children }: { cat: CategoryData; children: React.ReactNode }) {
  const hasHoldings = cat.holdings.length > 0 || cat.subtypes.some((s) => s.holdings.length > 0);
  if (!hasHoldings) return <>{children}</>;

  const content = cat.subtypes.length > 0
    ? cat.subtypes.map((st) => (
        <div key={st.name} className="mb-1.5 last:mb-0">
          <p className="font-semibold text-foreground/70 mb-0.5">{st.name}</p>
          <HoldingsList holdings={st.holdings} />
        </div>
      ))
    : <HoldingsList holdings={cat.holdings} />;

  return <GlassTooltip content={content}>{children}</GlassTooltip>;
}

export function CategorySummary({ report: r, title, embedded }: { report: ReportData; title: string; embedded?: boolean }) {
  const allCategories = [...r.equityCategories, ...r.nonEquityCategories];
  const totalValue = allCategories.reduce((s, c) => s + c.value, 0);
  const totalPct = allCategories.reduce((s, c) => s + c.pct, 0);
  const totalTarget = allCategories.reduce((s, c) => s + c.target, 0);
  const totalDeviation = totalPct - totalTarget;

  const inner = (
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
                    <CategoryTooltip cat={cat}>{cat.name}</CategoryTooltip>
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
                      <GlassTooltip content={<HoldingsList holdings={sub.holdings} />}>
                        <em>{sub.name}</em>
                      </GlassTooltip>
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

            {/* Non-Equity group — parent row with aggregated values */}
            {(() => {
              const neValue = r.nonEquityCategories.reduce((s, c) => s + c.value, 0);
              const nePct = r.nonEquityCategories.reduce((s, c) => s + c.pct, 0);
              const neTarget = r.nonEquityCategories.reduce((s, c) => s + c.target, 0);
              const neDeviation = nePct - neTarget;
              return (
                <Fragment>
                  <TableRow className="hover:bg-white/10 dark:hover:bg-white/5 transition-colors">
                    <TableCell className="font-medium">Non-Equity</TableCell>
                    <TableCell className="text-right hidden sm:table-cell">
                      {fmtCurrency(neValue)}
                    </TableCell>
                    <TableCell className="text-right">
                      {fmtPct(nePct, false)}
                    </TableCell>
                    <TableCell className="text-right">
                      {fmtPct(neTarget, false)}
                    </TableCell>
                    <DeviationCell value={neDeviation} />
                  </TableRow>
                  {r.nonEquityCategories.map((cat) => (
                    <TableRow
                      key={cat.name}
                      className="hover:bg-white/10 dark:hover:bg-white/5 transition-colors"
                    >
                      <TableCell className="text-muted-foreground pl-6">
                        <CategoryTooltip cat={cat}><em>{cat.name}</em></CategoryTooltip>
                      </TableCell>
                      <TableCell className="text-right text-muted-foreground hidden sm:table-cell">
                        {fmtCurrency(cat.value)}
                      </TableCell>
                      <TableCell className="text-right text-muted-foreground">
                        {fmtPct(cat.pct, false)}
                      </TableCell>
                      <TableCell />
                      <TableCell />
                    </TableRow>
                  ))}
                </Fragment>
              );
            })()}

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
  );

  if (embedded) return inner;

  return (
    <section>
      <SectionHeader>{title}</SectionHeader>
      <SectionBody>{inner}</SectionBody>
    </section>
  );
}
