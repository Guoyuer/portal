"""Unit tests for the Fidelity source module (post class→module refactor)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import etl.sources.fidelity as fidelity_src
from etl.sources._types import PriceContext
from tests.unit.sources.conftest import FIDELITY_HEADER_SHORT, write_fidelity_csv


@pytest.fixture
def config(tmp_path: Path) -> dict[str, object]:
    return {
        "fidelity_accounts": {"X12345678": "FZFXX"},
        "mutual_funds": ["FXAIX"],
    }


def test_positions_at_values_holding(tmp_path: Path, empty_db: Path, config: dict[str, object]) -> None:
    """Fidelity positions value replayed shares with the requested price date."""
    csv = tmp_path / "Accounts_History.csv"
    write_fidelity_csv(csv, [
        '01/02/2024,"Roth IRA",X12345678,"YOU BOUGHT FXAIX",FXAIX,"Fidelity 500 Index",Cash,10,150,-1500,01/03/2024',
    ], header=FIDELITY_HEADER_SHORT)

    fidelity_src.ingest(empty_db, tmp_path)

    prices = pd.DataFrame(
        {"FXAIX": [150.0]},
        index=pd.to_datetime([date(2024, 1, 2)]).map(lambda d: d.date()),
    )
    ctx = PriceContext(
        prices=prices,
        price_date=date(2024, 1, 2),
        mf_price_date=date(2024, 1, 2),
    )
    rows = fidelity_src.positions_at(empty_db, date(2024, 1, 2), ctx, config)

    fxaix = [r for r in rows if r.ticker == "FXAIX"]
    assert len(fxaix) == 1
    assert fxaix[0].value_usd == pytest.approx(1500.0)


def test_positions_at_t_bill_cusip_aggregates_to_t_bills(
    tmp_path: Path, empty_db: Path, config: dict[str, object],
) -> None:
    """CUSIPs (8+ digits) get valued at face quantity and aggregated as 'T-Bills'."""
    csv = tmp_path / "Accounts_History.csv"
    write_fidelity_csv(csv, [
        '01/02/2024,"Fidelity taxable",X12345678,"YOU BOUGHT",912796XA1,"T-BILL",Cash,5000,99.5,-4975,01/03/2024',
        '01/02/2024,"Fidelity taxable",X12345678,"YOU BOUGHT",912796XB2,"T-BILL",Cash,3000,99.2,-2976,01/03/2024',
    ], header=FIDELITY_HEADER_SHORT)

    fidelity_src.ingest(empty_db, tmp_path)
    prices = pd.DataFrame(index=pd.to_datetime([date(2024, 1, 2)]).map(lambda d: d.date()))
    ctx = PriceContext(prices=prices, price_date=date(2024, 1, 2), mf_price_date=date(2024, 1, 2))
    rows = fidelity_src.positions_at(empty_db, date(2024, 1, 2), ctx, config)

    t_bills = [r for r in rows if r.ticker == "T-Bills"]
    # Two CUSIPs should produce two separate PositionRow entries under ticker="T-Bills"
    assert len(t_bills) == 2
    assert sum(r.value_usd for r in t_bills) == pytest.approx(8000.0)


def test_positions_at_routes_cash_to_mm_fund(
    tmp_path: Path, empty_db: Path, config: dict[str, object],
) -> None:
    """Each Fidelity account's cash balance is routed to its configured MM fund."""
    csv = tmp_path / "Accounts_History.csv"
    write_fidelity_csv(csv, [
        '01/02/2024,"Roth IRA",X12345678,"Electronic Funds Transfer Received",,,,,,1000,01/03/2024',
    ], header=FIDELITY_HEADER_SHORT)

    fidelity_src.ingest(empty_db, tmp_path)
    prices = pd.DataFrame(index=pd.to_datetime([date(2024, 1, 2)]).map(lambda d: d.date()))
    ctx = PriceContext(prices=prices, price_date=date(2024, 1, 2), mf_price_date=date(2024, 1, 2))
    rows = fidelity_src.positions_at(empty_db, date(2024, 1, 2), ctx, config)

    # X12345678 maps to FZFXX in config; cash should show up as FZFXX
    fzfxx = [r for r in rows if r.ticker == "FZFXX"]
    assert len(fzfxx) == 1
    assert fzfxx[0].value_usd == pytest.approx(1000.0)


def test_positions_at_unknown_account_defaults_to_fzfxx(tmp_path: Path, empty_db: Path) -> None:
    """Accounts not in fidelity_accounts mapping fall back to FZFXX."""
    config: dict[str, object] = {
        "fidelity_accounts": {},
    }

    csv = tmp_path / "Accounts_History.csv"
    write_fidelity_csv(csv, [
        '01/02/2024,"Unknown Account",Z99999999,"Electronic Funds Transfer Received",,,,,,500,01/03/2024',
    ], header=FIDELITY_HEADER_SHORT)

    fidelity_src.ingest(empty_db, tmp_path)
    prices = pd.DataFrame(index=pd.to_datetime([date(2024, 1, 2)]).map(lambda d: d.date()))
    ctx = PriceContext(prices=prices, price_date=date(2024, 1, 2), mf_price_date=date(2024, 1, 2))
    rows = fidelity_src.positions_at(empty_db, date(2024, 1, 2), ctx, config)

    fzfxx = [r for r in rows if r.ticker == "FZFXX"]
    assert len(fzfxx) == 1
    assert fzfxx[0].value_usd == pytest.approx(500.0)


def test_default_mutual_funds_when_config_missing(tmp_path: Path, empty_db: Path) -> None:
    """Unspecified ``mutual_funds`` uses the documented T-1 lookup set."""
    config: dict[str, object] = {
        "fidelity_accounts": {"X12345678": "FZFXX"},
    }
    csv = tmp_path / "Accounts_History.csv"
    write_fidelity_csv(csv, [
        '01/02/2024,"Roth IRA",X12345678,"YOU BOUGHT FXAIX",FXAIX,"Fidelity 500 Index",Cash,10,150,-1500,01/03/2024',
    ], header=FIDELITY_HEADER_SHORT)

    fidelity_src.ingest(empty_db, tmp_path)
    prices = pd.DataFrame(
        {"FXAIX": [150.0]},
        index=pd.to_datetime([date(2024, 1, 1)]).map(lambda d: d.date()),
    )
    ctx = PriceContext(
        prices=prices,
        price_date=date(2024, 1, 2),
        mf_price_date=date(2024, 1, 1),
    )
    rows = fidelity_src.positions_at(empty_db, date(2024, 1, 2), ctx, config)

    fxaix = [r for r in rows if r.ticker == "FXAIX"]
    assert len(fxaix) == 1
    assert fxaix[0].value_usd == pytest.approx(1500.0)
