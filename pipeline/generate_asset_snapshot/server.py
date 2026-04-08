"""FastAPI server for the timemachine API."""
from __future__ import annotations

import logging
import os
from collections import defaultdict
from pathlib import Path
from typing import Any, cast

import uvicorn
import yfinance as yf
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from .db import get_connection
from .market.yahoo import build_market_data, fetch_cny_rate, fetch_index_returns

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
    def timeline() -> dict[str, list[dict[str, Any]]]:
        conn = get_connection(db_path)
        try:
            cur = conn.execute("SELECT date, total, us_equity, non_us_equity, crypto, safe_net FROM computed_daily ORDER BY date")
            daily = [
                {
                    "date": row[0],
                    "total": row[1],
                    "usEquity": row[2],
                    "nonUsEquity": row[3],
                    "crypto": row[4],
                    "safeNet": row[5],
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
        finally:
            conn.close()

        return {"daily": daily, "prefix": prefix}

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

    @app.get("/market")
    def market() -> dict[str, Any]:
        """Return live market data from yfinance + FRED. May be slow."""
        result: dict[str, Any] = {
            "indices": [],
            "fedRate": None,
            "treasury10y": None,
            "cpi": None,
            "unemployment": None,
            "vix": None,
            "dxy": None,
            "usdCny": None,
            "goldReturn": None,
            "btcReturn": None,
            "portfolioMonthReturn": None,
        }

        try:
            cny_rate = fetch_cny_rate()
            result["usdCny"] = cny_rate
        except Exception:  # noqa: BLE001
            log.warning("Failed to fetch CNY rate", exc_info=True)
            cny_rate = None

        try:
            md = build_market_data(cny_rate or 7.0)
            if md is not None:
                result["indices"] = [
                    {
                        "ticker": idx.ticker,
                        "name": idx.name,
                        "monthReturn": idx.month_return,
                        "ytdReturn": idx.ytd_return,
                        "current": idx.current,
                        "sparkline": idx.sparkline,
                        "high52w": idx.high_52w,
                        "low52w": idx.low_52w,
                    }
                    for idx in md.indices
                ]
                if md.fed_rate is not None:
                    result["fedRate"] = md.fed_rate
                if md.treasury_10y is not None:
                    result["treasury10y"] = md.treasury_10y
                if md.cpi is not None:
                    result["cpi"] = md.cpi
                if md.unemployment is not None:
                    result["unemployment"] = md.unemployment
                if md.vix is not None:
                    result["vix"] = md.vix
                if md.dxy is not None:
                    result["dxy"] = md.dxy
                if md.gold_return is not None:
                    result["goldReturn"] = md.gold_return
                if md.btc_return is not None:
                    result["btcReturn"] = md.btc_return
        except Exception:  # noqa: BLE001
            log.warning("Failed to build market data", exc_info=True)

        # Optionally fetch FRED data
        fred_key = os.environ.get("FRED_API_KEY", "")
        if fred_key:
            try:
                from .market.fred import fetch_fred_data

                fred = fetch_fred_data(fred_key)
                if fred and "snapshot" in fred:
                    snap = cast(dict[str, Any], fred["snapshot"])
                    if result["fedRate"] is None and "fedFundsRate" in snap:
                        result["fedRate"] = snap["fedFundsRate"]
                    if result["treasury10y"] is None and "treasury10y" in snap:
                        result["treasury10y"] = snap["treasury10y"]
                    if result["cpi"] is None and "cpiYoy" in snap:
                        result["cpi"] = snap["cpiYoy"]
                    if result["unemployment"] is None and "unemployment" in snap:
                        result["unemployment"] = snap["unemployment"]
                    if result["vix"] is None and "vix" in snap:
                        result["vix"] = snap["vix"]
                    result["fred"] = fred
            except Exception:  # noqa: BLE001
                log.warning("Failed to fetch FRED data", exc_info=True)

        return result

    # ── GET /holdings-detail ───────────────────────────────────────────

    @app.get("/holdings-detail")
    def holdings_detail() -> dict[str, Any]:
        """Return per-ticker detail for portfolio holdings."""
        empty: dict[str, Any] = {"topPerformers": [], "bottomPerformers": [], "upcomingEarnings": []}

        # Read tickers from the latest date in computed_daily_tickers
        conn = get_connection(db_path)
        try:
            row = conn.execute("SELECT date FROM computed_daily_tickers ORDER BY date DESC LIMIT 1").fetchone()
            if row is None:
                return empty
            latest_date = row[0]
            ticker_rows = conn.execute(
                "SELECT ticker, value FROM computed_daily_tickers WHERE date = ?",
                (latest_date,),
            ).fetchall()
        finally:
            conn.close()

        # Filter to real tickers (skip "401k sp500", "CNY Assets", etc.)
        tickers = {t: v for t, v in ticker_rows if t.isascii() and " " not in t and len(t) <= 5 and t}
        if not tickers:
            return empty

        # Batch download 1-month returns
        try:
            returns = fetch_index_returns(list(tickers.keys()), period="1mo")
        except Exception:  # noqa: BLE001
            log.warning("Holdings: failed to fetch returns", exc_info=True)
            return empty

        if not returns:
            return empty

        stocks: list[dict[str, Any]] = []
        for ticker, data in returns.items():
            value = tickers.get(ticker, 0.0)
            month_ret = data["return_pct"]
            start_value = value / (1 + month_ret / 100) if month_ret != -100 else 0.0

            detail: dict[str, Any] = {
                "ticker": ticker,
                "monthReturn": month_ret,
                "startValue": round(start_value, 2),
                "endValue": round(value, 2),
                "peRatio": None,
                "marketCap": None,
                "high52w": None,
                "low52w": None,
                "vsHigh": None,
                "nextEarnings": None,
            }

            try:
                ticker_obj = yf.Ticker(ticker)
                info = ticker_obj.info
                if info:
                    high = info.get("fiftyTwoWeekHigh")
                    low = info.get("fiftyTwoWeekLow")
                    price = info.get("currentPrice") or info.get("regularMarketPrice")
                    detail["peRatio"] = info.get("trailingPE")
                    detail["marketCap"] = info.get("marketCap")
                    detail["high52w"] = high
                    detail["low52w"] = low
                    if high and price:
                        detail["vsHigh"] = round((price / high - 1) * 100, 2)
                    cal = ticker_obj.calendar
                    if cal is not None and "Earnings Date" in cal:
                        dates = cal["Earnings Date"]
                        if dates:
                            detail["nextEarnings"] = dates[0].strftime("%Y-%m-%d")
            except Exception:  # noqa: BLE001
                pass

            stocks.append(detail)

        sorted_by_return = sorted(stocks, key=lambda s: s["monthReturn"], reverse=True)
        top = sorted_by_return[:5]
        bottom = sorted(sorted_by_return[-5:][::-1], key=lambda s: s["monthReturn"]) if len(sorted_by_return) > 5 else []
        upcoming = sorted(
            [s for s in stocks if s["nextEarnings"]],
            key=lambda s: s["nextEarnings"] or "",
        )

        return {"topPerformers": top, "bottomPerformers": bottom, "upcomingEarnings": upcoming}

    return app


# ── CLI entry point ─────────────────────────────────────────────────────────


def main() -> None:
    """Run the timemachine server."""
    db_path = Path(__file__).resolve().parent.parent / "data" / "timemachine.db"
    app = create_app(db_path)
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
