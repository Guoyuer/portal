"""Shared types for the investment-source layer.

Extracted out of ``etl/sources/__init__.py`` so that modules which need
the types (``etl/replay.py``, concrete source modules) can import without
triggering the full source registry (which loads every ``fidelity`` /
``robinhood`` / ``empower`` module and would re-enter ``etl.replay``
mid-load).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Protocol

import pandas as pd

from etl.types import RawConfig


class ActionKind(StrEnum):
    """Normalized transaction action types. Each source translates its raw
    action strings (e.g. 'YOU BOUGHT', 'Buy') into one of these at ingest time.

    ``DISTRIBUTION`` is position-only, not dividend income: Fidelity records
    stock splits as distribution quantity rows. Reclassifying it would drop
    split shares; price split validation checks those rows separately.
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
    warning_keys: set[tuple[str, str]] = field(default_factory=set, compare=False, repr=False)

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

    def should_warn_once(self, kind: str, key: str) -> bool:
        """Return True once per warning kind/key for this allocation compute."""
        token = (kind, key)
        if token in self.warning_keys:
            return False
        self.warning_keys.add(token)
        return True


@dataclass(frozen=True)
class PositionRow:
    ticker: str
    value_usd: float
    cost_basis_usd: float | None = None


class InvestmentSource(Protocol):
    """Structural type for source modules.

    Every source module used by allocation must expose ``positions_at``.
    Kept as a ``Protocol`` so mypy catches accidental signature drift; there
    is no runtime class hierarchy.

    ``config`` is the full :class:`etl.types.RawConfig` — each source reads
    only the keys it cares about. Using one union type (instead of per-source
    narrow TypedDicts) keeps the call sites simple: no slicing, no casts.
    Because :class:`RawConfig` is ``total=False``, missing optional keys are
    already well-typed via ``.get()``.
    """

    def positions_at(self, db_path: Path, as_of: date, prices: PriceContext, config: RawConfig) -> list[PositionRow]: ...
