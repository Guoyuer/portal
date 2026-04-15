"""Unit tests for the source-module composition API.

After the class→module refactor, source identity is the module itself, and
the shared types live in :mod:`etl.sources`. This file exercises the public
surface consumed by ``etl.allocation`` + ``scripts.build_timemachine_db``.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from etl.sources import (
    SOURCES,
    ActionKind,
    InvestmentSource,
    PositionRow,
    PriceContext,
    ingest_all,
    positions_at_all,
)


def test_action_kind_is_str_enum() -> None:
    for k in ("BUY", "SELL", "DIVIDEND", "REINVESTMENT", "WITHDRAWAL", "DEPOSIT"):
        assert hasattr(ActionKind, k)


def test_position_row_defaults() -> None:
    row = PositionRow(ticker="FXAIX", value_usd=1500.0)
    assert row.quantity is None
    assert row.cost_basis_usd is None
    assert row.account is None


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


def test_sources_module_list_contains_all_three() -> None:
    """The ordered SOURCES list drives ingest/positions composition."""
    from etl.sources import empower, fidelity, robinhood
    assert fidelity in SOURCES
    assert robinhood in SOURCES
    assert empower in SOURCES


def test_every_source_module_implements_protocol() -> None:
    """mypy + runtime: every module in SOURCES exposes the 3-call contract."""
    for mod in SOURCES:
        assert isinstance(mod, InvestmentSource), f"{mod.__name__} missing protocol methods"


def test_positions_at_all_returns_empty_on_empty_db(tmp_path: Path) -> None:
    """Against a fresh DB every source returns ``[]`` — sanity check."""
    from etl.db import init_db
    db = tmp_path / "tm.db"
    init_db(db)
    ctx = PriceContext(prices=pd.DataFrame(), price_date=date(2024, 1, 2), mf_price_date=date(2024, 1, 1))
    rows = positions_at_all(db, date(2024, 1, 2), ctx, {})
    assert rows == []


def test_ingest_all_is_silent_with_empty_config(tmp_path: Path) -> None:
    """Every ingest path must tolerate a missing-inputs config."""
    from etl.db import init_db
    db = tmp_path / "tm.db"
    init_db(db)
    ingest_all(db, {"fidelity_downloads": tmp_path})  # no CSVs → no-op
