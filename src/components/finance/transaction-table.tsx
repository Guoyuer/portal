"use client";

// ── Shared transaction table (used by TickerChartDialog + GroupChartDialog) ──
// Single column on narrow screens, two transactions per row (to halve
// height) once the dialog is wide enough to give each column headroom.

import { fmtCurrency, fmtDateMedium, fmtQty } from "@/lib/format/format";
import { useIsDark, useIsMobile } from "@/lib/hooks/hooks";
import type { TickerTransaction } from "@/lib/schemas";
import type { Selection } from "./ticker-markers";

export function TransactionTable({
  transactions,
  selected,
  tableScrollRef,
}: {
  transactions: TickerTransaction[];
  selected: Selection | null;
  tableScrollRef: React.RefObject<HTMLDivElement | null>;
}) {
  const isDark = useIsDark();
  const isMobile = useIsMobile();
  if (transactions.length === 0) return null;

  const selectedDateSet = selected ? new Set(selected.dates) : null;
  const highlightBg = selected?.side === "sell"
    ? (isDark ? "bg-amber-900/30" : "bg-amber-100")
    : (isDark ? "bg-emerald-900/30" : "bg-emerald-100");

  const emptyCells = (
    <>
      <td className="px-2 py-1.5" />
      <td className="px-2 py-1.5" />
      <td className="px-2 py-1.5" />
      <td className="px-2 py-1.5" />
      <td className="px-2 py-1.5" />
    </>
  );

  const renderCells = (t: TickerTransaction | null) => {
    if (!t) return emptyCells;
    const sideMatches = selected
      ? selected.side === "sell"
        ? t.actionType === "sell"
        : t.actionType === "buy" || t.actionType === "reinvestment"
      : false;
    const isHighlighted = sideMatches && (selectedDateSet?.has(t.runDate) ?? false);
    const bg = isHighlighted ? highlightBg : "";
    const dataSide = t.actionType === "sell" ? "sell" : "buy";
    return (
      <>
        <td data-date={t.runDate} data-side={dataSide} className={`px-2 py-1.5 ${bg}`}>{fmtDateMedium(t.runDate)}</td>
        {/* Arbitrary-value Tailwind classes must be literal strings so the JIT can extract them */}
        <td className={`px-2 py-1.5 capitalize ${bg} ${t.actionType === "sell" ? "text-[#E69F00]" : "text-[#009E73]"}`}>{t.actionType}</td>
        <td className={`px-2 py-1.5 text-right font-mono ${bg}`}>{fmtQty(Math.abs(t.quantity))}</td>
        <td className={`px-2 py-1.5 text-right font-mono ${bg}`}>{fmtCurrency(t.price)}</td>
        <td className={`px-2 py-1.5 text-right font-mono ${bg}`}>{fmtCurrency(Math.abs(t.amount))}</td>
      </>
    );
  };

  const headerGroup = (
    <>
      <th className="text-left px-2 py-1.5 font-medium">Date</th>
      <th className="text-left px-2 py-1.5 font-medium">Type</th>
      <th className="text-right px-2 py-1.5 font-medium">Qty</th>
      <th className="text-right px-2 py-1.5 font-medium">Price</th>
      <th className="text-right px-2 py-1.5 font-medium">Amount</th>
    </>
  );

  const rowBase = `border-b ${isDark ? "border-zinc-800" : "border-zinc-100"} transition-colors`;
  const headRowClass = `text-xs ${isDark ? "text-zinc-400" : "text-zinc-500"} border-b ${isDark ? "border-zinc-700" : "border-zinc-200"}`;

  const body = isMobile
    ? transactions.map((t, i) => (
        <tr key={i} className={rowBase}>{renderCells(t)}</tr>
      ))
    : (() => {
        const pairs: [TickerTransaction, TickerTransaction | null][] = [];
        for (let i = 0; i < transactions.length; i += 2) {
          pairs.push([transactions[i], transactions[i + 1] ?? null]);
        }
        return pairs.map(([a, b], i) => (
          <tr key={i} className={rowBase}>
            {renderCells(a)}
            <td className="w-6" />
            {renderCells(b)}
          </tr>
        ));
      })();

  return (
    <div ref={tableScrollRef} className="shrink-0 max-h-[40%] overflow-auto px-3 sm:px-5 pb-4 border-t border-foreground/10">
      <table className="w-full text-sm whitespace-nowrap">
        <thead>
          <tr className={headRowClass}>
            {headerGroup}
            {!isMobile && (
              <>
                <th className="w-6" />
                {headerGroup}
              </>
            )}
          </tr>
        </thead>
        <tbody>{body}</tbody>
      </table>
    </div>
  );
}
