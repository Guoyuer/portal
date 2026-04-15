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


def test_registry_contains_fidelity() -> None:
    """After Phase 3 (Task 14) FidelitySource self-registers on import."""
    import etl.sources.fidelity  # noqa: F401  (import side effect: register)
    from etl.sources.fidelity import FidelitySource
    assert FidelitySource in _REGISTRY


def test_build_investment_sources_returns_fidelity(tmp_path: Path) -> None:
    """With FidelitySource registered, build returns at least Fidelity."""
    import etl.sources.fidelity  # noqa: F401
    from etl.sources.fidelity import FidelitySource
    raw = {"fidelity_downloads": tmp_path, "fidelity_accounts": {}}
    built = build_investment_sources(raw, tmp_path / "tm.db")
    assert any(isinstance(s, FidelitySource) for s in built)
