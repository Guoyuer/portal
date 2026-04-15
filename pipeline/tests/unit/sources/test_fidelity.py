"""Unit tests for the Fidelity source module (post class→module refactor)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from etl.db import init_db
from etl.sources import PriceContext
from etl.sources import fidelity as fidelity_src


@pytest.fixture
def config(tmp_path: Path) -> dict[str, object]:
    return {
        "fidelity_downloads": tmp_path,
        "fidelity_accounts": {"X12345678": "FZFXX"},
        "mutual_funds": ["FXAIX"],
    }


def _write_csv(path: Path, header: str, rows: list[str]) -> None:
    """Write a minimal Fidelity Accounts History CSV (two blank lines + header + rows)."""
    body = "\n\n" + header + "\n" + "\n".join(rows) + "\n"
    path.write_text(body, encoding="utf-8")


def test_produces_positions_always_on() -> None:
    assert fidelity_src.produces_positions({}) is True


def test_positions_at_surfaces_cost_basis(tmp_path: Path, config: dict[str, object]) -> None:
    """Fidelity positions must surface cost_basis_usd (spec invariant)."""
    db = tmp_path / "tm.db"
    init_db(db)

    csv = tmp_path / "Accounts_History.csv"
    header = "Run Date,Account,Account Number,Action,Symbol,Description,Type,Quantity,Price,Amount,Settlement Date"
    _write_csv(csv, header, [
        '01/02/2024,"Roth IRA",X12345678,"YOU BOUGHT FXAIX",FXAIX,"Fidelity 500 Index",Cash,10,150,-1500,01/03/2024',
    ])

    fidelity_src.ingest(db, config)

    prices = pd.DataFrame(
        {"FXAIX": [150.0]},
        index=pd.to_datetime([date(2024, 1, 2)]).map(lambda d: d.date()),
    )
    ctx = PriceContext(
        prices=prices,
        price_date=date(2024, 1, 2),
        mf_price_date=date(2024, 1, 2),
    )
    rows = fidelity_src.positions_at(db, date(2024, 1, 2), ctx, config)

    fxaix = [r for r in rows if r.ticker == "FXAIX"]
    assert len(fxaix) == 1
    assert fxaix[0].cost_basis_usd == pytest.approx(1500.0)
    assert fxaix[0].quantity == pytest.approx(10.0)
    assert fxaix[0].value_usd == pytest.approx(1500.0)


def test_positions_at_t_bill_cusip_aggregates_to_t_bills(
    tmp_path: Path, config: dict[str, object],
) -> None:
    """CUSIPs (8+ digits) get valued at face quantity and aggregated as 'T-Bills'."""
    db = tmp_path / "tm.db"
    init_db(db)

    csv = tmp_path / "Accounts_History.csv"
    header = "Run Date,Account,Account Number,Action,Symbol,Description,Type,Quantity,Price,Amount,Settlement Date"
    _write_csv(csv, header, [
        '01/02/2024,"Fidelity taxable",X12345678,"YOU BOUGHT",912796XA1,"T-BILL",Cash,5000,99.5,-4975,01/03/2024',
        '01/02/2024,"Fidelity taxable",X12345678,"YOU BOUGHT",912796XB2,"T-BILL",Cash,3000,99.2,-2976,01/03/2024',
    ])

    fidelity_src.ingest(db, config)
    prices = pd.DataFrame(index=pd.to_datetime([date(2024, 1, 2)]).map(lambda d: d.date()))
    ctx = PriceContext(prices=prices, price_date=date(2024, 1, 2), mf_price_date=date(2024, 1, 2))
    rows = fidelity_src.positions_at(db, date(2024, 1, 2), ctx, config)

    t_bills = [r for r in rows if r.ticker == "T-Bills"]
    # Two CUSIPs should produce two separate PositionRow entries under ticker="T-Bills"
    assert len(t_bills) == 2
    assert sum(r.value_usd for r in t_bills) == pytest.approx(8000.0)


def test_positions_at_routes_cash_to_mm_fund(
    tmp_path: Path, config: dict[str, object],
) -> None:
    """Each Fidelity account's cash balance is routed to its configured MM fund."""
    db = tmp_path / "tm.db"
    init_db(db)

    csv = tmp_path / "Accounts_History.csv"
    header = "Run Date,Account,Account Number,Action,Symbol,Description,Type,Quantity,Price,Amount,Settlement Date"
    _write_csv(csv, header, [
        '01/02/2024,"Roth IRA",X12345678,"Electronic Funds Transfer Received",,,,,,1000,01/03/2024',
    ])

    fidelity_src.ingest(db, config)
    prices = pd.DataFrame(index=pd.to_datetime([date(2024, 1, 2)]).map(lambda d: d.date()))
    ctx = PriceContext(prices=prices, price_date=date(2024, 1, 2), mf_price_date=date(2024, 1, 2))
    rows = fidelity_src.positions_at(db, date(2024, 1, 2), ctx, config)

    # X12345678 maps to FZFXX in config; cash should show up as FZFXX
    fzfxx = [r for r in rows if r.ticker == "FZFXX"]
    assert len(fzfxx) == 1
    assert fzfxx[0].value_usd == pytest.approx(1000.0)
    assert fzfxx[0].account == "X12345678"


def test_positions_at_unknown_account_defaults_to_fzfxx(tmp_path: Path) -> None:
    """Accounts not in fidelity_accounts mapping fall back to FZFXX."""
    config: dict[str, object] = {
        "fidelity_downloads": tmp_path,
        "fidelity_accounts": {},
    }
    db = tmp_path / "tm.db"
    init_db(db)

    csv = tmp_path / "Accounts_History.csv"
    header = "Run Date,Account,Account Number,Action,Symbol,Description,Type,Quantity,Price,Amount,Settlement Date"
    _write_csv(csv, header, [
        '01/02/2024,"Unknown Account",Z99999999,"Electronic Funds Transfer Received",,,,,,500,01/03/2024',
    ])

    fidelity_src.ingest(db, config)
    prices = pd.DataFrame(index=pd.to_datetime([date(2024, 1, 2)]).map(lambda d: d.date()))
    ctx = PriceContext(prices=prices, price_date=date(2024, 1, 2), mf_price_date=date(2024, 1, 2))
    rows = fidelity_src.positions_at(db, date(2024, 1, 2), ctx, config)

    fzfxx = [r for r in rows if r.ticker == "FZFXX"]
    assert len(fzfxx) == 1
    assert fzfxx[0].value_usd == pytest.approx(500.0)


def test_default_mutual_funds_when_config_missing(tmp_path: Path) -> None:
    """Unspecified ``mutual_funds`` → the documented default set."""
    from etl.sources.fidelity import pricing
    config: dict[str, object] = {
        "fidelity_downloads": tmp_path,
        "fidelity_accounts": {"X12345678": "FZFXX"},
    }
    assert "FXAIX" in pricing.mutual_funds(config)
