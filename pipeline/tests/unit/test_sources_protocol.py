"""Unit tests for the InvestmentSource Protocol scaffolding (Phase 2 — Task 11)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from etl.sources import (
    _REGISTRY,
    ActionKind,
    InvestmentSource,  # noqa: F401  (imported for side-effect of verifying presence)
    PositionRow,
    PriceContext,
    SourceKind,
    build_investment_sources,
)


def test_source_kind_is_str_enum() -> None:
    assert SourceKind.FIDELITY == "fidelity"
    assert str(SourceKind.ROBINHOOD) == "robinhood"
    assert set(SourceKind) == {SourceKind.FIDELITY, SourceKind.ROBINHOOD, SourceKind.EMPOWER}


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


def test_registry_starts_empty() -> None:
    assert _REGISTRY == []


def test_build_investment_sources_returns_empty_list_for_empty_registry() -> None:
    assert build_investment_sources({}, Path("/tmp/x.db")) == []
