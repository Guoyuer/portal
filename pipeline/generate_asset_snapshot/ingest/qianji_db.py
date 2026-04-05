"""Read Qianji data directly from the local SQLite database.

Platform-specific default paths:
- macOS: ~/Library/Containers/com.mutangtech.qianji.fltios/Data/Documents/qianjiapp.db
- Windows: %APPDATA%/com.mutangtech.qianji.win/qianji_flutter/qianjiapp.db

This is more reliable than CSV export:
- Always up-to-date (synced by the app)
- No manual export needed
- Includes accurate account balances (user_asset.money)
- Includes all transactions (user_bill)
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..types import QJ_EXPENSE, QJ_INCOME, QJ_REPAYMENT, QJ_TRANSFER, QianjiRecord

log = logging.getLogger(__name__)

_MAC_DB_PATH = Path.home() / "Library/Containers/com.mutangtech.qianji.fltios/Data/Documents/qianjiapp.db"
_WIN_DB_PATH = Path(os.environ.get("APPDATA", "")) / "com.mutangtech.qianji.win/qianji_flutter/qianjiapp.db"

DEFAULT_DB_PATH = _WIN_DB_PATH if sys.platform == "win32" else _MAC_DB_PATH

# Qianji type codes → internal type names
_TYPE_MAP = {0: QJ_EXPENSE, 1: QJ_INCOME, 2: QJ_TRANSFER, 3: QJ_REPAYMENT}

_BILL_QUERY = "SELECT id, type, money, fromact, targetact, remark, time, cateid FROM user_bill ORDER BY time"


def _load_records(conn: sqlite3.Connection) -> list[QianjiRecord]:
    """Load cashflow records from an open DB connection."""
    categories = dict(conn.execute("SELECT id, name FROM category"))
    records: list[QianjiRecord] = []
    for bill_id, bill_type, money, fromact, targetact, remark, ts, cateid in conn.execute(_BILL_QUERY):
        mapped_type = _TYPE_MAP.get(bill_type)
        if mapped_type is None:
            continue
        dt = datetime.fromtimestamp(ts, tz=UTC)
        records.append(
            {
                "id": str(bill_id),
                "date": dt.strftime("%Y-%m-%d %H:%M:%S"),
                "category": categories.get(cateid, ""),
                "subcategory": "",
                "type": mapped_type,
                "amount": float(money),
                "currency": "USD",
                "account_from": fromact or "",
                "account_to": targetact or "",
                "note": remark or "",
            }
        )
    by_type: dict[str, int] = {}
    for r in records:
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1
    log.info("Qianji records: %d total (%s)", len(records), ", ".join(f"{t}={c}" for t, c in sorted(by_type.items())))
    return records


def _load_balances(conn: sqlite3.Connection) -> dict[str, float]:
    """Load account balances from an open DB connection."""
    balances = {
        name: float(money)
        for name, money, _currency in conn.execute("SELECT name, money, currency FROM user_asset WHERE status = 0")
    }
    log.info("Qianji balances: %d accounts", len(balances))
    return balances


def _fetch_live_cny_rate() -> float:
    """Fetch live USD/CNY rate. Raises if unavailable."""
    from ..market.yahoo import fetch_cny_rate

    rate = fetch_cny_rate()
    log.info("USD/CNY rate: %.4f (live from Yahoo Finance)", rate)
    return rate


def _build_snapshot(db_path: Path, balances: dict[str, float]) -> dict[str, Any]:
    """Build a snapshot dict from balances and DB file modification time."""
    mtime = os.path.getmtime(db_path)
    return {
        "date": datetime.fromtimestamp(mtime, tz=UTC).strftime("%Y-%m-%d"),
        "cny_rate": _fetch_live_cny_rate(),
        "balances": balances,
    }


def load_all_from_db(
    db_path: Path = DEFAULT_DB_PATH,
) -> tuple[list[QianjiRecord], dict[str, Any]]:
    """Load both cashflow records and balances in a single DB connection.

    Returns (cashflow_records, balance_snapshot). If DB doesn't exist,
    returns ([], {}).
    """
    if not db_path.exists():
        return [], {}

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        records = _load_records(conn)
        balances = _load_balances(conn)
        snapshot = _build_snapshot(db_path, balances)
        return records, snapshot
    finally:
        conn.close()
