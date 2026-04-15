"""Compute daily portfolio allocation from the :class:`InvestmentSource` registry + Qianji.

This module reconstructs historical asset allocation by combining:
  - Investment-source positions (each source returns a list of PositionRow;
    Fidelity, Robinhood, and Empower 401k are all routed through the
    :class:`InvestmentSource` registry.)
  - Historical prices (from timemachine.db.daily_close)
  - Qianji account balances (from Qianji SQLite DB)

The per-day math is isolated in ``step_one_day(state, sources, current)`` —
a pure function with no I/O. ``compute_daily_allocation`` is the orchestrator
that refreshes ``ReplayState`` when Qianji transactions change, then delegates
the valuation to ``step_one_day``. Anything that has full per-day state in
hand (e.g. a CI projection script reconstructing state from D1) can call
``step_one_day`` directly without touching the Python replay engines.
"""
from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

from .prices import load_cny_rates, load_prices
from .sources import InvestmentSource, PriceContext, build_investment_sources
from .sources import empower as _empower_source_module  # noqa: F401 — import side-effect registers EmpowerSource
from .sources import fidelity as _fidelity_source_module  # noqa: F401 — import side-effect registers FidelitySource
from .sources import robinhood as _robinhood_source_module  # noqa: F401 — import side-effect registers RobinhoodSource
from .sources.robinhood import RobinhoodSource
from .timemachine import (
    replay_qianji,
    replay_qianji_currencies,
)
from .types import AllocationRow, AssetInfo, RawConfig, TickerDetail

log = logging.getLogger(__name__)

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


# ── Pure per-day step ──────────────────────────────────────────────────────


@dataclass
class AllocationSources:
    """Static inputs resolved once, reused across every day in the window.

    Everything a per-day valuation needs besides the (changing) portfolio
    state: prices + CNY, config-derived routing tables, and the registered
    :class:`InvestmentSource` instances. Keeping these in one bag lets
    ``step_one_day`` stay pure — no hidden globals, no re-reads, no
    surprises.
    """

    prices: pd.DataFrame
    cny_rates: dict[date, float]
    assets: Mapping[str, AssetInfo]
    ticker_map: dict[str, str]
    qianji_currencies: dict[str, str]
    skip_qj_accounts: frozenset[str]
    # Registered :class:`InvestmentSource` instances — populated by
    # ``_build_sources`` via :func:`build_investment_sources`. All three
    # investment sources (Fidelity, Robinhood, Empower 401k) are now routed
    # through this list; ``step_one_day`` is source-kind-agnostic.
    investment_sources: list[InvestmentSource] = field(default_factory=list)


@dataclass
class ReplayState:
    """Portfolio state as of the most recent Fidelity/Qianji replay.

    ``compute_daily_allocation`` rebinds these fields whenever new
    transactions mean the state has changed; ``step_one_day`` only reads.
    Callers outside the replay loop (e.g. a CI projection reconstructing
    state from D1) populate these directly and skip the orchestrator.
    """

    positions: dict[tuple[str, str], float] = field(default_factory=dict)
    cash: dict[str, float] = field(default_factory=dict)
    cost_basis: dict[tuple[str, str], float] = field(default_factory=dict)
    qj_balances: dict[str, float] = field(default_factory=dict)


def step_one_day(
    state: ReplayState,
    sources: AllocationSources,
    current: date,
) -> AllocationRow:
    """Value the portfolio for a single day. Pure — no I/O, no arg mutation.

    Every registered :class:`InvestmentSource` contributes via the uniform
    ``positions_at`` API — no source-kind branching. New sources added to
    :data:`etl.sources._REGISTRY` flow through without touching this function.
    """
    price_date, mf_price_date, cny_rate = _resolve_date_windows(
        sources.prices, sources.cny_rates, current
    )

    ticker_values: dict[str, float] = {}
    cost_basis_by_ticker: dict[str, float] = {}

    # ── Aggregate every investment source via the uniform registry API. ──
    ctx = PriceContext(prices=sources.prices, price_date=price_date, mf_price_date=mf_price_date)
    for src in sources.investment_sources:
        for row in src.positions_at(current, ctx):
            ticker_values[row.ticker] = ticker_values.get(row.ticker, 0.0) + row.value_usd
            if row.cost_basis_usd is not None:
                cost_basis_by_ticker[row.ticker] = (
                    cost_basis_by_ticker.get(row.ticker, 0.0) + row.cost_basis_usd
                )

    _add_qianji_balances(
        ticker_values, state.qj_balances, sources.qianji_currencies,
        sources.ticker_map, sources.assets, cny_rate, sources.skip_qj_accounts,
    )

    return _build_allocation_row(current, ticker_values, sources.assets, cost_basis_by_ticker)


def _build_allocation_row(
    current: date,
    ticker_values: dict[str, float],
    assets: Mapping[str, AssetInfo],
    cost_basis_by_ticker: dict[str, float],
) -> AllocationRow:
    """Categorize each non-zero ticker and produce the per-day allocation dict."""
    category_totals: dict[str, float] = {}
    total = 0.0
    liabilities = 0.0
    ticker_detail: list[TickerDetail] = []

    for ticker, value in ticker_values.items():
        if value == 0:
            continue
        row = _categorize_ticker(ticker, value, assets, cost_basis_by_ticker)
        ticker_detail.append(row)
        if value < 0:
            liabilities += value
        else:
            category_totals[row["category"]] = category_totals.get(row["category"], 0) + value
            total += value

    return AllocationRow(
        date=current.isoformat(),
        total=round(total, 2),
        us_equity=round(category_totals.get("US Equity", 0), 2),
        non_us_equity=round(category_totals.get("Non-US Equity", 0), 2),
        crypto=round(category_totals.get("Crypto", 0), 2),
        safe_net=round(category_totals.get("Safe Net", 0), 2),
        liabilities=round(liabilities, 2),
        tickers=ticker_detail,
    )


# ── Daily allocation ───────────────────────────────────────────────────────


def _build_sources(
    db_path: Path,
    qj_db: Path,
    config: RawConfig,
    investment_sources: list[InvestmentSource] | None = None,
) -> AllocationSources:
    """Load prices + config-derived routing tables into an AllocationSources."""
    assets = config.get("assets", {})
    qj_accounts = config.get("qianji_accounts", {})
    ticker_map = dict(qj_accounts.get("ticker_map", {}))
    ticker_map.setdefault("401k", "401k sp500")

    # If the caller didn't supply an explicit list of sources, build them from
    # ``config`` here so legacy call sites (tests, ad-hoc scripts) keep
    # working without threading a new argument through.
    if investment_sources is None:
        investment_sources = build_investment_sources(dict(config), db_path)

    # Skip the "Robinhood" Qianji account iff a RobinhoodSource with an
    # existing CSV is registered — otherwise the Qianji balance remains the
    # sole source of truth (legacy behaviour for users who never exported the
    # CSV). Fidelity replay-accounts are always skipped because Fidelity
    # positions are authoritative from transaction replay whenever the DB
    # has any fidelity_transactions rows at all.
    skip_qj = _FIDELITY_REPLAY_ACCOUNTS | {"401k"}
    for src in investment_sources:
        if isinstance(src, RobinhoodSource) and src._config.csv_path.exists():
            skip_qj = skip_qj | {"Robinhood"}
            break

    return AllocationSources(
        prices=load_prices(db_path),
        cny_rates=load_cny_rates(db_path),
        assets=assets,
        ticker_map=ticker_map,
        qianji_currencies=replay_qianji_currencies(qj_db),
        skip_qj_accounts=skip_qj,
        investment_sources=investment_sources,
    )


def compute_daily_allocation(
    db_path: Path,
    qj_db: Path,
    config: RawConfig,
    start: date,
    end: date,
    *,
    investment_sources: list[InvestmentSource] | None = None,
) -> list[AllocationRow]:
    """Compute daily allocation from start to end.

    Orchestrates per-day valuation. The math is in ``step_one_day``; this
    function just decides when to re-run the Qianji replay to keep
    ``ReplayState`` current. Every investment source self-manages its
    replay inside :meth:`InvestmentSource.positions_at`; none needs state
    here.

    Args:
        db_path: Path to timemachine.db (prices + CNY rates).
        qj_db: Path to Qianji SQLite DB.
        config: Config dict with ``assets`` and ``qianji_accounts`` keys.
        start: First date to compute.
        end: Last date to compute.
        investment_sources: Pre-built registry instances. Optional: omitted
            callers fall back to :func:`build_investment_sources` against
            ``config``.

    Returns:
        list of per-day allocation dicts (see ``_build_allocation_row``).
    """
    sources = _build_sources(
        db_path, qj_db, config,
        investment_sources=investment_sources,
    )

    qj_txn_dates = set(_qianji_transaction_dates(qj_db))

    results: list[AllocationRow] = []
    state = ReplayState()
    last_qj_replay: date | None = None
    qj_replayed = False

    current = start
    while current <= end:
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue

        # Replay Qianji only when new balances exist in the window.
        needs_qj = not qj_replayed or any(
            d > (last_qj_replay or date.min) and d <= current for d in qj_txn_dates
        )
        if needs_qj:
            state.qj_balances = replay_qianji(qj_db, current)
            last_qj_replay = current
            qj_replayed = True

        results.append(step_one_day(state, sources, current))
        current += timedelta(days=1)

    return results
