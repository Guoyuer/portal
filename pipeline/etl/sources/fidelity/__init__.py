"""Fidelity source — CSV ingest plus point-in-time positions.

Public surface mirrors the other broker modules: ``ingest(...)`` persists raw
input rows and ``positions_at(...)`` reconstructs point-in-time holdings.

``positions_at`` delegates transaction replay to the source-agnostic
:func:`etl.replay.replay_transactions` primitive via ``FIDELITY_REPLAY``
(see :class:`etl.replay.ReplayConfig`). The primitive covers Fidelity's
full action vocabulary — BUY / SELL / REINVESTMENT plus the qty-only
kinds REDEMPTION / DISTRIBUTION / EXCHANGE / TRANSFER (``REDEMPTION
PAYOUT``, ``TRANSFERRED FROM/TO``, ``DISTRIBUTION``, ``EXCHANGED TO``)
— and ``exclude_tickers`` filters MM fund symbols out of share
accumulation while still letting them flow through the cash ledger.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import date
from pathlib import Path

from etl.parsing import is_cusip
from etl.replay import PositionState, ReplayConfig, replay_transactions
from etl.sources._types import PositionRow, PriceContext
from etl.types import RawConfig

from . import parse
from .parse import TABLE

log = logging.getLogger(__name__)

# Fidelity-specific money-market fund tickers. Treated as $1/share cash, so
# they stay out of the per-share position accumulator and instead flow
# through the cash ledger (and, for REINVESTMENT rows, into the MM DRIP
# adjustment that corrects for shares credited without a paired cash entry).
MM_SYMBOLS: frozenset[str] = frozenset({"SPAXX", "FZFXX", "FDRXX"})

# Default set of mutual-fund tickers that need T-1 price lookup.
#
# yfinance stamps open-end mutual-fund NAV with the PREVIOUS trading day's
# date. ETFs + closed-end funds trade intraday and report T-0 correctly.
_DEFAULT_MUTUAL_FUNDS: frozenset[str] = frozenset({"FXAIX", "FSSNX", "FNJHX", "FTIHX"})
_DEFAULT_MM_TICKER = "FZFXX"

# Per-source replay config — passed to :func:`etl.replay.replay_transactions`.
FIDELITY_REPLAY = ReplayConfig(
    table=TABLE,
    date_col="run_date",
    ticker_col="symbol",
    amount_col="amount",
    account_col="account_number",
    exclude_tickers=MM_SYMBOLS,
    track_cash=True,
    lot_type_col="lot_type",
    mm_drip_tickers=MM_SYMBOLS,
)


def _mutual_funds(config: RawConfig) -> frozenset[str]:
    raw = config.get("mutual_funds")
    if raw is None:
        return _DEFAULT_MUTUAL_FUNDS
    return frozenset(raw)


def _position_rows(
    positions: Mapping[tuple[str, str], PositionState],
    prices: PriceContext,
    mutual_fund_set: frozenset[str],
) -> list[PositionRow]:
    rows: list[PositionRow] = []

    for (_acct, sym), state in positions.items():
        qty = state.quantity
        if is_cusip(sym):
            rows.append(PositionRow(
                ticker="T-Bills",
                value_usd=qty,
            ))
            continue

        price = prices.lookup(sym, mutual_fund=sym in mutual_fund_set)
        if price is not None:
            rows.append(PositionRow(
                ticker=sym,
                value_usd=qty * price,
            ))
            continue
        if prices.should_warn_once("fidelity_missing_price", sym):
            p_date = prices.mf_price_date if sym in mutual_fund_set else prices.price_date
            log.warning(
                "No price for %s on %s (holding %.3f shares) — excluded from allocation",
                sym, p_date, qty,
            )

    return rows


def _cash_rows(cash_by_account: dict[str, float], config: RawConfig) -> list[PositionRow]:
    accounts = config.get("fidelity_accounts") or {}
    return [
        PositionRow(
            ticker=accounts.get(acct, _DEFAULT_MM_TICKER),
            value_usd=bal,
        )
        for acct, bal in cash_by_account.items()
    ]


# ── Public API (module protocol) ───────────────────────────────────────────


def ingest(db_path: Path, downloads_dir: Path) -> None:
    """Scan ``downloads_dir`` for ``Accounts_History*.csv`` and ingest each file.

    Files can overlap and be partial on boundary dates, so Fidelity ingest
    rebuilds the table from the canonical union of every observed CSV row.
    Repeated observations across files are de-duplicated; same-file duplicate
    rows are preserved.
    """
    raw_csvs = sorted(downloads_dir.glob("Accounts_History*.csv"))
    parse.ingest_csvs(db_path, raw_csvs)


def positions_at(
    db_path: Path,
    as_of: date,
    prices: PriceContext,
    config: RawConfig,
) -> list[PositionRow]:
    """Return one PositionRow per (account, ticker) position + cash bucket.

    Delegates to :func:`etl.replay.replay_transactions` with the module-
    level :data:`FIDELITY_REPLAY` config (schema + cash-ledger knobs).
    MM fund symbols are excluded from share accumulation but still flow
    through cash — the ``mm_drip_tickers`` knob credits ``REINVESTMENT``
    rows' share counts back to the cash ledger the way the legacy replay
    did.
    """
    result = replay_transactions(db_path, FIDELITY_REPLAY, as_of)

    rows = _position_rows(
        positions=result.positions,
        prices=prices,
        mutual_fund_set=_mutual_funds(config),
    )
    rows.extend(_cash_rows(result.cash, config))
    return rows
