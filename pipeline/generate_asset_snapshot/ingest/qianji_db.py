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

import json
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

_BASE_CURRENCY = "USD"
# Minimum difference between base-currency and source-currency amounts to consider
# a real conversion (filters out unconverted records where bv == sv).
_CONVERSION_TOLERANCE = 0.01

_BILL_QUERY = "SELECT id, type, money, fromact, targetact, remark, time, cateid, extra FROM user_bill WHERE status = 1 ORDER BY time"


def _parse_amount(money: float, extra_str: str | None) -> float:
    """Return base-currency amount, using currency conversion from extra if available."""
    if not extra_str or extra_str == "null":
        return float(money)
    try:
        extra = json.loads(extra_str)
    except (json.JSONDecodeError, TypeError):
        return float(money)
    curr = extra.get("curr") if isinstance(extra, dict) else None
    if not isinstance(curr, dict):
        return float(money)
    ss, bs, bv, sv = curr.get("ss"), curr.get("bs"), curr.get("bv"), curr.get("sv")
    if ss and bs and ss != bs and bv is not None and sv is not None and abs(bv - sv) > _CONVERSION_TOLERANCE:
        return float(bv)
    return float(money)


def _load_records(conn: sqlite3.Connection) -> list[QianjiRecord]:
    """Load cashflow records from an open DB connection."""
    categories = dict(conn.execute("SELECT id, name FROM category"))
    records: list[QianjiRecord] = []
    cny_converted = 0
    for bill_id, bill_type, money, fromact, targetact, remark, ts, cateid, extra_str in conn.execute(_BILL_QUERY):
        mapped_type = _TYPE_MAP.get(bill_type)
        if mapped_type is None:
            continue
        dt = datetime.fromtimestamp(ts, tz=UTC)
        amount = _parse_amount(money, extra_str)
        if abs(amount - float(money)) > 0.01:
            cny_converted += 1
        records.append(
            {
                "id": str(bill_id),
                "date": dt.strftime("%Y-%m-%d %H:%M:%S"),
                "category": categories.get(cateid, ""),
                "subcategory": "",
                "type": mapped_type,
                "amount": amount,
                "currency": "USD",
                "account_from": fromact or "",
                "account_to": targetact or "",
                "note": remark or "",
            }
        )
    by_type: dict[str, int] = {}
    for r in records:
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1
    log.info("Qianji records: %d total (%s), %d CNY→USD converted", len(records), ", ".join(f"{t}={c}" for t, c in sorted(by_type.items())), cny_converted)
    return records


def _load_balances(conn: sqlite3.Connection) -> dict[str, tuple[float, str]]:
    """Load account balances and currencies from an open DB connection."""
    balances = {
        name: (float(money), currency or _BASE_CURRENCY)
        for name, money, currency in conn.execute("SELECT name, money, currency FROM user_asset WHERE status = 0")
    }
    log.info("Qianji balances: %d accounts", len(balances))
    return balances


def _fetch_live_cny_rate() -> float:
    """Fetch live USD/CNY rate. Raises if unavailable."""
    from ..market.yahoo import fetch_cny_rate

    rate = fetch_cny_rate()
    log.info("USD/CNY rate: %.4f (live from Yahoo Finance)", rate)
    return rate


def _build_snapshot(db_path: Path, balances: dict[str, tuple[float, str]]) -> dict[str, Any]:
    """Build a snapshot dict from balances and DB file modification time."""
    mtime = os.path.getmtime(db_path)
    return {
        "date": datetime.fromtimestamp(mtime, tz=UTC).strftime("%Y-%m-%d"),
        "cny_rate": _fetch_live_cny_rate(),
        "balances": {name: bal for name, (bal, _) in balances.items()},
        "currencies": {name: curr for name, (_, curr) in balances.items()},
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
