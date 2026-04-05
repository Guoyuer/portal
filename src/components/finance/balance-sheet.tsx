import type { BalanceSheetData } from "@/lib/types";
import { fmtCurrency, fmtYuan } from "@/lib/format";
import { CC_GROUP_THRESHOLD } from "@/lib/style-helpers";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { SectionHeader, SectionBody, TOTAL_ROW_CLASS } from "@/components/finance/shared";

export function BalanceSheet({ data }: { data: BalanceSheetData }) {
  return (
    <section>
      <SectionHeader>Balance Sheet</SectionHeader>
      <SectionBody>
        <div className="grid md:grid-cols-2 gap-6">
          {/* Assets */}
          <div className="overflow-x-auto">
            <h3 className="font-semibold mb-2">Assets</h3>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Account</TableHead>
                  <TableHead className="text-right">Balance</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                <TableRow className="even:bg-muted/50">
                  <TableCell className="font-medium">Investments</TableCell>
                  <TableCell className="text-right">
                    {fmtCurrency(data.investmentTotal)}
                  </TableCell>
                </TableRow>
                {data.accounts.map((a) => (
                  <TableRow key={a.name} className="even:bg-muted/50">
                    <TableCell
                      className={a.currency === "CNY" ? "pl-6 text-muted-foreground" : ""}
                    >
                      {a.name}
                    </TableCell>
                    <TableCell className="text-right">
                      {a.currency === "CNY"
                        ? fmtYuan(a.balance)
                        : fmtCurrency(a.balance)}
                    </TableCell>
                  </TableRow>
                ))}
                <TableRow className={TOTAL_ROW_CLASS}>
                  <TableCell>Total Assets</TableCell>
                  <TableCell className="text-right">
                    {fmtCurrency(data.totalAssets)}
                  </TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </div>

          {/* Liabilities */}
          <div className="overflow-x-auto">
            <h3 className="font-semibold mb-2">Liabilities</h3>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Account</TableHead>
                  <TableHead className="text-right">Balance</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.totalLiabilities < CC_GROUP_THRESHOLD && data.creditCards.length > 1 ? (
                  <TableRow className="even:bg-muted/50">
                    <TableCell>Credit Cards ({data.creditCards.length})</TableCell>
                    <TableCell className="text-right text-red-500">
                      {fmtCurrency(data.creditCards.reduce((s, c) => s + c.balance, 0))}
                    </TableCell>
                  </TableRow>
                ) : (
                  data.creditCards.map((l) => (
                    <TableRow key={l.name} className="even:bg-muted/50">
                      <TableCell>{l.name}</TableCell>
                      <TableCell className="text-right text-red-500">
                        {fmtCurrency(l.balance)}
                      </TableCell>
                    </TableRow>
                  ))
                )}
                <TableRow className={TOTAL_ROW_CLASS}>
                  <TableCell>Total Liabilities</TableCell>
                  <TableCell className="text-right text-red-500">
                    {fmtCurrency(data.totalLiabilities)}
                  </TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </div>
        </div>

        {/* Net Worth total */}
        <div className="mt-4 flex flex-wrap justify-between items-center gap-2 px-2 py-3 border-t-2 border-b-2 border-foreground/20 font-bold text-lg">
          <span>Net Worth</span>
          <span>{fmtCurrency(data.netWorth)}</span>
        </div>
      </SectionBody>
    </section>
  );
}
