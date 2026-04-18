"""Historical price fetching and caching via timemachine.db.

Prices are stored in the ``daily_close`` table (shared with CNY rates).
The yfinance download is only triggered for gaps in the cache.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

from .db import get_connection
from .market._yfinance import extract_close
from .parsing import parse_date_iso
from .refresh import refresh_window_start
from .sources import ActionKind
from .sources.fidelity import MM_SYMBOLS

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


def _persist_close(
    conn: sqlite3.Connection,
    symbol: str,
    date_iso: str,
    close: float,
    refresh_cutoff_iso: str,
    *,
    refresh_in_window: bool = True,
) -> bool:
    """Insert a daily_close row with the historical-immutability invariant.

    - ``date_iso < refresh_cutoff_iso`` → INSERT OR IGNORE (preserve existing).
    - ``date_iso >= refresh_cutoff_iso`` AND ``refresh_in_window=True`` → INSERT OR REPLACE.
    - ``refresh_in_window=False`` → INSERT OR IGNORE for every date (intraday-immutable).

    Returns True when the row was actually inserted/replaced; False when an
    IGNORE skipped because the row already existed.
    """
    if not refresh_in_window or date_iso < refresh_cutoff_iso:
        cur = conn.execute(
            "INSERT OR IGNORE INTO daily_close (symbol, date, close) VALUES (?, ?, ?)",
            (symbol, date_iso, close),
        )
        return cur.rowcount > 0
    conn.execute(
        "INSERT OR REPLACE INTO daily_close (symbol, date, close) VALUES (?, ?, ?)",
        (symbol, date_iso, close),
    )
    return True


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
        inserted = _persist_close(conn, symbol, d_iso, value, refresh_cutoff_iso, refresh_in_window=refresh_in_window)
        if d_iso < refresh_cutoff_iso or not refresh_in_window:
            if inserted:
                new_historical += 1
        else:
            refreshed_recent += 1
    return new_historical, refreshed_recent


# ── Symbol holding periods ──────────────────────────────────────────────────


def _holding_periods_from_action_kind_rows(
    rows: list[tuple[str, str, str, float]],
) -> dict[str, tuple[date, date | None]]:
    """Compute ``{symbol: (first_buy_date, last_sell_date_or_None)}``.

    Each row is ``(run_date_iso, symbol, action_kind, quantity)`` —
    pre-fetched from a Fidelity-shaped table. Symbol-stripping + ``qty``
    coercion happen here so call sites can pass raw DB rows directly.
    Used by :func:`symbol_holding_periods_from_db` (against a local
    SQLite DB) and the nightly D1 sync (against rows pulled via wrangler).
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

        txn_date = parse_date_iso(run_date)
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

    return _holding_periods_from_action_kind_rows(list(db_rows))


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


# ── Split cross-validation ──────────────────────────────────────────────────

# Absolute share-count tolerance for matching a Yahoo split against a Fidelity
# DISTRIBUTION row. Splits announce integer ratios (2:1, 3:2) and Fidelity
# always rounds to whole shares, so any mismatch beyond a fractional sliver
# is a real discrepancy worth raising.
SPLIT_QTY_TOLERANCE = 0.01


class SplitValidationError(RuntimeError):
    """Raised when Yahoo splits and Fidelity DISTRIBUTION rows don't agree.

    Two directions are checked:

    1. Every Yahoo split that falls inside a holding period must have a
       Fidelity ``DISTRIBUTION`` row with ``qty ≈ pre_split_qty × (ratio - 1)``.
       Failure means Yahoo knows about a split we didn't apply to shares.

    2. Every Fidelity ``DISTRIBUTION`` row with ``qty > 0`` must map to a
       Yahoo split on the same date. Failure means we changed the share
       count but will NOT reverse Yahoo's price adjustment — so pre-split
       dates would be stored at split-adjusted (wrong) prices.

    Either direction failing indicates a data-integrity problem that would
    silently produce wrong historical valuations. Fail loud, fix upstream.
    """


def _validate_splits_against_transactions(
    conn: sqlite3.Connection,
    holding_periods: dict[str, tuple[date, date | None]],
    split_factors: dict[str, list[tuple[date, float]]],
    *,
    today: date | None = None,
) -> None:
    """Two-way cross-check Yahoo splits vs Fidelity DISTRIBUTION rows.

    See :class:`SplitValidationError` for the invariants. ``today`` is
    injected by tests; production callers leave it ``None`` and the
    function uses :meth:`date.today`.
    """
    today = today or date.today()
    mismatches: list[str] = []

    # Direction 1: every Yahoo split inside a holding period must have a
    # matching DISTRIBUTION row with the expected qty delta.
    checked_pairs: set[tuple[str, date]] = set()
    for sym, splits in split_factors.items():
        hp = holding_periods.get(sym)
        if hp is None:
            continue
        hp_start, hp_end_raw = hp
        hp_end = hp_end_raw or today
        for split_date, ratio in splits:
            if split_date <= hp_start or split_date > hp_end:
                continue
            pre_qty = 0.0
            for (qty,) in conn.execute(
                "SELECT quantity FROM fidelity_transactions"
                " WHERE symbol = ?"
                " AND action_kind IN ('buy','sell','reinvestment',"
                "'distribution','redemption','exchange','transfer')"
                " AND run_date < ?",
                (sym, split_date.isoformat()),
            ):
                pre_qty += qty or 0.0
            if pre_qty < SPLIT_QTY_TOLERANCE:
                continue  # not held at split boundary
            actual = 0.0
            for (qty,) in conn.execute(
                "SELECT quantity FROM fidelity_transactions"
                " WHERE symbol = ? AND action_kind = 'distribution'"
                " AND run_date = ?",
                (sym, split_date.isoformat()),
            ):
                actual += qty or 0.0
            expected = pre_qty * (ratio - 1)
            if abs(expected - actual) > SPLIT_QTY_TOLERANCE:
                mismatches.append(
                    f"{sym} {split_date.isoformat()} {ratio}:1 — "
                    f"pre-qty={pre_qty:.4f}, expected DISTRIBUTION qty+={expected:.4f}, "
                    f"got={actual:.4f}"
                )
            checked_pairs.add((sym, split_date))

    # Direction 2: every Fidelity DISTRIBUTION row (qty > 0) must map to a
    # Yahoo split on the same date. Catches silent _build_split_factors
    # failures — without a Yahoo entry, Yahoo's split-adjusted pre-split
    # Close values would be stored un-reversed.
    for sym, run_date, qty in conn.execute(
        "SELECT symbol, run_date, SUM(quantity) FROM fidelity_transactions"
        " WHERE action_kind = 'distribution' AND quantity > 0"
        " GROUP BY symbol, run_date"
    ):
        if not qty or qty <= SPLIT_QTY_TOLERANCE:
            continue
        split_date = date.fromisoformat(run_date)
        if (sym, split_date) in checked_pairs:
            continue  # already validated by direction 1
        mismatches.append(
            f"{sym} {run_date} — Fidelity DISTRIBUTION qty+={qty:.4f} "
            f"but no matching Yahoo split (pre-split price un-adjustment would be skipped)"
        )

    if mismatches:
        msg = (
            "Split cross-validation failed — Yahoo and Fidelity disagree:\n  "
            + "\n  ".join(mismatches)
        )
        raise SplitValidationError(msg)


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
    refresh_cutoff_iso = refresh_window_start(end).isoformat()
    conn = get_connection(db_path)
    try:
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
