"""FastAPI server for the timemachine API."""
from __future__ import annotations

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
                "SELECT total, us_equity, non_us_equity, crypto, safe_net "
                "FROM computed_daily WHERE date = ?",
                (date,),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return {"date": date, "categories": []}

        total: float = row[0]
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

        return {"date": date, "total": total, "categories": categories}

    return app


# ── CLI entry point ─────────────────────────────────────────────────────────


def main() -> None:
    """Run the timemachine server."""
    db_path = Path(__file__).resolve().parent.parent / "data" / "timemachine.db"
    app = create_app(db_path)
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
