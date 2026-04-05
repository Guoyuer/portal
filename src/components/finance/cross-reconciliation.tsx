import type { CrossReconciliationData } from "@/lib/types";
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

export function CrossReconciliation({ data }: { data: CrossReconciliationData }) {
  const diff = Math.abs(data.qianjiTotal - data.fidelityTotal);
  const allMatched = data.unmatchedQianji.length === 0 && data.unmatchedFidelity.length === 0;

  return (
    <section>
      <SectionHeader>Transfer Reconciliation</SectionHeader>
      <SectionBody>
        {/* Summary */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm mb-4">
          <div>
            <p className="text-muted-foreground">Qianji Transfers</p>
            <p className="font-medium">{fmtCurrency(data.qianjiTotal)}</p>
          </div>
          <div>
            <p className="text-muted-foreground">Fidelity Deposits</p>
            <p className="font-medium">{fmtCurrency(data.fidelityTotal)}</p>
          </div>
          <div>
            <p className="text-muted-foreground">Matched</p>
            <p className="font-medium">{data.matched.length}</p>
          </div>
          <div>
            <p className="text-muted-foreground">Status</p>
            <p className={`font-medium ${allMatched ? "text-green-600" : "text-yellow-600"}`}>
              {allMatched ? "All matched" : `${data.unmatchedQianji.length + data.unmatchedFidelity.length} unmatched`}
            </p>
          </div>
        </div>

        {/* Matched transfers */}
        {data.matched.length > 0 && (
          <div>
            <h3 className="font-semibold mb-2 text-sm">Matched Transfers</h3>
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Date</TableHead>
                    <TableHead className="text-right">Amount</TableHead>
                    <TableHead className="hidden sm:table-cell">Description</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.matched.map((m, i) => (
                    <TableRow key={i} className="even:bg-muted/50">
                      <TableCell className="text-sm">{m.dateQianji}</TableCell>
                      <TableCell className="text-right">{fmtCurrency(m.amount)}</TableCell>
                      <TableCell className="text-sm text-muted-foreground hidden sm:table-cell">
                        {m.fidelityDesc || m.qianjiNote || "—"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
        )}

        {/* Unmatched warning */}
        {!allMatched && diff > 0 && (
          <p className="text-sm text-yellow-600 mt-3">
            Unmatched amount: {fmtCurrency(diff)}
          </p>
        )}
      </SectionBody>
    </section>
  );
}
