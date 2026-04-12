"""Compute daily portfolio allocation from Fidelity replay + Qianji + 401k.

This module reconstructs historical asset allocation by combining:
  - Fidelity positions (from transaction CSV replay)
  - Historical prices (from timemachine.db.daily_close)
  - Qianji account balances (from Qianji SQLite DB)
  - 401k values (pre-computed via proxy interpolation)
"""
from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

from .ingest.robinhood_history import replay_robinhood
from .prices import load_cny_rates, load_prices
from .timemachine import (
    _parse_date,
    replay_from_db,
    replay_qianji,
    replay_qianji_currencies,
)

log = logging.getLogger(__name__)

# yfinance labels mutual fund NAV with date T but it's actually T+1's NAV.
# Use T-1 price for mutual funds to align with the correct trading day.
_MUTUAL_FUNDS = frozenset({"FXAIX", "FSSNX", "FNJHX"})

_FIDELITY_REPLAY_ACCOUNTS = frozenset({
    "Fidelity taxable",
    "Roth IRA",
    "Fidelity Cash Management",
})


# ── Qianji transaction dates ───────────────────────────────────────────────


def _qianji_transaction_dates(db_path: Path) -> list[date]:
    """Return sorted unique dates of Qianji transactions."""
    if not db_path.exists():
        return []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dates: set[date] = set()
    for (ts,) in conn.execute("SELECT time FROM user_bill WHERE status = 1"):
        dates.add(datetime.fromtimestamp(ts, UTC).date())
    conn.close()
    return sorted(dates)


def _find_price_date(prices: pd.DataFrame, target: date, floor: date) -> date:
    """Find the latest date ≤ target that has prices, walking back to ``floor``."""
    d = target
    while d not in prices.index and d > floor:
        d -= timedelta(days=1)
    return d


def _categorize_ticker(
    ticker: str,
    value: float,
    assets: dict[str, object],
    cost_basis_by_ticker: dict[str, float],
) -> dict[str, object]:
    """Classify a single ticker into a detail row with category/subtype/gain-loss."""
    if value < 0:
        return {
            "ticker": ticker, "value": round(value, 2),
            "category": "Liability", "subtype": "",
            "cost_basis": 0, "gain_loss": 0, "gain_loss_pct": 0,
        }
    asset_entry = assets.get(ticker)
    if not isinstance(asset_entry, dict):
        raise KeyError(f"Ticker {ticker!r} not in config.assets — add it to config.json to classify this holding")
    cat = asset_entry.get("category", "")
    sub = asset_entry.get("subtype", "")
    if not cat:
        raise KeyError(f"Ticker {ticker!r} has no 'category' in config.assets")
    cb = cost_basis_by_ticker.get(ticker, 0)
    gl = round(value - cb, 2) if cb > 0 else 0
    gl_pct = round(gl / cb * 100, 2) if cb > 0 else 0
    return {
        "ticker": ticker, "value": round(value, 2),
        "category": cat, "subtype": sub,
        "cost_basis": round(cb, 2), "gain_loss": gl, "gain_loss_pct": gl_pct,
    }


# ── Per-source aggregation helpers ─────────────────────────────────────────


def _resolve_date_windows(
    prices: pd.DataFrame,
    cny_rates: dict[date, float],
    current: date,
    start: date,
) -> tuple[date, date, float]:
    """Resolve (price_date, mf_price_date, cny_rate) for a given date via forward-fill."""
    price_date = _find_price_date(prices, current, start)
    mf_price_date = _find_price_date(prices, price_date - timedelta(days=1), start)
    cny_date = current
    while cny_date not in cny_rates and cny_date > start:
        cny_date -= timedelta(days=1)
    if cny_date not in cny_rates:
        raise ValueError(f"No CNY rate available at or before {current} — daily_close is missing CNY=X data")
    return price_date, mf_price_date, cny_rates[cny_date]


def _add_fidelity_positions(
    ticker_values: dict[str, float],
    positions: dict[tuple[str, str], float],
    prices: pd.DataFrame,
    price_date: date,
    mf_price_date: date,
) -> None:
    """Add Fidelity positions (qty × price) into ticker_values.

    CUSIPs (8+ chars starting with digit) are valued at face and aggregated as T-Bills.
    Mutual funds use T-1 price to correct for yfinance's off-by-one NAV dating.
    """
    for (_acct, sym), qty in positions.items():
        if sym[0].isdigit() and len(sym) >= 8:
            ticker_values["T-Bills"] = ticker_values.get("T-Bills", 0) + qty
            continue
        p_date = mf_price_date if sym in _MUTUAL_FUNDS else price_date
        if sym in prices.columns and p_date in prices.index:
            price = prices.loc[p_date, sym]
            if pd.notna(price):
                ticker_values[sym] = ticker_values.get(sym, 0) + qty * float(price)
        else:
            log.warning("No price for %s on %s (holding %.3f shares) — excluded from allocation", sym, p_date, qty)


def _add_fidelity_cash(
    ticker_values: dict[str, float],
    fidelity_cash: dict[str, float],
    fidelity_accounts: dict[str, str],
) -> None:
    """Route Fidelity cash balances to each account's money market fund ticker (fallback FZFXX)."""
    for acct_num, bal in fidelity_cash.items():
        mm_ticker = fidelity_accounts.get(acct_num, "FZFXX")
        ticker_values[mm_ticker] = ticker_values.get(mm_ticker, 0) + bal


def _add_qianji_balances(
    ticker_values: dict[str, float],
    qj_balances: dict[str, float],
    currencies: dict[str, str],
    ticker_map: dict[str, str],
    assets: dict[str, object],
    cny_rate: float,
    skip_accounts: frozenset[str],
) -> None:
    """Map Qianji balances to tickers. Handles CNY conversion, liabilities, CNY-Assets fallback."""
    for qj_acct, bal in qj_balances.items():
        if qj_acct in skip_accounts or abs(bal) < 0.01:
            continue
        curr = currencies.get(qj_acct, "USD")
        usd_val = bal / cny_rate if curr == "CNY" else bal
        if usd_val < 0:
            # Liability (credit card) — use account name as ticker
            ticker_values[qj_acct] = ticker_values.get(qj_acct, 0) + usd_val
            continue
        ticker = ticker_map.get(qj_acct)
        if ticker and ticker in assets:
            ticker_values[ticker] = ticker_values.get(ticker, 0) + usd_val
        elif curr == "CNY":
            ticker_values["CNY Assets"] = ticker_values.get("CNY Assets", 0) + usd_val
        else:
            log.warning("Qianji account %r (%.2f USD) has no ticker_map entry — excluded from allocation", qj_acct, usd_val)


def _add_401k(
    ticker_values: dict[str, float],
    k401_daily: dict[date, dict[str, float]],
    current: date,
) -> None:
    """Add pre-computed 401k daily values (already keyed by config ticker)."""
    if current not in k401_daily:
        return
    for ticker, val in k401_daily[current].items():
        ticker_values[ticker] = ticker_values.get(ticker, 0) + val


def _add_robinhood(
    ticker_values: dict[str, float],
    rh_replay_fn: Callable[..., dict[str, object]] | None,
    robinhood_csv: Path | None,
    current: date,
    prices: pd.DataFrame,
    price_date: date,
) -> dict[str, float]:
    """Add Robinhood positions (qty × price). Returns cost-basis dict by symbol."""
    if not (rh_replay_fn and robinhood_csv):
        return {}
    rh_result = rh_replay_fn(robinhood_csv, as_of=current)
    positions: dict[str, float] = rh_result["positions"]  # type: ignore[assignment]
    for sym, qty in positions.items():
        if sym in prices.columns and price_date in prices.index:
            price = prices.loc[price_date, sym]
            if pd.notna(price):
                ticker_values[sym] = ticker_values.get(sym, 0) + qty * float(price)
    return rh_result.get("cost_basis", {})  # type: ignore[return-value]


def _build_allocation_row(
    current: date,
    ticker_values: dict[str, float],
    assets: dict[str, object],
    cost_basis_by_ticker: dict[str, float],
) -> dict[str, object]:
    """Categorize each non-zero ticker and produce the per-day allocation dict."""
    category_totals: dict[str, float] = {}
    total = 0.0
    liabilities = 0.0
    ticker_detail: list[dict[str, object]] = []

    for ticker, value in ticker_values.items():
        if value == 0:
            continue
        row = _categorize_ticker(ticker, value, assets, cost_basis_by_ticker)
        ticker_detail.append(row)
        if value < 0:
            liabilities += value
        else:
            cat_key = str(row["category"])
            category_totals[cat_key] = category_totals.get(cat_key, 0) + value
            total += value

    return {
        "date": current.isoformat(),
        "total": round(total, 2),
        "us_equity": round(category_totals.get("US Equity", 0), 2),
        "non_us_equity": round(category_totals.get("Non-US Equity", 0), 2),
        "crypto": round(category_totals.get("Crypto", 0), 2),
        "safe_net": round(category_totals.get("Safe Net", 0), 2),
        "liabilities": round(liabilities, 2),
        "tickers": ticker_detail,
    }


# ── Daily allocation ───────────────────────────────────────────────────────


def compute_daily_allocation(
    db_path: Path,
    qj_db: Path,
    config: dict[str, object],
    k401_daily: dict[date, dict[str, float]],
    start: date,
    end: date,
    *,
    robinhood_csv: Path | None = None,
) -> list[dict[str, object]]:
    """Compute daily allocation from start to end.

    Reads prices + CNY rates from timemachine.db.daily_close.
    Reads Fidelity positions from CSV via replay().
    Reads Qianji balances live from qj_db.

    Args:
        db_path: Path to timemachine.db (prices + CNY rates).
        qj_db: Path to Qianji SQLite DB.
        config: Config dict with ``assets`` and ``qianji_accounts`` keys.
        k401_daily: Pre-computed 401k daily values by config ticker.
        start: First date to compute.
        end: Last date to compute.

    Returns:
        list of {date, total, safe_net, safe_net_pct, us_equity_pct,
                 non_us_equity_pct, crypto_pct}.
    """
    # ── Load prices + CNY from DB ──
    prices = load_prices(db_path)
    cny_rates = load_cny_rates(db_path)

    assets: dict[str, object] = config["assets"]  # type: ignore[assignment]
    qj_accounts: dict[str, object] = config.get("qianji_accounts", {})  # type: ignore[assignment]
    ticker_map: dict[str, str] = qj_accounts.get("ticker_map", {})  # type: ignore[assignment]
    ticker_map.setdefault("401k", "401k sp500")
    # Per-Fidelity-account money market fund ticker (cash sweep vehicle)
    fidelity_accounts: dict[str, str] = config.get("fidelity_accounts", {})  # type: ignore[assignment]
    currencies = replay_qianji_currencies(qj_db)

    # ── Robinhood replay (optional) ──
    rh_replay_fn: Callable[..., dict[str, object]] | None = (
        replay_robinhood if robinhood_csv and robinhood_csv.exists() else None
    )

    # ── Account exclusion sets ──
    skip_qj_accounts = _FIDELITY_REPLAY_ACCOUNTS | {"401k"}
    if rh_replay_fn:
        skip_qj_accounts = skip_qj_accounts | {"Robinhood"}  # Don't double-count

    # ── Pre-compute transaction dates for caching ──
    _conn = sqlite3.connect(str(db_path))
    fidelity_txn_dates = sorted({
        _parse_date(r[0]) for r in _conn.execute("SELECT DISTINCT run_date FROM fidelity_transactions")
    })
    _conn.close()
    qj_txn_dates = set(_qianji_transaction_dates(qj_db))

    results: list[dict[str, object]] = []
    cached_positions: dict[tuple[str, str], float] = {}
    cached_cash: dict[str, float] = {}
    cached_qj: dict[str, float] | None = None
    cached_fidelity_cost_basis: dict[tuple[str, str], float] = {}
    last_fidelity_replay: date | None = None
    last_qj_replay: date | None = None

    current = start
    while current <= end:
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue

        # ── Replay Fidelity only when positions changed ──
        latest_fidelity = max((d for d in fidelity_txn_dates if d <= current), default=None)
        if latest_fidelity != last_fidelity_replay:
            result = replay_from_db(db_path, current)
            cached_positions = result["positions"]
            cached_cash = result["cash"]
            cached_fidelity_cost_basis = result.get("cost_basis") or {}
            last_fidelity_replay = latest_fidelity

        # ── Replay Qianji only when balances changed ──
        needs_qj = cached_qj is None or any(
            d > (last_qj_replay or date.min) and d <= current for d in qj_txn_dates
        )
        if needs_qj:
            cached_qj = replay_qianji(qj_db, current)
            last_qj_replay = current

        price_date, mf_price_date, cny_rate = _resolve_date_windows(prices, cny_rates, current, start)

        # ── Aggregate ticker values from all sources ──
        ticker_values: dict[str, float] = {}
        _add_fidelity_positions(ticker_values, cached_positions, prices, price_date, mf_price_date)
        _add_fidelity_cash(ticker_values, cached_cash, fidelity_accounts)
        _add_qianji_balances(ticker_values, cached_qj or {}, currencies, ticker_map, assets, cny_rate, skip_qj_accounts)
        _add_401k(ticker_values, k401_daily, current)
        rh_cost_basis = _add_robinhood(ticker_values, rh_replay_fn, robinhood_csv, current, prices, price_date)

        # ── Aggregate cost basis per ticker (Fidelity + Robinhood) ──
        cost_basis_by_ticker: dict[str, float] = {}
        for (_, sym), cb in cached_fidelity_cost_basis.items():
            cost_basis_by_ticker[sym] = cost_basis_by_ticker.get(sym, 0) + cb
        for sym, cb in rh_cost_basis.items():
            cost_basis_by_ticker[sym] = cost_basis_by_ticker.get(sym, 0) + cb

        results.append(_build_allocation_row(current, ticker_values, assets, cost_basis_by_ticker))
        current += timedelta(days=1)

    return results
