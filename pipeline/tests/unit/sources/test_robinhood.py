"""Unit tests for the Robinhood source module (post class→module refactor)."""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from etl.db import init_db
from etl.sources import PriceContext
from etl.sources import robinhood as robinhood_src


@pytest.fixture
def fixture_csv(tmp_path: Path) -> Path:
    p = tmp_path / "rh.csv"
    p.write_text(
        "Activity Date,Process Date,Settle Date,Instrument,Description,Trans Code,Quantity,Price,Amount\n"
        "1/5/2024,1/5/2024,1/8/2024,VTI,Vanguard Total Stock Mkt ETF,Buy,5,230.00,($1150.00)\n"
        "2/10/2024,2/10/2024,2/13/2024,VTI,Vanguard Total Stock Mkt ETF,CDIV,0,0,$3.25\n",
        encoding="utf-8",
    )
    return p


def test_produces_positions_always_on() -> None:
    assert robinhood_src.produces_positions({}) is True


def test_ingest_persists_normalized_rows(fixture_csv: Path, tmp_path: Path) -> None:
    db = tmp_path / "tm.db"
    init_db(db)
    robinhood_src.ingest(db, {"robinhood_csv": fixture_csv})
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT txn_date, action_kind, ticker, quantity, amount_usd FROM robinhood_transactions ORDER BY id"
    ).fetchall()
    conn.close()
    assert rows[0] == ("2024-01-05", "buy", "VTI", 5.0, -1150.0)   # ($x.xx) → negative
    assert rows[1] == ("2024-02-10", "dividend", "VTI", 0.0, 3.25)


def test_positions_at_with_prices(fixture_csv: Path, tmp_path: Path) -> None:
    db = tmp_path / "tm.db"
    init_db(db)
    robinhood_src.ingest(db, {"robinhood_csv": fixture_csv})
    prices = pd.DataFrame(
        {"VTI": [250.0]},
        index=pd.to_datetime([date(2024, 2, 10)]).map(lambda d: d.date()),
    )
    ctx = PriceContext(prices=prices, price_date=date(2024, 2, 10), mf_price_date=date(2024, 2, 10))
    rows = robinhood_src.positions_at(db, date(2024, 2, 10), ctx, {})
    vti = [r for r in rows if r.ticker == "VTI"]
    assert len(vti) == 1
    assert vti[0].quantity == pytest.approx(5.0)
    assert vti[0].value_usd == pytest.approx(1250.0)
    assert vti[0].cost_basis_usd == pytest.approx(1150.0)


def test_ingest_is_idempotent(fixture_csv: Path, tmp_path: Path) -> None:
    """Running ingest twice must not double the rows (range-replace)."""
    db = tmp_path / "tm.db"
    init_db(db)
    cfg: dict[str, object] = {"robinhood_csv": fixture_csv}
    robinhood_src.ingest(db, cfg)
    robinhood_src.ingest(db, cfg)
    conn = sqlite3.connect(str(db))
    count = conn.execute("SELECT COUNT(*) FROM robinhood_transactions").fetchone()[0]
    conn.close()
    assert count == 2


def test_ingest_missing_csv_is_noop(tmp_path: Path) -> None:
    """A missing Robinhood CSV is treated as 'user has no Robinhood holdings' — no error."""
    db = tmp_path / "tm.db"
    init_db(db)
    robinhood_src.ingest(db, {"robinhood_csv": tmp_path / "does_not_exist.csv"})
    conn = sqlite3.connect(str(db))
    count = conn.execute("SELECT COUNT(*) FROM robinhood_transactions").fetchone()[0]
    conn.close()
    assert count == 0


def test_ingest_missing_config_key_is_noop(tmp_path: Path) -> None:
    """Missing ``robinhood_csv`` key → silent no-op (sentinel path doesn't exist)."""
    db = tmp_path / "tm.db"
    init_db(db)
    robinhood_src.ingest(db, {})
    conn = sqlite3.connect(str(db))
    count = conn.execute("SELECT COUNT(*) FROM robinhood_transactions").fetchone()[0]
    conn.close()
    assert count == 0
