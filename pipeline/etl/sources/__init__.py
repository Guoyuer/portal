"""Investment source registry and shared protocol.

Architecture rule: all source-specific logic lives in etl/sources/<name>.py.
This module contains only the shared types and the registry list.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Protocol

import pandas as pd


class SourceKind(StrEnum):
    FIDELITY = "fidelity"
    ROBINHOOD = "robinhood"
    EMPOWER = "empower"


class ActionKind(StrEnum):
    """Normalized transaction action types. Each source translates its raw
    action strings (e.g. 'YOU BOUGHT', 'Buy') into one of these at ingest time."""
    BUY = "buy"
    SELL = "sell"
    DIVIDEND = "dividend"
    REINVESTMENT = "reinvestment"
    WITHDRAWAL = "withdrawal"
    DEPOSIT = "deposit"
    TRANSFER = "transfer"
    OTHER = "other"


@dataclass(frozen=True)
class PriceContext:
    """Passed uniformly to every InvestmentSource.positions_at.

    Sources that don't need prices (Empower uses pre-computed daily values)
    simply ignore this argument.
    """
    prices: pd.DataFrame
    price_date: date
    mf_price_date: date


@dataclass(frozen=True)
class PositionRow:
    ticker: str
    value_usd: float
    quantity: float | None = None
    cost_basis_usd: float | None = None
    account: str | None = None


class InvestmentSource(Protocol):
    """Protocol for all investment sources. Each concrete source holds its own
    typed config and db_path via __init__; methods take only the per-call
    varying arguments."""
    kind: ClassVar[SourceKind]

    def ingest(self) -> None: ...
    def positions_at(self, as_of: date, prices: PriceContext) -> list[PositionRow]: ...


# Populated by each source module registering itself. Order matters only for
# deterministic test output — it does not affect allocation correctness.
_REGISTRY: list[type[InvestmentSource]] = []


def build_investment_sources(raw: dict[str, object], db_path: Path) -> list[InvestmentSource]:
    """Instantiate every registered source with its config slice."""
    return [cls.from_raw_config(raw, db_path) for cls in _REGISTRY]  # type: ignore[attr-defined]
