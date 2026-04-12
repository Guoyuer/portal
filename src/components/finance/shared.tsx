"use client";

import { useState } from "react";
import { fmtCurrency, fmtPct } from "@/lib/format";
import { valueColor } from "@/lib/style-helpers";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { TickerChart } from "./ticker-chart";

export const ACTIVITY_TOP_SYMBOLS = 5;

export const TOTAL_ROW_CLASS = "font-bold border-t-2 border-b-2 border-foreground/20";

export function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-foreground font-semibold text-lg tracking-tight mb-3">
      {children}
    </div>
  );
}

export function SectionBody({ children }: { children: React.ReactNode }) {
  return (
    <div className="liquid-glass p-3 sm:p-5">{children}</div>
  );
}

export function DeviationCell({ value }: { value: number }) {
  return (
    <TableCell
      className={`text-right hidden sm:table-cell ${valueColor(value)}`}
    >
      {fmtPct(value, true)}
    </TableCell>
  );
}

export function TickerTable({
  title,
  data,
  startDate,
  endDate,
}: {
  title: string;
  data: { symbol: string; count: number; total: number }[];
  startDate?: string;
  endDate?: string;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const top = data.slice(0, ACTIVITY_TOP_SYMBOLS);
  const rest = data.slice(ACTIVITY_TOP_SYMBOLS);
  const restTotal = rest.reduce((s, t) => s + t.total, 0);

  const toggle = (sym: string) => setExpanded((prev) => (prev === sym ? null : sym));

  return (
    <div className="overflow-x-auto">
      <h3 className="font-semibold mb-2">{title}</h3>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Symbol</TableHead>
            <TableHead className="text-right">Trades</TableHead>
            <TableHead className="text-right">Total</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {top.map(({ symbol, count, total }) => (
            <TickerRow
              key={symbol}
              symbol={symbol}
              count={count}
              total={total}
              expanded={expanded === symbol}
              onToggle={() => toggle(symbol)}
              startDate={startDate}
              endDate={endDate}
            />
          ))}
          {rest.length > 0 && (
            <TableRow>
              <TableCell colSpan={3} className="p-0">
                <details className="group">
                  <summary className="px-2 py-1.5 text-sm text-muted-foreground cursor-pointer hover:text-foreground">
                    ... and {rest.length} more ({fmtCurrency(restTotal)})
                  </summary>
                  <table className="w-full text-sm">
                    <tbody>
                      {rest.map(({ symbol, count, total }) => (
                        <RestTickerRow
                          key={symbol}
                          symbol={symbol}
                          count={count}
                          total={total}
                          expanded={expanded === symbol}
                          onToggle={() => toggle(symbol)}
                          startDate={startDate}
                          endDate={endDate}
                        />
                      ))}
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

// ── Expandable ticker row ─────────────────────────────────────────────────

function TickerRow({
  symbol,
  count,
  total,
  expanded,
  onToggle,
  startDate,
  endDate,
}: {
  symbol: string;
  count: number;
  total: number;
  expanded: boolean;
  onToggle: () => void;
  startDate?: string;
  endDate?: string;
}) {
  return (
    <>
      <TableRow
        className="even:bg-muted/50 cursor-pointer hover:bg-muted/80 group"
        onClick={onToggle}
      >
        <TableCell className="font-mono">
          <span className={`inline-block w-3 text-[10px] text-muted-foreground transition-transform ${expanded ? "rotate-90" : ""}`}>&#9654;</span>
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

function RestTickerRow({
  symbol,
  count,
  total,
  expanded,
  onToggle,
  startDate,
  endDate,
}: {
  symbol: string;
  count: number;
  total: number;
  expanded: boolean;
  onToggle: () => void;
  startDate?: string;
  endDate?: string;
}) {
  return (
    <>
      <tr
        className="border-b border-border even:bg-muted/50 cursor-pointer hover:bg-muted/80"
        onClick={onToggle}
      >
        <td className="px-2 py-1.5 font-mono text-muted-foreground">
          <span className={`inline-block w-3 text-[10px] transition-transform ${expanded ? "rotate-90" : ""}`}>&#9654;</span>
          {symbol}
        </td>
        <td className="px-2 py-1.5 text-right text-muted-foreground">{count}</td>
        <td className="px-2 py-1.5 text-right text-muted-foreground">{fmtCurrency(total)}</td>
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
