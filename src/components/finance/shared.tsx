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

export const ACTIVITY_TOP_SYMBOLS = 5;

export const TOTAL_ROW_CLASS = "font-bold border-t-2 border-b-2 border-foreground/20";

export function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-[#16213e] text-white px-4 py-2.5 rounded-t-md font-bold">
      {children}
    </div>
  );
}

export function SectionBody({ children }: { children: React.ReactNode }) {
  return (
    <div className="border border-border rounded-b-md p-4">{children}</div>
  );
}

export function DeviationCell({ value }: { value: number }) {
  return (
    <TableCell
      className={`text-right ${valueColor(value)}`}
    >
      {fmtPct(value)}
    </TableCell>
  );
}

export function TickerTable({
  title,
  data,
}: {
  title: string;
  data: [string, number, number][]; // [symbol, trades, total]
}) {
  const top = data.slice(0, ACTIVITY_TOP_SYMBOLS);
  const rest = data.slice(ACTIVITY_TOP_SYMBOLS);
  const restTotal = rest.reduce((s, t) => s + t[2], 0);
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
          {top.map(([symbol, trades, total]) => (
            <TableRow key={symbol} className="even:bg-muted/50">
              <TableCell className="font-mono">{symbol}</TableCell>
              <TableCell className="text-right">{trades}</TableCell>
              <TableCell className="text-right">
                {fmtCurrency(total)}
              </TableCell>
            </TableRow>
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
                      {rest.map(([symbol, trades, total]) => (
                        <tr
                          key={symbol}
                          className="border-b border-border even:bg-muted/50"
                        >
                          <td className="px-2 py-1.5 font-mono text-muted-foreground">
                            {symbol}
                          </td>
                          <td className="px-2 py-1.5 text-right text-muted-foreground">
                            {trades}
                          </td>
                          <td className="px-2 py-1.5 text-right text-muted-foreground">
                            {fmtCurrency(total)}
                          </td>
                        </tr>
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
