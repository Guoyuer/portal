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
import re
import sqlite3
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

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

# Qianji stores each bill's ``time`` as a Unix epoch captured at the moment
# the user taps save — the timestamp itself is timezone-agnostic, but which
# *day* we attribute it to depends on the user's wall-clock. Truncating in
# UTC is almost never right: for a user on the US West Coast, 39% of bills
# (everything logged after ~16:00 local) get attributed to the following
# UTC day — systematically mis-dating daily cashflow by one day.
#
# ``QIANJI_USER_TZ`` lets callers pin a different zone for tests / fixtures.
# Default is the zone the user actually lives in (PT); the L2 regression
# fixture overrides to UTC to keep the golden deterministic.
_USER_TZ = ZoneInfo(os.environ.get("QIANJI_USER_TZ", "America/Los_Angeles"))

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


def _resolve_cny_rate(
    bill_date: date | None,
    historical: Mapping[date, float] | None,
    fallback: float | None,
) -> float | None:
    """Pick the CNY rate for a given bill date, with weekend walk-back.

    yfinance only publishes CNY=X close rates on weekdays, but Qianji bills
    get wall-clock timestamps that fall on any day. Walking back up to a
    week covers weekends + US market holidays. Returns the scalar fallback
    when no historical data is available at all — preserves backward-compat
    for callers (and tests) that never supply historical rates.
    """
    if bill_date is not None and historical:
        for delta in range(8):
            d = bill_date - timedelta(days=delta)
            if d in historical:
                return historical[d]
    return fallback


def parse_qj_amount(
    money: float,
    extra_str: str | None,
    cny_rate: float | None = None,
    *,
    bill_date: date | None = None,
    historical_cny_rates: Mapping[date, float] | None = None,
) -> float:
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
      - If ``bill_date`` + ``historical_cny_rates`` are supplied, use the
        rate for the bill's date (walks back up to 7 days for weekends /
        holidays). **This is the primary path** — it makes the USD amount
        stable across runs, so the changelog snapshot's content-tuple
        identity doesn't ghost on FX drift.
      - Else if ``cny_rate`` is supplied, use the scalar (legacy path,
        still needed by tests and offline fixtures).
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
        if ss == "CNY" and bs == "USD":
            rate = _resolve_cny_rate(bill_date, historical_cny_rates, cny_rate)
            if rate:
                log.warning(
                    "Qianji bill with unconverted CNY→USD label (bv=sv=%.2f); "
                    "converting source amount %.2f CNY → USD at rate %.4f",
                    sv, money, rate,
                )
                return float(money) / rate
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


def _fetch_live_cny_rate() -> float:
    """Fetch live USD/CNY rate. Raises if unavailable.

    ``QIANJI_CNY_RATE_OVERRIDE`` lets offline callers (L2 regression
    fixtures, CI when Yahoo is flaky) pin a known rate instead of going
    through yfinance. Unset in production.
    """
    override = os.environ.get("QIANJI_CNY_RATE_OVERRIDE")
    if override:
        rate = float(override)
        log.info("USD/CNY rate: %.4f (override via QIANJI_CNY_RATE_OVERRIDE)", rate)
        return rate
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
    *,
    historical_cny_rates: Mapping[date, float] | None = None,
) -> tuple[list[QianjiRecord], dict[str, Any]]:
    """Load both cashflow records and balances in a single DB connection.

    The live USD/CNY rate is fetched once: records use it only as a fallback
    when ``historical_cny_rates`` is missing or doesn't cover the bill's
    date; the snapshot still uses the live rate directly because it
    represents current account balances. Returns ``([], {})`` when the
    Qianji DB file doesn't exist.
    """
    if not db_path.exists():
        return [], {}

    cny_rate = _fetch_live_cny_rate()
    conn = get_readonly_connection(db_path)
    try:
        records = _load_records(
            conn, cny_rate=cny_rate, historical_cny_rates=historical_cny_rates,
        )
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


# ── Point-in-time balance replay (read from Qianji source DB) ───────────────


@dataclass(frozen=True)
class QianjiSnapshot:
    """Qianji account state at some as_of date.

    ``balances`` is per-account balance in each account's native currency;
    ``currencies`` maps account name to ISO currency code (e.g. ``USD`` /
    ``CNY``). Currencies are snapshot-time independent — they're read from
    ``user_asset.currency`` alongside balances in a single SELECT so
    consumers don't need a second query.
    """
    balances: dict[str, float] = field(default_factory=dict)
    currencies: dict[str, str] = field(default_factory=dict)


def qianji_balances_at(db_path: Path, as_of: date | None = None) -> QianjiSnapshot:
    """Return Qianji account balances + currencies at ``as_of``.

    Starts from current balances (``user_asset``); when ``as_of`` is given,
    reverses every bill with ``time`` after end-of-day ``as_of`` (wall-clock
    in :data:`_USER_TZ`). Each account balance stays in its native currency.

    Qianji bill-type conventions:
      - expense  (type 0): fromact loses money
      - income   (type 1): fromact gains money
      - transfer (type 2): fromact→targetact (cross-currency uses
        ``extra.curr.tv``)
      - repayment(type 3): same as transfer

    When the Qianji DB doesn't exist, returns an empty snapshot.
    """
    if not db_path.exists():
        log.warning("Qianji DB not found: %s", db_path)
        return QianjiSnapshot()

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        balances: dict[str, float] = {}
        currencies: dict[str, str] = {}
        for name, money, currency in conn.execute(
            "SELECT name, money, currency FROM user_asset WHERE status = 0"
        ):
            balances[name] = float(money)
            currencies[name] = currency or _BASE_CURRENCY

        if as_of is None:
            return QianjiSnapshot(balances=balances, currencies=currencies)

        # Reverse all transactions after end of as_of day, anchored in the
        # user's wall-clock timezone. UTC cutoff would make "as_of=2026-04-15"
        # end at 4 PM PT that day, mis-reversing any late-evening activity.
        cutoff = datetime(
            as_of.year, as_of.month, as_of.day, 23, 59, 59, tzinfo=_USER_TZ,
        ).timestamp()

        for bill_type, money, fromact, targetact, extra_str in conn.execute(
            "SELECT type, money, fromact, targetact, extra "
            "FROM user_bill WHERE status = 1 AND time > ? ORDER BY time",
            (cutoff,),
        ):
            money = float(money)
            fromact = fromact or ""
            targetact = targetact or ""
            tv = parse_qj_target_amount(money, extra_str)

            if bill_type == 0:  # expense: fromact lost money → add back
                if fromact:
                    balances[fromact] = balances.get(fromact, 0) + money
            elif bill_type == 1:  # income: fromact gained money → subtract
                if fromact:
                    balances[fromact] = balances.get(fromact, 0) - money
            elif bill_type in (2, 3):  # transfer/repayment: reverse both sides
                if fromact:
                    balances[fromact] = balances.get(fromact, 0) + money
                if targetact:
                    balances[targetact] = balances.get(targetact, 0) - tv

        return QianjiSnapshot(balances=balances, currencies=currencies)
    finally:
        conn.close()
