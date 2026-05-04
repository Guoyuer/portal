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

function MainCategoryRow({
  label,
  value,
  pct,
  target,
  deviation,
  className = "hover:bg-white/10 dark:hover:bg-white/5 transition-colors",
  labelClassName = "font-medium",
}: {
  label: React.ReactNode;
  value: number;
  pct: number;
  target: number;
  deviation: number;
  className?: string;
  labelClassName?: string;
}) {
  return (
    <TableRow className={className}>
      <TableCell className={labelClassName}>{label}</TableCell>
      <TableCell className="text-right hidden sm:table-cell">{fmtCurrency(value)}</TableCell>
      <TableCell className="text-right">{fmtPct(pct, false)}</TableCell>
      <TableCell className="text-right">{fmtPct(target, false)}</TableCell>
      <DeviationCell value={deviation} />
    </TableRow>
  );
}

function DetailCategoryRow({ label, value, pct }: { label: React.ReactNode; value: number; pct: number }) {
  return (
    <TableRow className="hover:bg-white/10 dark:hover:bg-white/5 transition-colors">
      <TableCell className="text-muted-foreground pl-6">{label}</TableCell>
      <TableCell className="text-right text-muted-foreground hidden sm:table-cell">{fmtCurrency(value)}</TableCell>
      <TableCell className="text-right text-muted-foreground">{fmtPct(pct, false)}</TableCell>
      <TableCell />
      <TableCell className="hidden sm:table-cell" />
    </TableRow>
  );
}

function EquityCategoryRows({ categories }: { categories: GroupedCategory[] }) {
  return categories.map((cat) => (
    <Fragment key={cat.name}>
      <MainCategoryRow
        label={<CategoryTooltip cat={cat}>{cat.name}</CategoryTooltip>}
        value={cat.value}
        pct={cat.pct}
        target={cat.target}
        deviation={cat.deviation}
      />
      {cat.subtypes.map((sub) => (
        <DetailCategoryRow
          key={`${cat.name}-${sub.name}`}
          label={
            <GlassTooltip content={<HoldingsList holdings={sub.tickers} />}>
              <em>{sub.name}</em>
            </GlassTooltip>
          }
          value={sub.value}
          pct={sub.pct}
        />
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
      <MainCategoryRow
        label="Non-Equity"
        value={model.nonEquityAggregate.value}
        pct={model.nonEquityAggregate.pct}
        target={model.nonEquityAggregate.target}
        deviation={model.nonEquityAggregate.deviation}
      />
      {model.nonEquityCats.map((cat) => (
        <DetailCategoryRow
          key={cat.name}
          label={<CategoryTooltip cat={cat}><em>{cat.name}</em></CategoryTooltip>}
          value={cat.value}
          pct={cat.pct}
        />
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
        <MainCategoryRow
          label="Total"
          value={totalValue}
          pct={model.totalPct}
          target={model.totalTarget}
          deviation={model.totalDeviation}
          className={TOTAL_ROW_CLASS}
          labelClassName=""
        />
      </TableBody>
    </Table>
  );
}
