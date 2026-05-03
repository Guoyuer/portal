"use client";

import { Fragment, useState } from "react";
import type { ApiTicker } from "@/lib/compute/computed-types";
import { fmtCurrency, fmtCurrencyShort, fmtPct } from "@/lib/format/format";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { DeviationCell, TOTAL_ROW_CLASS } from "@/components/finance/ticker-table";
import type { CategorySummaryModel, GroupedCategory } from "@/components/finance/category-summary-model";

function HoldingsList({ holdings }: { holdings: ApiTicker[] }) {
  return (
    <div className="grid grid-cols-[auto_auto] gap-x-3 gap-y-0">
      {holdings.map((h) => (
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

function EquityCategoryRows({ categories }: { categories: GroupedCategory[] }) {
  return categories.map((cat) => (
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
  ));
}

function NonEquityRows({
  model,
}: {
  model: Pick<CategorySummaryModel, "nonEquityAggregate" | "nonEquityCats">;
}) {
  if (!model.nonEquityAggregate) return null;

  return (
    <Fragment>
      <TableRow className="hover:bg-white/10 dark:hover:bg-white/5 transition-colors">
        <TableCell className="font-medium">Non-Equity</TableCell>
        <TableCell className="text-right hidden sm:table-cell">
          {fmtCurrency(model.nonEquityAggregate.value)}
        </TableCell>
        <TableCell className="text-right">
          {fmtPct(model.nonEquityAggregate.pct, false)}
        </TableCell>
        <TableCell className="text-right">
          {fmtPct(model.nonEquityAggregate.target, false)}
        </TableCell>
        <DeviationCell value={model.nonEquityAggregate.deviation} />
      </TableRow>
      {model.nonEquityCats.map((cat) => (
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
}

export function CategoryAllocationTable({
  model,
  totalValue,
}: {
  model: CategorySummaryModel;
  totalValue: number;
}) {
  return (
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
        <EquityCategoryRows categories={model.equityCats} />
        <NonEquityRows model={model} />
        <TableRow className={TOTAL_ROW_CLASS}>
          <TableCell>Total</TableCell>
          <TableCell className="text-right hidden sm:table-cell">
            {fmtCurrency(totalValue)}
          </TableCell>
          <TableCell className="text-right">
            {fmtPct(model.totalPct, false)}
          </TableCell>
          <TableCell className="text-right">
            {fmtPct(model.totalTarget, false)}
          </TableCell>
          <DeviationCell value={model.totalDeviation} />
        </TableRow>
      </TableBody>
    </Table>
  );
}
