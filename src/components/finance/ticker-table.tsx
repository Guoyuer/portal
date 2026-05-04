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
import { TickerChart, TickerDialogOnly } from "./ticker/ticker-chart";
import { GroupChartDialog } from "./group/group-dialog";
import { SourceBadge } from "./source-badge";
import type { InvestmentTxn } from "@/lib/compute/compute";
import type { ActivityTicker, SourceKind } from "@/lib/compute/computed-types";
import type { DailyTicker } from "@/lib/schemas/timeline";

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
  ticker: string;
  count: number;
  total: number;
  groupKey?: string;
  sources: SourceKind[];
  expanded: boolean;
  onToggle: () => void;
  startDate?: string;
  endDate?: string;
  overflow?: boolean;
}

function ExpanderIndicator({ expanded, isGroup }: { expanded: boolean; isGroup: boolean }) {
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

function SourceBadges({ sources }: { sources: SourceKind[] }) {
  return sources.map((s) => (
    <span key={s} className="ml-1">
      <SourceBadge source={s} />
    </span>
  ));
}

/** Activity row: top-level table rows use shadcn cells; overflow rows live in nested raw <table>. */
function TickerRow({ ticker, count, total, groupKey, sources, expanded, onToggle, startDate, endDate, overflow = false }: TickerRowProps) {
  const isGroup = groupKey !== undefined;
  const symbolCell = (
    <>
      <ExpanderIndicator expanded={expanded} isGroup={isGroup} />
      {ticker}
      <SourceBadges sources={sources} />
    </>
  );

  if (overflow) {
    const numCell = "px-2 py-1.5 text-right text-muted-foreground";
    return (
      <>
        <tr className="border-b border-border even:bg-muted/50 cursor-pointer hover:bg-muted/80" onClick={onToggle}>
          <td className="px-2 py-1.5 font-mono text-muted-foreground">{symbolCell}</td>
          <td className={numCell}>{count}</td>
          <td className={numCell}>{fmtCurrency(total)}</td>
        </tr>
        {expanded && !isGroup && (
          <tr>
            <td colSpan={3} className="px-2 py-2">
              <TickerChart symbol={ticker} startDate={startDate} endDate={endDate} />
            </td>
          </tr>
        )}
      </>
    );
  }

  return (
    <>
      <TableRow className="even:bg-muted/50 cursor-pointer hover:bg-muted/80 group" onClick={onToggle}>
        <TableCell className="font-mono">{symbolCell}</TableCell>
        <TableCell className="text-right">{count}</TableCell>
        <TableCell className="text-right">{fmtCurrency(total)}</TableCell>
      </TableRow>
      {expanded && !isGroup && (
        <TableRow>
          <TableCell colSpan={3} className="p-2">
            <TickerChart symbol={ticker} startDate={startDate} endDate={endDate} />
          </TableCell>
        </TableRow>
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
  investmentTxns,
}: {
  title: string;
  data: ActivityTicker[];
  startDate?: string;
  endDate?: string;
  countLabel?: string;
  dailyTickers?: DailyTicker[];
  investmentTxns?: InvestmentTxn[];
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  // A group dialog and a ticker dialog are mutually exclusive — modeling
  // them as a single discriminated union makes that structural, not incidental.
  type OpenDialog = { kind: "group"; key: string } | { kind: "ticker"; symbol: string };
  const [dialog, setDialog] = useState<OpenDialog | null>(null);
  const top = data.slice(0, ACTIVITY_TOP_SYMBOLS);
  const rest = data.slice(ACTIVITY_TOP_SYMBOLS);
  const restTotal = rest.reduce((s, t) => s + t.total, 0);

  const rowProps = (item: ActivityTicker): TickerRowProps => {
    const groupKey = item.groupKey;
    return {
      ticker: item.ticker,
      count: item.count,
      total: item.total,
      groupKey,
      sources: item.sources,
      expanded: expanded === item.ticker,
      onToggle: groupKey
        ? () => setDialog({ kind: "group", key: groupKey })
        : () => setExpanded((prev) => (prev === item.ticker ? null : item.ticker)),
      startDate,
      endDate,
    };
  };

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
          {top.map((item) => <TickerRow key={item.ticker} {...rowProps(item)} />)}
          {rest.length > 0 && (
            <TableRow>
              <TableCell colSpan={3} className="p-0">
                <details className="group">
                  <summary className="px-2 py-1.5 text-sm text-muted-foreground cursor-pointer hover:text-foreground">
                    ... and {rest.length} more ({fmtCurrency(restTotal)})
                  </summary>
                  <table className="w-full text-sm">
                    <tbody>
                      {rest.map((item) => <TickerRow key={item.ticker} {...rowProps(item)} overflow />)}
                    </tbody>
                  </table>
                </details>
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
      {dialog?.kind === "group" && dailyTickers && investmentTxns && (
        <GroupChartDialog
          groupKey={dialog.key}
          dailyTickers={dailyTickers}
          investmentTxns={investmentTxns}
          startDate={startDate}
          endDate={endDate}
          onClose={() => setDialog(null)}
          onSelectTicker={(sym) => setDialog({ kind: "ticker", symbol: sym })}
        />
      )}
      {dialog?.kind === "ticker" && (
        <TickerDialogOnly symbol={dialog.symbol} onClose={() => setDialog(null)} />
      )}
    </div>
  );
}
