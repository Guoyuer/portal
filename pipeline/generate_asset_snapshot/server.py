"""FastAPI server for the timemachine API."""
from __future__ import annotations

import logging
import os
from collections import defaultdict
from pathlib import Path
from typing import Any, cast

import uvicorn
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from .db import get_connection

log = logging.getLogger(__name__)

# ── Target allocation weights ──────────────────────────────────────────────

_CATEGORIES: list[tuple[str, str, int]] = [
    ("US Equity", "us_equity", 55),
    ("Non-US Equity", "non_us_equity", 15),
    ("Crypto", "crypto", 3),
    ("Safe Net", "safe_net", 27),
]


# ── App factory ─────────────────────────────────────────────────────────────


def create_app(db_path: Path) -> FastAPI:
    """Create and return the FastAPI application."""
    app = FastAPI(title="Timemachine API")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── GET /timeline ───────────────────────────────────────────────────

    @app.get("/timeline")
    def timeline() -> dict[str, Any]:
        conn = get_connection(db_path)
        try:
            # ── Daily + prefix (existing) ──────────────────────────────
            cur = conn.execute(
                "SELECT date, total, us_equity, non_us_equity, crypto, safe_net, liabilities "
                "FROM computed_daily ORDER BY date"
            )
            daily = [
                {
                    "date": row[0],
                    "total": row[1],
                    "usEquity": row[2],
                    "nonUsEquity": row[3],
                    "crypto": row[4],
                    "safeNet": row[5],
                    "liabilities": row[6],
                }
                for row in cur.fetchall()
            ]

            cur = conn.execute(
                "SELECT date, income, expenses, buys, sells, dividends, net_cash_in, cc_payments "
                "FROM computed_prefix ORDER BY date"
            )
            prefix = [
                {
                    "date": row[0],
                    "income": row[1],
                    "expenses": row[2],
                    "buys": row[3],
                    "sells": row[4],
                    "dividends": row[5],
                    "netCashIn": row[6],
                    "ccPayments": row[7],
                }
                for row in cur.fetchall()
            ]

            # ── Daily tickers (for allocation) ────────────────────────
            cur = conn.execute(
                "SELECT date, ticker, value, category, subtype, cost_basis, gain_loss, gain_loss_pct "
                "FROM computed_daily_tickers ORDER BY date, value DESC"
            )
            daily_tickers = [
                {
                    "date": row[0],
                    "ticker": row[1],
                    "value": row[2],
                    "category": row[3],
                    "subtype": row[4],
                    "costBasis": row[5],
                    "gainLoss": row[6],
                    "gainLossPct": row[7],
                }
                for row in cur.fetchall()
            ]

            # ── Raw transactions (for cashflow + activity) ────────────
            cur = conn.execute(
                "SELECT run_date, action, symbol, amount FROM fidelity_transactions ORDER BY id"
            )
            fidelity_txns = [
                {
                    "runDate": row[0],
                    "action": row[1],
                    "symbol": row[2],
                    "amount": row[3],
                }
                for row in cur.fetchall()
            ]

            cur = conn.execute(
                "SELECT date, type, category, amount FROM qianji_transactions ORDER BY date"
            )
            qianji_txns = [
                {
                    "date": row[0],
                    "type": row[1],
                    "category": row[2],
                    "amount": row[3],
                }
                for row in cur.fetchall()
            ]
        finally:
            conn.close()

        # ── Market + holdings (reuse existing logic) ──────────────────
        mkt = market()
        hd = holdings_detail()

        return {
            "daily": daily,
            "prefix": prefix,
            "dailyTickers": daily_tickers,
            "fidelityTxns": fidelity_txns,
            "qianjiTxns": qianji_txns,
            "market": mkt,
            "holdingsDetail": hd,
        }

    # ── GET /allocation ─────────────────────────────────────────────────

    @app.get("/allocation")
    def allocation(date: str = Query(...)) -> dict[str, Any]:
        conn = get_connection(db_path)
        try:
            row = conn.execute(
                "SELECT total, us_equity, non_us_equity, crypto, safe_net, liabilities "
                "FROM computed_daily WHERE date = ?",
                (date,),
            ).fetchone()

            tickers_rows = conn.execute(
                "SELECT ticker, value, category, subtype, cost_basis, gain_loss, gain_loss_pct "
                "FROM computed_daily_tickers WHERE date = ? ORDER BY value DESC",
                (date,),
            ).fetchall()
        finally:
            conn.close()

        if row is None:
            return {"date": date, "total": 0, "netWorth": 0, "liabilities": 0, "categories": [], "tickers": []}

        total: float = row[0]
        liabilities: float = row[5]
        categories: list[dict[str, Any]] = []
        for i, (name, _col, target) in enumerate(_CATEGORIES):
            value: float = row[1 + i]
            pct = round(value / total * 100, 1) if total else 0.0
            categories.append({
                "name": name,
                "value": value,
                "pct": pct,
                "target": target,
                "deviation": round(pct - target, 1),
            })

        tickers = [
            {
                "ticker": r[0],
                "value": r[1],
                "category": r[2],
                "subtype": r[3],
                "costBasis": r[4],
                "gainLoss": r[5],
                "gainLossPct": r[6],
            }
            for r in tickers_rows
        ]

        return {
            "date": date,
            "total": total,
            "netWorth": round(total + liabilities, 2),
            "liabilities": liabilities,
            "categories": categories,
            "tickers": tickers,
        }

    # ── GET /activity ──────────────────────────────────────────────────

    @app.get("/activity")
    def activity(start: str = Query(...), end: str = Query(...)) -> dict[str, Any]:
        start_sort = start.replace("-", "")
        end_sort = end.replace("-", "")

        conn = get_connection(db_path)
        try:
            rows = conn.execute(
                "SELECT action, symbol, quantity, amount FROM fidelity_transactions "
                "WHERE substr(run_date,7,4) || substr(run_date,1,2) || substr(run_date,4,2) "
                "BETWEEN ? AND ?",
                (start_sort, end_sort),
            ).fetchall()
        finally:
            conn.close()

        buys: dict[str, dict[str, float]] = defaultdict(lambda: {"count": 0, "total": 0.0})
        sells: dict[str, dict[str, float]] = defaultdict(lambda: {"count": 0, "total": 0.0})
        dividends: dict[str, dict[str, float]] = defaultdict(lambda: {"count": 0, "total": 0.0})

        for action, symbol, _qty, amount in rows:
            action_upper = action.upper()
            if not symbol:
                continue
            if action_upper.startswith("YOU BOUGHT"):
                buys[symbol]["count"] += 1
                buys[symbol]["total"] += abs(amount)
            elif action_upper.startswith("YOU SOLD"):
                sells[symbol]["count"] += 1
                sells[symbol]["total"] += abs(amount)
            elif action_upper.startswith("DIVIDEND"):
                dividends[symbol]["count"] += 1
                dividends[symbol]["total"] += amount

        def _to_list(d: dict[str, dict[str, float]]) -> list[dict[str, Any]]:
            items: list[dict[str, Any]] = [
                {"symbol": sym, "count": int(v["count"]), "total": round(v["total"], 2)} for sym, v in d.items()
            ]
            items.sort(key=lambda x: float(x["total"]), reverse=True)
            return items

        return {
            "start": start,
            "end": end,
            "buysBySymbol": _to_list(buys),
            "sellsBySymbol": _to_list(sells),
            "dividendsBySymbol": _to_list(dividends),
        }

    # ── GET /cashflow ──────────────────────────────────────────────────

    @app.get("/cashflow")
    def cashflow(start: str = Query(...), end: str = Query(...)) -> dict[str, Any]:
        conn = get_connection(db_path)
        try:
            rows = conn.execute(
                "SELECT type, category, amount FROM qianji_transactions WHERE date BETWEEN ? AND ?",
                (start, end),
            ).fetchall()
        finally:
            conn.close()

        income_map: dict[str, dict[str, float]] = defaultdict(lambda: {"amount": 0.0, "count": 0})
        expense_map: dict[str, dict[str, float]] = defaultdict(lambda: {"amount": 0.0, "count": 0})
        cc_payments = 0.0

        for txn_type, category, amount in rows:
            if txn_type == "income":
                income_map[category]["amount"] += amount
                income_map[category]["count"] += 1
            elif txn_type == "expense":
                expense_map[category]["amount"] += amount
                expense_map[category]["count"] += 1
            elif txn_type == "repayment":
                cc_payments += amount

        income_items: list[dict[str, Any]] = sorted(
            [{"category": cat, "amount": round(v["amount"], 2), "count": int(v["count"])} for cat, v in income_map.items()],
            key=lambda x: float(x["amount"]),
            reverse=True,
        )
        expense_items: list[dict[str, Any]] = sorted(
            [{"category": cat, "amount": round(v["amount"], 2), "count": int(v["count"])} for cat, v in expense_map.items()],
            key=lambda x: float(x["amount"]),
            reverse=True,
        )

        total_income = round(sum(float(i["amount"]) for i in income_items), 2)
        total_expenses = round(sum(float(e["amount"]) for e in expense_items), 2)
        net_cashflow = round(total_income - total_expenses, 2)
        savings_rate = round((total_income - total_expenses) / total_income * 100, 2) if total_income else 0.0

        return {
            "start": start,
            "end": end,
            "incomeItems": income_items,
            "expenseItems": expense_items,
            "totalIncome": total_income,
            "totalExpenses": total_expenses,
            "netCashflow": net_cashflow,
            "ccPayments": round(cc_payments, 2),
            "savingsRate": savings_rate,
        }

    # ── GET /market ──────────────────────────────────────────────────

    index_names: dict[str, str] = {
        "^GSPC": "S&P 500",
        "^NDX": "NASDAQ 100",
        "VXUS": "VXUS",
        "000300.SS": "CSI 300",
    }

    @app.get("/market")
    def market() -> dict[str, Any]:
        """Return market data from DB (indices, CNY) + optional live FRED."""
        conn = get_connection(db_path)
        try:
            # Latest CNY rate
            cny_row = conn.execute(
                "SELECT close FROM daily_close WHERE symbol='CNY=X' ORDER BY date DESC LIMIT 1"
            ).fetchone()
            usd_cny = cny_row[0] if cny_row else None

            # Index data from daily_close
            indices: list[dict[str, Any]] = []
            for ticker, name in index_names.items():
                rows = conn.execute(
                    "SELECT date, close FROM daily_close WHERE symbol = ? ORDER BY date",
                    (ticker,),
                ).fetchall()
                if len(rows) < 2:
                    continue
                closes = [r[1] for r in rows]
                dates = [r[0] for r in rows]
                current = closes[-1]

                # Month return (~22 trading days back)
                month_idx = max(0, len(closes) - 23)
                month_return = round((current / closes[month_idx] - 1) * 100, 2)

                # YTD return (first trading day of current year)
                current_year = dates[-1][:4]
                ytd_start = next((c for d, c in zip(dates, closes, strict=False) if d.startswith(current_year)), closes[0])
                ytd_return = round((current / ytd_start - 1) * 100, 2)

                # 52w high/low (~252 trading days)
                year_closes = closes[-252:]
                high_52w = max(year_closes)
                low_52w = min(year_closes)

                # Sparkline: last 252 days
                indices.append({
                    "ticker": ticker,
                    "name": name,
                    "monthReturn": month_return,
                    "ytdReturn": ytd_return,
                    "current": current,
                    "sparkline": year_closes,
                    "high52w": high_52w,
                    "low52w": low_52w,
                })
        finally:
            conn.close()

        result: dict[str, Any] = {
            "indices": indices,
            "fedRate": None,
            "treasury10y": None,
            "cpi": None,
            "unemployment": None,
            "vix": None,
            "dxy": None,
            "usdCny": usd_cny,
            "goldReturn": None,
            "btcReturn": None,
            "portfolioMonthReturn": None,
        }

        # Optionally fetch FRED data (only external call)
        fred_key = os.environ.get("FRED_API_KEY", "")
        if fred_key:
            try:
                from .market.fred import fetch_fred_data

                fred = fetch_fred_data(fred_key)
                if fred and "snapshot" in fred:
                    snap = cast(dict[str, Any], fred["snapshot"])
                    for src, dst in [
                        ("fedFundsRate", "fedRate"), ("treasury10y", "treasury10y"),
                        ("cpiYoy", "cpi"), ("unemployment", "unemployment"), ("vix", "vix"),
                    ]:
                        if src in snap:
                            result[dst] = snap[src]
                    result["fred"] = fred
            except Exception:  # noqa: BLE001
                log.warning("Failed to fetch FRED data", exc_info=True)

        return result

    # ── GET /holdings-detail ───────────────────────────────────────────

    @app.get("/holdings-detail")
    def holdings_detail() -> dict[str, Any]:
        """Return per-ticker detail from DB prices (month return, 52w high/low)."""
        empty: dict[str, Any] = {"topPerformers": [], "bottomPerformers": [], "upcomingEarnings": []}

        conn = get_connection(db_path)
        try:
            # Latest date in computed_daily_tickers
            row = conn.execute("SELECT date FROM computed_daily_tickers ORDER BY date DESC LIMIT 1").fetchone()
            if row is None:
                return empty
            latest_date = row[0]
            ticker_rows = conn.execute(
                "SELECT ticker, value FROM computed_daily_tickers WHERE date = ? AND value > 0",
                (latest_date,),
            ).fetchall()

            # Filter to real tickers (skip "401k sp500", "CNY Assets", etc.)
            real_tickers = {t: v for t, v in ticker_rows if t.isascii() and " " not in t and len(t) <= 5}
            if not real_tickers:
                return empty

            stocks: list[dict[str, Any]] = []
            for ticker, value in real_tickers.items():
                closes = conn.execute(
                    "SELECT close FROM daily_close WHERE symbol = ? ORDER BY date",
                    (ticker,),
                ).fetchall()
                if len(closes) < 2:
                    continue
                prices = [r[0] for r in closes]
                current = prices[-1]

                # Month return (~22 trading days)
                month_idx = max(0, len(prices) - 23)
                month_ret = round((current / prices[month_idx] - 1) * 100, 2)
                start_value = round(value / (1 + month_ret / 100), 2) if month_ret != -100 else 0.0

                # 52w high/low
                year_prices = prices[-252:]
                high = max(year_prices)
                low = min(year_prices)
                vs_high = round((current / high - 1) * 100, 2)

                stocks.append({
                    "ticker": ticker,
                    "monthReturn": month_ret,
                    "startValue": start_value,
                    "endValue": round(value, 2),
                    "peRatio": None,
                    "marketCap": None,
                    "high52w": high,
                    "low52w": low,
                    "vsHigh": vs_high,
                    "nextEarnings": None,
                })
        finally:
            conn.close()

        sorted_by_return = sorted(stocks, key=lambda s: float(s["monthReturn"]), reverse=True)
        top = sorted_by_return[:5]
        bottom = sorted_by_return[-5:][::-1] if len(sorted_by_return) > 5 else []
        return {"topPerformers": top, "bottomPerformers": bottom, "upcomingEarnings": []}

    return app


# ── CLI entry point ─────────────────────────────────────────────────────────


def main() -> None:
    """Run the timemachine server."""
    db_path = Path(__file__).resolve().parent.parent / "data" / "timemachine.db"
    app = create_app(db_path)
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
