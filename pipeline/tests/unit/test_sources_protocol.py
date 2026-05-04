"""Unit tests for the source-module contracts.

After the class→module refactor, source identity is the module itself, and
the shared types live in :mod:`etl.sources._types`.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

import etl.sources.empower as empower_src
import etl.sources.fidelity as fidelity_src
import etl.sources.robinhood as robinhood_src
from etl.sources._types import ActionKind, PositionRow, PriceContext

SOURCE_MODULES = (fidelity_src, robinhood_src, empower_src)


def test_action_kind_is_str_enum() -> None:
    for k in ("BUY", "SELL", "DIVIDEND", "REINVESTMENT", "WITHDRAWAL", "DEPOSIT"):
        assert hasattr(ActionKind, k)


def test_position_row_defaults() -> None:
    row = PositionRow(ticker="FXAIX", value_usd=1500.0)
    assert row.cost_basis_usd is None


def test_price_context_required_fields() -> None:
    ctx = PriceContext(prices=pd.DataFrame(), price_date=date(2024, 1, 2), mf_price_date=date(2024, 1, 1))
    assert ctx.price_date == date(2024, 1, 2)


def test_price_context_lookup_returns_none_for_missing() -> None:
    """Empty frame → None; missing ticker → None; missing date → None."""
    empty = PriceContext(prices=pd.DataFrame(), price_date=date(2024, 1, 2), mf_price_date=date(2024, 1, 1))
    assert empty.lookup("VTI") is None

    df = pd.DataFrame({"VTI": [100.0]}, index=[date(2024, 1, 2)])
    ctx = PriceContext(prices=df, price_date=date(2024, 1, 2), mf_price_date=date(2024, 1, 1))
    assert ctx.lookup("VTI") == 100.0
    assert ctx.lookup("AAPL") is None
    # mf_price_date lookup on a non-indexed date returns None
    assert ctx.lookup("VTI", mutual_fund=True) is None


def test_every_source_module_exposes_positions_at() -> None:
    for mod in SOURCE_MODULES:
        assert callable(mod.positions_at), f"{mod.__name__} missing positions_at"


def test_source_modules_return_empty_on_empty_db(empty_db: Path) -> None:
    ctx = PriceContext(prices=pd.DataFrame(), price_date=date(2024, 1, 2), mf_price_date=date(2024, 1, 1))
    for mod in SOURCE_MODULES:
        assert mod.positions_at(empty_db, date(2024, 1, 2), ctx, {}) == []
