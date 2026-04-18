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
import { TickerChart } from "./ticker-chart";

const ACTIVITY_TOP_SYMBOLS = 5;

export const TOTAL_ROW_CLASS = "font-bold border-t-2 border-b-2 border-foreground/20";

export function DeviationCell({ value }: { value: number }) {
  return (
    <TableCell className={`text-right hidden sm:table-cell ${valueColor(value)}`}>
      {fmtPct(value, true)}
    </TableCell>
  );
}

interface TickerRowProps {
  symbol: string;
  count: number;
  total: number;
  expanded: boolean;
  onToggle: () => void;
  startDate?: string;
  endDate?: string;
}

function ExpanderIndicator({ expanded }: { expanded: boolean }) {
  return (
    <span className={`inline-block w-3 text-[10px] text-muted-foreground transition-transform ${expanded ? "rotate-90" : ""}`}>
      &#9654;
    </span>
  );
}

/** Primary table row: uses shadcn TableRow/TableCell. */
function TickerRow({ symbol, count, total, expanded, onToggle, startDate, endDate }: TickerRowProps) {
  return (
    <>
      <TableRow className="even:bg-muted/50 cursor-pointer hover:bg-muted/80 group" onClick={onToggle}>
        <TableCell className="font-mono">
          <ExpanderIndicator expanded={expanded} />
          {symbol}
        </TableCell>
        <TableCell className="text-right">{count}</TableCell>
        <TableCell className="text-right">{fmtCurrency(total)}</TableCell>
      </TableRow>
      {expanded && (
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
function TickerRowOverflow({ symbol, count, total, expanded, onToggle, startDate, endDate }: TickerRowProps) {
  const numCell = "px-2 py-1.5 text-right text-muted-foreground";
  return (
    <>
      <tr
        className="border-b border-border even:bg-muted/50 cursor-pointer hover:bg-muted/80"
        onClick={onToggle}
      >
        <td className="px-2 py-1.5 font-mono text-muted-foreground">
          <ExpanderIndicator expanded={expanded} />
          {symbol}
        </td>
        <td className={numCell}>{count}</td>
        <td className={numCell}>{fmtCurrency(total)}</td>
      </tr>
      {expanded && (
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
}: {
  title: string;
  data: { symbol: string; count: number; total: number }[];
  startDate?: string;
  endDate?: string;
  countLabel?: string;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const top = data.slice(0, ACTIVITY_TOP_SYMBOLS);
  const rest = data.slice(ACTIVITY_TOP_SYMBOLS);
  const restTotal = rest.reduce((s, t) => s + t.total, 0);

  const rowProps = (item: { symbol: string; count: number; total: number }): TickerRowProps => ({
    symbol: item.symbol,
    count: item.count,
    total: item.total,
    expanded: expanded === item.symbol,
    onToggle: () => setExpanded((prev) => (prev === item.symbol ? null : item.symbol)),
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
    </div>
  );
}
