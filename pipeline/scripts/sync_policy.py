"""Shared D1 sync policy.

This module is the single source of truth for which tables are synced and
which write mode each table uses. ``sync_to_d1.py`` uses it to generate SQL;
``verify_vs_prod.py`` uses it to decide which prod rows are at risk if local
is short before a sync.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Literal

SyncMode = Literal["diff", "range", "full"]

# When --since is not supplied, derive cutoff as (latest fidelity run_date - N
# days). 60 days comfortably exceeds Fidelity's typical CSV export window.
AUTO_SINCE_LOOKBACK_DAYS = 60

TABLES_TO_SYNC: list[str] = [
    "computed_daily",
    "computed_daily_tickers",
    "fidelity_transactions",
    "robinhood_transactions",
    "empower_contributions",
    "qianji_transactions",
    "computed_market_indices",
    "computed_holdings_detail",
    "econ_series",
    "daily_close",
    "categories",
]

# Tables that use INSERT OR IGNORE in diff mode. Local can be short without
# deleting prod rows because prod extras are preserved.
DIFF_TABLES: set[str] = {"daily_close"}

# Tables that use range-replace in diff mode. Value is a SQL expression that
# yields a YYYY-MM-DD-sortable string for date comparison.
RANGE_TABLES: dict[str, str] = {
    "fidelity_transactions": "run_date",
    "robinhood_transactions": "txn_date",
    "qianji_transactions": "date",
    "computed_daily": "date",
    "computed_daily_tickers": "date",
}


def sync_mode_for_table(table: str, *, full: bool = False) -> SyncMode:
    """Return the write mode used for ``table`` in the selected sync."""
    if full:
        return "full"
    if table in DIFF_TABLES:
        return "diff"
    if table in RANGE_TABLES:
        return "range"
    return "full"


def auto_derive_since(conn: sqlite3.Connection, *, today: date | None = None) -> str:
    """Derive the default range-replace cutoff from local Fidelity rows."""
    row = conn.execute("SELECT MAX(run_date) FROM fidelity_transactions").fetchone()
    if row and row[0]:
        latest = date.fromisoformat(row[0])
    else:
        latest = today or date.today()
    return (latest - timedelta(days=AUTO_SINCE_LOOKBACK_DAYS)).isoformat()
