"""DB read/write helpers for daily_close.

Handles everything except yfinance I/O and split cross-validation:
  * :func:`_persist_close_batch` — write path honoring the refresh-window
    immutability invariant.
  * :func:`_cached_start` — earliest cached date for a symbol.
  * :func:`holding_periods_from_action_kind_rows` — pure computation over
    Fidelity-shaped rows.
  * :func:`symbol_holding_periods_from_db` — wraps the row query against
    ``fidelity_transactions`` in the local SQLite DB.
  * :func:`load_prices` / :func:`load_cny_rates` — read paths consumed by
    :mod:`etl.allocation` and friends.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

from ..db import get_connection
from ..sources._types import ActionKind
from ..sources.fidelity import MM_SYMBOLS

# Action kinds that change share count — same set the replay primitive uses
# for position accumulation (BUY / SELL / REINVESTMENT plus the qty-only
# kinds for redemptions, distributions, exchanges, and transfers).
_POSITION_KINDS = frozenset({
    ActionKind.BUY,
    ActionKind.SELL,
    ActionKind.REINVESTMENT,
    ActionKind.REDEMPTION,
    ActionKind.DISTRIBUTION,
    ActionKind.EXCHANGE,
    ActionKind.TRANSFER,
})


def _persist_close_batch(
    conn: sqlite3.Connection,
    symbol: str,
    rows: list[tuple[date, float]],
    refresh_cutoff_iso: str,
    *,
    refresh_in_window: bool = True,
) -> tuple[int, int]:
    """Persist ``(date, close)`` rows for one symbol, honoring the refresh window.

    Returns ``(new_historical, refreshed_recent)`` — counts of rows written to
    the immutable history vs the refresh window. Used by both
    ``fetch_and_store_prices`` and ``fetch_and_store_cny_rates``; factored out
    to keep the accounting identical in both places.

    When ``refresh_in_window=False`` (used for CNY=X to avoid intraday FX drift),
    every date is INSERT OR IGNORE; ``refreshed_recent`` is always 0.
    """
    new_historical = 0
    refreshed_recent = 0
    for d, value in rows:
        d_iso = d.isoformat()
        if d_iso < refresh_cutoff_iso or not refresh_in_window:
            cur = conn.execute(
                "INSERT OR IGNORE INTO daily_close (symbol, date, close) VALUES (?, ?, ?)",
                (symbol, d_iso, value),
            )
            if cur.rowcount > 0:
                new_historical += 1
        else:
            conn.execute(
                "INSERT OR REPLACE INTO daily_close (symbol, date, close) VALUES (?, ?, ?)",
                (symbol, d_iso, value),
            )
            refreshed_recent += 1
    return new_historical, refreshed_recent


# ── Symbol holding periods ──────────────────────────────────────────────────


def holding_periods_from_action_kind_rows(
    rows: list[tuple[str, str, str, float]],
) -> dict[str, tuple[date, date | None]]:
    """Compute ``{symbol: (first_buy_date, last_sell_date_or_None)}``.

    Each row is ``(run_date_iso, symbol, action_kind, quantity)`` —
    pre-fetched from a Fidelity-shaped table. Symbol-stripping + ``qty``
    coercion happen here so call sites can pass raw DB rows directly.
    Used by :func:`symbol_holding_periods_from_db` against a local SQLite DB.
    """
    holdings: dict[str, float] = {}
    first_held: dict[str, date] = {}
    last_zero: dict[str, date] = {}

    for run_date, sym, action_kind, qty in rows:
        sym = (sym or "").strip()
        qty = qty or 0.0
        if not sym or sym in MM_SYMBOLS or qty == 0:
            continue
        try:
            kind = ActionKind(action_kind) if action_kind else ActionKind.OTHER
        except ValueError:
            kind = ActionKind.OTHER
        if kind not in _POSITION_KINDS:
            continue

        txn_date = date.fromisoformat(run_date.strip())
        holdings[sym] = holdings.get(sym, 0) + qty

        if sym not in first_held:
            first_held[sym] = txn_date

        if abs(holdings[sym]) < 0.001:
            last_zero[sym] = txn_date

    result: dict[str, tuple[date, date | None]] = {}
    for sym in first_held:
        if sym[0].isdigit():  # skip CUSIPs
            continue
        start = first_held[sym]
        end = last_zero.get(sym) if abs(holdings.get(sym, 0)) < 0.001 else None
        result[sym] = (start, end)

    return result


def symbol_holding_periods_from_db(db_path: Path) -> dict[str, tuple[date, date | None]]:
    """Return ``{symbol: (first_buy_date, last_sell_date_or_None)}`` from the
    ``fidelity_transactions`` table.

    A "holding period" is the chronological span between the first
    position-impacting action on a symbol and the most recent date the
    cumulative quantity dropped to zero. Symbols still held at the cutoff
    have ``end=None``. CUSIPs (T-Bills, brokered CDs) and money-market
    funds are excluded — neither participates in the price-fetch path that
    consumes this function.
    """
    conn = get_connection(db_path)
    try:
        db_rows = conn.execute(
            "SELECT run_date, symbol, action_kind, quantity FROM fidelity_transactions"
            " ORDER BY run_date, id"
        ).fetchall()
    finally:
        conn.close()

    return holding_periods_from_action_kind_rows(list(db_rows))


# ── Cache helpers ───────────────────────────────────────────────────────────


def _cached_start(conn: sqlite3.Connection, symbol: str) -> date | None:
    row = conn.execute(
        "SELECT MIN(date) FROM daily_close WHERE symbol = ?", (symbol,)
    ).fetchone()
    if row and row[0]:
        return date.fromisoformat(row[0])
    return None


# ── Loading from DB ─────────────────────────────────────────────────────────


def load_prices(db_path: Path) -> pd.DataFrame:
    """Load all non-CNY prices from daily_close as a forward-filled DataFrame.

    Returns DataFrame indexed by date with one column per symbol.
    """
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT symbol, date, close FROM daily_close WHERE symbol != 'CNY=X' ORDER BY date"
        ).fetchall()
    finally:
        conn.close()

    result: dict[str, dict[str, float]] = {}
    for sym, d, close in rows:
        if d not in result:
            result[d] = {}
        result[d][sym] = close

    if not result:
        return pd.DataFrame()

    df = pd.DataFrame.from_dict(result, orient="index")
    df.index = [date.fromisoformat(d) for d in df.index]
    df = df.sort_index()
    df = df.ffill()
    print(f"Prices loaded: {df.shape[0]} days x {df.shape[1]} symbols")
    return df


def load_cny_rates(db_path: Path) -> dict[date, float]:
    """Load CNY=X rates from daily_close."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT date, close FROM daily_close WHERE symbol = 'CNY=X' ORDER BY date"
        ).fetchall()
    finally:
        conn.close()

    rates: dict[date, float] = {}
    for d, close in rows:
        rates[date.fromisoformat(d)] = close
    print(f"CNY rates loaded: {len(rates)} days")
    return rates
