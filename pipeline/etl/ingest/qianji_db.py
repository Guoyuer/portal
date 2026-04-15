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

from ..db import get_connection, get_readonly_connection
from ..types import QJ_EXPENSE, QJ_INCOME, QJ_REPAYMENT, QJ_TRANSFER, QianjiRecord

log = logging.getLogger(__name__)

_MAC_DB_PATH = Path.home() / "Library/Containers/com.mutangtech.qianji.fltios/Data/Documents/qianjiapp.db"
_WIN_DB_PATH = Path(os.environ.get("APPDATA", "")) / "com.mutangtech.qianji.win/qianji_flutter/qianjiapp.db"

# ``QIANJI_DB_PATH_OVERRIDE`` lets L2 regression tests point the build at a
# fixture DB without touching the caller's home directory / %APPDATA%. Unset
# in production; real builds keep the per-platform default.
_OVERRIDE_PATH = os.environ.get("QIANJI_DB_PATH_OVERRIDE")
if _OVERRIDE_PATH:
    DEFAULT_DB_PATH = Path(_OVERRIDE_PATH)
else:
    DEFAULT_DB_PATH = _WIN_DB_PATH if sys.platform == "win32" else _MAC_DB_PATH

# Qianji type codes → internal type names
_TYPE_MAP = {0: QJ_EXPENSE, 1: QJ_INCOME, 2: QJ_TRANSFER, 3: QJ_REPAYMENT}

_BASE_CURRENCY = "USD"
# Minimum difference between base-currency and source-currency amounts to consider
# a real conversion (filters out unconverted records where bv == sv).
_CONVERSION_TOLERANCE = 0.01

_BILL_QUERY = "SELECT id, type, money, fromact, targetact, remark, time, cateid, extra FROM user_bill WHERE status = 1 ORDER BY time"


def _decode_curr(extra_str: str | None) -> dict[str, Any] | None:
    """Return ``extra.curr`` dict, or None if absent/malformed."""
    if not extra_str or extra_str == "null":
        return None
    try:
        extra = json.loads(extra_str)
    except (json.JSONDecodeError, TypeError):
        return None
    curr = extra.get("curr") if isinstance(extra, dict) else None
    return curr if isinstance(curr, dict) else None


def parse_qj_amount(money: float, extra_str: str | None, cny_rate: float | None = None) -> float:
    """Return the base-currency (USD) amount for a Qianji bill.

    Qianji's ``extra.curr`` encodes currency conversion metadata:
      - ``ss`` / ``sv`` — source currency + amount
      - ``bs`` / ``bv`` — base currency + amount (USD-denominated)
      - ``ts`` / ``tv`` — target currency + amount (transfers only)

    For cashflow aggregation we need USD, so return ``bv`` when the bill
    crossed currencies and ``bv != sv``.

    **Qianji data quirk:** Some bills have ``ss != bs`` (e.g. source CNY, base
    USD) but ``bv == sv`` — Qianji labelled the base as USD but the user
    never entered the conversion. When this happens:
      - If ``cny_rate`` is provided and ``ss == "CNY"`` and ``bs == "USD"``,
        convert ``money`` (source CNY) using the live rate.
      - Else log a warning and fall back to ``money`` unchanged.
    """
    curr = _decode_curr(extra_str)
    if curr is None:
        return float(money)
    ss, bs, bv, sv = curr.get("ss"), curr.get("bs"), curr.get("bv"), curr.get("sv")
    if ss and bs and ss != bs and bv is not None and sv is not None:
        if abs(bv - sv) > _CONVERSION_TOLERANCE:
            return float(bv)
        # Unconverted quirk: ss != bs but bv == sv.
        if ss == "CNY" and bs == "USD" and cny_rate:
            log.warning(
                "Qianji bill with unconverted CNY→USD label (bv=sv=%.2f); "
                "converting source amount %.2f CNY → USD at live rate %.4f",
                sv, money, cny_rate,
            )
            return float(money) / cny_rate
        log.warning(
            "Qianji bill with unconverted cross-currency label (ss=%s bs=%s "
            "bv==sv=%.2f); returning source amount unchanged", ss, bs, sv,
        )
    return float(money)


def parse_qj_target_amount(money: float, extra_str: str | None) -> float:
    """Return the target-currency amount received by ``targetact`` in a transfer.

    For a cross-currency transfer, ``extra.curr.tv`` holds the amount the
    target account received in its native currency. Same-currency or
    non-transfer rows fall back to ``money`` (source amount).
    """
    curr = _decode_curr(extra_str)
    if curr is None:
        return float(money)
    ss, ts, tv = curr.get("ss"), curr.get("ts"), curr.get("tv")
    if ss and ts and ss != ts and tv is not None and tv > 0:
        return float(tv)
    return float(money)


def _load_records(conn: sqlite3.Connection, cny_rate: float | None = None) -> list[QianjiRecord]:
    """Load cashflow records from an open DB connection.

    ``cny_rate`` (optional) is passed through to :func:`parse_qj_amount` so
    the data-quirk fallback (``ss != bs`` but ``bv == sv``) can convert
    CNY source amounts to USD. When not provided, quirky rows emit a
    warning and fall back to ``money`` unchanged.
    """
    categories = dict(conn.execute("SELECT id, name FROM category"))
    records: list[QianjiRecord] = []
    cny_converted = 0
    for bill_id, bill_type, money, fromact, targetact, remark, ts, cateid, extra_str in conn.execute(_BILL_QUERY):
        mapped_type = _TYPE_MAP.get(bill_type)
        if mapped_type is None:
            continue
        dt = datetime.fromtimestamp(ts, tz=UTC)
        amount = parse_qj_amount(money, extra_str, cny_rate=cny_rate)
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


def _build_snapshot(
    db_path: Path,
    balances: dict[str, tuple[float, str]],
    cny_rate: float,
) -> dict[str, Any]:
    """Build a snapshot dict from balances, DB file modification time, and a pre-fetched CNY rate.

    The rate is passed in (rather than fetched here) so ``load_all_from_db``
    can share one Yahoo call across both :func:`_load_records` (for the
    cross-currency data-quirk fallback in :func:`parse_qj_amount`) and this
    snapshot — the user's monthly cashflow math and their balance snapshot
    must use the same rate, and two separate fetches would risk drift.
    """
    mtime = os.path.getmtime(db_path)
    return {
        "date": datetime.fromtimestamp(mtime, tz=UTC).strftime("%Y-%m-%d"),
        "cny_rate": cny_rate,
        "balances": {name: bal for name, (bal, _) in balances.items()},
        "currencies": {name: curr for name, (_, curr) in balances.items()},
    }


def load_all_from_db(
    db_path: Path = DEFAULT_DB_PATH,
) -> tuple[list[QianjiRecord], dict[str, Any]]:
    """Load both cashflow records and balances in a single DB connection.

    Fetches the live USD/CNY rate once and shares it with both the record
    loader (for the unconverted-label data quirk — see :func:`parse_qj_amount`)
    and the snapshot. Returns ``([], {})`` when the DB doesn't exist.
    """
    if not db_path.exists():
        return [], {}

    cny_rate = _fetch_live_cny_rate()
    conn = get_readonly_connection(db_path)
    try:
        records = _load_records(conn, cny_rate=cny_rate)
        balances = _load_balances(conn)
        snapshot = _build_snapshot(db_path, balances, cny_rate)
        return records, snapshot
    finally:
        conn.close()


# ── Ingestion into timemachine database ──────────────────────────────────────


def ingest_qianji_transactions(
    db_path: Path,
    records: list[QianjiRecord],
    *,
    retirement_categories: list[str] | None = None,
) -> int:
    """Ingest Qianji transaction records into the database.

    Clears and replaces all rows. An ``is_retirement`` flag is set on income
    rows whose ``category`` (exact match, case-sensitive) appears in
    ``retirement_categories`` — this is the canonical way for the frontend
    to compute take-home savings rate without substring sniffing.

    Returns row count.
    """
    retirement_set = set(retirement_categories or [])

    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM qianji_transactions")
        if records:
            conn.executemany(
                "INSERT INTO qianji_transactions"
                " (date, type, category, amount, account, note, is_retirement)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        r["date"][:10],  # truncate datetime to date
                        r["type"],
                        r.get("category", ""),
                        r["amount"],
                        r.get("account_from", ""),
                        r.get("note", ""),
                        1 if (r["type"] == "income" and r.get("category", "") in retirement_set) else 0,
                    )
                    for r in records
                ],
            )
        conn.commit()
        count: int = conn.execute("SELECT COUNT(*) FROM qianji_transactions").fetchone()[0]
    finally:
        conn.close()
    return count
