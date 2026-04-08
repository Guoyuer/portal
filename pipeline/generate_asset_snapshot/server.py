"""FastAPI server for the timemachine API."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from .db import get_connection

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

    return app


# ── CLI entry point ─────────────────────────────────────────────────────────


def main() -> None:
    """Run the timemachine server."""
    db_path = Path(__file__).resolve().parent.parent / "data" / "timemachine.db"
    app = create_app(db_path)
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
