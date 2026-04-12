"""Historical price fetching and caching via timemachine.db.

Prices are stored in the ``daily_close`` table (shared with CNY rates).
The yfinance download is only triggered for gaps in the cache.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

from .db import get_connection
from .timemachine import MM_SYMBOLS, POSITION_PREFIXES, _float, _load_raw_rows, _parse_date

# ── Symbol holding periods ──────────────────────────────────────────────────


def symbol_holding_periods(store_path: Path) -> dict[str, tuple[date, date | None]]:
    """Return {symbol: (first_buy_date, last_sell_date_or_None)} from Fidelity CSV.

    last date is None if still held at end of data.
    Only includes tradeable tickers (no CUSIPs like 912796CR8).
    """
    rows = _load_raw_rows(store_path)

    holdings: dict[str, float] = {}
    first_held: dict[str, date] = {}
    last_zero: dict[str, date] = {}

    for row in rows:
        sym = (row.get("Symbol") or "").strip()
        action = (row.get("Action") or "").upper()
        qty = _float(row.get("Quantity", ""))
        if not sym or sym in MM_SYMBOLS or qty == 0:
            continue
        if not any(action.startswith(p) for p in POSITION_PREFIXES):
            continue

        txn_date = _parse_date(row["Run Date"])
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
    """Like symbol_holding_periods but reads from fidelity_transactions table."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT run_date, symbol, action, quantity FROM fidelity_transactions"
            " ORDER BY substr(run_date,7,4)||substr(run_date,1,2)||substr(run_date,4,2), id"
        ).fetchall()
    finally:
        conn.close()

    holdings: dict[str, float] = {}
    first_held: dict[str, date] = {}
    last_zero: dict[str, date] = {}

    for run_date, sym, action, qty in rows:
        sym = sym.strip()
        action_upper = action.upper()
        if not sym or sym in MM_SYMBOLS or qty == 0:
            continue
        if not any(action_upper.startswith(p) for p in POSITION_PREFIXES):
            continue
        txn_date = _parse_date(run_date)
        holdings[sym] = holdings.get(sym, 0) + qty
        if sym not in first_held:
            first_held[sym] = txn_date
        if abs(holdings[sym]) < 0.001:
            last_zero[sym] = txn_date

    result: dict[str, tuple[date, date | None]] = {}
    for sym in first_held:
        if sym[0].isdigit():
            continue
        start = first_held[sym]
        end = last_zero.get(sym) if abs(holdings.get(sym, 0)) < 0.001 else None
        result[sym] = (start, end)
    return result


# ── Cache helpers ───────────────────────────────────────────────────────────


def _cached_range(conn: sqlite3.Connection, symbol: str) -> tuple[date | None, date | None]:
    row = conn.execute(
        "SELECT MIN(date), MAX(date) FROM daily_close WHERE symbol = ?", (symbol,)
    ).fetchone()
    if row and row[0]:
        return date.fromisoformat(row[0]), date.fromisoformat(row[1])
    return None, None


# ── Split adjustment reversal ──────────────────────────────────────────────


def _build_split_factors(symbols: list[str]) -> dict[str, list[tuple[date, float]]]:
    """Fetch split history for symbols and return {symbol: [(split_date, ratio), ...]}."""
    result: dict[str, list[tuple[date, float]]] = {}
    for sym in symbols:
        try:
            splits = yf.Ticker(sym).splits
            if splits.empty:
                continue
            entries: list[tuple[date, float]] = []
            for dt, ratio in splits.items():
                d = dt.date() if hasattr(dt, "date") else dt
                if hasattr(d, "date"):
                    d = d.date()  # handle tz-aware Timestamp
                entries.append((d, float(ratio)))
            if entries:
                result[sym] = sorted(entries)
        except Exception:
            continue
    if result:
        print(f"  Splits found: {', '.join(f'{s} ({len(v)})' for s, v in result.items())}")
    return result


def _reverse_split_factor(d: date, splits: list[tuple[date, float]]) -> float:
    """Compute the cumulative factor to reverse Yahoo's retroactive split adjustment.

    Yahoo adjusts all historical Close prices for splits. To recover the actual
    market close, multiply by the product of all split ratios after the given date.
    """
    factor = 1.0
    for split_date, ratio in splits:
        if d < split_date:
            factor *= ratio
    return factor


# ── Price fetching + storage ────────────────────────────────────────────────


def fetch_and_store_prices(
    db_path: Path,
    holding_periods: dict[str, tuple[date, date | None]],
    end: date,
    global_start: date | None = None,
) -> None:
    """Fetch daily close prices via yfinance and store in timemachine.db.daily_close.

    Only fetches symbols/ranges not already cached.
    If global_start is provided, fetches from the earlier of global_start
    and first_buy_date so the ticker chart can display price history before
    the first buy within the global brush range.
    """
    conn = get_connection(db_path)
    try:
        to_fetch: dict[str, tuple[date, date]] = {}
        for sym, (hp_start, hp_end) in holding_periods.items():
            fetch_start = min(hp_start, global_start) if global_start else hp_start
            need_end = hp_end or end
            cached_lo, cached_hi = _cached_range(conn, sym)
            if cached_lo is None or cached_lo > fetch_start or (
                cached_hi and cached_hi < need_end - timedelta(days=4)
            ):
                to_fetch[sym] = (fetch_start, need_end)

        if to_fetch:
            batch_start = min(s for s, _ in to_fetch.values())
            batch_end = max(e for _, e in to_fetch.values())
            syms = set(to_fetch.keys())
            print(f"Fetching prices for {len(syms)} symbols ({batch_start} -> {batch_end})...")
            tickers = " ".join(sorted(syms))
            try:
                df = yf.download(
                    tickers,
                    start=batch_start.isoformat(),
                    end=(batch_end + timedelta(days=1)).isoformat(),
                    auto_adjust=False,
                    progress=False,
                )
            except Exception:
                print(f"ERROR: yfinance download failed for {len(syms)} symbols")
                raise
            if df.empty:
                msg = f"yfinance returned empty DataFrame for {len(syms)} symbols"
                raise RuntimeError(msg)
            if isinstance(df.columns, pd.MultiIndex):
                close_df = df["Close"]
            elif len(syms) == 1:
                close_df = df[["Close"]].rename(columns={"Close": list(syms)[0]})
            else:
                close_df = df.get("Close", pd.DataFrame())

            # Fetch split data to reverse Yahoo's retroactive split adjustment
            split_factors = _build_split_factors(sorted(syms))

            rows_inserted = 0
            for sym in close_df.columns:
                hp_start, hp_end_raw = holding_periods.get(sym, (batch_start, None))
                fetch_start = min(hp_start, global_start) if global_start else hp_start
                hp_end = hp_end_raw or end
                factors = split_factors.get(sym, [])
                for dt, price in close_df[sym].dropna().items():
                    d = dt.date() if hasattr(dt, "date") else dt
                    if d < fetch_start or d > hp_end:
                        continue
                    unadj = float(price) * _reverse_split_factor(d, factors)
                    conn.execute(
                        "INSERT OR REPLACE INTO daily_close (symbol, date, close) VALUES (?, ?, ?)",
                        (sym, d.isoformat(), unadj),
                    )
                    rows_inserted += 1
            conn.commit()
            print(f"Cached {rows_inserted} price records")
        else:
            print(f"All {len(holding_periods)} symbols cached")
    finally:
        conn.close()


def fetch_and_store_cny_rates(db_path: Path, start: date, end: date) -> None:
    """Fetch daily USD/CNY rate from yfinance and store in timemachine.db."""
    sym = "CNY=X"
    conn = get_connection(db_path)
    try:
        cached_lo, cached_hi = _cached_range(conn, sym)
        if cached_lo is None or cached_lo > start or (
            cached_hi and cached_hi < end - timedelta(days=4)
        ):
            print(f"Fetching USD/CNY rates {start} -> {end}...")
            try:
                df = yf.download(
                    sym,
                    start=start.isoformat(),
                    end=(end + timedelta(days=1)).isoformat(),
                    auto_adjust=False,
                    progress=False,
                )
            except Exception:
                print("ERROR: yfinance CNY rate download failed")
                raise
            if df.empty:
                msg = "yfinance returned empty CNY data"
                raise RuntimeError(msg)
            if isinstance(df.columns, pd.MultiIndex):
                close = df["Close"].iloc[:, 0]
            elif "Close" in df.columns:
                close = df["Close"]
            else:
                close = df.iloc[:, 0]
            cnt = 0
            for dt, rate in close.dropna().items():
                d = dt.date() if hasattr(dt, "date") else dt
                d_str = d.isoformat() if hasattr(d, "isoformat") else str(d)
                conn.execute(
                    "INSERT OR REPLACE INTO daily_close (symbol, date, close) VALUES (?, ?, ?)",
                    (sym, d_str, float(rate)),
                )
                cnt += 1
            conn.commit()
            print(f"Cached {cnt} CNY rate records")
    finally:
        conn.close()


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


def load_proxy_prices(db_path: Path, proxy_tickers: dict[str, str]) -> dict[str, dict[date, float]]:
    """Load proxy ticker prices from daily_close for 401k interpolation."""
    proxy_prices: dict[str, dict[date, float]] = {}
    conn = get_connection(db_path)
    try:
        for proxy in proxy_tickers.values():
            proxy_prices[proxy] = {}
            for d, close in conn.execute(
                "SELECT date, close FROM daily_close WHERE symbol = ? ORDER BY date",
                (proxy,),
            ):
                proxy_prices[proxy][date.fromisoformat(d)] = close
    finally:
        conn.close()
    return proxy_prices
