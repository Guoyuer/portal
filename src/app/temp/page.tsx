"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { REPORT_URL } from "@/lib/config";
import { ReportDataSchema, type ReportData } from "@/lib/schema";
import { fmtCurrency, fmtPct } from "@/lib/format";
import { valueColor } from "@/lib/style-helpers";
import type { StockDetail } from "@/lib/types";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { SectionHeader, SectionBody } from "@/components/finance/shared";
import { GainLoss } from "@/components/finance/gain-loss";

// ── Performers Table ──────────────────────────────────────────────────

function PerformersTable({ title, data }: { title: string; data: StockDetail[] }) {
  if (data.length === 0) return null;
  return (
    <div className="mb-6 overflow-x-auto">
      <h3 className="font-semibold mb-2">{title}</h3>
      <Table>
        <TableHeader><TableRow>
          <TableHead>Ticker</TableHead>
          <TableHead className="text-right">Month Return</TableHead>
          <TableHead className="text-right">Value</TableHead>
          <TableHead className="text-right">vs 52W High</TableHead>
        </TableRow></TableHeader>
        <TableBody>
          {data.map((s) => (
            <TableRow key={s.ticker} className="even:bg-muted/50">
              <TableCell className="font-mono">{s.ticker}</TableCell>
              <TableCell className={`text-right ${valueColor(s.monthReturn)}`}>{fmtPct(s.monthReturn, true)}</TableCell>
              <TableCell className="text-right">{fmtCurrency(s.endValue)}</TableCell>
              <TableCell className={`text-right ${valueColor(s.vsHigh ?? -1)}`}>{s.vsHigh != null ? fmtPct(s.vsHigh, true) : "N/A"}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

// ── Temp Page ─────────────────────────────────────────────────────────

export default function TempPage() {
  const [r, setReport] = useState<ReportData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchReport = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(REPORT_URL, { cache: "no-store" });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const json = await res.json();
      const parsed = ReportDataSchema.safeParse(json);
      if (!parsed.success) {
        throw new Error(`Invalid report data: ${parsed.error.issues[0]?.message ?? "schema mismatch"}`);
      }
      setReport(parsed.data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load report");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchReport(); }, [fetchReport]);

  const upcomingEarnings = useMemo(() => {
    if (!r?.holdingsDetail) return [];
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() + 30);
    return r.holdingsDetail.upcomingEarnings.filter((s) => {
      if (!s.nextEarnings) return false;
      const d = new Date(s.nextEarnings);
      return d >= new Date() && d <= cutoff;
    });
  }, [r]);

  if (loading) {
    return (
      <div className="max-w-5xl mx-auto py-20 text-center text-muted-foreground">
        Loading report...
      </div>
    );
  }

  if (error || !r) {
    return (
      <div className="max-w-5xl mx-auto py-20 text-center">
        <p className="text-red-500 mb-4">{error ?? "No data"}</p>
        <Button onClick={fetchReport} variant="outline">Retry</Button>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto space-y-10">
      <h1 className="text-xl sm:text-2xl font-semibold tracking-tight">Holdings</h1>

      {(r.holdingsDetail || r.equityCategories.length > 0) && (
        <section>
          <SectionHeader>Performance</SectionHeader>
          <SectionBody>
            {r.holdingsDetail && (
              <>
                <PerformersTable title="Top Performers" data={r.holdingsDetail.topPerformers} />
                <PerformersTable title="Bottom Performers" data={r.holdingsDetail.bottomPerformers} />
                {upcomingEarnings.length > 0 && (
                  <div>
                    <h3 className="font-semibold mb-2">Upcoming Earnings</h3>
                    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2 text-sm">
                      {upcomingEarnings.map((s) => (
                        <div key={s.ticker}>
                          <span className="font-mono font-medium">{s.ticker}</span>
                          <span className="text-muted-foreground"> &mdash; {s.nextEarnings}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
          </SectionBody>
        </section>
      )}

      <GainLoss report={r} />
    </div>
  );
}
