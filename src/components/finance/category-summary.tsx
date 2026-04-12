"use client";

import { Fragment, useState } from "react";
import type { ApiCategory, ApiTicker, CategoryData } from "@/lib/computed-types";
import { fmtCurrency, fmtCurrencyShort, fmtPct } from "@/lib/format";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { SectionHeader, SectionBody } from "@/components/finance/section";
import { DeviationCell, TOTAL_ROW_CLASS } from "@/components/finance/ticker-table";
import { AllocationDonut } from "@/components/finance/charts";

// ── Equity categories for classification ──────────────────────────────────

const EQUITY_CATEGORIES = new Set(["US Equity", "Non-US Equity", "Crypto"]);

// ── Group tickers into category → subtype → tickers ──────────────────────

interface GroupedCategory {
  name: string;
  value: number;
  pct: number;
  target: number;
  deviation: number;
  isEquity: boolean;
  subtypes: { name: string; tickers: ApiTicker[]; value: number; pct: number }[];
}

function groupTickers(categories: ApiCategory[], tickers: ApiTicker[], total: number): GroupedCategory[] {
  const tickersByCategory: Record<string, Record<string, ApiTicker[]>> = {};
  for (const t of tickers) {
    if (!tickersByCategory[t.category]) tickersByCategory[t.category] = {};
    const sub = t.subtype || "(other)";
    if (!tickersByCategory[t.category][sub]) tickersByCategory[t.category][sub] = [];
    tickersByCategory[t.category][sub].push(t);
  }

  return categories.map((cat) => {
    const subs = tickersByCategory[cat.name] ?? {};
    const subtypes = Object.entries(subs).map(([name, ts]) => {
      const subValue = ts.reduce((s, t) => s + t.value, 0);
      return {
        name,
        tickers: ts,
        value: subValue,
        pct: total > 0 ? (subValue / total) * 100 : 0,
      };
    });
    return {
      name: cat.name,
      value: cat.value,
      pct: cat.pct,
      target: cat.target,
      deviation: cat.deviation,
      isEquity: EQUITY_CATEGORIES.has(cat.name),
      subtypes,
    };
  });
}

function HoldingsList({ holdings }: { holdings: ApiTicker[] }) {
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
  return (
    <span
      className="glass-tooltip"
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
      onFocus={() => setShow(true)}
      onBlur={() => setShow(false)}
      tabIndex={0}
    >
      {children}
      {show && (
        <div className="glass-tooltip-content" role="tooltip" style={{ display: "block" }}>
          {content}
        </div>
      )}
    </span>
  );
}

function CategoryTooltip({ cat, children }: { cat: GroupedCategory; children: React.ReactNode }) {
  const hasHoldings = cat.subtypes.some((s) => s.tickers.length > 0);
  if (!hasHoldings) return <>{children}</>;

  const content = cat.subtypes.map((st) => (
    <div key={st.name} className="mb-1.5 last:mb-0">
      <p className="font-semibold text-foreground/70 mb-0.5">{st.name}</p>
      <HoldingsList holdings={st.tickers} />
    </div>
  ));

  return <GlassTooltip content={content}>{children}</GlassTooltip>;
}

export function CategorySummary({
  categories,
  tickers,
  total: totalValue,
  title,
  embedded,
  colorByName,
}: {
  categories: ApiCategory[];
  tickers: ApiTicker[];
  total: number;
  title: string;
  embedded?: boolean;
  colorByName: Record<string, string>;
}) {
  const grouped = groupTickers(categories, tickers, totalValue);
  const equityCats = grouped.filter((c) => c.isEquity);
  const nonEquityCats = grouped.filter((c) => !c.isEquity);

  const totalPct = categories.reduce((s, c) => s + c.pct, 0);
  const totalTarget = categories.reduce((s, c) => s + c.target, 0);
  const totalDeviation = totalPct - totalTarget;

  // Build CategoryData-compatible array for the donut chart
  const donutCategories: CategoryData[] = categories.map((c) => ({
    name: c.name,
    value: c.value,
    pct: c.pct,
    lots: 0,
    target: c.target,
    deviation: c.deviation,
    isEquity: EQUITY_CATEGORIES.has(c.name),
    subtypes: [],
    holdings: [],
  }));

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
              <TableHead className="text-right hidden sm:table-cell">Dev</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {/* Equity categories */}
            {equityCats.map((cat) => (
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
                      <GlassTooltip content={<HoldingsList holdings={sub.tickers} />}>
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
                    <TableCell className="hidden sm:table-cell" />
                  </TableRow>
                ))}
              </Fragment>
            ))}

            {/* Non-Equity group — parent row with aggregated values */}
            {nonEquityCats.length > 0 && (() => {
              const neValue = nonEquityCats.reduce((s, c) => s + c.value, 0);
              const nePct = nonEquityCats.reduce((s, c) => s + c.pct, 0);
              const neTarget = nonEquityCats.reduce((s, c) => s + c.target, 0);
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
                  {nonEquityCats.map((cat) => (
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
                      <TableCell className="hidden sm:table-cell" />
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
        <AllocationDonut categories={donutCategories} total={totalValue} colorByName={colorByName} />
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
