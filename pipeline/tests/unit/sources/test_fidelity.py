"""Unit tests for FidelitySource (Phase 3 — Task 14)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from etl.db import init_db
from etl.sources import PriceContext, SourceKind
from etl.sources.fidelity import FidelitySource, FidelitySourceConfig


@pytest.fixture
def empty_config(tmp_path: Path) -> FidelitySourceConfig:
    return FidelitySourceConfig(
        downloads_dir=tmp_path,
        fidelity_accounts={"X12345678": "FZFXX"},
        mutual_funds=frozenset({"FXAIX"}),
    )


def _write_csv(path: Path, header: str, rows: list[str]) -> None:
    """Write a minimal Fidelity Accounts History CSV (two blank lines + header + rows)."""
    body = "\n\n" + header + "\n" + "\n".join(rows) + "\n"
    path.write_text(body, encoding="utf-8")


def test_kind_is_fidelity() -> None:
    assert FidelitySource.kind == SourceKind.FIDELITY


def test_from_raw_config_reads_keys(tmp_path: Path) -> None:
    raw = {
        "fidelity_downloads": tmp_path,
        "fidelity_accounts": {"X12345678": "FZFXX"},
        "mutual_funds": ["FXAIX"],
    }
    src = FidelitySource.from_raw_config(raw, tmp_path / "tm.db")
    assert src._config.fidelity_accounts == {"X12345678": "FZFXX"}
    assert src._config.mutual_funds == frozenset({"FXAIX"})
    assert src._config.downloads_dir == tmp_path


def test_from_raw_config_handles_missing_mutual_funds(tmp_path: Path) -> None:
    """Unspecified mutual_funds should fall back to the documented default set."""
    raw = {
        "fidelity_downloads": tmp_path,
        "fidelity_accounts": {"X12345678": "FZFXX"},
    }
    src = FidelitySource.from_raw_config(raw, tmp_path / "tm.db")
    # Default mutual funds match allocation._MUTUAL_FUNDS to preserve behavior
    assert "FXAIX" in src._config.mutual_funds


def test_positions_at_surfaces_cost_basis(
    tmp_path: Path, empty_config: FidelitySourceConfig
) -> None:
    """Fidelity positions must surface cost_basis_usd (spec invariant)."""
    db = tmp_path / "tm.db"
    init_db(db)

    csv = tmp_path / "Accounts_History.csv"
    header = "Run Date,Account,Account Number,Action,Symbol,Description,Type,Quantity,Price,Amount,Settlement Date"
    _write_csv(csv, header, [
        '01/02/2024,"Roth IRA",X12345678,"YOU BOUGHT FXAIX",FXAIX,"Fidelity 500 Index",Cash,10,150,-1500,01/03/2024',
    ])

    src = FidelitySource(empty_config, db)
    src.ingest()

    prices = pd.DataFrame(
        {"FXAIX": [150.0]},
        index=pd.to_datetime([date(2024, 1, 2)]).map(lambda d: d.date()),
    )
    ctx = PriceContext(
        prices=prices,
        price_date=date(2024, 1, 2),
        mf_price_date=date(2024, 1, 2),
    )
    rows = src.positions_at(date(2024, 1, 2), ctx)

    fxaix = [r for r in rows if r.ticker == "FXAIX"]
    assert len(fxaix) == 1
    assert fxaix[0].cost_basis_usd == pytest.approx(1500.0)
    assert fxaix[0].quantity == pytest.approx(10.0)
    assert fxaix[0].value_usd == pytest.approx(1500.0)


def test_positions_at_t_bill_cusip_aggregates_to_t_bills(
    tmp_path: Path, empty_config: FidelitySourceConfig
) -> None:
    """CUSIPs (8+ digits) get valued at face quantity and aggregated as 'T-Bills'."""
    db = tmp_path / "tm.db"
    init_db(db)

    csv = tmp_path / "Accounts_History.csv"
    header = "Run Date,Account,Account Number,Action,Symbol,Description,Type,Quantity,Price,Amount,Settlement Date"
    # 9-digit CUSIPs (Treasury bills) — valued at face value quantity
    _write_csv(csv, header, [
        '01/02/2024,"Fidelity taxable",X12345678,"YOU BOUGHT",912796XA1,"T-BILL",Cash,5000,99.5,-4975,01/03/2024',
        '01/02/2024,"Fidelity taxable",X12345678,"YOU BOUGHT",912796XB2,"T-BILL",Cash,3000,99.2,-2976,01/03/2024',
    ])

    src = FidelitySource(empty_config, db)
    src.ingest()
    prices = pd.DataFrame(index=pd.to_datetime([date(2024, 1, 2)]).map(lambda d: d.date()))
    ctx = PriceContext(prices=prices, price_date=date(2024, 1, 2), mf_price_date=date(2024, 1, 2))
    rows = src.positions_at(date(2024, 1, 2), ctx)

    t_bills = [r for r in rows if r.ticker == "T-Bills"]
    # Two CUSIPs should produce two separate PositionRow entries under ticker="T-Bills"
    assert len(t_bills) == 2
    assert sum(r.value_usd for r in t_bills) == pytest.approx(8000.0)


def test_positions_at_routes_cash_to_mm_fund(
    tmp_path: Path, empty_config: FidelitySourceConfig
) -> None:
    """Each Fidelity account's cash balance is routed to its configured MM fund."""
    db = tmp_path / "tm.db"
    init_db(db)

    csv = tmp_path / "Accounts_History.csv"
    header = "Run Date,Account,Account Number,Action,Symbol,Description,Type,Quantity,Price,Amount,Settlement Date"
    # Electronic Funds Transfer (deposit) leaves a cash balance in the account
    _write_csv(csv, header, [
        '01/02/2024,"Roth IRA",X12345678,"Electronic Funds Transfer Received",,,,,,1000,01/03/2024',
    ])

    src = FidelitySource(empty_config, db)
    src.ingest()
    prices = pd.DataFrame(index=pd.to_datetime([date(2024, 1, 2)]).map(lambda d: d.date()))
    ctx = PriceContext(prices=prices, price_date=date(2024, 1, 2), mf_price_date=date(2024, 1, 2))
    rows = src.positions_at(date(2024, 1, 2), ctx)

    # X12345678 maps to FZFXX in empty_config; cash should show up as FZFXX
    fzfxx = [r for r in rows if r.ticker == "FZFXX"]
    assert len(fzfxx) == 1
    assert fzfxx[0].value_usd == pytest.approx(1000.0)
    assert fzfxx[0].account == "X12345678"


def test_positions_at_unknown_account_defaults_to_fzfxx(
    tmp_path: Path,
) -> None:
    """Accounts not in fidelity_accounts mapping fall back to FZFXX."""
    config = FidelitySourceConfig(
        downloads_dir=tmp_path,
        fidelity_accounts={},  # no mapping at all
        mutual_funds=frozenset(),
    )
    db = tmp_path / "tm.db"
    init_db(db)

    csv = tmp_path / "Accounts_History.csv"
    header = "Run Date,Account,Account Number,Action,Symbol,Description,Type,Quantity,Price,Amount,Settlement Date"
    _write_csv(csv, header, [
        '01/02/2024,"Unknown Account",Z99999999,"Electronic Funds Transfer Received",,,,,,500,01/03/2024',
    ])

    src = FidelitySource(config, db)
    src.ingest()
    prices = pd.DataFrame(index=pd.to_datetime([date(2024, 1, 2)]).map(lambda d: d.date()))
    ctx = PriceContext(prices=prices, price_date=date(2024, 1, 2), mf_price_date=date(2024, 1, 2))
    rows = src.positions_at(date(2024, 1, 2), ctx)

    fzfxx = [r for r in rows if r.ticker == "FZFXX"]
    assert len(fzfxx) == 1
    assert fzfxx[0].value_usd == pytest.approx(500.0)


def test_registered_in_registry() -> None:
    """Importing the module must register FidelitySource in _REGISTRY."""
    import etl.sources.fidelity  # noqa: F401
    from etl.sources import _REGISTRY
    assert FidelitySource in _REGISTRY
