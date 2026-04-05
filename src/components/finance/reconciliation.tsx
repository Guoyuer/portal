import type { ReconciliationData } from "@/lib/types";
import { fmtCurrency } from "@/lib/format";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { SectionHeader, SectionBody } from "@/components/finance/shared";

function TierRow({ label, tier }: { label: string; tier: { startValue: number; endValue: number; netChange: number } }) {
  return (
    <TableRow className="even:bg-muted/50">
      <TableCell className="font-medium">{label}</TableCell>
      <TableCell className="text-right">{fmtCurrency(tier.startValue)}</TableCell>
      <TableCell className="text-right">{fmtCurrency(tier.endValue)}</TableCell>
      <TableCell className={`text-right ${tier.netChange >= 0 ? "text-green-600" : "text-red-500"}`}>
        {fmtCurrency(tier.netChange)}
      </TableCell>
    </TableRow>
  );
}

export function Reconciliation({ data }: { data: ReconciliationData }) {
  const fid = data.fidelity.details as Record<string, number>;

  return (
    <section>
      <SectionHeader>
        Portfolio Reconciliation
        {data.prevDate && data.currDate && (
          <span className="text-sm font-normal text-muted-foreground ml-2">
            {data.prevDate} &rarr; {data.currDate}
          </span>
        )}
      </SectionHeader>
      <SectionBody>
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Tier</TableHead>
                <TableHead className="text-right">Start</TableHead>
                <TableHead className="text-right">End</TableHead>
                <TableHead className="text-right">Change</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TierRow label="Fidelity" tier={data.fidelity} />
              <TierRow label="Linked" tier={data.linked} />
              <TierRow label="Manual" tier={data.manual} />
              <TableRow className="font-bold border-t-2 border-b-2 border-foreground/20">
                <TableCell>Total</TableCell>
                <TableCell className="text-right">{fmtCurrency(data.totalStart)}</TableCell>
                <TableCell className="text-right">{fmtCurrency(data.totalEnd)}</TableCell>
                <TableCell className={`text-right ${data.totalChange >= 0 ? "text-green-600" : "text-red-500"}`}>
                  {fmtCurrency(data.totalChange)}
                </TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </div>

        {/* Fidelity breakdown */}
        {fid && (
          <div className="mt-4">
            <h3 className="font-semibold mb-2 text-sm">Fidelity Breakdown</h3>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
              <div>
                <p className="text-muted-foreground">Deposits</p>
                <p className="font-medium">{fmtCurrency(fid.deposits ?? 0)}</p>
              </div>
              <div>
                <p className="text-muted-foreground">Dividends (net)</p>
                <p className="font-medium">{fmtCurrency(fid.dividends_net ?? 0)}</p>
              </div>
              <div>
                <p className="text-muted-foreground">Trades (net)</p>
                <p className="font-medium">{fmtCurrency(fid.trades_net ?? 0)}</p>
              </div>
              <div>
                <p className="text-muted-foreground">Market Movement</p>
                <p className={`font-medium ${(fid.market_movement ?? 0) >= 0 ? "text-green-600" : "text-red-500"}`}>
                  {fmtCurrency(fid.market_movement ?? 0)}
                </p>
              </div>
            </div>
          </div>
        )}
      </SectionBody>
    </section>
  );
}
