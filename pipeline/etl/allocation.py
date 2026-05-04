"""Compute daily portfolio allocation from investment sources + Qianji.

This module reconstructs historical asset allocation by combining:
  - Investment-source positions (each source module returns a list of
    PositionRow; Fidelity, Robinhood, and Empower 401k are composed here).
  - Historical prices (from timemachine.db.daily_close)
  - Qianji account balances (from Qianji SQLite DB)

The per-day math is isolated in ``step_one_day(qj_balances, sources, current)``.
``compute_daily_allocation`` is the orchestrator that replays Qianji for each
trading day, then delegates the valuation to ``step_one_day``.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

import etl.sources.empower as empower_src
import etl.sources.fidelity as fidelity_src
import etl.sources.robinhood as robinhood_src

from ._category_totals import accumulate_category_totals
from .db import get_connection
from .prices.store import load_cny_rates, load_prices
from .qianji.balances import qianji_balances_at, qianji_currencies
from .sources._types import InvestmentSource, PriceContext
from .types import AllocationRow, AssetInfo, RawConfig, TickerDetail

log = logging.getLogger(__name__)

_POSITION_SOURCES: tuple[InvestmentSource, ...] = (fidelity_src, robinhood_src, empower_src)

# Qianji accounts that the Fidelity + Empower sources supersede once their
# respective ingest tables are populated. The Robinhood account stays
# data-gated on the CSV's presence — a user who never exported Robinhood
# activity still has their Qianji balance counted.
_FIDELITY_SUPERSEDED_QJ_ACCOUNTS = frozenset({
    "Fidelity taxable",
    "Roth IRA",
    "Fidelity Cash Management",
})


def _find_price_date(prices: pd.DataFrame, target: date) -> date:
    """Find the latest date ≤ target that has prices.

    Walks back day-by-day, stopping at ``prices.index[0]`` (earliest available
    data). The floor is intentionally the global data start, not any caller's
    compute range, so mutual-fund T-1 lookups walk across weekends regardless
    of whether the compute window is full or incremental.
    """
    if prices.empty:
        return target
    earliest = prices.index[0]
    d = target
    while d not in prices.index and d > earliest:
        d -= timedelta(days=1)
    return d


def _categorize_ticker(
    ticker: str,
    value: float,
    assets: Mapping[str, AssetInfo],
    cost_basis_by_ticker: dict[str, float],
) -> TickerDetail:
    """Classify a single ticker into a detail row with category/subtype/gain-loss."""
    if value < 0:
        return TickerDetail(
            ticker=ticker, value=round(value, 2),
            category="Liability", subtype="",
            cost_basis=0, gain_loss=0, gain_loss_pct=0,
        )
    asset_entry = assets.get(ticker)
    if asset_entry is None:
        raise KeyError(f"Ticker {ticker!r} not in config.assets — add it to config.json to classify this holding")
    cat = asset_entry.get("category", "")
    sub = asset_entry.get("subtype", "")
    if not cat:
        raise KeyError(f"Ticker {ticker!r} has no 'category' in config.assets")
    cb = cost_basis_by_ticker.get(ticker, 0)
    gl = round(value - cb, 2) if cb > 0 else 0
    gl_pct = round(gl / cb * 100, 2) if cb > 0 else 0
    return TickerDetail(
        ticker=ticker, value=round(value, 2),
        category=cat, subtype=sub,
        cost_basis=round(cb, 2), gain_loss=gl, gain_loss_pct=gl_pct,
    )


# ── Per-source aggregation helpers ─────────────────────────────────────────


def _resolve_date_windows(
    prices: pd.DataFrame,
    cny_rates: dict[date, float],
    current: date,
) -> tuple[date, date, float]:
    """Resolve (price_date, mf_price_date, cny_rate) for a given date via forward-fill."""
    price_date = _find_price_date(prices, current)
    mf_price_date = _find_price_date(prices, price_date - timedelta(days=1))
    cny_date = current
    earliest_cny = min(cny_rates) if cny_rates else current
    while cny_date not in cny_rates and cny_date > earliest_cny:
        cny_date -= timedelta(days=1)
    if cny_date not in cny_rates:
        raise ValueError(f"No CNY rate available at or before {current} — daily_close is missing CNY=X data")
    return price_date, mf_price_date, cny_rates[cny_date]


def _add_qianji_balances(
    ticker_values: dict[str, float],
    qj_balances: dict[str, float],
    currencies: dict[str, str],
    ticker_map: dict[str, str],
    assets: Mapping[str, AssetInfo],
    cny_rate: float,
    skip_accounts: frozenset[str],
    warning_keys: set[tuple[str, str]],
) -> None:
    """Map Qianji balances to tickers. Every unmapped account (CNY or USD) warns and is excluded."""
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
        else:
            token = ("qianji_unmapped", qj_acct)
            if token not in warning_keys:
                warning_keys.add(token)
                log.warning(
                    "Qianji account %r (%s %.2f → $%.2f USD) has no ticker_map entry "
                    "(add a mapping under config.json → ticker_map.<account>) — excluded from allocation",
                    qj_acct, curr, bal, usd_val,
                )


# ── Pure per-day step ──────────────────────────────────────────────────────


@dataclass
class AllocationSources:
    """Static inputs resolved once, reused across every day in the window.

    Everything a per-day valuation needs besides the (changing) portfolio
    state: prices + CNY, config-derived routing tables, and the per-run
    ``db_path`` + raw config dict that the source modules need to answer
    ``positions_at`` queries. Keeping these in one bag lets ``step_one_day``
    stay pure — no hidden globals, no re-reads, no surprises.
    """

    prices: pd.DataFrame
    cny_rates: dict[date, float]
    assets: Mapping[str, AssetInfo]
    ticker_map: dict[str, str]
    qianji_currencies: dict[str, str]
    skip_qj_accounts: frozenset[str]
    db_path: Path
    source_config: RawConfig
    warning_keys: set[tuple[str, str]]


def step_one_day(
    qj_balances: dict[str, float],
    sources: AllocationSources,
    current: date,
) -> AllocationRow:
    """Value the portfolio for a single day. Pure — no I/O, no arg mutation.

    Every source module contributes through the uniform ``positions_at`` call;
    no source-kind branching lives in the valuation math.
    """
    price_date, mf_price_date, cny_rate = _resolve_date_windows(
        sources.prices, sources.cny_rates, current
    )

    ticker_values: dict[str, float] = {}
    cost_basis_by_ticker: dict[str, float] = {}

    # ── Aggregate every source module via the uniform positions API. ──
    ctx = PriceContext(
        prices=sources.prices,
        price_date=price_date,
        mf_price_date=mf_price_date,
        warning_keys=sources.warning_keys,
    )
    for mod in _POSITION_SOURCES:
        for row in mod.positions_at(sources.db_path, current, ctx, sources.source_config):
            ticker_values[row.ticker] = ticker_values.get(row.ticker, 0.0) + row.value_usd
            if row.cost_basis_usd is not None:
                cost_basis_by_ticker[row.ticker] = (
                    cost_basis_by_ticker.get(row.ticker, 0.0) + row.cost_basis_usd
                )

    _add_qianji_balances(
        ticker_values, qj_balances, sources.qianji_currencies,
        sources.ticker_map, sources.assets, cny_rate, sources.skip_qj_accounts,
        sources.warning_keys,
    )

    return _build_allocation_row(current, ticker_values, sources.assets, cost_basis_by_ticker)


def _build_allocation_row(
    current: date,
    ticker_values: dict[str, float],
    assets: Mapping[str, AssetInfo],
    cost_basis_by_ticker: dict[str, float],
) -> AllocationRow:
    """Categorize each non-zero ticker and produce the per-day allocation dict."""
    ticker_detail: list[TickerDetail] = []
    # Use the raw (unrounded) value for the sign/bucket decision — matches
    # the pre-refactor behaviour exactly, so rows with |value| < 0.005 round
    # to 0 in TickerDetail but still accumulate at full precision.
    fold_pairs: list[tuple[float, str]] = []
    for ticker, value in ticker_values.items():
        if value == 0:
            continue
        row = _categorize_ticker(ticker, value, assets, cost_basis_by_ticker)
        ticker_detail.append(row)
        fold_pairs.append((value, row["category"]))
    # Fold the per-ticker (value, category) pairs into the 4 canonical buckets.
    totals = accumulate_category_totals(fold_pairs)
    return AllocationRow(
        date=current.isoformat(),
        total=totals.total,
        us_equity=totals.us_equity,
        non_us_equity=totals.non_us_equity,
        crypto=totals.crypto,
        safe_net=totals.safe_net,
        liabilities=totals.liabilities,
        tickers=ticker_detail,
    )


# ── Daily allocation ───────────────────────────────────────────────────────


def _build_sources(
    db_path: Path,
    qj_db: Path,
    config: RawConfig,
) -> AllocationSources:
    """Load prices + config-derived routing tables into an AllocationSources."""
    assets = config.get("assets", {})
    qj_accounts = config.get("qianji_accounts", {})
    ticker_map = dict(qj_accounts.get("ticker_map", {}))

    # ``401k`` is skipped unconditionally because Empower snapshots are
    # authoritative. Fidelity replay-accounts are always skipped because
    # transaction replay is authoritative. Robinhood is gated on persisted DB
    # rows, not Downloads presence: valuation reads ``robinhood_transactions``,
    # so deleting the source CSV after ingest must not re-enable Qianji balance
    # counting for the same account.
    skip_qj = _FIDELITY_SUPERSEDED_QJ_ACCOUNTS | {"401k"}
    conn = get_connection(db_path)
    try:
        has_robinhood_rows = conn.execute("SELECT 1 FROM robinhood_transactions LIMIT 1").fetchone() is not None
    finally:
        conn.close()
    if has_robinhood_rows:
        skip_qj = skip_qj | {"Robinhood"}

    # currencies are snapshot-time independent (from user_asset.currency),
    # so the as_of=None call is cheap and read-once here.
    return AllocationSources(
        prices=load_prices(db_path),
        cny_rates=load_cny_rates(db_path),
        assets=assets,
        ticker_map=ticker_map,
        qianji_currencies=qianji_currencies(qj_db),
        skip_qj_accounts=skip_qj,
        db_path=db_path,
        source_config=config,
        warning_keys=set(),
    )


def compute_daily_allocation(
    db_path: Path,
    qj_db: Path,
    config: RawConfig,
    start: date,
    end: date,
) -> list[AllocationRow]:
    """Compute daily allocation from start to end.

    Orchestrates per-day valuation. The math is in ``step_one_day``. Qianji is
    replayed for each trading day so its local-day cutoff remains the single
    source of truth; every investment source self-manages its replay inside its
    ``positions_at`` function.

    Args:
        db_path: Path to timemachine.db (prices + CNY rates).
        qj_db: Path to Qianji SQLite DB.
        config: Config dict with ``assets`` and ``qianji_accounts`` keys.
        start: First date to compute.
        end: Last date to compute.

    Returns:
        list of per-day allocation dicts (see ``_build_allocation_row``).
    """
    sources = _build_sources(db_path, qj_db, config)

    results: list[AllocationRow] = []

    current = start
    while current <= end:
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue

        qj_balances = qianji_balances_at(qj_db, current)
        results.append(step_one_day(qj_balances, sources, current))
        current += timedelta(days=1)

    return results
