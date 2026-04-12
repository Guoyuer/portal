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
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

from .timemachine import (
    _parse_date,
    replay_from_db,
    replay_qianji,
    replay_qianji_currencies,
)

log = logging.getLogger(__name__)

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
    from .prices import load_cny_rates, load_prices

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
    rh_replay_fn = None
    if robinhood_csv and robinhood_csv.exists():
        from .ingest.robinhood_history import replay_robinhood
        rh_replay_fn = replay_robinhood

    # ── Account exclusion sets ──
    fidelity_replay_accounts = {
        "Fidelity taxable",
        "Roth IRA",
        "Fidelity Cash Management",
    }
    skip_qj_accounts = fidelity_replay_accounts | {"401k"}
    if rh_replay_fn:
        skip_qj_accounts.add("Robinhood")  # Don't double-count

    # ── Pre-compute transaction dates for caching ──
    import sqlite3 as _sqlite3
    _conn = _sqlite3.connect(str(db_path))
    fidelity_txn_dates = sorted({
        _parse_date(r[0]) for r in _conn.execute("SELECT DISTINCT run_date FROM fidelity_transactions")
    })
    _conn.close()
    qj_txn_dates = set(_qianji_transaction_dates(qj_db))

    results: list[dict[str, object]] = []
    cached_positions: dict[tuple[str, str], float] | None = None
    cached_cash: dict[str, float] | None = None
    cached_qj: dict[str, float] | None = None
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
            last_fidelity_replay = latest_fidelity

        # ── Replay Qianji only when balances changed ──
        needs_qj = cached_qj is None or any(
            d > (last_qj_replay or date.min) and d <= current for d in qj_txn_dates
        )
        if needs_qj:
            cached_qj = replay_qianji(qj_db, current)
            last_qj_replay = current

        positions = cached_positions or {}
        fidelity_cash = cached_cash or {}
        qj_balances = cached_qj or {}

        # ── Find nearest price date + CNY rate (forward-fill) ──
        price_date = _find_price_date(prices, current, start)
        cny_date = current
        while cny_date not in cny_rates and cny_date > start:
            cny_date -= timedelta(days=1)
        if cny_date not in cny_rates:
            raise ValueError(f"No CNY rate available at or before {current} — daily_close is missing CNY=X data")
        cny_rate = cny_rates[cny_date]

        # ── Compute values per ticker ──
        ticker_values: dict[str, float] = {}

        # yfinance labels mutual fund NAV with date T but it's actually T+1's NAV.
        # Use T-1 price for mutual funds to align with the correct trading day.
        mutual_funds = frozenset({"FXAIX", "FSSNX", "FNJHX"})
        mf_price_date = _find_price_date(prices, price_date - timedelta(days=1), start)

        # Fidelity positions x price
        for (_acct, sym), qty in positions.items():
            # CUSIPs (T-Bills, brokered CDs): value at face ($1/unit), aggregate as "T-Bills"
            if sym[0].isdigit() and len(sym) >= 8:
                ticker_values["T-Bills"] = ticker_values.get("T-Bills", 0) + qty
                continue
            p_date = mf_price_date if sym in mutual_funds else price_date
            if sym in prices.columns and p_date in prices.index:
                price = prices.loc[p_date, sym]
                if pd.notna(price):
                    ticker_values[sym] = ticker_values.get(sym, 0) + qty * float(price)
            else:
                log.warning("No price for %s on %s (holding %.3f shares) — excluded from allocation", sym, p_date, qty)

        # Fidelity cash -> per-account money market ticker (from config)
        for acct_num, bal in fidelity_cash.items():
            mm_ticker = fidelity_accounts.get(acct_num, "FZFXX")
            ticker_values[mm_ticker] = ticker_values.get(mm_ticker, 0) + bal

        # Qianji balances -> mapped tickers (including liabilities)
        for qj_acct, bal in qj_balances.items():
            if qj_acct in skip_qj_accounts or abs(bal) < 0.01:
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

        # 401k (Empower QFX): daily values by config ticker
        if current in k401_daily:
            for ticker, val in k401_daily[current].items():
                ticker_values[ticker] = ticker_values.get(ticker, 0) + val

        # Robinhood positions x price (replaces Qianji "Robinhood" book value)
        rh_cost_basis: dict[str, float] = {}
        if rh_replay_fn and robinhood_csv:
            rh_result = rh_replay_fn(robinhood_csv, as_of=current)
            for sym, qty in rh_result["positions"].items():
                if sym in prices.columns and price_date in prices.index:
                    price = prices.loc[price_date, sym]
                    if pd.notna(price):
                        ticker_values[sym] = ticker_values.get(sym, 0) + qty * float(price)
            rh_cost_basis = rh_result.get("cost_basis", {})

        # ── Categorize + build ticker detail ──
        category_totals: dict[str, float] = {}
        total = 0.0
        liabilities = 0.0
        ticker_detail: list[dict[str, object]] = []

        # Aggregate cost basis by ticker from Fidelity + Robinhood replay
        cost_basis_by_ticker: dict[str, float] = {}
        for (_, sym), cb in (result.get("cost_basis") or {}).items():
            cost_basis_by_ticker[sym] = cost_basis_by_ticker.get(sym, 0) + cb
        for sym, cb in rh_cost_basis.items():
            cost_basis_by_ticker[sym] = cost_basis_by_ticker.get(sym, 0) + cb

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

        safe_net = category_totals.get("Safe Net", 0)

        results.append({
            "date": current.isoformat(),
            "total": round(total, 2),
            "us_equity": round(category_totals.get("US Equity", 0), 2),
            "non_us_equity": round(category_totals.get("Non-US Equity", 0), 2),
            "crypto": round(category_totals.get("Crypto", 0), 2),
            "safe_net": round(safe_net, 2),
            "liabilities": round(liabilities, 2),
            "tickers": ticker_detail,
        })

        current += timedelta(days=1)

    return results
