import type { BalanceSheetData } from "@/lib/types";
import { fmtCurrency, fmtYuan } from "@/lib/format";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { SectionHeader, SectionBody } from "@/components/finance/shared";

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
                {(() => {
                  const usdAccounts = data.accounts.filter((a) => a.currency !== "CNY");
                  const cnyAccounts = data.accounts.filter((a) => a.currency === "CNY");
                  return (
                    <>
                      {usdAccounts.map((a) => (
                        <TableRow key={a.name} className="even:bg-muted/50">
                          <TableCell>{a.name}</TableCell>
                          <TableCell className="text-right">{fmtCurrency(a.balance)}</TableCell>
                        </TableRow>
                      ))}
                      {cnyAccounts.length > 0 && (
                        <>
                          <TableRow className="bg-muted">
                            <TableCell colSpan={2} className="text-xs font-semibold text-muted-foreground">CNY Accounts</TableCell>
                          </TableRow>
                          {cnyAccounts.map((a) => (
                            <TableRow key={a.name} className="even:bg-muted/50">
                              <TableCell className="pl-6 text-muted-foreground">{a.name}</TableCell>
                              <TableCell className="text-right text-muted-foreground">{fmtYuan(a.balance)}</TableCell>
                            </TableRow>
                          ))}
                        </>
                      )}
                    </>
                  );
                })()}
                <TableRow className="font-bold border-t-2 border-b-2 border-foreground/20">
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
                {data.totalLiabilities < 500 && data.creditCards.length > 1 ? (
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
                <TableRow className="font-bold border-t-2 border-b-2 border-foreground/20">
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
