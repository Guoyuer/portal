"""Qianji ingest — read bills from the source SQLite DB, write ``qianji_transactions``.

Covers:

- ``_load_records`` / ``_load_balances`` — raw readers keyed off the live
  ``user_bill`` / ``user_asset`` tables.
- ``load_all_from_db`` — the public "give me all bills as :class:`QianjiRecord`"
  entry point; wraps :func:`parse_qj_amount` for USD conversion.
- ``ingest_qianji_transactions`` — writes the timemachine DB's
  ``qianji_transactions`` table (replace-all, no upsert).
- Balance-adjustment filtering (manual user reconciliations that aren't
  real cashflow).
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path

from ..db import get_connection, get_readonly_connection
from ..types import QianjiRecord
from .config import _BASE_CURRENCY, _TYPE_MAP, _USER_TZ, DEFAULT_DB_PATH
from .currency import _fetch_live_cny_rate, parse_qj_amount

log = logging.getLogger(__name__)

# Qianji "Balance adjustment(X ~ Y)" rows are manual reconciliations the
# user makes when Qianji's tracked balance drifts from the real bank
# balance — they're arithmetic corrections, not actual spending or income.
# Drop them at ingest so they never pollute cashflow / savings-rate math.
#
# Seen patterns:
#   "Balance adjustment(29,338.34 ~ 25,524.00)"  — long-form with old/new
#   "adjust"                                       — short-form
_BALANCE_ADJUSTMENT_RE = re.compile(
    r"^\s*(balance\s*adjustment|adjust)\b", re.IGNORECASE,
)


def _is_balance_adjustment(remark: str | None) -> bool:
    """True when the bill's remark marks it as a manual balance correction."""
    return bool(remark) and bool(_BALANCE_ADJUSTMENT_RE.match(remark or ""))

_BILL_QUERY = "SELECT id, type, money, fromact, targetact, remark, time, cateid, extra FROM user_bill WHERE status = 1 ORDER BY time"


def _load_records(
    conn: sqlite3.Connection,
    cny_rate: float | None = None,
    *,
    historical_cny_rates: Mapping[date, float] | None = None,
) -> list[QianjiRecord]:
    """Load cashflow records from an open DB connection.

    For the CNY→USD unconverted-label quirk, ``historical_cny_rates`` is the
    primary input: it's a per-date dict of closing rates (loaded via
    :func:`etl.prices.load_cny_rates`) so each bill gets revalued at the FX
    rate of the day it was spent — not today's live rate. That stabilises
    the USD amount of legacy bills across runs. ``cny_rate`` remains as a
    scalar fallback for offline tests that don't build a historical dict.

    Bills are date-truncated in ``_USER_TZ`` (default ``America/Los_Angeles``)
    so the daily cashflow reflects the user's wall-clock, not UTC.
    Balance-adjustment rows (manual reconciliations) are filtered out —
    they're not real cashflow.
    """
    categories = dict(conn.execute("SELECT id, name FROM category"))
    records: list[QianjiRecord] = []
    cny_converted = 0
    skipped_balance_adjustments = 0
    for bill_id, bill_type, money, fromact, targetact, remark, ts, cateid, extra_str in conn.execute(_BILL_QUERY):
        mapped_type = _TYPE_MAP.get(bill_type)
        if mapped_type is None:
            continue
        if _is_balance_adjustment(remark):
            skipped_balance_adjustments += 1
            continue
        dt = datetime.fromtimestamp(ts, tz=_USER_TZ)
        amount = parse_qj_amount(
            money, extra_str, cny_rate=cny_rate,
            bill_date=dt.date(), historical_cny_rates=historical_cny_rates,
        )
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
    log.info(
        "Qianji records: %d total (%s), %d CNY→USD converted, %d balance-adjustment rows skipped",
        len(records),
        ", ".join(f"{t}={c}" for t, c in sorted(by_type.items())),
        cny_converted,
        skipped_balance_adjustments,
    )
    return records


def _load_balances(conn: sqlite3.Connection) -> dict[str, tuple[float, str]]:
    """Load account balances and currencies from an open DB connection."""
    balances = {
        name: (float(money), currency or _BASE_CURRENCY)
        for name, money, currency in conn.execute("SELECT name, money, currency FROM user_asset WHERE status = 0")
    }
    log.info("Qianji balances: %d accounts", len(balances))
    return balances


def load_all_from_db(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    historical_cny_rates: Mapping[date, float] | None = None,
) -> list[QianjiRecord]:
    """Load Qianji cashflow records.

    The live USD/CNY rate is fetched as a last-resort fallback for
    :func:`parse_qj_amount`'s cross-currency quirk handling — in normal
    operation ``historical_cny_rates`` covers every bill's date (with
    7-day weekend walk-back). Returns ``[]`` when the Qianji DB file
    doesn't exist.
    """
    if not db_path.exists():
        return []

    cny_rate = _fetch_live_cny_rate()
    conn = get_readonly_connection(db_path)
    try:
        return _load_records(
            conn, cny_rate=cny_rate, historical_cny_rates=historical_cny_rates,
        )
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
                " (date, type, category, amount, note, is_retirement)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        r["date"][:10],  # truncate datetime to date
                        r["type"],
                        r.get("category", ""),
                        r["amount"],
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
