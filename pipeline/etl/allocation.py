"""Compute daily portfolio allocation from Fidelity replay + Qianji + 401k.

This module reconstructs historical asset allocation by combining:
  - Fidelity positions (from transaction CSV replay)
  - Historical prices (from timemachine.db.daily_close)
  - Qianji account balances (from Qianji SQLite DB)
  - 401k values (pre-computed via proxy interpolation)

The per-day math is isolated in ``step_one_day(state, sources, current)`` —
a pure function with no I/O. ``compute_daily_allocation`` is the orchestrator
that refreshes ``ReplayState`` when Fidelity/Qianji transactions change, then
delegates the valuation to ``step_one_day``. Anything that has full per-day
state in hand (e.g. a CI projection script reconstructing state from D1) can
call ``step_one_day`` directly without touching the Python replay engines.

Refactor hint (audit C04, ``docs/code-design-audit-2026-04-13.md``):
``compute_daily_allocation`` currently takes 6 positional args + 1 keyword,
and each new data source adds another positional. When the 7th source lands,
migrate the signature to an ``AllocationRequest`` dataclass and make the
internal ``_add_*`` helpers return ``dict[str, float]`` instead of mutating
``ticker_values`` in place. That also makes per-source unit tests trivial
(today they need the full dict assembled first).
"""
from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

from .ingest.robinhood_history import replay_robinhood
from .prices import load_cny_rates, load_prices
from .sources import InvestmentSource, PriceContext, SourceKind, build_investment_sources
from .sources import fidelity as _fidelity_source_module  # noqa: F401 — import side-effect registers FidelitySource
from .timemachine import (
    replay_qianji,
    replay_qianji_currencies,
)
from .types import AllocationRow, AssetInfo, RawConfig, RobinhoodReplayResult, TickerDetail

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
    fidelity_accounts: Mapping[str, str],
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
    rh_replay_fn: Callable[..., RobinhoodReplayResult] | None,
    robinhood_csv: Path | None,
    current: date,
    prices: pd.DataFrame,
    price_date: date,
) -> dict[str, float]:
    """Add Robinhood positions (qty × price). Returns cost-basis dict by symbol."""
    if not (rh_replay_fn and robinhood_csv):
        return {}
    rh_result = rh_replay_fn(robinhood_csv, as_of=current)
    for sym, qty in rh_result["positions"].items():
        if sym in prices.columns and price_date in prices.index:
            price = prices.loc[price_date, sym]
            if pd.notna(price):
                ticker_values[sym] = ticker_values.get(sym, 0) + qty * float(price)
    return rh_result["cost_basis"]


# ── Pure per-day step ──────────────────────────────────────────────────────


@dataclass
class AllocationSources:
    """Static inputs resolved once, reused across every day in the window.

    Everything a per-day valuation needs besides the (changing) portfolio
    state: prices + CNY, config-derived routing tables, and the pre-computed
    401k daily map. Keeping these in one bag lets ``step_one_day`` stay pure
    — no hidden globals, no re-reads, no surprises.
    """

    prices: pd.DataFrame
    cny_rates: dict[date, float]
    assets: Mapping[str, AssetInfo]
    ticker_map: dict[str, str]
    fidelity_accounts: Mapping[str, str]
    qianji_currencies: dict[str, str]
    skip_qj_accounts: frozenset[str]
    k401_daily: dict[date, dict[str, float]]
    rh_replay_fn: Callable[..., RobinhoodReplayResult] | None = None
    robinhood_csv: Path | None = None
    # Registered :class:`InvestmentSource` instances — populated by
    # ``_build_sources`` via :func:`build_investment_sources`. Currently only
    # Fidelity is migrated; other sources (Robinhood, 401k) still run through
    # their legacy ``_add_*`` helpers until Phases 4-5 land.
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

    Fidelity routes through the :class:`InvestmentSource` registry
    (``sources.investment_sources``). Robinhood and 401k still run their
    legacy ``_add_*`` helpers and are migrated in Phases 4-5.
    """
    price_date, mf_price_date, cny_rate = _resolve_date_windows(
        sources.prices, sources.cny_rates, current
    )

    ticker_values: dict[str, float] = {}
    cost_basis_by_ticker: dict[str, float] = {}

    # ── Fidelity via the registry ──
    ctx = PriceContext(prices=sources.prices, price_date=price_date, mf_price_date=mf_price_date)
    for src in sources.investment_sources:
        if src.kind != SourceKind.FIDELITY:
            continue
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
    _add_401k(ticker_values, sources.k401_daily, current)
    rh_cost_basis = _add_robinhood(
        ticker_values, sources.rh_replay_fn, sources.robinhood_csv,
        current, sources.prices, price_date,
    )

    for sym, cb in rh_cost_basis.items():
        cost_basis_by_ticker[sym] = cost_basis_by_ticker.get(sym, 0) + cb

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
    k401_daily: dict[date, dict[str, float]],
    robinhood_csv: Path | None,
    investment_sources: list[InvestmentSource] | None = None,
) -> AllocationSources:
    """Load prices + config-derived routing tables into an AllocationSources."""
    assets = config.get("assets", {})
    qj_accounts = config.get("qianji_accounts", {})
    ticker_map = dict(qj_accounts.get("ticker_map", {}))
    ticker_map.setdefault("401k", "401k sp500")
    fidelity_accounts = config.get("fidelity_accounts", {})

    rh_replay_fn: Callable[..., RobinhoodReplayResult] | None = (
        replay_robinhood if robinhood_csv and robinhood_csv.exists() else None
    )
    skip_qj = _FIDELITY_REPLAY_ACCOUNTS | {"401k"}
    if rh_replay_fn:
        skip_qj = skip_qj | {"Robinhood"}

    # If the caller didn't supply an explicit list of sources, build them from
    # ``config`` here so legacy call sites (tests, ad-hoc scripts) keep
    # working without threading a new argument through.
    if investment_sources is None:
        investment_sources = build_investment_sources(dict(config), db_path)

    return AllocationSources(
        prices=load_prices(db_path),
        cny_rates=load_cny_rates(db_path),
        assets=assets,
        ticker_map=ticker_map,
        fidelity_accounts=fidelity_accounts,
        qianji_currencies=replay_qianji_currencies(qj_db),
        skip_qj_accounts=skip_qj,
        k401_daily=k401_daily,
        rh_replay_fn=rh_replay_fn,
        robinhood_csv=robinhood_csv,
        investment_sources=investment_sources,
    )


def compute_daily_allocation(
    db_path: Path,
    qj_db: Path,
    config: RawConfig,
    k401_daily: dict[date, dict[str, float]],
    start: date,
    end: date,
    *,
    robinhood_csv: Path | None = None,
    investment_sources: list[InvestmentSource] | None = None,
) -> list[AllocationRow]:
    """Compute daily allocation from start to end.

    Orchestrates per-day valuation. The math is in ``step_one_day``; this
    function just decides when to re-run the Qianji replay to keep
    ``ReplayState`` current. Fidelity now self-manages its replay inside
    :class:`FidelitySource.positions_at` and no longer needs state here.

    Args:
        db_path: Path to timemachine.db (prices + CNY rates).
        qj_db: Path to Qianji SQLite DB.
        config: Config dict with ``assets`` and ``qianji_accounts`` keys.
        k401_daily: Pre-computed 401k daily values by config ticker.
        start: First date to compute.
        end: Last date to compute.
        robinhood_csv: Optional Robinhood CSV; routes through the legacy
            helper until Phase 4.
        investment_sources: Pre-built registry instances. Optional: omitted
            callers fall back to :func:`build_investment_sources` against
            ``config``.

    Returns:
        list of per-day allocation dicts (see ``_build_allocation_row``).
    """
    sources = _build_sources(
        db_path, qj_db, config, k401_daily, robinhood_csv,
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
