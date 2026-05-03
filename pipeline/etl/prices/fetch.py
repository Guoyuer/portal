"""Yahoo I/O: daily close prices, USD/CNY rates, and split history.

Each public fetcher writes results through the refresh-window-aware helpers in
:mod:`store` so re-runs are idempotent and past history can't be overwritten.
Pre-split price unadjustment is cross-validated against Fidelity's DISTRIBUTION
rows via :mod:`validate` *before* any prices are persisted — see
:class:`SplitValidationError` for why.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import yfinance as yf

from ..db import get_connection
from ..market._yfinance import extract_close
from . import refresh_window_start
from .store import _cached_range, _persist_close_batch
from .validate import _validate_splits_against_transactions

# ── Split adjustment reversal ──────────────────────────────────────────────


def _build_split_factors(symbols: list[str]) -> dict[str, list[tuple[date, float]]]:
    """Fetch split history for symbols and return {symbol: [(split_date, ratio), ...]}.

    A failure here silently drops splits for the symbol, which would leave
    ``_reverse_split_factor`` unable to undo Yahoo's retroactive adjustment and
    corrupt historical prices. Failures WARN on stderr; the real safety net is
    :func:`_validate_splits_against_transactions`, which cross-checks the
    returned dict against Fidelity's ``DISTRIBUTION`` rows and raises if either
    side is inconsistent — catching both silent yfinance failures and
    Fidelity/Yahoo drift.
    """
    result: dict[str, list[tuple[date, float]]] = {}
    for sym in symbols:
        if not sym:
            continue
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
        except Exception as e:  # noqa: BLE001 — yfinance raises a grab-bag of exception types
            print(f"  WARN: splits fetch failed for {sym}: {type(e).__name__}: {e}", file=sys.stderr)
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
            window_start = max(fetch_start, refresh_window_start(need_end))
            if cached_lo is None:
                # Never fetched — pull the full range.
                to_fetch[sym] = (fetch_start, need_end)
            else:
                # Any cache exists → trust ``cached_lo`` as the ticker's
                # earliest-available date. Yahoo returns empty for dates
                # before a ticker's inception, so re-attempting them on
                # every run is wasted work that also drags ``batch_start``
                # (min across to_fetch) back to ``global_start``, turning
                # what should be an incremental refresh into a multi-year
                # batch download for every symbol. Refresh the recent
                # window only — ``_persist_close`` keeps older dates
                # immutable so this is idempotent.
                to_fetch[sym] = (window_start, need_end)

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
            close_df = extract_close(df, sorted(syms))

            # Fetch split data to reverse Yahoo's retroactive split adjustment.
            # Cross-validate against Fidelity DISTRIBUTION rows BEFORE persisting
            # any prices — if Yahoo and Fidelity disagree, pre-split prices
            # would land wrong and INSERT OR IGNORE would freeze the bad values.
            split_factors = _build_split_factors(sorted(syms))
            _validate_splits_against_transactions(conn, holding_periods, split_factors)

            refresh_cutoff_iso = refresh_window_start(end).isoformat()
            total_new_historical = 0
            total_refreshed_recent = 0
            for sym in close_df.columns:
                hp_start, hp_end_raw = holding_periods.get(sym, (batch_start, None))
                fetch_start = min(hp_start, global_start) if global_start else hp_start
                hp_end = hp_end_raw or end
                factors = split_factors.get(sym, [])
                rows: list[tuple[date, float]] = []
                for dt, price in close_df[sym].dropna().items():
                    d = dt.date() if hasattr(dt, "date") else dt
                    if d < fetch_start or d > hp_end:
                        continue
                    unadj = float(price) * _reverse_split_factor(d, factors)
                    rows.append((d, unadj))
                nh, rr = _persist_close_batch(conn, sym, rows, refresh_cutoff_iso)
                total_new_historical += nh
                total_refreshed_recent += rr
            conn.commit()
            print(f"Cached prices: {total_new_historical} new historical, "
                  f"{total_refreshed_recent} refreshed in window")
        else:
            print(f"All {len(holding_periods)} symbols cached")
    finally:
        conn.close()


def fetch_and_store_cny_rates(db_path: Path, start: date, end: date) -> None:
    """Fetch daily USD/CNY rate from yfinance and store in timemachine.db.

    Writes go through ``_persist_close``: dates older than the refresh window
    are never overwritten once stored. Re-runs are therefore idempotent — a
    partial or wrong Yahoo response cannot corrupt already-captured history.
    """
    sym = "CNY=X"
    conn = get_connection(db_path)
    try:
        cached_lo, _cached_hi = _cached_range(conn, sym)
        fetch_start = start
        if cached_lo is not None and cached_lo <= start:
            fetch_start = max(start, refresh_window_start(end))
        refresh_cutoff_iso = refresh_window_start(end).isoformat()

        print(f"Fetching USD/CNY rates {fetch_start} -> {end}...")
        try:
            df = yf.download(
                sym,
                start=fetch_start.isoformat(),
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
        close_df = extract_close(df, [sym])
        if close_df.empty:
            raise RuntimeError("yfinance CNY data has no Close column")
        close = close_df.iloc[:, 0]
        rows: list[tuple[date, float]] = []
        for dt, rate in close.dropna().items():
            d = dt.date() if hasattr(dt, "date") else dt
            rows.append((d, float(rate)))
        new_historical, refreshed_recent = _persist_close_batch(
            conn, sym, rows, refresh_cutoff_iso, refresh_in_window=False,
        )
        conn.commit()
        print(f"CNY rates: {new_historical} new historical, {refreshed_recent} refreshed in window")
    finally:
        conn.close()
