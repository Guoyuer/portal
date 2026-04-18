"""Shared types for the investment-source layer.

Extracted out of ``etl/sources/__init__.py`` so that modules which need
the types (``etl/replay.py``, concrete source modules) can import without
triggering the full source registry (which loads every ``fidelity`` /
``robinhood`` / ``empower`` module and would re-enter ``etl.replay``
mid-load).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

import pandas as pd


class ActionKind(StrEnum):
    """Normalized transaction action types. Each source translates its raw
    action strings (e.g. 'YOU BOUGHT', 'Buy') into one of these at ingest time.

    The position-only kinds (``REDEMPTION`` / ``DISTRIBUTION`` / ``EXCHANGE`` /
    ``TRANSFER``) change share count without touching cost basis —
    :func:`etl.replay.replay_transactions` applies ``qty += q`` for these
    and leaves ``cost`` alone. They mirror Fidelity's legacy
    ``POSITION_PREFIXES`` (``REDEMPTION PAYOUT``, ``TRANSFERRED FROM/TO``,
    ``DISTRIBUTION``, ``EXCHANGED TO``).

    **Stock splits arrive as DISTRIBUTION.** Fidelity records a 3:1 split on
    SCHD as ``DISTRIBUTION SCHWAB US DIVIDEND EQUITY ETF (SCHD)`` with
    ``quantity = pre_split_qty × 2`` (the new shares) and ``price = 0``.
    The qty-only handling in :func:`etl.replay.replay_transactions` is
    correct for splits: no cash changes hands, and the per-share cost basis
    drops proportionally because total cost stays the same. Do NOT
    reclassify DISTRIBUTION as DIVIDEND — that would silently drop the
    split quantity update. :func:`etl.prices._validate_splits_against_transactions`
    cross-checks Yahoo's ``.splits`` history against these DISTRIBUTION
    rows to catch either side drifting.
    """
    BUY = "buy"
    SELL = "sell"
    DIVIDEND = "dividend"
    REINVESTMENT = "reinvestment"
    WITHDRAWAL = "withdrawal"
    DEPOSIT = "deposit"
    TRANSFER = "transfer"
    REDEMPTION = "redemption"
    DISTRIBUTION = "distribution"
    EXCHANGE = "exchange"
    OTHER = "other"


@dataclass(frozen=True)
class PriceContext:
    """Passed uniformly to every source's ``positions_at``.

    Sources that don't need prices (Empower uses pre-computed daily values)
    simply ignore this argument.
    """
    prices: pd.DataFrame
    price_date: date
    mf_price_date: date

    def lookup(self, ticker: str, *, mutual_fund: bool = False) -> float | None:
        """Return the close price for ``ticker`` on the appropriate date, or None.

        Uses ``mf_price_date`` (T-1) when ``mutual_fund=True``, otherwise
        ``price_date``. Returns ``None`` when the ticker or the date is missing,
        or when the cell is NaN — callers log + exclude the row.
        """
        p_date = self.mf_price_date if mutual_fund else self.price_date
        if ticker in self.prices.columns and p_date in self.prices.index:
            v = self.prices.loc[p_date, ticker]
            if pd.notna(v):
                return float(v)
        return None


@dataclass(frozen=True)
class PositionRow:
    ticker: str
    value_usd: float
    quantity: float | None = None
    cost_basis_usd: float | None = None
    account: str | None = None


@runtime_checkable
class InvestmentSource(Protocol):
    """Structural type for source modules.

    Every module in :data:`etl.sources.SOURCES` must expose these three
    callables. Kept as a ``Protocol`` so mypy catches accidental signature
    drift; there is no runtime class hierarchy.
    """

    def ingest(self, db_path: Path, config: dict[str, object]) -> None: ...
    def positions_at(
        self, db_path: Path, as_of: date, prices: PriceContext, config: dict[str, object]
    ) -> list[PositionRow]: ...
    def produces_positions(self, config: dict[str, object]) -> bool: ...
