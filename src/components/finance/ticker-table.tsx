"use client";

import { useState } from "react";
import { fmtCurrency, fmtPct } from "@/lib/format/format";
import { valueColor } from "@/lib/format/thresholds";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { TickerChart, TickerDialogOnly } from "./ticker-chart";
import { GroupChartDialog } from "./group-dialog";
import type { DailyTicker, FidelityTxn } from "@/lib/schemas";

const ACTIVITY_TOP_SYMBOLS = 5;

export const TOTAL_ROW_CLASS = "font-bold border-t-2 border-b-2 border-foreground/20";

export function DeviationCell({ value }: { value: number }) {
  return (
    <TableCell className={`text-right hidden sm:table-cell ${valueColor(value)}`}>
      {fmtPct(value, true)}
    </TableCell>
  );
}

export type ActivityTableRow = {
  symbol: string;
  count: number;
  total: number;
  isGroup?: boolean;
  groupKey?: string;
};

interface TickerRowProps {
  symbol: string;
  count: number;
  total: number;
  isGroup?: boolean;
  groupKey?: string;
  expanded: boolean;
  onToggle: () => void;
  startDate?: string;
  endDate?: string;
}

function ExpanderIndicator({ expanded, isGroup }: { expanded: boolean; isGroup?: boolean }) {
  if (isGroup) {
    // Groups open a full-screen dialog — use a pop-out icon so users can
    // predict the interaction instead of seeing the same chevron as solo rows.
    return (
      <span className="inline-block w-3 text-[10px] text-muted-foreground" aria-label="Opens full view">
        &#x2197;
      </span>
    );
  }
  return (
    <span className={`inline-block w-3 text-[10px] text-muted-foreground transition-transform ${expanded ? "rotate-90" : ""}`}>
      &#9654;
    </span>
  );
}

/** Primary table row: uses shadcn TableRow/TableCell. */
function TickerRow({ symbol, count, total, isGroup, expanded, onToggle, startDate, endDate }: TickerRowProps) {
  return (
    <>
      <TableRow className="even:bg-muted/50 cursor-pointer hover:bg-muted/80 group" onClick={onToggle}>
        <TableCell className="font-mono">
          <ExpanderIndicator expanded={expanded} isGroup={isGroup} />
          {symbol}
        </TableCell>
        <TableCell className="text-right">{count}</TableCell>
        <TableCell className="text-right">{fmtCurrency(total)}</TableCell>
      </TableRow>
      {expanded && !isGroup && (
        <TableRow>
          <TableCell colSpan={3} className="p-2">
            <TickerChart symbol={symbol} startDate={startDate} endDate={endDate} />
          </TableCell>
        </TableRow>
      )}
    </>
  );
}

/** Overflow row rendered inside a nested <details> <table>; raw tr/td + muted palette. */
function TickerRowOverflow({ symbol, count, total, isGroup, expanded, onToggle, startDate, endDate }: TickerRowProps) {
  const numCell = "px-2 py-1.5 text-right text-muted-foreground";
  return (
    <>
      <tr
        className="border-b border-border even:bg-muted/50 cursor-pointer hover:bg-muted/80"
        onClick={onToggle}
      >
        <td className="px-2 py-1.5 font-mono text-muted-foreground">
          <ExpanderIndicator expanded={expanded} isGroup={isGroup} />
          {symbol}
        </td>
        <td className={numCell}>{count}</td>
        <td className={numCell}>{fmtCurrency(total)}</td>
      </tr>
      {expanded && !isGroup && (
        <tr>
          <td colSpan={3} className="px-2 py-2">
            <TickerChart symbol={symbol} startDate={startDate} endDate={endDate} />
          </td>
        </tr>
      )}
    </>
  );
}

export function TickerTable({
  title,
  data,
  startDate,
  endDate,
  countLabel = "Trades",
  dailyTickers,
  fidelityTxns,
}: {
  title: string;
  data: ActivityTableRow[];
  startDate?: string;
  endDate?: string;
  countLabel?: string;
  dailyTickers?: DailyTicker[];
  fidelityTxns?: FidelityTxn[];
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const [dialogGroupKey, setDialogGroupKey] = useState<string | null>(null);
  const [dialogTicker, setDialogTicker] = useState<string | null>(null);
  const top = data.slice(0, ACTIVITY_TOP_SYMBOLS);
  const rest = data.slice(ACTIVITY_TOP_SYMBOLS);
  const restTotal = rest.reduce((s, t) => s + t.total, 0);

  const rowProps = (item: ActivityTableRow): TickerRowProps => ({
    symbol: item.symbol,
    count: item.count,
    total: item.total,
    isGroup: item.isGroup,
    groupKey: item.groupKey,
    expanded: expanded === item.symbol,
    onToggle: item.isGroup && item.groupKey
      ? () => setDialogGroupKey(item.groupKey!)
      : () => setExpanded((prev) => (prev === item.symbol ? null : item.symbol)),
    startDate,
    endDate,
  });

  return (
    <div className="overflow-x-auto">
      <h3 className="font-semibold mb-2">{title}</h3>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Symbol</TableHead>
            <TableHead className="text-right">{countLabel}</TableHead>
            <TableHead className="text-right">Total</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {top.map((item) => <TickerRow key={item.symbol} {...rowProps(item)} />)}
          {rest.length > 0 && (
            <TableRow>
              <TableCell colSpan={3} className="p-0">
                <details className="group">
                  <summary className="px-2 py-1.5 text-sm text-muted-foreground cursor-pointer hover:text-foreground">
                    ... and {rest.length} more ({fmtCurrency(restTotal)})
                  </summary>
                  <table className="w-full text-sm">
                    <tbody>
                      {rest.map((item) => <TickerRowOverflow key={item.symbol} {...rowProps(item)} />)}
                    </tbody>
                  </table>
                </details>
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
      {dialogGroupKey && dailyTickers && fidelityTxns && (
        <GroupChartDialog
          groupKey={dialogGroupKey}
          dailyTickers={dailyTickers}
          fidelityTxns={fidelityTxns}
          startDate={startDate}
          endDate={endDate}
          onClose={() => setDialogGroupKey(null)}
          onSelectTicker={(sym) => setDialogTicker(sym)}
        />
      )}
      {dialogTicker && (
        <TickerDialogOnly symbol={dialogTicker} onClose={() => setDialogTicker(null)} />
      )}
    </div>
  );
}
